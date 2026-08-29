"""The LLM layer's domain errors must surface as HTTP status codes at the boundary.

The dish-lookup route resolves the LLM client from request headers via a
dependency, so a bad provider header fails before any model call; these tests
prove the exception handlers map the domain errors to 400/501.
"""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.dependencies import build_dish_lookup_agent
from app.llm.errors import LLMInvocationError, LLMRejectedError


async def test_unknown_provider_header_yields_400(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/meals/propose",
        json={"dish": "omelette"},
        headers={"X-LLM-Provider": "banana"},
    )
    assert resp.status_code == 400


async def test_reserved_provider_header_yields_501(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/meals/propose",
        json={"dish": "omelette"},
        headers={"X-LLM-Provider": "modal"},
    )
    assert resp.status_code == 501


class _FailingAgent:
    """Stands in for DishLookupAgent, failing the way a test names."""

    def __init__(self, failure: Exception) -> None:
        self._failure = failure

    async def propose(self, dish: str) -> None:
        raise self._failure


@pytest.mark.parametrize(
    ("failure", "status"),
    [
        (LLMRejectedError("the provider would not run that model"), 400),
        (LLMInvocationError("the model would not answer"), 502),
    ],
)
async def test_a_failed_call_maps_to_its_status(
    test_app: FastAPI, failure: Exception, status: int
) -> None:
    """A refusal is the caller's model or key (400); anything else is upstream (502).

    The handler lookup walks the exception's MRO, so the rejection handler answers
    even though the error also is an ``LLMInvocationError``.
    """
    test_app.dependency_overrides[build_dish_lookup_agent] = lambda: _FailingAgent(failure)

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/meals/propose", json={"dish": "omelette"})

    assert resp.status_code == status
