"""Page tests for the landing and legal pages, plus the shell every page shares.

These drive the same ASGI app as the API tests, so a page is asserted on its
rendered HTML: the status, the template's own heading, and the fields or figures
the page promises. Markup is deliberately not asserted beyond that — restyling a
page must not fail its test.
"""

from datetime import UTC, datetime, timedelta

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import ApprovalStatus
from tests.web.factories import add_curated_meal, add_daily_suggestion


async def test_home_renders_without_any_data(client: AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Fight back against histamine." in response.text
    assert "hasn't been set yet" in response.text


async def test_home_shows_the_revealed_board(client: AsyncClient, session: AsyncSession) -> None:
    await add_daily_suggestion(
        session, reveal_at=datetime.now(UTC) - timedelta(hours=2), name="Courgette ribbons"
    )

    response = await client.get("/")

    assert response.status_code == 200
    assert "Courgette ribbons" in response.text
    assert "Breakfast" in response.text


async def test_home_counts_down_to_a_locked_board(
    client: AsyncClient, session: AsyncSession
) -> None:
    await add_daily_suggestion(
        session, reveal_at=datetime.now(UTC) + timedelta(hours=3), name="Still secret"
    )

    response = await client.get("/")

    assert response.status_code == 200
    # A locked board discloses nothing but its unlock time.
    assert "Still secret" not in response.text
    assert "unlocks at" in response.text


async def test_home_counts_only_approved_meals(client: AsyncClient, session: AsyncSession) -> None:
    await add_curated_meal(session, name="Approved salad")
    await add_curated_meal(session, name="Pending bake", approval_status=ApprovalStatus.PENDING)

    response = await client.get("/")

    assert response.status_code == 200
    assert "1 meals in the safe corner" in response.text


async def test_privacy_page_renders(client: AsyncClient) -> None:
    response = await client.get("/privacy")

    assert response.status_code == 200
    assert "Privacy policy" in response.text
    assert "privacy@histaminefighter.com" in response.text


async def test_terms_page_renders(client: AsyncClient) -> None:
    response = await client.get("/terms")

    assert response.status_code == 200
    assert "Terms of service" in response.text
    assert "Not medical advice" in response.text


async def test_every_page_carries_the_shell(client: AsyncClient) -> None:
    """Navigation and the medical disclaimer come from base.html, so they are on all of them."""
    for path in ("/", "/daily", "/meals", "/learn", "/privacy", "/terms"):
        response = await client.get(path)

        assert response.status_code == 200, path
        assert "not medical advice" in response.text, path
        assert 'href="/meals"' in response.text, path


async def test_unrouted_path_renders_an_html_not_found(client: AsyncClient) -> None:
    """A path matching no route at all — the commonest 404 — must not answer with JSON."""
    response = await client.get("/mealz")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "There is no page at that address." in response.text


async def test_web_route_404_keeps_its_own_message(client: AsyncClient) -> None:
    response = await client.get("/daily?on=1999-01-01")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "No board is available for that date." in response.text


async def test_json_api_404_is_still_json(client: AsyncClient) -> None:
    """The HTML error page must not leak into the API's error contract."""
    response = await client.get("/api/v1/daily/meals/1999-01-01")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "No board is available for that date."}


async def test_stylesheet_is_served_from_the_static_mount(client: AsyncClient) -> None:
    response = await client.get("/static/app.css")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")


async def test_robots_txt_keeps_crawlers_out_of_the_admin_area(client: AsyncClient) -> None:
    response = await client.get("/robots.txt")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "Disallow: /admin" in response.text
