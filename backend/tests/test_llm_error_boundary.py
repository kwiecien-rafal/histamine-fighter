"""The LLM layer's domain errors must surface as HTTP status codes at the boundary.

The dish-lookup route resolves the LLM client from request headers via a
dependency, so a bad provider header fails before any model call; these tests
prove the exception handlers map the domain errors to 400/501.
"""

from httpx import AsyncClient


async def test_unknown_provider_header_yields_400(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/meals/lookup",
        json={"dish": "omelette"},
        headers={"X-LLM-Provider": "banana"},
    )
    assert resp.status_code == 400


async def test_reserved_provider_header_yields_501(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/meals/lookup",
        json={"dish": "omelette"},
        headers={"X-LLM-Provider": "modal"},
    )
    assert resp.status_code == 501
