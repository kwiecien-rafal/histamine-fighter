from fastapi import Request
from pydantic import BaseModel


class LLMRequestConfig(BaseModel):
    """Per-request LLM configuration parsed from ``X-LLM-*`` headers.

    Each field falls through to a server-side default in
    :func:`app.llm.factory.build_llm_client` when ``None``. Keys are
    handled here only — they must never be persisted or logged.
    """

    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None

    @classmethod
    def from_headers(cls, request: Request) -> "LLMRequestConfig":
        headers = request.headers
        return cls(
            provider=headers.get("x-llm-provider"),
            model=headers.get("x-llm-model"),
            base_url=headers.get("x-llm-base-url"),
            api_key=headers.get("x-llm-api-key"),
        )
