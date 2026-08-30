"""Page tests for the shelf, one saved copy, and erasing an account.

The saving rules themselves — the approval and reveal gates, the dedupe, the
per-user cap — are covered against the JSON API in test_saved_meals.py, and these
routes call those very handlers. What is asserted here is the browser's side: the
form that reaches them, where a write redirects to, and what a refusal says.
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.enums import ApprovalStatus, MealType, Role, SavedMealTag, SaveSource
from app.llm.errors import LLMInvocationError
from app.models import SavedMeal
from app.models.user import User
from app.schemas.meal import RecipeGeneration
from app.schemas.usage import LLMUsage
from app.web import profile
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD
from tests.web.factories import add_curated_meal, add_daily_suggestion, add_saved_meal


async def _reload(session: AsyncSession, saved: SavedMeal) -> SavedMeal:
    """Re-read a saved row, flushing the request's pending writes on the way.

    A refresh would drop them instead: the routes leave committing to the request
    session, which the test transaction stands in for.
    """
    return (await session.execute(select(SavedMeal).where(SavedMeal.id == saved.id))).scalar_one()


def _edit_form(**overrides: object) -> dict[str, object]:
    """A complete edit submission, so a test only states the field it is about."""
    return {
        "name": "Courgette ribbons",
        "description": "ribbons with oil and herbs",
        "ingredient": ["courgette", "olive oil"],
        "recipe": "Peel into ribbons.\nToss with oil.",
        "tags": [SavedMealTag.LUNCH.value],
    } | overrides


class _StubRecipeAgent:
    """Stands in for RecipeAgent; raises when a test says no call may happen."""

    def __init__(self, steps: list[str] | None) -> None:
        self._steps = steps

    async def run(self, **kwargs: object) -> RecipeGeneration:
        if self._steps is None:
            raise LLMInvocationError("the model would not answer")
        return RecipeGeneration(steps=self._steps, model="recipe/model", usage=LLMUsage())


@pytest.fixture
def stub_recipe_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer the recipe button with fixed steps instead of a model call.

    Patched on the module rather than through ``dependency_overrides``: the page
    builds its agent inside the handler so an unresolvable provider can be page
    copy rather than the API's JSON error body.
    """
    monkeypatch.setattr(
        profile,
        "build_recipe_agent",
        lambda *args: _StubRecipeAgent(["Peel into ribbons.", "Toss with oil."]),
    )


# --- the shelf --------------------------------------------------------------------


async def test_the_shelf_sends_an_anonymous_visitor_to_sign_in(client: AsyncClient) -> None:
    response = await client.get("/profile")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


async def test_an_empty_shelf_says_how_to_fill_it(user_client: AsyncClient) -> None:
    response = await user_client.get("/profile")

    assert response.status_code == 200
    assert "Nothing saved yet" in response.text
    # Personal, so it must not be kept by any cache along the way.
    assert response.headers["cache-control"] == "no-store"


async def test_the_shelf_lists_the_visitors_saves(
    user_client: AsyncClient, session: AsyncSession, public_user: User
) -> None:
    await add_saved_meal(session, user_id=public_user.id, name="Herb spaghetti")

    response = await user_client.get("/profile")

    assert response.status_code == 200
    assert "Herb spaghetti" in response.text
    assert "Depends" in response.text
    assert "From a dish check" in response.text


async def test_the_shelf_hides_another_accounts_saves(
    user_client: AsyncClient, session: AsyncSession
) -> None:
    stranger = User(email="stranger@example.com", password_hash=None, role=Role.USER)
    session.add(stranger)
    await session.flush()
    await add_saved_meal(session, user_id=stranger.id, name="Not yours")

    response = await user_client.get("/profile")

    assert "Not yours" not in response.text


async def test_the_shelf_filters_to_one_meal_slot(
    user_client: AsyncClient, session: AsyncSession, public_user: User
) -> None:
    await add_saved_meal(
        session,
        user_id=public_user.id,
        source=SaveSource.CURATED,
        name="Ribbon salad",
        meal_type=MealType.LUNCH,
        verdict=None,
    )
    await add_saved_meal(session, user_id=public_user.id, name="A dish check")

    lunch = await user_client.get("/profile?filter=lunch")
    checks = await user_client.get("/profile?filter=lookup")

    assert "Ribbon salad" in lunch.text
    assert "A dish check" not in lunch.text
    assert "A dish check" in checks.text
    assert "Ribbon salad" not in checks.text


async def test_an_unknown_filter_shows_the_whole_shelf(
    user_client: AsyncClient, session: AsyncSession, public_user: User
) -> None:
    """A stale link should show everything rather than an empty shelf."""
    await add_saved_meal(session, user_id=public_user.id, name="Herb spaghetti")

    response = await user_client.get("/profile?filter=brunch")

    assert response.status_code == 200
    assert "Herb spaghetti" in response.text


