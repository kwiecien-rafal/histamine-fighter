"""Page tests for the admin panel: the gate, the two review queues, and the meal forms.

The moderation rules themselves — who may approve, what the index gate refuses, what an
approval stamps — are covered against the JSON API in test_admin_endpoints.py and
test_admin_edit_gate.py, and these routes call those very handlers. What is asserted here
is the browser's side: who gets the sign-in form, what the panel puts on screen, where a
write redirects to, and what a refusal says.

The compose triggers are deliberately absent. They stream over POST and are driven by
admin.js, so the panel only has to offer the forms; the streams have their own tests in
test_compose_endpoints.py.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.enums import ApprovalStatus, Compatibility, MealType, Role
from app.models import CuratedMeal, DailySuggestion, HistamineIngredient
from app.models.generation_settings import GenerationSettings
from app.models.user import User
from app.services.meal_service import MANUAL_MODEL
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD
from tests.web.factories import add_curated_meal, add_daily_suggestion

_TOMORROW = datetime.now(UTC) + timedelta(days=1)


async def _seed_index(session: AsyncSession) -> None:
    """The two index readings a submitted meal can be judged on: cleared, and flagged."""
    session.add_all(
        [
            HistamineIngredient(
                name="courgette",
                sources=["test"],
                compatibility=Compatibility.WELL_TOLERATED,
                category="vegetable",
            ),
            HistamineIngredient(
                name="parmesan",
                sources=["test"],
                compatibility=Compatibility.INCOMPATIBLE,
                category="aged hard cheese",
            ),
        ]
    )
    await session.flush()


def _meal_form(**overrides: str) -> dict[str, str]:
    """A complete meal submission, so a test only states the field it is about."""
    return {
        "meal_type": MealType.LUNCH.value,
        "name": "Courgette ribbon salad",
        "description": "raw courgette ribbons with olive oil and fresh herbs",
        "ingredients": "courgette | vegetable\nolive oil",
        "recipe": "Peel into ribbons.\nToss with oil.",
        "tags": "fresh, quick",
    } | overrides


async def _reload_meal(session: AsyncSession, meal: CuratedMeal) -> CuratedMeal:
    """Re-read a curated row, flushing the request's pending writes on the way."""
    return (
        await session.execute(select(CuratedMeal).where(CuratedMeal.id == meal.id))
    ).scalar_one()


@pytest.fixture
def configured_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin one cloud provider as configured, whatever the developer's .env holds."""
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(settings, "gemini_api_key", None)
    monkeypatch.setattr(settings, "openrouter_api_key", None)


# --- the gate ---------------------------------------------------------------------


async def test_an_anonymous_visitor_gets_the_sign_in_form(client: AsyncClient) -> None:
    response = await client.get("/admin")

    assert response.status_code == 200
    assert 'action="/admin/login"' in response.text
    assert "Daily queue" not in response.text
    # Privileged either way, so nothing along the way may keep it.
    assert response.headers["cache-control"] == "no-store"


async def test_a_signed_in_visitor_without_admin_is_told_so(user_client: AsyncClient) -> None:
    response = await user_client.get("/admin")

    assert response.status_code == 200
    assert "not an admin account" in response.text
    assert 'action="/admin/login"' not in response.text


async def test_a_write_from_an_anonymous_visitor_goes_back_to_the_form(
    client: AsyncClient, session: AsyncSession
) -> None:
    meal = await add_curated_meal(session, approval_status=ApprovalStatus.PENDING)

    response = await client.post(f"/admin/ui/meals/{meal.id}/approve")

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    assert (await _reload_meal(session, meal)).approval_status is ApprovalStatus.PENDING


async def test_the_panel_shows_every_section(authenticated_client: AsyncClient) -> None:
    response = await authenticated_client.get("/admin")

    assert response.status_code == 200
    for section in ("Composer model", "Daily queue", "Curated pool"):
        assert section in response.text


