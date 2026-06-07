from typing import assert_never

from app.config import settings
from app.llm.anthropic_client import AnthropicClient
from app.llm.base import LLMClient
from app.llm.config import LLMRequestConfig
from app.llm.gemini_client import GeminiClient
from app.llm.ollama_client import OllamaClient
from app.llm.openai_compatible import OpenAICompatibleClient
from app.llm.providers import OPENROUTER_BASE_URL, Provider, resolve_llm_config

_OPENAI_BASE_URL = "https://api.openai.com/v1"


def build_llm_client(cfg: LLMRequestConfig) -> LLMClient:
    """Resolve an :class:`LLMClient` for a request or a script.

    Provider rules and defaults come from :func:`app.llm.providers.resolve_llm_config`,
    shared with :func:`app.llm.langchain_factory.build_chat_model`. This function
    only maps the resolved provider to its concrete client.
    """
    cfg_resolved = resolve_llm_config(cfg)

    match cfg_resolved.provider:
        case Provider.OLLAMA:
            return OllamaClient(
                base_url=cfg_resolved.base_url or settings.ollama_base_url,
                model=cfg_resolved.model,
            )
        case Provider.OPENAI:
            return OpenAICompatibleClient(
                base_url=_OPENAI_BASE_URL,
                api_key=cfg_resolved.require_key(),
                model=cfg_resolved.model,
                label=cfg_resolved.provider.value,
            )
        case Provider.OPENROUTER:
            return OpenAICompatibleClient(
                base_url=OPENROUTER_BASE_URL,
                api_key=cfg_resolved.require_key(),
                model=cfg_resolved.model,
                label=cfg_resolved.provider.value,
            )
        case Provider.ANTHROPIC:
            return AnthropicClient(
                api_key=cfg_resolved.require_key(), model=cfg_resolved.model
            )
        case Provider.GEMINI:
            return GeminiClient(
                api_key=cfg_resolved.require_key(), model=cfg_resolved.model
            )
        case _:  # pragma: no cover - exhaustive over Provider
            assert_never(cfg_resolved.provider)
