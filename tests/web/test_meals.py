"""Page tests for the daily board, the curated browse, and one meal in full."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.enums import ApprovalStatus, MealType
from app.web.meals import BROWSE_PAGE_SIZE
from tests.web.factories import add_curated_meal, add_daily_suggestion

# --- /daily -----------------------------------------------------------------------


async def test_board_shows_the_full_meal_once_revealed(
    client: AsyncClient, session: AsyncSession
) -> None:
    await add_daily_suggestion(session, reveal_at=datetime.now(UTC) - timedelta(hours=2))

    response = await client.get("/daily")

    assert response.status_code == 200
    assert "Courgette ribbon salad" in response.text
    assert "courgette" in response.text
    assert "Peel into ribbons." in response.text
    # The trace is filtered to the code-authored steps; the model's own prose never
    # reaches a visitor.
    assert "All ingredients cleared the index." in response.text
    assert "the model thinking out loud" not in response.text


async def test_board_discloses_nothing_before_its_reveal(
    client: AsyncClient, session: AsyncSession
) -> None:
    await add_daily_suggestion(
        session, reveal_at=datetime.now(UTC) + timedelta(hours=3), name="Still secret"
    )

    response = await client.get("/daily")

    assert response.status_code == 200
    assert "Still secret" not in response.text
    assert "Next board in" in response.text


async def test_board_for_a_past_day_without_an_approval(
    client: AsyncClient, session: AsyncSession
) -> None:
    yesterday = datetime.now(UTC) - timedelta(days=1)
    await add_daily_suggestion(session, reveal_at=yesterday, approval_status=ApprovalStatus.PENDING)

    response = await client.get(f"/daily?on={yesterday.date()}")

    assert response.status_code == 200
    assert "No board was published on" in response.text


async def test_board_day_links_stop_at_the_history_window(client: AsyncClient) -> None:
    today = datetime.now(UTC).date()
    earliest = today - timedelta(days=settings.daily_history_days)

    response = await client.get(f"/daily?on={earliest}")

    assert response.status_code == 200
    # Today has no next day and the earliest day has no previous one, so both ends of
    # the walk offer only the link that stays inside the window.
    assert f'href="/daily?on={earliest + timedelta(days=1)}"' in response.text
    assert f'href="/daily?on={earliest - timedelta(days=1)}"' not in response.text


async def test_board_outside_the_history_window_is_not_found(client: AsyncClient) -> None:
    too_old = datetime.now(UTC).date() - timedelta(days=settings.daily_history_days + 1)

    response = await client.get(f"/daily?on={too_old}")

    assert response.status_code == 404


# --- /meals -----------------------------------------------------------------------


async def test_browse_lists_only_approved_meals(client: AsyncClient, session: AsyncSession) -> None:
    await add_curated_meal(session, name="Approved salad")
    await add_curated_meal(session, name="Pending bake", approval_status=ApprovalStatus.PENDING)
    await add_curated_meal(session, name="Rejected stew", approval_status=ApprovalStatus.REJECTED)

    response = await client.get("/meals")

    assert response.status_code == 200
    assert "Approved salad" in response.text
    assert "Pending bake" not in response.text
    assert "Rejected stew" not in response.text


async def test_browse_filters_by_meal_type(client: AsyncClient, session: AsyncSession) -> None:
    await add_curated_meal(session, name="Morning oats", meal_type=MealType.BREAKFAST)
    await add_curated_meal(session, name="Midday salad", meal_type=MealType.LUNCH)

    response = await client.get("/meals?meal_type=breakfast")

    assert response.status_code == 200
    assert "Morning oats" in response.text
    assert "Midday salad" not in response.text


async def test_browse_reports_an_empty_filter(client: AsyncClient, session: AsyncSession) -> None:
    await add_curated_meal(session, name="Midday salad", meal_type=MealType.LUNCH)

    response = await client.get("/meals?meal_type=snack")

    assert response.status_code == 200
    assert "No approved snack meals yet." in response.text


async def test_browse_pages_forward_and_back(client: AsyncClient, session: AsyncSession) -> None:
    stamp = datetime.now(UTC)
    for index in range(BROWSE_PAGE_SIZE + 1):
        await add_curated_meal(
            session,
            name=f"Meal {index:02d}",
            meal_type=MealType.LUNCH,
            created_at=stamp - timedelta(minutes=index),
        )

    first_page = await client.get("/meals?meal_type=lunch")
    second_page = await client.get(f"/meals?meal_type=lunch&offset={BROWSE_PAGE_SIZE}")

    assert first_page.status_code == 200
    assert f'href="/meals?meal_type=lunch&amp;offset={BROWSE_PAGE_SIZE}"' in first_page.text
    assert "Meal 00" in first_page.text
    assert "Meal 24" not in first_page.text

    assert second_page.status_code == 200
    # The last page offers only the way back, and the filter survives the round trip.
    assert "Meal 24" in second_page.text
    assert 'href="/meals?meal_type=lunch"' in second_page.text


# --- /meals/{id} ------------------------------------------------------------------


async def test_meal_detail_renders_recipe_trace_and_attribution(
    client: AsyncClient, session: AsyncSession
) -> None:
    meal = await add_curated_meal(session, name="Courgette ribbon salad")

    response = await client.get(f"/meals/{meal.id}")

    assert response.status_code == 200
    assert "Courgette ribbon salad" in response.text
    assert "Toss with oil and herbs." in response.text
    assert "All ingredients cleared the index." in response.text
    assert "fake/test" in response.text


async def test_meal_detail_flags_a_cautioned_ingredient(
    client: AsyncClient, session: AsyncSession
) -> None:
    meal = await add_curated_meal(session, cautioned=[{"name": "courgette", "note": "fresh only"}])

    response = await client.get(f"/meals/{meal.id}")

    assert response.status_code == 200
    assert "In moderation" in response.text
    assert "fresh only" in response.text


async def test_hand_authored_meal_names_no_model(
    client: AsyncClient, session: AsyncSession
) -> None:
    meal = await add_curated_meal(session, model="manual")

    response = await client.get(f"/meals/{meal.id}")

    assert response.status_code == 200
    assert "Curated by admin" in response.text
    assert "Composed by" not in response.text


async def test_unapproved_meal_reads_as_not_found(
    client: AsyncClient, session: AsyncSession
) -> None:
    meal = await add_curated_meal(session, approval_status=ApprovalStatus.PENDING)

    response = await client.get(f"/meals/{meal.id}")

    assert response.status_code == 404
    assert "That meal is not in the public pool." in response.text


async def test_unknown_meal_is_not_found(client: AsyncClient) -> None:
    response = await client.get(f"/meals/{uuid4()}")

    assert response.status_code == 404
