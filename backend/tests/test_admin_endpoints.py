"""Endpoint tests for the curated-meal review gate: list, approve, reject, delete.

These run against the test database (the conftest ``authenticated_client`` shares the
rolled-back session and carries the session cookie), so they cover the real path end
to end: the gate re-reads the user, and the review service flips status on real rows.
Login, logout, and the auth/role gate itself live in test_auth_endpoints.
"""

from datetime import UTC, datetime

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.embeddings import EMBEDDING_DIM
from app.enums import ApprovalStatus, Compatibility, MealType
from app.models import CuratedMeal, HistamineIngredient
from app.models.user import User
from app.schemas.admin import AdminMealUpdate
from app.schemas.meal import (
    MAX_CONFIRMED_INGREDIENTS,
    MAX_DESCRIPTION_CHARS,
    MAX_DISH_CHARS,
    MAX_INGREDIENT_CHARS,
)
from app.services.meal_service import MANUAL_MODEL

_ZERO_VECTOR = [0.0] * EMBEDDING_DIM


async def _seed_index(session: AsyncSession) -> None:
    session.add_all(
        [
            HistamineIngredient(
                name="parmesan",
                sources=["test"],
                compatibility=Compatibility.INCOMPATIBLE,
                category="aged hard cheese",
            ),
            HistamineIngredient(
                name="courgette",
                sources=["test"],
                compatibility=Compatibility.WELL_TOLERATED,
                category="vegetable",
            ),
            HistamineIngredient(
                name="spinach",
                sources=["test"],
                compatibility=Compatibility.MODERATELY_COMPATIBLE,
                category="vegetable",
            ),
        ]
    )
    await session.flush()


def _edit_body(meal: CuratedMeal, *, ingredients: list[dict[str, str]]) -> dict[str, object]:
    """A full edit body that changes only the ingredients, so the text is untouched."""
    return {
        "name": meal.name,
        "description": meal.description,
        "ingredients": ingredients,
        "recipe": meal.recipe,
        "tags": list(meal.tags),
    }


def _create_body(
    *,
    ingredients: list[dict[str, str]],
    meal_type: str = "lunch",
    recipe: list[str] | None = None,
    tags: list[str] | None = None,
) -> dict[str, object]:
    """A manual-meal create body: the editable fields plus the slot a new meal needs."""
    return {
        "name": "Courgette ribbon salad",
        "description": "raw courgette ribbons with olive oil and fresh herbs",
        "meal_type": meal_type,
        "ingredients": ingredients,
        "recipe": recipe,
        "tags": tags or [],
    }


async def _add_meal(
    session: AsyncSession,
    *,
    name: str = "Courgette ribbon salad",
    approval_status: ApprovalStatus = ApprovalStatus.PENDING,
    created_at: datetime | None = None,
) -> CuratedMeal:
    meal = CuratedMeal(
        name=name,
        meal_type=MealType.LUNCH,
        description="raw courgette ribbons with olive oil and fresh herbs",
        ingredients=[{"name": "courgette", "category": "vegetable"}],
        recipe=["Peel into ribbons.", "Toss with oil and herbs."],
        tags=["fresh"],
        model="fake/test",
        reasoning_trace=[{"kind": "verify", "text": "All ingredients cleared the index."}],
        approval_status=approval_status,
        embedding=_ZERO_VECTOR,
    )
    # now() is transaction-scoped in Postgres, so same-transaction rows share a
    # created_at; set it explicitly when a test needs a deterministic order.
    if created_at is not None:
        meal.created_at = created_at
    session.add(meal)
    await session.flush()
    return meal


# --- GET /admin/meals -------------------------------------------------------------


