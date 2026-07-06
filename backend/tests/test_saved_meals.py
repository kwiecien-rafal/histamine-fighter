"""Endpoint tests for per-user saved meals: gates, snapshots, dedupe, edit, delete.

These run against the test database (the conftest ``user_client`` shares the
rolled-back session). The save gates mirror the public reads: only approved
curated meals and revealed daily suggestions can be copied, and every miss is a
404 so ids cannot be probed.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.embeddings import EMBEDDING_DIM
from app.enums import ApprovalStatus, MealType, Role, SafetyLevel, SaveSource
from app.models import CuratedMeal, DailySuggestion, SavedMeal
from app.models.user import User

_ZERO_VECTOR = [0.0] * EMBEDDING_DIM


async def _add_meal(
    session: AsyncSession,
    *,
    approval_status: ApprovalStatus = ApprovalStatus.APPROVED,
) -> CuratedMeal:
    meal = CuratedMeal(
        name="Courgette ribbon salad",
        meal_type=MealType.LUNCH,
        description="raw courgette ribbons with olive oil and fresh herbs",
        ingredients=[{"name": "courgette", "category": "vegetable"}],
        recipe=["Peel into ribbons.", "Toss with oil and herbs."],
        tags=["fresh"],
        cautioned_ingredients=[{"name": "spinach", "note": "fresh only"}],
        model="fake/test",
        reasoning_trace=[{"kind": "verify", "text": "All ingredients cleared the index."}],
        approval_status=approval_status,
        embedding=_ZERO_VECTOR,
    )
    session.add(meal)
    await session.flush()
    return meal


async def _add_suggestion(
    session: AsyncSession,
    *,
    reveal_at: datetime,
    approval_status: ApprovalStatus = ApprovalStatus.APPROVED,
) -> DailySuggestion:
    row = DailySuggestion(
        suggestion_date=reveal_at.date(),
        meal_type=MealType.BREAKFAST,
        content={
            "name": "Oat porridge",
            "description": "oats with pear and maple",
            "ingredients": [{"name": "oats", "category": "grain"}],
            "recipe": ["Simmer the oats.", "Top with pear."],
            "tags": ["warm"],
            "unverified_ingredients": ["maple syrup"],
        },
        model="fake/test",
        reasoning_trace=[],
        reveal_at=reveal_at,
        approval_status=approval_status,
    )
    session.add(row)
    await session.flush()
    return row


def _lookup_body(dish: str = "Spaghetti with herb sauce") -> dict[str, object]:
    return {
        "source": "lookup",
        "dish": dish,
        "verdict": "depends",
        "description": "Fresh tomato swapped for courgette keeps it in range.",
        "ingredients": [{"name": "courgette", "category": "vegetable"}],
        "model": "fake/test",
    }


# --- auth gate ------------------------------------------------------------------


async def test_every_route_requires_a_session(client: AsyncClient) -> None:
    some_id = uuid4()
    assert (await client.get("/api/v1/me/meals")).status_code == 401
    assert (await client.get(f"/api/v1/me/meals/{some_id}")).status_code == 401
    assert (await client.post("/api/v1/me/meals", json=_lookup_body())).status_code == 401
    assert (
        await client.patch(f"/api/v1/me/meals/{some_id}", json={"name": "x"})
    ).status_code == 401
    assert (await client.delete(f"/api/v1/me/meals/{some_id}")).status_code == 401


# --- saving curated meals ---------------------------------------------------------


async def test_save_approved_curated_meal_snapshots_it(
    user_client: AsyncClient, session: AsyncSession
) -> None:
    meal = await _add_meal(session)

    resp = await user_client.post(
        "/api/v1/me/meals", json={"source": "curated", "source_id": str(meal.id)}
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["source"] == "curated"
    assert body["source_key"] == str(meal.id)
    assert body["name"] == meal.name
    assert body["ingredients"] == [{"name": "courgette", "category": "vegetable"}]
    assert body["recipe"] == meal.recipe
    assert body["cautioned_ingredients"] == [{"name": "spinach", "note": "fresh only"}]
    # Free-form source tags are dropped; the copy is seeded with its meal slot.
    assert body["tags"] == ["lunch"]
    assert body["verdict"] is None
    assert body["edited_at"] is None


async def test_save_unapproved_curated_meal_is_404(
    user_client: AsyncClient, session: AsyncSession
) -> None:
    meal = await _add_meal(session, approval_status=ApprovalStatus.PENDING)

    resp = await user_client.post(
        "/api/v1/me/meals", json={"source": "curated", "source_id": str(meal.id)}
    )

    assert resp.status_code == 404


async def test_duplicate_save_returns_the_existing_snapshot(
    user_client: AsyncClient, session: AsyncSession
) -> None:
    meal = await _add_meal(session)
    body = {"source": "curated", "source_id": str(meal.id)}

    first = await user_client.post("/api/v1/me/meals", json=body)
    second = await user_client.post("/api/v1/me/meals", json=body)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    rows = (await session.execute(select(SavedMeal))).scalars().all()
    assert len(rows) == 1


# --- saving daily suggestions -----------------------------------------------------


async def test_save_revealed_daily_suggestion_snapshots_public_content(
    user_client: AsyncClient, session: AsyncSession
) -> None:
    row = await _add_suggestion(session, reveal_at=datetime.now(UTC) - timedelta(hours=2))

    resp = await user_client.post(
        "/api/v1/me/meals", json={"source": "daily", "source_id": str(row.id)}
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["source"] == "daily"
    assert body["name"] == "Oat porridge"
    assert body["meal_type"] == "breakfast"
    assert body["tags"] == ["breakfast"]
    # The review-only field never reaches the user's copy.
    stored = (await session.execute(select(SavedMeal))).scalar_one()
    assert "unverified_ingredients" not in stored.ingredients[0]
    assert stored.name == "Oat porridge"


async def test_save_unrevealed_daily_suggestion_is_404(
    user_client: AsyncClient, session: AsyncSession
) -> None:
    row = await _add_suggestion(session, reveal_at=datetime.now(UTC) + timedelta(hours=2))

    resp = await user_client.post(
        "/api/v1/me/meals", json={"source": "daily", "source_id": str(row.id)}
    )

    assert resp.status_code == 404


async def test_save_unapproved_daily_suggestion_is_404(
    user_client: AsyncClient, session: AsyncSession
) -> None:
    row = await _add_suggestion(
        session,
        reveal_at=datetime.now(UTC) - timedelta(hours=2),
        approval_status=ApprovalStatus.PENDING,
    )

    resp = await user_client.post(
        "/api/v1/me/meals", json={"source": "daily", "source_id": str(row.id)}
    )

    assert resp.status_code == 404


# --- saving lookup results --------------------------------------------------------


async def test_save_lookup_result_derives_the_key_server_side(
    user_client: AsyncClient,
) -> None:
    resp = await user_client.post("/api/v1/me/meals", json=_lookup_body("  Spaghetti  "))

    assert resp.status_code == 201
    body = resp.json()
    assert body["source"] == "lookup"
    assert body["source_key"] == "spaghetti"
    assert body["name"] == "Spaghetti"
    assert body["verdict"] == "depends"
    assert body["meal_type"] is None
    assert body["recipe"] is None
    assert body["tags"] == ["dish_check"]


async def test_resave_of_a_reassessed_dish_keeps_the_first_snapshot(
    user_client: AsyncClient,
) -> None:
    first = await user_client.post("/api/v1/me/meals", json=_lookup_body("Spaghetti"))
    changed = _lookup_body("SPAGHETTI")
    changed["verdict"] = "avoid"

    second = await user_client.post("/api/v1/me/meals", json=changed)

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["verdict"] == "depends"


async def test_lookup_save_with_no_usable_ingredients_is_422(
    user_client: AsyncClient,
) -> None:
    body = _lookup_body()
    body["ingredients"] = [{"name": "   "}]

    resp = await user_client.post("/api/v1/me/meals", json=body)

    assert resp.status_code == 422


async def test_lookup_save_truncates_oversized_text(user_client: AsyncClient) -> None:
    body = _lookup_body()
    body["description"] = "x" * 5000

    resp = await user_client.post("/api/v1/me/meals", json=body)

    assert resp.status_code == 201
    assert len(resp.json()["description"]) == 1000


async def test_save_cap_answers_409(
    user_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "saved_meals_cap", 1)
    assert (
        await user_client.post("/api/v1/me/meals", json=_lookup_body("First dish"))
    ).status_code == 201

    resp = await user_client.post("/api/v1/me/meals", json=_lookup_body("Second dish"))

    assert resp.status_code == 409
    # Re-saving the dish already held still answers with the existing row.
    assert (
        await user_client.post("/api/v1/me/meals", json=_lookup_body("First dish"))
    ).status_code == 200


# --- list / detail / ownership ----------------------------------------------------


async def test_list_returns_own_saves_newest_first(
    user_client: AsyncClient, session: AsyncSession
) -> None:
    meal = await _add_meal(session)
    await user_client.post(
        "/api/v1/me/meals", json={"source": "curated", "source_id": str(meal.id)}
    )
    await user_client.post("/api/v1/me/meals", json=_lookup_body())

    resp = await user_client.get("/api/v1/me/meals")

    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 2
    assert {item["source"] for item in items} == {"curated", "lookup"}
    assert all("ingredients" not in item for item in items)
    assert resp.headers["Cache-Control"] == "no-store"


async def test_foreign_and_unknown_save_ids_read_as_404(
    user_client: AsyncClient, session: AsyncSession, public_user: User
) -> None:
    other = User(email="other@example.com", role=Role.USER, password_hash=None)
    session.add(other)
    await session.flush()
    foreign = SavedMeal(
        user_id=other.id,
        source=SaveSource.LOOKUP,
        source_key="their dish",
        name="Their dish",
        description="not yours",
        ingredients=[{"name": "oats", "category": None}],
        model="fake/test",
        verdict=SafetyLevel.SAFE,
    )
    session.add(foreign)
    await session.flush()

    assert (await user_client.get(f"/api/v1/me/meals/{foreign.id}")).status_code == 404
    assert (await user_client.get(f"/api/v1/me/meals/{uuid4()}")).status_code == 404
    assert (
        await user_client.patch(
            f"/api/v1/me/meals/{foreign.id}",
            json={
                "name": "hijacked",
                "description": "x",
                "ingredients": [{"name": "oats"}],
            },
        )
    ).status_code == 404
    foreign_id = foreign.id
    assert (await user_client.delete(f"/api/v1/me/meals/{foreign_id}")).status_code == 404
    session.expire_all()
    untouched = await session.get(SavedMeal, foreign_id)
    assert untouched is not None and untouched.name == "Their dish"


# --- edit and delete --------------------------------------------------------------


async def test_edit_marks_the_copy_user_modified(
    user_client: AsyncClient, session: AsyncSession
) -> None:
    meal = await _add_meal(session)
    created = await user_client.post(
        "/api/v1/me/meals", json={"source": "curated", "source_id": str(meal.id)}
    )
    save_id = created.json()["id"]

    resp = await user_client.patch(
        f"/api/v1/me/meals/{save_id}",
        json={
            "name": "My courgette salad",
            "description": "with extra herbs",
            "ingredients": [{"name": "courgette", "category": "vegetable"}],
            "recipe": ["Peel.", "Toss."],
            "tags": ["Lunch", "green", "lunch"],
        },
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "My courgette salad"
    # Vocabulary tags are casefolded and deduped, order preserved.
    assert body["tags"] == ["lunch", "green"]
    assert body["edited_at"] is not None
    # The source meal is untouched; only the user's copy changed.
    meal_id = meal.id
    session.expire_all()
    source = await session.get(CuratedMeal, meal_id)
    assert source is not None and source.name == "Courgette ribbon salad"


async def test_edit_with_a_tag_outside_the_vocabulary_is_422(
    user_client: AsyncClient, session: AsyncSession
) -> None:
    meal = await _add_meal(session)
    created = await user_client.post(
        "/api/v1/me/meals", json={"source": "curated", "source_id": str(meal.id)}
    )
    save_id = created.json()["id"]

    resp = await user_client.patch(
        f"/api/v1/me/meals/{save_id}",
        json={
            "name": "My courgette salad",
            "description": "with extra herbs",
            "ingredients": [{"name": "courgette", "category": "vegetable"}],
            "tags": ["mine"],
        },
    )

    assert resp.status_code == 422


async def test_edit_keeps_tags_beyond_the_composer_cap(
    user_client: AsyncClient, session: AsyncSession
) -> None:
    meal = await _add_meal(session)
    created = await user_client.post(
        "/api/v1/me/meals", json={"source": "curated", "source_id": str(meal.id)}
    )
    save_id = created.json()["id"]

    # Nine vocabulary tags: past the composer's 8-tag cap, which would have silently
    # dropped the last one. The closed vocabulary bounds the count instead.
    tags = ["breakfast", "lunch", "dinner", "snack", "dish_check", "pink", "red", "green", "blue"]
    resp = await user_client.patch(
        f"/api/v1/me/meals/{save_id}",
        json={
            "name": "Every-tag salad",
            "description": "tagged to the hilt",
            "ingredients": [{"name": "courgette", "category": "vegetable"}],
            "tags": tags,
        },
    )

    assert resp.status_code == 200
    assert resp.json()["tags"] == tags


async def test_unsave_removes_the_row(user_client: AsyncClient, session: AsyncSession) -> None:
    created = await user_client.post("/api/v1/me/meals", json=_lookup_body())
    save_id = created.json()["id"]

    resp = await user_client.delete(f"/api/v1/me/meals/{save_id}")

    assert resp.status_code == 204
    await session.flush()
    session.expire_all()
    rows = (await session.execute(select(SavedMeal))).scalars().all()
    assert rows == []
