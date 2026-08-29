"""Magic-link login: request (Turnstile, blocklist, rate limit) and verify
(link token, 6-digit code, single use, expiry, signup controls)."""

import re
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

from app.config import settings
from app.core.ratelimit import limiter
from app.core.security import create_access_token, create_purpose_token
from app.models.magic_link_token import MagicLinkToken
from app.models.user import User
from app.services.magic_link_service import MagicLinkService
from tests.fakes import FakeQuotaService, quota_exhausted

EMAIL = "gerald@example.com"


@pytest.fixture(autouse=True)
def _attempts_on_test_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route the code-attempt counter through the test transaction.

    The real ``_record_attempt`` commits on its own connection, which can
    neither see the test's uncommitted rows nor stay inside the rollback
    isolation; its durability is a production concern the direct service tests
    cover.
    """

    async def record(self: MagicLinkService, jti: UUID) -> int:
        row = await self._session.get(MagicLinkToken, jti)
        if row is None:
            return settings.magic_link_max_attempts + 1
        row.attempts += 1
        await self._session.flush()
        return row.attempts

    monkeypatch.setattr(MagicLinkService, "_record_attempt", record)


async def _request_link(client: AsyncClient, email: str = EMAIL) -> dict[str, str]:
    """Request a magic link and return the dev-mode log's url and code."""
    with capture_logs() as logs:
        resp = await client.post("/api/v1/auth/magic/request", json={"email": email})
    assert resp.status_code == 200
    sent = next(log for log in logs if log["event"] == "email.magic_link.dev")
    return {"url": str(sent["url"]), "code": str(sent["code"])}


def _token_from_url(url: str) -> str:
    match = re.search(r"token=([^&]+)", url)
    assert match is not None
    return match.group(1)


# --- POST /api/v1/auth/magic/request ---------------------------------------------


async def test_request_answers_uniformly_and_logs_the_link_in_dev_mode(
    client: AsyncClient,
) -> None:
    with capture_logs() as logs:
        resp = await client.post("/api/v1/auth/magic/request", json={"email": EMAIL})

    assert resp.status_code == 200
    assert "sign-in email" in resp.json()["detail"]
    sent = next(log for log in logs if log["event"] == "email.magic_link.dev")
    assert "/login/verify?token=" in str(sent["url"])
    assert re.fullmatch(r"\d{6}", str(sent["code"]))


async def test_request_refuses_disposable_domains(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/auth/magic/request", json={"email": "throwaway@mailinator.com"}
    )

    assert resp.status_code == 400
    assert "Disposable" in resp.json()["detail"]


async def test_request_requires_turnstile_when_configured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "turnstile_secret_key", "secret")

    resp = await client.post("/api/v1/auth/magic/request", json={"email": EMAIL})

    assert resp.status_code == 400
    assert "Turnstile" in resp.json()["detail"]


async def test_request_is_rate_limited(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "auth_rate_limit_per_minute", 2)
    limiter.reset()
    limiter.enabled = True
    try:
        for _ in range(2):
            resp = await client.post("/api/v1/auth/magic/request", json={"email": EMAIL})
            assert resp.status_code == 200
        resp = await client.post("/api/v1/auth/magic/request", json={"email": EMAIL})
        assert resp.status_code == 429
    finally:
        limiter.enabled = False


# --- POST /api/v1/auth/magic/verify ----------------------------------------------


async def test_link_token_signs_in_and_creates_the_account(
    client: AsyncClient,
    session: AsyncSession,
    fake_quota: FakeQuotaService,
) -> None:
    sent = await _request_link(client)

    resp = await client.post(
        "/api/v1/auth/magic/verify", json={"token": _token_from_url(sent["url"])}
    )

    assert resp.status_code == 200
    assert resp.json() == {"email": EMAIL, "role": "user"}
    cookie = resp.headers["set-cookie"]
    assert f"{settings.session_cookie_name}=" in cookie
    assert "HttpOnly" in cookie
    # Public users get the long TTL, not the admin's 60 minutes.
    assert f"Max-Age={settings.user_session_cookie_max_age}" in cookie
    assert resp.headers["Cache-Control"] == "no-store"

    user = (await session.execute(select(User).where(User.email == EMAIL))).scalar_one()
    assert user.password_hash is None
    assert user.created_from_ip is not None
    assert user.last_login_at is not None
    assert fake_quota.signup_charges != []


async def test_link_token_is_single_use(client: AsyncClient) -> None:
    sent = await _request_link(client)
    token = _token_from_url(sent["url"])

    first = await client.post("/api/v1/auth/magic/verify", json={"token": token})
    second = await client.post("/api/v1/auth/magic/verify", json={"token": token})

    assert first.status_code == 200
    assert second.status_code == 401


