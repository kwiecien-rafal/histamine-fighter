"""OAuth sign-in round trips against scripted Google and GitHub endpoints."""

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.dependencies import get_http_client
from app.models.user import User
from tests.fakes import FakeQuotaService, quota_exhausted

EMAIL = "gerald@example.com"


@pytest.fixture(autouse=True)
def _oauth_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "google_client_id", "google-client")
    monkeypatch.setattr(settings, "google_client_secret", "google-secret")
    monkeypatch.setattr(settings, "github_client_id", "github-client")
    monkeypatch.setattr(settings, "github_client_secret", "github-secret")


def _script_provider_responses(
    test_app: FastAPI,
    *,
    email: str = EMAIL,
    email_verified: bool = True,
    github_emails: list[dict[str, object]] | None = None,
) -> None:
    """Point the app's outbound HTTP at fake provider endpoints."""

    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host in ("oauth2.googleapis.com", "github.com"):
            return httpx.Response(200, json={"access_token": "provider-token"})
        if host == "openidconnect.googleapis.com":
            return httpx.Response(200, json={"email": email, "email_verified": email_verified})
        if host == "api.github.com":
            emails = github_emails or [{"email": email, "primary": True, "verified": True}]
            return httpx.Response(200, json=emails)
        raise AssertionError(f"unexpected outbound call: {request.url}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    test_app.dependency_overrides[get_http_client] = lambda: client


async def _start(client: AsyncClient, provider: str) -> str:
    """Drive /start and return the state the provider would echo back."""
    resp = await client.get(f"/api/v1/auth/oauth/{provider}/start")
    assert resp.status_code == 302
    query = parse_qs(urlparse(resp.headers["location"]).query)
    return query["state"][0]


# --- /start -----------------------------------------------------------------------


async def test_start_redirects_to_google_with_pkce_and_state_cookie(
    client: AsyncClient,
) -> None:
    resp = await client.get("/api/v1/auth/oauth/google/start")

    assert resp.status_code == 302
    location = urlparse(resp.headers["location"])
    assert location.hostname == "accounts.google.com"
    query = parse_qs(location.query)
    assert query["client_id"] == ["google-client"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["state"][0]
    cookie = resp.headers["set-cookie"]
    assert "hf_oauth_google=" in cookie
    assert "HttpOnly" in cookie


async def test_start_404s_unknown_and_501s_unconfigured_providers(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (await client.get("/api/v1/auth/oauth/facebook/start")).status_code == 404

    monkeypatch.setattr(settings, "github_client_id", None)
    resp = await client.get("/api/v1/auth/oauth/github/start")
    assert resp.status_code == 501
    assert "not configured" in resp.json()["detail"]


# --- /callback --------------------------------------------------------------------


async def test_google_callback_signs_in_a_new_user(
    client: AsyncClient,
    test_app: FastAPI,
    session: AsyncSession,
    fake_quota: FakeQuotaService,
) -> None:
    _script_provider_responses(test_app)
    state = await _start(client, "google")

    resp = await client.get(
        "/api/v1/auth/oauth/google/callback", params={"code": "auth-code", "state": state}
    )

    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/login/complete")
    assert f"{settings.session_cookie_name}=" in resp.headers["set-cookie"]

    user = (await session.execute(select(User).where(User.email == EMAIL))).scalar_one()
    assert user.password_hash is None
    assert fake_quota.signup_charges != []


async def test_github_callback_uses_the_primary_verified_email(
    client: AsyncClient, test_app: FastAPI, session: AsyncSession
) -> None:
    _script_provider_responses(
        test_app,
        github_emails=[
            {"email": "noreply@users.github.com", "primary": False, "verified": False},
            {"email": EMAIL, "primary": True, "verified": True},
        ],
    )
    state = await _start(client, "github")

    resp = await client.get(
        "/api/v1/auth/oauth/github/callback", params={"code": "auth-code", "state": state}
    )

    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/login/complete")
    user = (await session.execute(select(User).where(User.email == EMAIL))).scalar_one()
    assert user.role.value == "user"


async def test_callback_rejects_a_state_mismatch(client: AsyncClient, test_app: FastAPI) -> None:
    _script_provider_responses(test_app)
    await _start(client, "google")

    resp = await client.get(
        "/api/v1/auth/oauth/google/callback",
        params={"code": "auth-code", "state": "forged"},
    )

    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/login?error=oauth")
    assert "set-cookie" not in {k.lower() for k, _ in resp.headers.multi_items()} or (
        settings.session_cookie_name not in resp.headers.get("set-cookie", "")
    )


async def test_callback_without_the_state_cookie_fails(client: AsyncClient) -> None:
    resp = await client.get(
        "/api/v1/auth/oauth/google/callback", params={"code": "x", "state": "y"}
    )

    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/login?error=oauth")


async def test_callback_rejects_an_unverified_email(
    client: AsyncClient, test_app: FastAPI, session: AsyncSession
) -> None:
    _script_provider_responses(test_app, email_verified=False)
    state = await _start(client, "google")

    resp = await client.get(
        "/api/v1/auth/oauth/google/callback", params={"code": "auth-code", "state": state}
    )

    assert resp.headers["location"].endswith("/login?error=oauth")
    found = (await session.execute(select(User).where(User.email == EMAIL))).scalar_one_or_none()
    assert found is None


async def test_callback_signs_in_an_existing_user_without_a_signup_charge(
    client: AsyncClient,
    test_app: FastAPI,
    public_user: User,
    fake_quota: FakeQuotaService,
) -> None:
    _script_provider_responses(test_app, email=public_user.email)
    state = await _start(client, "google")

    resp = await client.get(
        "/api/v1/auth/oauth/google/callback", params={"code": "auth-code", "state": state}
    )

    assert resp.headers["location"].endswith("/login/complete")
    assert fake_quota.signup_charges == []


async def test_callback_redirects_to_signup_limit_when_the_ip_cap_is_hit(
    client: AsyncClient, test_app: FastAPI, fake_quota: FakeQuotaService
) -> None:
    _script_provider_responses(test_app)
    fake_quota.signup_error = quota_exhausted("signup_ip", limit=3)
    state = await _start(client, "google")

    resp = await client.get(
        "/api/v1/auth/oauth/google/callback", params={"code": "auth-code", "state": state}
    )

    assert resp.headers["location"].endswith("/login?error=signup_limit")


async def test_callback_refuses_admin_accounts(
    client: AsyncClient, test_app: FastAPI, session: AsyncSession, admin_user: User
) -> None:
    # An admin's Google account must never open the panel: password login at
    # /admin/auth is the only admin path, and the refusal is the generic flag.
    _script_provider_responses(test_app, email=admin_user.email)
    state = await _start(client, "google")

    resp = await client.get(
        "/api/v1/auth/oauth/google/callback", params={"code": "auth-code", "state": state}
    )

    assert resp.status_code == 303
    assert resp.headers["location"].endswith("/login?error=oauth")
    assert settings.session_cookie_name not in resp.headers.get("set-cookie", "")


async def test_each_provider_gets_its_own_state_cookie(client: AsyncClient) -> None:
    # A Google attempt in one tab must not clobber a GitHub attempt in another.
    google = await client.get("/api/v1/auth/oauth/google/start")
    github = await client.get("/api/v1/auth/oauth/github/start")

    assert "hf_oauth_google=" in google.headers["set-cookie"]
    assert "hf_oauth_github=" in github.headers["set-cookie"]
