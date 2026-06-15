"""Resolve a LangChain chat model for the agentic tool-calling loop.

Returns a :class:`ChatModel` — the LangChain ``BaseChatModel`` paired with its
transparency-badge name — because ``BaseChatModel`` has no uniform way to report
its model (``ChatAnthropic.model_name`` is even ``None``). The name comes from
the shared resolver, so the badge works the same for every provider.

Provider rules come from :func:`app.llm.providers.resolve_llm_config`; this module
only constructs the chat model. Provider SDKs are imported eagerly at module load,
so a missing dependency fails at startup rather than mid-request.
"""

from dataclasses import dataclass
from typing import assert_never

import structlog
from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.config import settings
from app.llm.config import LLMRequestConfig
from app.llm.providers import (
    OPENROUTER_BASE_URL,
    Provider,
    ResolvedLLMConfig,
    resolve_llm_config,
)

log = structlog.get_logger(__name__)


# Per-call request timeout. The agentic loop makes several model calls; this
# bounds any single hung one so a stalled provider cannot block the request
# forever.
_REQUEST_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True, slots=True)
class ChatModel:
    """A chat model and the model name to show on the transparency badge."""

    model: BaseChatModel
    model_name: str


def build_chat_model(cfg: LLMRequestConfig, *, temperature: float = 0.0) -> ChatModel:
    """Resolve a tool-capable chat model for a request, with its badge name.

    Precedence and errors come from :func:`app.llm.providers.resolve_llm_config`.

    This is the gateway to the agentic tool-calling loop, so the model must
    support tool calls. The hosted providers here do; a small local Ollama model
    may not, and that only shows up when the loop first calls a tool, not at
    resolution — there is no reliable pre-flight check short of a wasted model
    call. The agent loop (Step 5) is responsible for turning a model that ignores
    or rejects tools into a clean error.

    ``temperature`` defaults to ``0.0`` to keep the dish-lookup flow steady run to
    run, which firms up caching and stable prose. GPT-5-class models (the OpenAI
    default) reject a custom temperature, so LangChain drops it there and they run
    at the provider default; the verdict is unaffected either way because it is
    computed in code from the index, never sampled. Creative agents (recipe, learn)
    can pass a higher value.
    """
    resolved = resolve_llm_config(cfg)
    log.debug("llm.chat_model", provider=resolved.provider.value, model=resolved.model)
    return ChatModel(model=_construct(resolved, temperature), model_name=resolved.model_name)


def _construct(resolved: ResolvedLLMConfig, temperature: float) -> BaseChatModel:
    match resolved.provider:
        case Provider.OLLAMA:
            # ChatOllama has no top-level timeout; it passes client_kwargs to the
            # underlying HTTP client, which is where the request timeout belongs.
            return ChatOllama(
                model=resolved.model,
                base_url=resolved.base_url or settings.ollama_base_url,
                temperature=temperature,
                client_kwargs={"timeout": _REQUEST_TIMEOUT_SECONDS},
            )
        case Provider.OPENAI:
            return ChatOpenAI(
                model=resolved.model,
                api_key=SecretStr(resolved.require_key()),
                temperature=temperature,
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        case Provider.OPENROUTER:
            return ChatOpenAI(
                model=resolved.model,
                api_key=SecretStr(resolved.require_key()),
                base_url=OPENROUTER_BASE_URL,
                temperature=temperature,
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        case Provider.ANTHROPIC:
            # model_name is the field's typed init alias; an explicit timeout and
            # stop are required by the constructor. timeout must be a real value —
            # None would disable the request timeout and let a call hang forever.
            return ChatAnthropic(
                model_name=resolved.model,
                api_key=SecretStr(resolved.require_key()),
                temperature=temperature,
                timeout=_REQUEST_TIMEOUT_SECONDS,
                stop=None,
            )
        case Provider.GEMINI:
            return ChatGoogleGenerativeAI(
                model=resolved.model,
                google_api_key=SecretStr(resolved.require_key()),
                temperature=temperature,
                timeout=_REQUEST_TIMEOUT_SECONDS,
            )
        case _:  # pragma: no cover - exhaustive over Provider
            assert_never(resolved.provider)
