"""Public session surface: /me, quota, logout(s), deletion, and session minting."""

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import Response
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.cookies import mint_session
from app.config import settings
from app.core.security import create_access_token
from app.enums import SafetyLevel, SaveSource
from app.models.magic_link_token import MagicLinkToken
from app.models.saved_meal import SavedMeal
from app.models.usage_counter import UsageCounter
from app.models.user import User
from app.services.quota_service import QuotaStatus
from tests.conftest import ADMIN_PASSWORD
from tests.fakes import FakeQuotaService


async def test_me_returns_the_signed_in_user(user_client: AsyncClient) -> None:
    resp = await user_client.get("/api/v1/auth/me")

    assert resp.status_code == 200
    assert resp.json() == {"email": "gerald@example.com", "role": "user"}
    assert resp.headers["Cache-Control"] == "no-store"


async def test_me_is_401_without_a_session(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/auth/me")

    assert resp.status_code == 401


async def test_me_quota_reports_the_daily_allowance(
    user_client: AsyncClient, fake_quota: FakeQuotaService
) -> None:
    fake_quota.status = QuotaStatus(
        used=7, limit=20, resets_at=datetime.now(UTC) + timedelta(hours=3)
    )

    resp = await user_client.get("/api/v1/auth/me/quota")

    assert resp.status_code == 200
    body = resp.json()
    assert (body["used"], body["limit"]) == (7, 20)
    assert body["resets_at"]


async def test_logout_clears_the_cookie_and_is_idempotent(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/auth/logout")

    assert resp.status_code == 204
    assert f'{settings.session_cookie_name}=""' in resp.headers["set-cookie"]


async def test_delete_me_erases_the_account_and_its_data(
    user_client: AsyncClient, session: AsyncSession, public_user: User
) -> None:
    session.add(UsageCounter(scope="user", key=str(public_user.id), date=date.today(), count=3))
    session.add(
        MagicLinkToken(
            email=public_user.email,
            code_hash="x",
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
    )
    session.add(
        SavedMeal(
            user_id=public_user.id,
            source=SaveSource.LOOKUP,
            source_key="spaghetti",
            name="Spaghetti",
            description="a saved dish",
            ingredients=[{"name": "courgette", "category": None}],
            model="fake/test",
            verdict=SafetyLevel.SAFE,
        )
    )
    await session.flush()

    resp = await user_client.delete("/api/v1/auth/me")

    assert resp.status_code == 204
    assert f'{settings.session_cookie_name}=""' in resp.headers["set-cookie"]
    # In production get_session commits (and so flushes) at request end; the test
    # session skips the commit for rollback isolation, so flush explicitly.
    await session.flush()
    session.expire_all()
    assert await session.get(User, public_user.id) is None
    counters = (await session.execute(select(UsageCounter))).scalars().all()
    assert counters == []
    tokens = (await session.execute(select(MagicLinkToken))).scalars().all()
    assert tokens == []
    saves = (await session.execute(select(SavedMeal))).scalars().all()
    assert saves == []

    # The cookie is gone from the jar, so the session is over.
    assert (await user_client.get("/api/v1/auth/me")).status_code == 401


async def test_delete_me_requires_a_session(client: AsyncClient) -> None:
    resp = await client.delete("/api/v1/auth/me")

    assert resp.status_code == 401


# --- session minting and revocation --------------------------------------------------


def _max_age(response: Response) -> int:
    cookie = response.headers["set-cookie"]
    for part in cookie.split(";"):
        name, _, value = part.strip().partition("=")
        if name.lower() == "max-age":
            return int(value)
    raise AssertionError(f"no Max-Age in {cookie!r}")


def test_public_users_get_the_long_session(public_user: User) -> None:
    response = Response()

    mint_session(response, public_user)

    assert _max_age(response) == settings.user_session_cookie_max_age


def test_mint_session_refuses_admin_accounts(admin_user: User) -> None:
    # Public login refuses admins upstream; this guard keeps a future call site
    # from ever handing a month-long cookie to an admin account.
    with pytest.raises(ValueError, match="admin"):
        mint_session(Response(), admin_user)


async def test_logout_all_revokes_every_outstanding_session(
    user_client: AsyncClient, session: AsyncSession, public_user: User
) -> None:
    # A second device's cookie, minted before the revocation.
    other_device_token = create_access_token(
        str(public_user.id), token_version=public_user.token_version
    )

    resp = await user_client.post("/api/v1/auth/logout/all")

    assert resp.status_code == 204
    assert f'{settings.session_cookie_name}=""' in resp.headers["set-cookie"]
    # get_session commits at request end in production; the test session skips
    # the commit for rollback isolation, so flush the bump explicitly.
    await session.flush()
    user_client.cookies.set(settings.session_cookie_name, other_device_token)
    assert (await user_client.get("/api/v1/auth/me")).status_code == 401


async def test_delete_me_refuses_admin_accounts(
    authenticated_client: AsyncClient, session: AsyncSession, admin_user: User
) -> None:
    admin_id = admin_user.id

    resp = await authenticated_client.delete("/api/v1/auth/me")

    assert resp.status_code == 403
    session.expire_all()
    assert await session.get(User, admin_id) is not None


async def test_admin_password_login_refuses_passwordless_accounts(
    client: AsyncClient, public_user: User
) -> None:
    # A public (OAuth/magic-link) account has no password; the admin gate answers
    # the same 401 as an unknown email, confirming nothing.
    resp = await client.post(
        "/admin/auth/login",
        json={"email": public_user.email, "password": ADMIN_PASSWORD},
    )

    assert resp.status_code == 401
    assert resp.json()["detail"] == "Incorrect email or password."
