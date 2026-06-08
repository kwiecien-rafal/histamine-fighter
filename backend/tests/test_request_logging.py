"""The request-correlation middleware returns an X-Request-ID on every response.

The id is bound into the logging context for the request and echoed as a header,
so an operator handed a failed response can find its log lines. It must appear on
error responses too: those are produced by the exception handlers inside the
middleware stack, so the header is also the proof they pass back through it (and
are logged as request.done, not request.failed).
"""

from httpx import AsyncClient


async def test_request_id_header_on_success(client: AsyncClient) -> None:
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.headers.get("x-request-id")


async def test_request_id_header_on_error(client: AsyncClient) -> None:
    # A bad provider header maps to 400 via the exception handler; the handler runs
    # inside the middleware, so the error response still carries the id.
    resp = await client.post(
        "/api/v1/meals/lookup",
        json={"dish": "omelette"},
        headers={"X-LLM-Provider": "banana"},
    )
    assert resp.status_code == 400
    assert resp.headers.get("x-request-id")