async def test_list_pending_meals_returns_the_queue_with_review_detail(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    await _add_meal(session, name="Pending salad", approval_status=ApprovalStatus.PENDING)
    await _add_meal(session, name="Approved bake", approval_status=ApprovalStatus.APPROVED)

    resp = await authenticated_client.get("/admin/meals")

    assert resp.status_code == 200
    body = resp.json()
    assert [meal["name"] for meal in body] == ["Pending salad"]
    meal = body[0]
    # The reviewer sees the ingredients and the trace, not just a title.
    assert meal["ingredients"] == [{"name": "courgette", "category": "vegetable"}]
    assert meal["reasoning_trace"][0]["kind"] == "verify"
    assert meal["approval_status"] == "pending"


async def test_list_meals_filters_by_status(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    await _add_meal(session, name="Pending salad", approval_status=ApprovalStatus.PENDING)
    await _add_meal(session, name="Approved bake", approval_status=ApprovalStatus.APPROVED)

    resp = await authenticated_client.get("/admin/meals", params={"status": "approved"})

    assert resp.status_code == 200
    assert [meal["name"] for meal in resp.json()] == ["Approved bake"]


async def test_list_meals_is_oldest_first(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    # Insert newest first to prove the order is by created_at, not insertion.
    await _add_meal(session, name="Newer", created_at=datetime(2026, 1, 2, tzinfo=UTC))
    await _add_meal(session, name="Older", created_at=datetime(2026, 1, 1, tzinfo=UTC))

    resp = await authenticated_client.get("/admin/meals")

    assert resp.status_code == 200
    assert [meal["name"] for meal in resp.json()] == ["Older", "Newer"]


async def test_list_meals_paginates_with_limit_and_offset(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    await _add_meal(session, name="First", created_at=datetime(2026, 1, 1, tzinfo=UTC))
    await _add_meal(session, name="Second", created_at=datetime(2026, 1, 2, tzinfo=UTC))
    await _add_meal(session, name="Third", created_at=datetime(2026, 1, 3, tzinfo=UTC))

    resp = await authenticated_client.get("/admin/meals", params={"limit": 1, "offset": 1})

    assert resp.status_code == 200
    assert [meal["name"] for meal in resp.json()] == ["Second"]


async def test_list_meals_rejects_an_out_of_range_limit(
    authenticated_client: AsyncClient,
) -> None:
    resp = await authenticated_client.get("/admin/meals", params={"limit": 0})

    assert resp.status_code == 422


# --- PATCH /admin/meals/{id}/approve | reject -------------------------------------


async def test_approve_flips_status_and_stamps_the_actor(
    authenticated_client: AsyncClient, session: AsyncSession, admin_user: User
) -> None:
    meal = await _add_meal(session)

    resp = await authenticated_client.patch(f"/admin/meals/{meal.id}/approve")

    assert resp.status_code == 200
    body = resp.json()
    assert body["approval_status"] == "approved"
    assert body["approved_by"] == admin_user.email
    assert body["approved_at"] is not None

    # The test session deliberately never commits, so flush the endpoint's pending
    # UPDATE before reading the row back from the database.
    await session.flush()
    await session.refresh(meal)
    assert meal.approval_status is ApprovalStatus.APPROVED
    assert meal.approved_by == admin_user.email
    assert meal.approved_at is not None


async def test_reject_flips_status_and_clears_any_approval_stamp(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    meal = await _add_meal(session, approval_status=ApprovalStatus.APPROVED)
    meal.approved_by = "someone@example.com"
    await session.flush()

    resp = await authenticated_client.patch(f"/admin/meals/{meal.id}/reject")

    assert resp.status_code == 200
    body = resp.json()
    assert body["approval_status"] == "rejected"
    assert body["approved_by"] is None
    assert body["approved_at"] is None


async def test_approve_unknown_meal_is_404(authenticated_client: AsyncClient) -> None:
    resp = await authenticated_client.patch(
        "/admin/meals/00000000-0000-0000-0000-000000000000/approve"
    )

    assert resp.status_code == 404


async def test_approve_without_a_session_is_401(client: AsyncClient, session: AsyncSession) -> None:
    meal = await _add_meal(session)

    resp = await client.patch(f"/admin/meals/{meal.id}/approve")

    assert resp.status_code == 401


# --- DELETE /admin/meals/{id} -----------------------------------------------------


async def test_delete_meal_removes_the_row(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    meal = await _add_meal(session)

    resp = await authenticated_client.delete(f"/admin/meals/{meal.id}")

    assert resp.status_code == 204
    await session.flush()
    remaining = (
        await session.execute(select(CuratedMeal).where(CuratedMeal.id == meal.id))
    ).scalar_one_or_none()
    assert remaining is None


async def test_delete_unknown_meal_is_404(authenticated_client: AsyncClient) -> None:
    resp = await authenticated_client.delete("/admin/meals/00000000-0000-0000-0000-000000000000")

    assert resp.status_code == 404


# --- POST /admin/meals (manual create) --------------------------------------------


def test_manual_model_sentinel_is_stable() -> None:
    # The frontend hardcodes the same literal (api/domain.ts MANUAL_MODEL) to render
    # "Curated by admin". Pinned on both sides so a one-sided rename fails a test here
    # rather than silently rendering a manual meal as "Composed by MANUAL".
    assert MANUAL_MODEL == "manual"


async def test_create_manual_meal_lands_pending(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_index(session)
    body = _create_body(ingredients=[{"name": "courgette", "category": "vegetable"}])

    resp = await authenticated_client.post("/admin/meals", json=body)

    assert resp.status_code == 201
    created = resp.json()
    assert created["approval_status"] == "pending"
    # No model composed it: the manual sentinel stands in, with no usage and no trace, so
    # the UI shows "Curated by admin" and offers no replay.
    assert created["model"] == MANUAL_MODEL
    assert created["usage"] is None
    assert created["reasoning_trace"] == []
    # The server-default timestamp is populated by the endpoint's flush, so the response
    # carries it (AdminMealRead requires created_at); a missing flush would 500 here.
    assert created["created_at"]

    await session.flush()
    stored = (
        await session.execute(select(CuratedMeal).where(CuratedMeal.id == created["id"]))
    ).scalar_one()
    assert stored.approval_status is ApprovalStatus.PENDING
    assert stored.model == MANUAL_MODEL


async def test_create_manual_meal_records_unverified_ingredients(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_index(session)
    # An indexed-safe ingredient plus one the index has no entry for: a miss is unknown,
    # not unsafe, so it clears the gate but is recorded for the approving admin.
    body = _create_body(
        ingredients=[
            {"name": "courgette", "category": "vegetable"},
            {"name": "mystery spice", "category": "spice"},
        ]
    )

    resp = await authenticated_client.post("/admin/meals", json=body)

    assert resp.status_code == 201
    assert resp.json()["unverified_ingredients"] == ["mystery spice"]


async def test_create_manual_meal_rejects_a_blocker(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_index(session)
    body = _create_body(
        ingredients=[
            {"name": "courgette", "category": "vegetable"},
            {"name": "parmesan", "category": "aged hard cheese"},
        ]
    )

    resp = await authenticated_client.post("/admin/meals", json=body)

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any("parmesan" in blocker for blocker in detail["blockers"])
    assert detail["can_confirm"] is True


async def test_create_manual_meal_allows_moderately_compatible(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    # A depends-level reading warns end users but never blocks a hand-authored meal.
    await _seed_index(session)
    body = _create_body(ingredients=[{"name": "spinach", "category": "vegetable"}])

    resp = await authenticated_client.post("/admin/meals", json=body)

    assert resp.status_code == 201
    assert resp.json()["unverified_ingredients"] == []


async def test_create_manual_meal_confirm_flagged_saves_and_records(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_index(session)
    body = _create_body(
        ingredients=[
            {"name": "courgette", "category": "vegetable"},
            {"name": "parmesan", "category": "aged hard cheese"},
        ]
    )
    body["confirm_flagged"] = True

    resp = await authenticated_client.post("/admin/meals", json=body)

    assert resp.status_code == 201
    # The confirmed flag is surfaced to the approving reviewer alongside the
    # not-indexed list, so the override is visible, not silent.
    assert resp.json()["unverified_ingredients"] == ["parmesan (avoid)"]


async def test_create_manual_meal_allows_a_risky_recipe_mention(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    # The admin gate does not scan recipe prose, so a note naming a flagged term passes.
    await _seed_index(session)
    body = _create_body(
        ingredients=[{"name": "courgette", "category": "vegetable"}],
        recipe=["Peel into ribbons.", "A little parmesan is fine in moderation."],
    )

    resp = await authenticated_client.post("/admin/meals", json=body)

    assert resp.status_code == 201


async def test_create_without_a_session_is_401(client: AsyncClient) -> None:
    body = _create_body(ingredients=[{"name": "courgette", "category": "vegetable"}])

    resp = await client.post("/admin/meals", json=body)

    assert resp.status_code == 401


# --- PATCH /admin/meals/{id} (edit) -----------------------------------------------


async def test_edit_pending_meal_rederives_unverified(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_index(session)
    meal = await _add_meal(session)
    # An indexed-safe ingredient plus one the index has no entry for.
    body = _edit_body(
        meal,
        ingredients=[
            {"name": "courgette", "category": "vegetable"},
            {"name": "mystery spice", "category": "spice"},
        ],
    )

    resp = await authenticated_client.patch(f"/admin/meals/{meal.id}", json=body)

    assert resp.status_code == 200
    updated = resp.json()
    assert updated["unverified_ingredients"] == ["mystery spice"]
    assert [item["name"] for item in updated["ingredients"]] == ["courgette", "mystery spice"]


async def test_edit_rejects_an_introduced_blocker(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_index(session)
    meal = await _add_meal(session)
    body = _edit_body(
        meal,
        ingredients=[
            {"name": "courgette", "category": "vegetable"},
            {"name": "parmesan", "category": "aged hard cheese"},
        ],
    )

    resp = await authenticated_client.patch(f"/admin/meals/{meal.id}", json=body)

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert any("parmesan" in blocker for blocker in detail["blockers"])
    assert detail["can_confirm"] is True


async def test_edit_confirm_flagged_saves_and_records(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_index(session)
    meal = await _add_meal(session)
    body = _edit_body(
        meal,
        ingredients=[
            {"name": "courgette", "category": "vegetable"},
            {"name": "parmesan", "category": "aged hard cheese"},
        ],
    )
    body["confirm_flagged"] = True

    resp = await authenticated_client.patch(f"/admin/meals/{meal.id}", json=body)

    assert resp.status_code == 200
    assert resp.json()["unverified_ingredients"] == ["parmesan (avoid)"]


async def test_edit_allows_a_risky_recipe_mention(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    # The admin gate does not scan recipe prose, so a note naming a flagged term passes.
    await _seed_index(session)
    meal = await _add_meal(session)
    body = _edit_body(meal, ingredients=[{"name": "courgette", "category": "vegetable"}])
    body["recipe"] = ["Peel into ribbons.", "A little parmesan is fine in moderation."]

    resp = await authenticated_client.patch(f"/admin/meals/{meal.id}", json=body)

    assert resp.status_code == 200


async def test_edit_an_approved_meal_is_409(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    meal = await _add_meal(session, approval_status=ApprovalStatus.APPROVED)
    body = _edit_body(meal, ingredients=[{"name": "courgette", "category": "vegetable"}])

    resp = await authenticated_client.patch(f"/admin/meals/{meal.id}", json=body)

    assert resp.status_code == 409


async def test_edit_with_an_oversized_ingredient_list_is_capped_like_the_composer(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    # The composer truncates an over-cap list rather than rejecting it; an edit normalizes
    # the same way, so a list past the cap is capped to MAX_CONFIRMED_INGREDIENTS, not 422'd.
    meal = await _add_meal(session)
    body = _edit_body(meal, ingredients=[{"name": f"item {n}", "category": "x"} for n in range(30)])

    resp = await authenticated_client.patch(f"/admin/meals/{meal.id}", json=body)

    assert resp.status_code == 200
    assert len(resp.json()["ingredients"]) == MAX_CONFIRMED_INGREDIENTS


async def test_edit_truncates_an_over_long_ingredient_name_like_the_composer(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    # The composer truncates an ingredient name to the per-item cap; the edit normalizes the
    # same way rather than 422'ing a name the composer would have stored.
    meal = await _add_meal(session)
    long_name = "x" * (MAX_INGREDIENT_CHARS + 50)
    body = _edit_body(meal, ingredients=[{"name": long_name, "category": ""}])

    resp = await authenticated_client.patch(f"/admin/meals/{meal.id}", json=body)

    assert resp.status_code == 200
    assert len(resp.json()["ingredients"][0]["name"]) == MAX_INGREDIENT_CHARS


def test_meal_edit_truncates_over_long_text_like_the_composer() -> None:
    # A composed name/description carries no length cap, so the edit schema truncates to the
    # same bound rather than rejecting a meal the composer could have produced.
    update = AdminMealUpdate.model_validate(
        {
            "name": "n" * (MAX_DISH_CHARS + 50),
            "description": "d" * (MAX_DESCRIPTION_CHARS + 50),
            "ingredients": [{"name": "courgette", "category": "vegetable"}],
            "recipe": None,
            "tags": [],
        }
    )

    assert len(update.name) == MAX_DISH_CHARS
    assert len(update.description) == MAX_DESCRIPTION_CHARS


async def test_edit_without_a_session_is_401(client: AsyncClient, session: AsyncSession) -> None:
    meal = await _add_meal(session)
    body = _edit_body(meal, ingredients=[{"name": "courgette", "category": "vegetable"}])

    resp = await client.patch(f"/admin/meals/{meal.id}", json=body)

    assert resp.status_code == 401