# --- saving from a public page ----------------------------------------------------


async def test_a_meal_page_offers_sign_in_to_an_anonymous_visitor(
    client: AsyncClient, session: AsyncSession
) -> None:
    meal = await add_curated_meal(session)

    response = await client.get(f"/meals/{meal.id}")

    assert "Sign in to save" in response.text
    assert 'action="/profile/meals"' not in response.text


async def test_saving_a_meal_returns_to_the_page_it_was_saved_from(
    user_client: AsyncClient, session: AsyncSession, public_user: User
) -> None:
    meal = await add_curated_meal(session)
    page = await user_client.get(f"/meals/{meal.id}")
    assert 'action="/profile/meals"' in page.text

    response = await user_client.post(
        "/profile/meals",
        data={"source": "curated", "source_id": str(meal.id), "back_url": f"/meals/{meal.id}"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/meals/{meal.id}"
    row = (await session.execute(select(SavedMeal))).scalar_one()
    assert row.user_id == public_user.id
    assert row.source_key == str(meal.id)
    # The page now offers the copy instead of a second save.
    assert f"/profile/meals/{row.id}" in (await user_client.get(f"/meals/{meal.id}")).text


async def test_saving_a_revealed_daily_slot_from_the_board(
    user_client: AsyncClient, session: AsyncSession
) -> None:
    now = datetime.now(UTC)
    slot = await add_daily_suggestion(session, reveal_at=now - timedelta(hours=2), on=now.date())

    response = await user_client.post(
        "/profile/meals",
        data={"source": "daily", "source_id": str(slot.id), "back_url": "/daily"},
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/daily"
    row = (await session.execute(select(SavedMeal))).scalar_one()
    assert row.source is SaveSource.DAILY
    # The board now offers the copy in place of a second save.
    assert f"/profile/meals/{row.id}" in (await user_client.get("/daily")).text


async def test_saving_refuses_to_redirect_off_the_site(
    user_client: AsyncClient, session: AsyncSession
) -> None:
    meal = await add_curated_meal(session)

    response = await user_client.post(
        "/profile/meals",
        data={
            "source": "curated",
            "source_id": str(meal.id),
            "back_url": "https://example.com/phish",
        },
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"


async def test_saving_a_meal_that_is_not_public_reads_as_missing(
    user_client: AsyncClient, session: AsyncSession
) -> None:
    meal = await add_curated_meal(session, approval_status=ApprovalStatus.PENDING)

    response = await user_client.post(
        "/profile/meals", data={"source": "curated", "source_id": str(meal.id)}
    )

    assert response.status_code == 404


async def test_a_full_shelf_is_refused_on_the_shelf_itself(
    user_client: AsyncClient,
    session: AsyncSession,
    public_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "saved_meals_cap", 1)
    await add_saved_meal(session, user_id=public_user.id, name="The only slot")
    meal = await add_curated_meal(session)

    response = await user_client.post(
        "/profile/meals", data={"source": "curated", "source_id": str(meal.id)}
    )

    assert response.status_code == 200
    assert "Save limit reached" in response.text
    assert "The only slot" in response.text


# --- one saved copy ---------------------------------------------------------------


async def test_the_saved_copy_renders_the_edit_form(
    user_client: AsyncClient, session: AsyncSession, public_user: User
) -> None:
    saved = await add_saved_meal(session, user_id=public_user.id)

    response = await user_client.get(f"/profile/meals/{saved.id}")

    assert response.status_code == 200
    assert 'name="name"' in response.text
    assert 'name="ingredient"' in response.text
    assert 'value="courgette"' in response.text
    assert 'name="tags"' in response.text


async def test_another_accounts_save_reads_as_missing(
    user_client: AsyncClient, session: AsyncSession
) -> None:
    stranger = User(email="stranger@example.com", password_hash=None, role=Role.USER)
    session.add(stranger)
    await session.flush()
    saved = await add_saved_meal(session, user_id=stranger.id)

    response = await user_client.get(f"/profile/meals/{saved.id}")

    assert response.status_code == 404


async def test_an_edit_updates_the_copy_and_stamps_it(
    user_client: AsyncClient, session: AsyncSession, public_user: User
) -> None:
    saved = await add_saved_meal(session, user_id=public_user.id)

    response = await user_client.post(f"/profile/meals/{saved.id}", data=_edit_form())

    assert response.status_code == 303
    assert response.headers["location"] == f"/profile/meals/{saved.id}"
    saved = await _reload(session, saved)
    assert saved.name == "Courgette ribbons"
    assert saved.ingredients == [
        {"name": "courgette", "category": "vegetable"},
        {"name": "olive oil", "category": None},
    ]
    assert saved.recipe == ["Peel into ribbons.", "Toss with oil."]
    assert saved.tags == ["lunch"]
    # The copy is now the user's own, so it stops reading as index-verified.
    assert saved.edited_at is not None


async def test_an_empty_recipe_box_clears_the_recipe(
    user_client: AsyncClient, session: AsyncSession, public_user: User
) -> None:
    saved = await add_saved_meal(session, user_id=public_user.id, recipe=["Simmer."])

    await user_client.post(f"/profile/meals/{saved.id}", data=_edit_form(recipe=""))

    saved = await _reload(session, saved)
    assert saved.recipe is None


async def test_a_nameless_edit_comes_back_to_the_form(
    user_client: AsyncClient, session: AsyncSession, public_user: User
) -> None:
    saved = await add_saved_meal(session, user_id=public_user.id)

    response = await user_client.post(
        f"/profile/meals/{saved.id}", data=_edit_form(name="   ", description="kept this")
    )

    assert response.status_code == 200
    assert "Give the meal a name." in response.text
    # What was typed survives, so the edit is corrected rather than retyped.
    assert "kept this" in response.text
    saved = await _reload(session, saved)
    assert saved.edited_at is None


async def test_a_tag_outside_the_vocabulary_is_refused(
    user_client: AsyncClient, session: AsyncSession, public_user: User
) -> None:
    saved = await add_saved_meal(session, user_id=public_user.id)

    response = await user_client.post(
        f"/profile/meals/{saved.id}", data=_edit_form(tags=["not-a-tag"])
    )

    assert response.status_code == 200
    assert "Pick tags from the list only." in response.text


async def test_removing_a_save_returns_to_the_shelf(
    user_client: AsyncClient, session: AsyncSession, public_user: User
) -> None:
    saved = await add_saved_meal(session, user_id=public_user.id)

    response = await user_client.post(f"/profile/meals/{saved.id}/delete")

    assert response.status_code == 303
    assert response.headers["location"] == "/profile"
    assert (await session.execute(select(SavedMeal))).scalar_one_or_none() is None


# --- writing a recipe for a save --------------------------------------------------


async def test_writing_a_recipe_persists_it_on_the_copy(
    user_client: AsyncClient,
    session: AsyncSession,
    public_user: User,
    stub_recipe_agent: None,
) -> None:
    saved = await add_saved_meal(session, user_id=public_user.id)
    assert "Write the recipe" in (await user_client.get(f"/profile/meals/{saved.id}")).text

    response = await user_client.post(f"/profile/meals/{saved.id}/recipe")

    assert response.status_code == 303
    saved = await _reload(session, saved)
    assert saved.recipe == ["Peel into ribbons.", "Toss with oil."]
    assert saved.recipe_model == "recipe/model"
    # Generated content is not a user edit: the verified badge must survive.
    assert saved.edited_at is None
    page = await user_client.get(f"/profile/meals/{saved.id}")
    assert "Write the recipe" not in page.text
    assert "recipe/model" in page.text


async def test_a_failed_recipe_is_said_on_the_page(
    user_client: AsyncClient,
    session: AsyncSession,
    public_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(profile, "build_recipe_agent", lambda *args: _StubRecipeAgent(None))
    saved = await add_saved_meal(session, user_id=public_user.id)

    response = await user_client.post(f"/profile/meals/{saved.id}/recipe")

    assert response.status_code == 200
    # The apostrophe in the copy is HTML-escaped, so the assertion sidesteps it.
    assert "write that recipe" in response.text
    saved = await _reload(session, saved)
    assert saved.recipe is None


# --- erasing the account ----------------------------------------------------------


async def test_the_deletion_page_spells_out_what_goes(user_client: AsyncClient) -> None:
    response = await user_client.get("/account/delete")

    assert response.status_code == 200
    assert "no undo" in response.text
    assert 'action="/account/delete"' in response.text


async def test_deleting_erases_the_account_and_signs_out(
    user_client: AsyncClient, session: AsyncSession, public_user: User
) -> None:
    await add_saved_meal(session, user_id=public_user.id)

    response = await user_client.post("/account/delete")

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert (
        await session.execute(select(User).where(User.id == public_user.id))
    ).scalar_one_or_none() is None
    assert (await session.execute(select(SavedMeal))).scalar_one_or_none() is None
    assert (await user_client.get("/account")).status_code == 303


async def test_an_admin_is_not_offered_self_deletion(client: AsyncClient, admin_user: User) -> None:
    """Admin accounts are operator-managed, so the page must not offer the button."""
    await client.post("/admin/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})

    response = await client.get("/account/delete")

    assert response.status_code == 200
    assert "managed from the command line" in response.text
    assert 'action="/account/delete"' not in response.text
