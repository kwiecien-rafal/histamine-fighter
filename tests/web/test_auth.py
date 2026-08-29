"""Page tests for signing in, the emailed-link landing, and the account controls.

The pages drive the same handlers the JSON API exposes, so what is asserted here is
the browser's side of the flow: which form comes back, what a failure says, and that
the session cookie is set or cleared. The login policy itself — the caps, the
blocklist, the attempt limit, the admin refusal — is covered against the API in
test_magic_link.py and is not re-tested through the HTML.
"""

import re
from uuid import UUID

import pytest
from httpx import AsyncClient
from structlog.testing import capture_logs

from app.config import settings
from app.models.user import User
from app.services.magic_link_service import MagicLinkService
from tests.conftest import PUBLIC_EMAIL

EMAIL = "gerald@example.com"


@pytest.fixture(autouse=True)
def _count_attempts_in_the_test_transaction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the code-attempt counter inside the test transaction.

    The real one commits on its own connection, which cannot see a row the test has
    only flushed; left alone it would refuse every code redeemed here. The cap it
    enforces is a policy concern, covered against the API in test_magic_link.py.
    """

    async def record(self: MagicLinkService, jti: UUID) -> int:
        return 1

    monkeypatch.setattr(MagicLinkService, "_record_attempt", record)


async def _request_link(client: AsyncClient, email: str = EMAIL) -> dict[str, str]:
    """Submit the sign-in form and read the link and code back off the dev-mode log."""
    with capture_logs() as logs:
        response = await client.post("/login", data={"email": email})
    assert response.status_code == 200
    sent = next(log for log in logs if log["event"] == "email.magic_link.dev")
    return {"url": str(sent["url"]), "code": str(sent["code"])}


def _token_from_url(url: str) -> str:
    match = re.search(r"token=([^&]+)", url)
    assert match is not None
    return match.group(1)


# --- the sign-in page -------------------------------------------------------------


async def test_login_page_offers_the_email_form(client: AsyncClient) -> None:
    response = await client.get("/login")

    assert response.status_code == 200
    assert 'name="email"' in response.text
    assert 'action="/login"' in response.text


async def test_login_page_renders_the_oauth_callbacks_error_flag(client: AsyncClient) -> None:
    response = await client.get("/login?error=oauth")

    assert response.status_code == 200
    assert "Try again, or use your email instead." in response.text


async def test_login_page_hides_oauth_buttons_when_no_provider_is_configured(
    client: AsyncClient,
) -> None:
    response = await client.get("/login")

    assert "/api/v1/auth/oauth/" not in response.text


async def test_login_page_links_a_configured_oauth_provider(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "google_client_id", "client-id")
    monkeypatch.setattr(settings, "google_client_secret", "client-secret")

    response = await client.get("/login")

    assert 'href="/api/v1/auth/oauth/google/start"' in response.text
    assert "Continue with Google" in response.text
    # The unconfigured provider stays off the page (the footer's GitHub link is not it).
    assert "/api/v1/auth/oauth/github/start" not in response.text


async def test_login_page_renders_the_turnstile_widget_when_configured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "turnstile_site_key", "site-key-123")

    response = await client.get("/login")

    assert 'data-sitekey="site-key-123"' in response.text
    assert "challenges.cloudflare.com" in response.text


async def test_login_page_sends_a_signed_in_visitor_to_their_account(
    user_client: AsyncClient,
) -> None:
    response = await user_client.get("/login")

    assert response.status_code == 303
    assert response.headers["location"] == "/account"


# --- requesting the link ----------------------------------------------------------


async def test_requesting_a_link_shows_the_code_form(client: AsyncClient) -> None:
    with capture_logs() as logs:
        response = await client.post("/login", data={"email": EMAIL})

    assert response.status_code == 200
    assert EMAIL in response.text
    assert 'name="code"' in response.text
    assert any(log["event"] == "email.magic_link.dev" for log in logs)


async def test_a_malformed_address_comes_back_to_the_form(client: AsyncClient) -> None:
    response = await client.post("/login", data={"email": "not-an-address"})

    assert response.status_code == 200
    assert "look like an email address" in response.text
    # What was typed survives, so the visitor can correct it rather than retype it.
    assert 'value="not-an-address"' in response.text


async def test_a_disposable_address_is_refused_on_the_page(client: AsyncClient) -> None:
    response = await client.post("/login", data={"email": "throwaway@mailinator.com"})

    assert response.status_code == 200
    assert "Disposable email addresses" in response.text
    assert 'name="code"' not in response.text


# --- signing in with the code -----------------------------------------------------


async def test_the_right_code_opens_the_session(client: AsyncClient) -> None:
    sent = await _request_link(client)

    response = await client.post("/login/code", data={"email": EMAIL, "code": sent["code"]})

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert settings.session_cookie_name in response.headers["set-cookie"]
    assert "httponly" in response.headers["set-cookie"].lower()


async def test_a_wrong_code_comes_back_to_the_code_form(client: AsyncClient) -> None:
    sent = await _request_link(client)
    wrong = f"{(int(sent['code']) + 1) % 10**6:06d}"

    response = await client.post("/login/code", data={"email": EMAIL, "code": wrong})

    assert response.status_code == 200
    assert "invalid or has expired" in response.text
    assert "set-cookie" not in response.headers


async def test_a_code_of_the_wrong_length_is_answered_by_the_page(client: AsyncClient) -> None:
    await _request_link(client)

    response = await client.post("/login/code", data={"email": EMAIL, "code": "123"})

    assert response.status_code == 200
    assert "Enter the 6-digit code from the email." in response.text


# --- signing in with the emailed link ---------------------------------------------


async def test_the_landing_page_does_not_spend_the_token(client: AsyncClient) -> None:
    """A mail scanner following the link must leave it usable; only the POST spends it."""
    token = _token_from_url((await _request_link(client))["url"])

    landing = await client.get(f"/login/verify?token={token}")
    assert landing.status_code == 200
    assert token in landing.text

    signed_in = await client.post("/login/verify", data={"token": token})
    assert signed_in.status_code == 303
    assert settings.session_cookie_name in signed_in.headers["set-cookie"]


async def test_a_spent_link_explains_itself(client: AsyncClient) -> None:
    token = _token_from_url((await _request_link(client))["url"])
    await client.post("/login/verify", data={"token": token})

    response = await client.post("/login/verify", data={"token": token})

    assert response.status_code == 200
    assert "That link no longer works" in response.text
    assert "invalid or has expired" in response.text


async def test_the_landing_page_without_a_token_explains_itself(client: AsyncClient) -> None:
    response = await client.get("/login/verify")

    assert response.status_code == 200
    assert "That link no longer works" in response.text


async def test_the_oauth_landing_hands_the_visitor_back_to_the_site(client: AsyncClient) -> None:
    response = await client.get("/login/complete")

    assert response.status_code == 303
    assert response.headers["location"] == "/"


# --- the account page -------------------------------------------------------------


async def test_the_account_page_sends_an_anonymous_visitor_to_sign_in(
    client: AsyncClient,
) -> None:
    response = await client.get("/account")

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


async def test_the_account_page_shows_the_address_and_the_shared_tier_allowance(
    user_client: AsyncClient,
) -> None:
    response = await user_client.get("/account")

    assert response.status_code == 200
    assert PUBLIC_EMAIL in response.text
    assert "of 20 left" in response.text
    # Personal, so it must not be kept by any cache along the way.
    assert response.headers["cache-control"] == "no-store"


async def test_signing_out_ends_the_session(client: AsyncClient) -> None:
    """Driven through the real sign-in so the cookie under test is the server's own."""
    sent = await _request_link(client)
    await client.post("/login/code", data={"email": EMAIL, "code": sent["code"]})
    assert (await client.get("/account")).status_code == 200

    response = await client.post("/logout")

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert (await client.get("/account")).status_code == 303


async def test_signing_out_everywhere_kills_the_token_itself(
    user_client: AsyncClient, public_user: User
) -> None:
    token = user_client.cookies[settings.session_cookie_name]

    response = await user_client.post("/logout/all")
    assert response.status_code == 303

    # The same token, offered again, is now refused: the account's session version moved.
    user_client.cookies.set(settings.session_cookie_name, token)
    assert (await user_client.get("/account")).status_code == 303


# --- the shell --------------------------------------------------------------------


async def test_the_masthead_offers_sign_in_to_an_anonymous_visitor(client: AsyncClient) -> None:
    response = await client.get("/")

    assert 'href="/login"' in response.text
    assert 'href="/account"' not in response.text


async def test_the_masthead_shows_the_account_to_a_signed_in_visitor(
    user_client: AsyncClient,
) -> None:
    response = await user_client.get("/meals")

    assert 'href="/account"' in response.text
    assert PUBLIC_EMAIL in response.text


async def test_html_pages_vary_on_the_session_cookie(client: AsyncClient) -> None:
    """The shell differs per session, so a shared cache must not reuse one for another."""
    response = await client.get("/")

    assert response.headers["vary"] == "Cookie"
