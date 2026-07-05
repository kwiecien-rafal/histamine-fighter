"""The shared LLM tier: header mediation, auth and key gates, quota charging."""

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from httpx import AsyncClient
from structlog.testing import capture_logs

from app.config import settings
from app.dependencies import RequestLLM, get_request_llm_config
from app.llm.errors import ProviderNotAvailableError
from app.models.user import User
from tests.fakes import FakeQuotaService, quota_exhausted


def _request(headers: dict[str, str], *, client_host: str = "203.0.113.7") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/meals/propose",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "query_string": b"",
        "client": (client_host, 1234),
        "server": ("test", 80),
        "scheme": "http",
    }
    return Request(scope)


# --- get_request_llm_config (unit) ------------------------------------------------


async def test_byo_headers_pass_through_untouched(fake_quota: FakeQuotaService) -> None:
    request = _request(
        {"X-LLM-Provider": "openai", "X-LLM-Model": "gpt-9", "X-LLM-API-Key": "sk-own"}
    )

    resolved = await get_request_llm_config(request, user=None, quota=fake_quota)

    assert resolved.config.provider == "openai"
    assert resolved.config.model == "gpt-9"
    assert resolved.config.api_key == "sk-own"
    # A BYO request carries no shared-tier charge, so charge() is a no-op.
    await resolved.charge()
    assert fake_quota.shared_charges == []


async def test_shared_requires_a_session(fake_quota: FakeQuotaService) -> None:
    request = _request({"X-LLM-Provider": "shared"})

    with pytest.raises(HTTPException) as exc:
        await get_request_llm_config(request, user=None, quota=fake_quota)

    assert exc.value.status_code == 401
    assert "Sign in" in exc.value.detail


async def test_shared_requires_a_server_side_key(
    public_user: User, fake_quota: FakeQuotaService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "openai_api_key", None)
    request = _request({"X-LLM-Provider": "shared"})

    with pytest.raises(ProviderNotAvailableError):
        await get_request_llm_config(request, user=public_user, quota=fake_quota)
    assert fake_quota.shared_charges == []


async def test_shared_pins_the_server_model_and_ignores_client_steering(
    public_user: User, fake_quota: FakeQuotaService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "sk-server")
    request = _request(
        {
            "X-LLM-Provider": "Shared",  # case-insensitive, like every provider header
            "X-LLM-Model": "gpt-9-max-expensive",
            "X-LLM-API-Key": "sk-attacker",
            "X-LLM-Base-URL": "http://evil.example",
        }
    )

    resolved = await get_request_llm_config(request, user=public_user, quota=fake_quota)

    assert resolved.config.provider == "openai"
    assert resolved.config.model == settings.shared_model
    assert resolved.config.api_key == "sk-server"
    assert resolved.config.base_url is None
    # The charge is deferred to the model-call boundary, not spent at resolution.
    assert fake_quota.shared_charges == []
    await resolved.charge()
    assert fake_quota.shared_charges == [(public_user.id, "203.0.113.7")]
    # One-shot: a second charge does not double-spend.
    await resolved.charge()
    assert fake_quota.shared_charges == [(public_user.id, "203.0.113.7")]