async def test_the_panel_wires_both_compose_triggers(authenticated_client: AsyncClient) -> None:
    """The one part of the panel a page test can still hold: what admin.js is handed.

    The streams themselves are covered in test_compose_endpoints.py; what the page owes
    them is the right endpoint on each form and an output region to write into.
    """
    response = await authenticated_client.get("/admin")

    assert 'data-url="/admin/compose/curated"' in response.text
    assert 'data-url="/admin/compose/daily"' in response.text
    assert 'data-board-url="/admin/compose/daily/board"' in response.text
    assert response.text.count("data-compose-log") == 2


# --- signing in -------------------------------------------------------------------


async def test_signing_in_opens_a_session(client: AsyncClient, admin_user: User) -> None:
    response = await client.post(
        "/admin/login", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"
    assert client.cookies.get(settings.session_cookie_name)


async def test_a_wrong_password_says_nothing_about_the_account(
    client: AsyncClient, admin_user: User
) -> None:
    response = await client.post("/admin/login", data={"email": ADMIN_EMAIL, "password": "not-it"})

    assert response.status_code == 200
    assert "Incorrect email or password." in response.text
    assert client.cookies.get(settings.session_cookie_name) is None


async def test_a_non_admin_account_cannot_sign_in_here(
    client: AsyncClient, public_user: User
) -> None:
    response = await client.post(
        "/admin/login", data={"email": public_user.email, "password": ADMIN_PASSWORD}
    )

    assert response.status_code == 200
    assert "Incorrect email or password." in response.text


# --- the curated pool -------------------------------------------------------------


async def test_the_pool_shows_what_an_approval_rests_on(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    await add_curated_meal(
        session,
        name="Herb spaghetti",
        approval_status=ApprovalStatus.PENDING,
        cautioned=[{"name": "spinach", "note": "fresh only"}],
    )

    response = await authenticated_client.get("/admin")

    assert response.status_code == 200
    assert "Herb spaghetti" in response.text
    assert "courgette" in response.text
    assert "fresh only" in response.text
    # The reviewing admin sees the whole trace, the model's own drafting included.
    assert "the model thinking out loud" in response.text


async def test_the_pool_lists_one_review_state_at_a_time(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    await add_curated_meal(session, name="Waiting", approval_status=ApprovalStatus.PENDING)
    await add_curated_meal(session, name="Published", approval_status=ApprovalStatus.APPROVED)

    pending = await authenticated_client.get("/admin")
    approved = await authenticated_client.get("/admin?status=approved")

    assert "Waiting" in pending.text and "Published" not in pending.text
    assert "Published" in approved.text and "Waiting" not in approved.text


async def test_an_unknown_review_state_falls_back_to_the_queue(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    await add_curated_meal(session, name="Waiting", approval_status=ApprovalStatus.PENDING)

    response = await authenticated_client.get("/admin?status=whatever")

    assert response.status_code == 200
    assert "Waiting" in response.text


@pytest.mark.parametrize(
    ("action", "expected"),
    [("approve", ApprovalStatus.APPROVED), ("reject", ApprovalStatus.REJECTED)],
)
async def test_a_decision_moves_the_meal_and_returns_to_its_tab(
    authenticated_client: AsyncClient,
    session: AsyncSession,
    action: str,
    expected: ApprovalStatus,
) -> None:
    meal = await add_curated_meal(session, approval_status=ApprovalStatus.PENDING)

    response = await authenticated_client.post(
        f"/admin/ui/meals/{meal.id}/{action}", data={"status": "pending"}
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin?status=pending#curated"
    assert (await _reload_meal(session, meal)).approval_status is expected


async def test_removing_a_meal_drops_the_row(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    meal = await add_curated_meal(session, approval_status=ApprovalStatus.PENDING)

    response = await authenticated_client.post(f"/admin/ui/meals/{meal.id}/delete")

    assert response.status_code == 303
    await session.flush()
    remaining = (
        await session.execute(select(CuratedMeal).where(CuratedMeal.id == meal.id))
    ).scalar_one_or_none()
    assert remaining is None


async def test_deciding_a_meal_that_is_already_gone_just_returns(
    authenticated_client: AsyncClient,
) -> None:
    response = await authenticated_client.post(f"/admin/ui/meals/{uuid4()}/approve")

    assert response.status_code == 303
    assert response.headers["location"] == "/admin?status=pending#curated"


# --- writing a meal by hand -------------------------------------------------------


async def test_a_hand_written_meal_lands_pending(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_index(session)

    response = await authenticated_client.post("/admin/ui/meals", data=_meal_form())

    assert response.status_code == 303
    assert response.headers["location"] == "/admin#curated"
    stored = (await session.execute(select(CuratedMeal))).scalar_one()
    assert stored.approval_status is ApprovalStatus.PENDING
    assert stored.model == MANUAL_MODEL
    assert [item["name"] for item in stored.ingredients] == ["courgette", "olive oil"]
    assert stored.recipe == ["Peel into ribbons.", "Toss with oil."]
    assert stored.tags == ["fresh", "quick"]


async def test_a_meal_without_a_name_says_which_field(
    authenticated_client: AsyncClient,
) -> None:
    response = await authenticated_client.post("/admin/ui/meals", data=_meal_form(name="  "))

    assert response.status_code == 200
    assert "Give the meal a name." in response.text
    # The rest of the submission comes back, so only the bad field is retyped.
    assert "raw courgette ribbons" in response.text


async def test_a_flagged_ingredient_stops_the_save_once(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_index(session)

    response = await authenticated_client.post(
        "/admin/ui/meals", data=_meal_form(ingredients="courgette | vegetable\nparmesan")
    )

    assert response.status_code == 200
    assert "parmesan (avoid)" in response.text
    assert 'name="confirm_flagged"' in response.text
    assert (await session.execute(select(CuratedMeal))).first() is None


async def test_a_flagged_ingredient_can_be_confirmed_past(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_index(session)

    response = await authenticated_client.post(
        "/admin/ui/meals",
        data=_meal_form(ingredients="courgette | vegetable\nparmesan", confirm_flagged="true"),
    )

    assert response.status_code == 303
    stored = (await session.execute(select(CuratedMeal))).scalar_one()
    # Recorded on the row, so the approving admin sees what was waved through.
    assert stored.unverified_ingredients == ["parmesan (avoid)"]


# --- editing a curated meal -------------------------------------------------------


async def test_the_edit_form_arrives_filled_in(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    meal = await add_curated_meal(session, approval_status=ApprovalStatus.PENDING)

    response = await authenticated_client.get(f"/admin/ui/meals/{meal.id}")

    assert response.status_code == 200
    assert "courgette | vegetable" in response.text
    assert "Peel into ribbons." in response.text


async def test_only_a_pending_meal_opens_an_edit_form(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    meal = await add_curated_meal(session, approval_status=ApprovalStatus.APPROVED)

    response = await authenticated_client.get(f"/admin/ui/meals/{meal.id}")

    assert response.status_code == 303
    assert response.headers["location"] == "/admin"


async def test_an_edit_rewrites_the_meal(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_index(session)
    meal = await add_curated_meal(session, approval_status=ApprovalStatus.PENDING)

    response = await authenticated_client.post(
        f"/admin/ui/meals/{meal.id}", data=_meal_form(name="Renamed salad")
    )

    assert response.status_code == 303
    assert (await _reload_meal(session, meal)).name == "Renamed salad"


# --- the daily queue --------------------------------------------------------------


async def test_the_queue_groups_the_upcoming_board(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    await add_daily_suggestion(
        session,
        reveal_at=_TOMORROW,
        approval_status=ApprovalStatus.PENDING,
        name="A breakfast for tomorrow",
    )

    response = await authenticated_client.get("/admin")

    assert response.status_code == 200
    assert "A breakfast for tomorrow" in response.text
    # Three slots of the day are still empty, and the panel says which.
    assert "Missing: lunch, dinner, snack." in response.text


@pytest.mark.parametrize(
    ("action", "expected"),
    [("approve", ApprovalStatus.APPROVED), ("reject", ApprovalStatus.REJECTED)],
)
async def test_a_decision_on_a_slot_moves_it(
    authenticated_client: AsyncClient,
    session: AsyncSession,
    action: str,
    expected: ApprovalStatus,
) -> None:
    slot = await add_daily_suggestion(
        session, reveal_at=_TOMORROW, approval_status=ApprovalStatus.PENDING
    )

    response = await authenticated_client.post(f"/admin/ui/daily/{slot.id}/{action}")

    assert response.status_code == 303
    assert response.headers["location"] == "/admin#queue"
    reloaded = (
        await session.execute(select(DailySuggestion).where(DailySuggestion.id == slot.id))
    ).scalar_one()
    assert reloaded.approval_status is expected


async def test_removing_a_slot_frees_it(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    slot = await add_daily_suggestion(
        session, reveal_at=_TOMORROW, approval_status=ApprovalStatus.PENDING
    )

    response = await authenticated_client.post(f"/admin/ui/daily/{slot.id}/delete")

    assert response.status_code == 303
    await session.flush()
    remaining = (
        await session.execute(select(DailySuggestion).where(DailySuggestion.id == slot.id))
    ).scalar_one_or_none()
    assert remaining is None


async def test_an_edit_rewrites_a_slot(
    authenticated_client: AsyncClient, session: AsyncSession
) -> None:
    await _seed_index(session)
    slot = await add_daily_suggestion(
        session, reveal_at=_TOMORROW, approval_status=ApprovalStatus.PENDING
    )

    response = await authenticated_client.post(
        f"/admin/ui/daily/{slot.id}", data=_meal_form(name="Renamed slot")
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin#queue"
    reloaded = (
        await session.execute(select(DailySuggestion).where(DailySuggestion.id == slot.id))
    ).scalar_one()
    assert reloaded.content["name"] == "Renamed slot"


# --- the composer's model ---------------------------------------------------------


async def test_the_panel_offers_the_configured_providers(
    authenticated_client: AsyncClient, configured_openai: None
) -> None:
    response = await authenticated_client.get("/admin")

    assert '<option value="openai"' in response.text
    assert '<option value="anthropic"' not in response.text


async def test_setting_the_composer_model_stores_it(
    authenticated_client: AsyncClient, session: AsyncSession, configured_openai: None
) -> None:
    response = await authenticated_client.post(
        "/admin/ui/settings", data={"provider": "openai", "model": "gpt-5.4-mini"}
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin#settings"
    stored = (await session.execute(select(GenerationSettings))).scalar_one()
    assert (stored.composer_provider, stored.composer_model) == ("openai", "gpt-5.4-mini")


async def test_a_setting_the_resolver_refuses_is_not_stored(
    authenticated_client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # OpenRouter exposes hundreds of models and has no default, so a blank model is the
    # one refusal a pick from the offered list can still hit.
    monkeypatch.setattr(settings, "openrouter_api_key", "or-test")

    response = await authenticated_client.post(
        "/admin/ui/settings", data={"provider": "openrouter", "model": ""}
    )

    assert response.status_code == 200
    assert "A model is required for OpenRouter" in response.text
    assert (await session.execute(select(GenerationSettings))).first() is None


async def test_an_admin_keeps_the_panel_after_a_role_change(
    authenticated_client: AsyncClient, session: AsyncSession, admin_user: User
) -> None:
    admin_user.role = Role.USER
    await session.flush()

    response = await authenticated_client.get("/admin")

    assert "not an admin account" in response.text
