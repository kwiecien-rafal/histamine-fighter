from fastapi import HTTPException, status

from app.config import settings
from app.llm.base import LLMClient
from app.llm.config import LLMRequestConfig
from app.llm.mock_client import MockLLMClient
from app.llm.ollama_client import OllamaClient

_NOT_YET_AVAILABLE = {"openai", "anthropic", "gemini", "groq", "modal"}


def build_llm_client(cfg: LLMRequestConfig) -> LLMClient:
    """Resolve an :class:`LLMClient` for a single request.

    Header values on ``cfg`` take precedence over server settings. Providers
    that are reserved for later phases return 501; truly unknown names
    return 400; provider rules gated by deployment mode (e.g. Ollama on a
    public deployment) raise 400 with a clear message for the frontend.
    """
    provider = (cfg.provider or settings.llm_provider).lower()

    if provider == "mock":
        return MockLLMClient()

    if provider == "ollama":
        if settings.public_deployment:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Ollama is only available in self-hosted deployments.",
            )
        return OllamaClient(
            base_url=cfg.base_url or settings.ollama_base_url,
            model=cfg.model or settings.ollama_model,
        )

    if provider in _NOT_YET_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"LLM provider '{provider}' is not yet available.",
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Unknown LLM provider: '{provider}'.",
    )