async def test_shared_charge_keys_on_the_ipv6_64_bucket(
    public_user: User, fake_quota: FakeQuotaService, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Hopping addresses inside one /64 must not mint fresh IP quota identities.
    monkeypatch.setattr(settings, "openai_api_key", "sk-server")
    request = _request({"X-LLM-Provider": "shared"}, client_host="2001:db8:1:2:aaaa:bbbb:cccc:dddd")

    resolved = await get_request_llm_config(request, user=public_user, quota=fake_quota)
    await resolved.charge()

    assert fake_quota.shared_charges == [(public_user.id, "2001:db8:1:2::/64")]


async def test_shared_charge_is_deferred_and_raises_when_exhausted(
    public_user: User, fake_quota: FakeQuotaService, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "sk-server")
    fake_quota.shared_error = quota_exhausted("user")
    request = _request({"X-LLM-Provider": "shared"})

    # Resolution itself never charges, so it never raises the quota error...
    resolved = await get_request_llm_config(request, user=public_user, quota=fake_quota)

    # ...only the deferred charge does.
    with pytest.raises(type(fake_quota.shared_error)):
        await resolved.charge()


# --- endpoint behavior --------------------------------------------------------------


async def test_anonymous_shared_lookup_is_401(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/meals/propose",
        json={"dish": "spaghetti"},
        headers={"X-LLM-Provider": "shared"},
    )

    assert resp.status_code == 401
    assert "Sign in" in resp.json()["detail"]


async def test_quota_exhausted_shared_lookup_is_429_with_the_quota_body(
    user_client: AsyncClient,
    fake_quota: FakeQuotaService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "sk-server")
    fake_quota.shared_error = quota_exhausted("user", limit=20)

    resp = await user_client.post(
        "/api/v1/meals/propose",
        json={"dish": "spaghetti"},
        headers={"X-LLM-Provider": "shared"},
    )

    assert resp.status_code == 429
    body = resp.json()
    assert "free-tier limit" in body["detail"]
    assert body["quota"]["scope"] == "user"
    assert body["quota"]["limit"] == 20
    assert body["quota"]["resets_at"]


async def test_shared_without_server_key_is_501_for_a_signed_in_user(
    user_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "openai_api_key", None)

    resp = await user_client.post(
        "/api/v1/meals/propose",
        json={"dish": "spaghetti"},
        headers={"X-LLM-Provider": "shared"},
    )

    assert resp.status_code == 501
    assert "not configured" in resp.json()["detail"]


async def test_backstop_bills_a_route_that_forgot_to_charge(
    test_app: FastAPI,
    user_client: AsyncClient,
    fake_quota: FakeQuotaService,
    public_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A hypothetical new LLM endpoint that resolves the shared config but never
    # calls charge(): the middleware tripwire must bill it anyway and log.
    monkeypatch.setattr(settings, "openai_api_key", "sk-server")

    @test_app.post("/api/v1/forgetful")
    async def forgetful(resolved: RequestLLM = Depends(get_request_llm_config)) -> dict[str, str]:
        return {"detail": "model call happened, nobody charged"}

    with capture_logs() as logs:
        resp = await user_client.post("/api/v1/forgetful", headers={"X-LLM-Provider": "shared"})

    assert resp.status_code == 200
    assert fake_quota.shared_charges == [(public_user.id, "127.0.0.1")]
    assert any(log["event"] == "llm.shared_charge_leaked" for log in logs)


async def test_waive_serves_a_cache_hit_free_without_tripping_the_backstop(
    test_app: FastAPI,
    user_client: AsyncClient,
    fake_quota: FakeQuotaService,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A route that resolves the shared config but serves without a model call (a
    # Learn cache hit) waives the charge: no quota is spent and the leak backstop
    # stays silent, unlike the forgot-to-charge case above.
    monkeypatch.setattr(settings, "openai_api_key", "sk-server")

    @test_app.post("/api/v1/cached")
    async def cached(resolved: RequestLLM = Depends(get_request_llm_config)) -> dict[str, str]:
        resolved.waive()
        return {"detail": "served from cache, nothing to charge"}

    with capture_logs() as logs:
        resp = await user_client.post("/api/v1/cached", headers={"X-LLM-Provider": "shared"})

    assert resp.status_code == 200
    assert fake_quota.shared_charges == []
    assert not any(log["event"] == "llm.shared_charge_leaked" for log in logs)


async def test_bare_openai_never_falls_back_to_the_server_key_on_public_deployment(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # On a public deployment the operator's key is reachable only through
    # provider="shared" (which pins it): naming "openai" with no client key is a
    # clean 400, never a silent, unmetered charge to the server key.
    monkeypatch.setattr(settings, "public_deployment", True)
    monkeypatch.setattr(settings, "openai_api_key", "sk-server")

    resp = await client.post(
        "/api/v1/meals/propose",
        json={"dish": "spaghetti"},
        headers={"X-LLM-Provider": "openai"},
    )

    assert resp.status_code == 400
    assert "API key required" in resp.json()["detail"]
