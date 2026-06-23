"""Endpoint tests for the admin session: login, logout, /me, and the auth gate.

These run against the test database (the conftest ``client``/``authenticated_client``
share the rolled-back session), so they cover the real cookie flow end to end: login
sets an httpOnly cookie, the gate re-reads the user, and require_admin enforces the
role. ``/admin/meals`` stands in as a representative require_admin route.
"""

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from app.config import settings
from app.core.ratelimit import limiter
from app.core.security import create_access_token, hash_password
from app.enums import Role
from app.models.user import User
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD


def _cookie_header(token: str) -> dict[str, str]:
    """A raw Cookie header carrying a session token, bypassing the client jar."""
    return {"Cookie": f"{settings.session_cookie_name}={token}"}


# --- POST /admin/auth/login -------------------------------------------------------


async def test_login_sets_an_httponly_cookie_and_returns_the_user(
    client: AsyncClient, admin_user: User
) -> None:
    resp = await client.post(
        "/admin/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )

    assert resp.status_code == 200
    # The user is returned; the token rides in the cookie, never the body.
    assert resp.json() == {"email": ADMIN_EMAIL, "role": "admin"}
    assert "access_token" not in resp.json()
    assert resp.headers["cache-control"] == "no-store"

    set_cookie = resp.headers["set-cookie"]
    assert settings.session_cookie_name in set_cookie
    assert "httponly" in set_cookie.lower()
    assert "path=/" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()
    # Secure is off in dev so the cookie still sets over http on localhost.
    assert "secure" not in set_cookie.lower()


async def test_login_is_case_insensitive_on_email(client: AsyncClient, admin_user: User) -> None:
    resp = await client.post(
        "/admin/auth/login", json={"email": "Admin@Example.com", "password": ADMIN_PASSWORD}
    )

    assert resp.status_code == 200
    assert resp.json()["email"] == ADMIN_EMAIL


async def test_login_with_a_wrong_password_is_401(client: AsyncClient, admin_user: User) -> None:
    resp = await client.post("/admin/auth/login", json={"email": ADMIN_EMAIL, "password": "nope"})

    assert resp.status_code == 401
    assert "set-cookie" not in resp.headers


async def test_login_with_an_unknown_email_is_401(client: AsyncClient) -> None:
    resp = await client.post(
        "/admin/auth/login", json={"email": "ghost@example.com", "password": ADMIN_PASSWORD}
    )

    assert resp.status_code == 401


async def test_login_for_a_disabled_account_is_401_without_a_cookie(
    client: AsyncClient, session: AsyncSession
) -> None:
    # Correct credentials on a deactivated account must not open a session. The kill
    # switch fails the login itself, not just the next request.
    user = User(
        email="disabled@example.com",
        password_hash=hash_password(ADMIN_PASSWORD),
        role=Role.ADMIN,
        is_active=False,
    )
    session.add(user)
    await session.flush()

    resp = await client.post(
        "/admin/auth/login", json={"email": "disabled@example.com", "password": ADMIN_PASSWORD}
    )

    assert resp.status_code == 401
    assert "set-cookie" not in resp.headers


async def test_successful_login_is_logged(client: AsyncClient, admin_user: User) -> None:
    with capture_logs() as logs:
        await client.post(
            "/admin/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )

    success = next(entry for entry in logs if entry["event"] == "admin.login.success")
    assert success["email"] == ADMIN_EMAIL


async def test_failed_login_is_logged_without_the_password(client: AsyncClient) -> None:
    with capture_logs() as logs:
        await client.post("/admin/auth/login", json={"email": ADMIN_EMAIL, "password": "nope"})

    failure = next(entry for entry in logs if entry["event"] == "admin.login.failed")
    assert failure["email"] == ADMIN_EMAIL
    # The password must never reach the logs.
    assert "nope" not in str(logs)


async def test_login_is_rate_limited(
    client: AsyncClient, admin_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 1)
    limiter.reset()
    limiter.enabled = True

    first = await client.post(
        "/admin/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    second = await client.post(
        "/admin/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )

    assert first.status_code == 200
    assert second.status_code == 429


# --- GET /admin/auth/me -----------------------------------------------------------


async def test_me_returns_the_current_user(
    authenticated_client: AsyncClient, admin_user: User
) -> None:
    resp = await authenticated_client.get("/admin/auth/me")

    assert resp.status_code == 200
    assert resp.json() == {"email": admin_user.email, "role": "admin"}


async def test_me_without_a_session_is_401(client: AsyncClient) -> None:
    resp = await client.get("/admin/auth/me")
    assert resp.status_code == 401


# --- POST /admin/auth/logout ------------------------------------------------------


async def test_logout_clears_the_session_cookie(authenticated_client: AsyncClient) -> None:
    resp = await authenticated_client.post("/admin/auth/logout")

    assert resp.status_code == 204
    set_cookie = resp.headers["set-cookie"]
    assert settings.session_cookie_name in set_cookie
    # Deletion expires the cookie immediately.
    assert "max-age=0" in set_cookie.lower()


async def test_logout_then_a_protected_request_is_401(authenticated_client: AsyncClient) -> None:
    await authenticated_client.post("/admin/auth/logout")

    resp = await authenticated_client.get("/admin/auth/me")
    assert resp.status_code == 401


# --- auth gate (authentication) ---------------------------------------------------


async def test_protected_route_without_a_cookie_is_401(client: AsyncClient) -> None:
    resp = await client.get("/admin/meals")
    assert resp.status_code == 401


async def test_a_garbage_cookie_is_401(client: AsyncClient) -> None:
    resp = await client.get("/admin/meals", headers=_cookie_header("not.a.jwt"))
    assert resp.status_code == 401


async def test_a_token_for_a_missing_user_is_401(client: AsyncClient) -> None:
    # Well-signed, but its subject (a random id) has no account.
    token = create_access_token(str(uuid4()), token_version=1)
    resp = await client.get("/admin/meals", headers=_cookie_header(token))
    assert resp.status_code == 401


async def test_a_token_with_a_stale_version_is_401(client: AsyncClient, admin_user: User) -> None:
    # A token minted under an earlier password (lower version) is revoked by a reset.
    token = create_access_token(str(admin_user.id), token_version=0)
    resp = await client.get("/admin/meals", headers=_cookie_header(token))
    assert resp.status_code == 401


async def test_an_inactive_user_is_rejected(client: AsyncClient, session: AsyncSession) -> None:
    # is_active is the kill switch checked at the gate: a valid token for a disabled
    # account still fails closed.
    user = User(
        email="disabled@example.com",
        password_hash=hash_password(ADMIN_PASSWORD),
        role=Role.ADMIN,
        is_active=False,
    )
    session.add(user)
    await session.flush()
    token = create_access_token(str(user.id), token_version=user.token_version)

    resp = await client.get("/admin/auth/me", headers=_cookie_header(token))
    assert resp.status_code == 401


# --- authorization (role) ---------------------------------------------------------


async def test_a_non_admin_user_is_forbidden_from_admin_routes(
    client: AsyncClient, session: AsyncSession
) -> None:
    # Forward-looking: login is authentication only, so a role=user account can sign
    # in and read /me, but require_admin refuses it on admin routes (403, not 401).
    user = User(
        email="user@example.com",
        password_hash=hash_password(ADMIN_PASSWORD),
        role=Role.USER,
    )
    session.add(user)
    await session.flush()

    login = await client.post(
        "/admin/auth/login", json={"email": "user@example.com", "password": ADMIN_PASSWORD}
    )
    assert login.status_code == 200
    assert login.json()["role"] == "user"

    assert (await client.get("/admin/auth/me")).status_code == 200
    assert (await client.get("/admin/meals")).status_code == 403


# --- CSRF Origin check ------------------------------------------------------------


async def test_a_cross_origin_post_is_rejected(client: AsyncClient) -> None:
    resp = await client.post(
        "/admin/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        headers={"Origin": "https://evil.example"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "Cross-origin request rejected."


async def test_an_allowed_origin_post_passes_the_check(
    client: AsyncClient, admin_user: User
) -> None:
    resp = await client.post(
        "/admin/auth/login",
        json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
        headers={"Origin": "http://localhost:5173"},
    )

    assert resp.status_code == 200