async def test_new_link_invalidates_the_previous_one(client: AsyncClient) -> None:
    first = await _request_link(client)
    second = await _request_link(client)

    stale = await client.post(
        "/api/v1/auth/magic/verify", json={"token": _token_from_url(first["url"])}
    )
    live = await client.post(
        "/api/v1/auth/magic/verify", json={"token": _token_from_url(second["url"])}
    )

    assert stale.status_code == 401
    assert live.status_code == 200


async def test_code_checks_against_the_newest_link(client: AsyncClient) -> None:
    first = await _request_link(client)
    second = await _request_link(client)

    stale = await client.post(
        "/api/v1/auth/magic/verify", json={"email": EMAIL, "code": first["code"]}
    )
    # A matching stale code may exist by collision only; the newest must work.
    live = await client.post(
        "/api/v1/auth/magic/verify", json={"email": EMAIL, "code": second["code"]}
    )

    if first["code"] != second["code"]:
        assert stale.status_code == 401
    assert live.status_code == 200


async def test_expired_and_tampered_tokens_are_refused(client: AsyncClient) -> None:
    expired = create_purpose_token("magic_link", jti=str(uuid4()), ttl=timedelta(minutes=-1))
    for token in (
        expired,
        "garbage",
        create_purpose_token("oauth_state", jti="x:y:z", ttl=timedelta(minutes=5)),
    ):
        resp = await client.post("/api/v1/auth/magic/verify", json={"token": token})
        assert resp.status_code == 401


async def test_session_token_cannot_be_replayed_as_a_magic_link(
    client: AsyncClient, public_user: User
) -> None:
    session_token = create_access_token(str(public_user.id), token_version=1)

    resp = await client.post("/api/v1/auth/magic/verify", json={"token": session_token})

    assert resp.status_code == 401


async def test_code_signs_in(client: AsyncClient) -> None:
    sent = await _request_link(client)

    resp = await client.post(
        "/api/v1/auth/magic/verify", json={"email": EMAIL, "code": sent["code"]}
    )

    assert resp.status_code == 200
    assert resp.json()["email"] == EMAIL


async def test_wrong_code_is_refused_and_attempts_cap_out(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "magic_link_max_attempts", 2)
    sent = await _request_link(client)
    wrong = "000000" if sent["code"] != "000000" else "111111"

    for _ in range(2):
        resp = await client.post("/api/v1/auth/magic/verify", json={"email": EMAIL, "code": wrong})
        assert resp.status_code == 401

    # Past the cap even the right code is refused, so guessing is bounded.
    resp = await client.post(
        "/api/v1/auth/magic/verify", json={"email": EMAIL, "code": sent["code"]}
    )
    assert resp.status_code == 401


async def test_verify_requires_exactly_one_path(client: AsyncClient) -> None:
    both = {"token": "x", "email": EMAIL, "code": "123456"}
    neither: dict[str, Any] = {}

    for payload in (both, neither):
        resp = await client.post("/api/v1/auth/magic/verify", json=payload)
        assert resp.status_code == 422


async def test_signup_velocity_cap_refuses_new_accounts(
    client: AsyncClient, fake_quota: FakeQuotaService
) -> None:
    fake_quota.signup_error = quota_exhausted("signup_ip", limit=3)
    sent = await _request_link(client)

    resp = await client.post(
        "/api/v1/auth/magic/verify", json={"token": _token_from_url(sent["url"])}
    )

    assert resp.status_code == 429
    assert resp.json()["quota"]["scope"] == "signup_ip"
    assert "new accounts" in resp.json()["detail"]


async def test_admin_accounts_cannot_sign_in_via_magic_link(
    client: AsyncClient, admin_user: User
) -> None:
    # Inbox possession must never open the panel; the refusal is the same
    # uniform 401 as any bad link, confirming nothing about the address.
    sent = await _request_link(client, admin_user.email)

    resp = await client.post(
        "/api/v1/auth/magic/verify", json={"token": _token_from_url(sent["url"])}
    )

    assert resp.status_code == 401
    assert settings.session_cookie_name not in resp.headers.get("set-cookie", "")


async def test_plus_aliases_collapse_to_one_account(
    client: AsyncClient, session: AsyncSession
) -> None:
    sent = await _request_link(client, "gerald+news@example.com")
    first = await client.post(
        "/api/v1/auth/magic/verify", json={"token": _token_from_url(sent["url"])}
    )
    sent = await _request_link(client, "gerald+other@example.com")
    second = await client.post(
        "/api/v1/auth/magic/verify", json={"token": _token_from_url(sent["url"])}
    )

    # One inbox, one account, one quota: both aliases resolve to the base address.
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["email"] == EMAIL
    users = (await session.execute(select(User))).scalars().all()
    assert [u.email for u in users] == [EMAIL]


async def test_inactive_account_cannot_sign_in(
    client: AsyncClient, session: AsyncSession, public_user: User
) -> None:
    public_user.is_active = False
    await session.flush()
    sent = await _request_link(client, public_user.email)

    resp = await client.post(
        "/api/v1/auth/magic/verify", json={"token": _token_from_url(sent["url"])}
    )

    assert resp.status_code == 401
