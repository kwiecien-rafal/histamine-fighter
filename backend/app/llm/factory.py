from fastapi import HTTPException, status

from app.config import settings
from app.llm.anthropic_client import AnthropicClient
from app.llm.base import LLMClient
from app.llm.config import LLMRequestConfig
from app.llm.gemini_client import GeminiClient
from app.llm.mock_client import MockLLMClient
from app.llm.ollama_client import OllamaClient
from app.llm.openai_compatible import OpenAICompatibleClient

_NOT_YET_AVAILABLE = {"modal"}


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

    if provider == "openai":
        return OpenAICompatibleClient(
            base_url="https://api.openai.com/v1",
            api_key=_require_api_key(provider, cfg.api_key, settings.openai_api_key),
            model=cfg.model or "gpt-4o-mini",
            label="openai",
        )

    if provider == "anthropic":
        return AnthropicClient(
            api_key=_require_api_key(provider, cfg.api_key, settings.anthropic_api_key),
            model=cfg.model or "claude-sonnet-4-6",
        )

    if provider == "gemini":
        return GeminiClient(
            api_key=_require_api_key(provider, cfg.api_key, settings.gemini_api_key),
            model=cfg.model or "gemini-2.5-flash",
        )

    if provider == "openrouter":
        api_key = _require_api_key(provider, cfg.api_key, settings.openrouter_api_key)
        if not cfg.model:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A model is required for OpenRouter — see https://openrouter.ai/models.",
            )
        return OpenAICompatibleClient(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key,
            model=cfg.model,
            label="openrouter",
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


def _require_api_key(provider: str, header_key: str | None, settings_key: str | None) -> str:
    key = header_key or settings_key
    if not key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"API key required for provider '{provider}'.",
        )
    return key
