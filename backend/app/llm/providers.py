"""The single source of provider truth for the LLM chat-model factory.

The provider identity (:class:`Provider`), the per-provider defaults, and every
resolution rule — key requirement, the Ollama deployment gate, OpenRouter's
required model, unknown/reserved handling — live here once. ``build_chat_model``
calls :func:`resolve_llm_config` and then only constructs the LangChain chat
model, so the provider rules live in one place rather than in the factory.
"""

from enum import StrEnum
from typing import NamedTuple

from app.config import settings
from app.llm.config import LLMRequestConfig
from app.llm.errors import LLMConfigError, ProviderNotAvailableError


class Provider(StrEnum):
    """An LLM provider the app can actually build a client for."""

    OPENAI = "openai"
    OPENROUTER = "openrouter"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    OLLAMA = "ollama"


# Providers recognised by name but not wired up yet; requested explicitly they
# are a 501, which is clearer than "unknown provider".
RESERVED_PROVIDERS: frozenset[str] = frozenset({"modal"})

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Default model when the request names none. OpenRouter is absent on purpose: it
# exposes hundreds of models with no sensible default, so it requires one.
DEFAULT_MODELS: dict[Provider, str] = {
    Provider.OPENAI: "gpt-4o-mini",
    Provider.ANTHROPIC: "claude-sonnet-4-6",
    Provider.GEMINI: "gemini-2.5-flash",
}


class ResolvedLLMConfig(NamedTuple):
    """A fully validated provider choice, ready to construct a client from.

    ``api_key`` is ``None`` only for Ollama (local, unauthenticated); ``base_url``
    is set only for Ollama (its server address). The cloud providers always carry
    a key, exposed as a non-optional ``str`` through :meth:`require_key`.
    """

    provider: Provider
    model: str
    api_key: str | None
    base_url: str | None

    @property
    def model_name(self) -> str:
        """The transparency-badge name, e.g. ``openai/gpt-4o-mini``."""
        return f"{self.provider.value}/{self.model}"

    def require_key(self) -> str:
        """Return the API key, asserting the cloud-provider invariant for typing."""
        if self.api_key is None:
            raise LLMConfigError(f"Provider '{self.provider.value}' requires an API key.")
        return self.api_key


def resolve_llm_config(cfg: LLMRequestConfig) -> ResolvedLLMConfig:
    """Resolve a request's LLM config: header overrides win, else server defaults.

    Raises:
        ProviderNotAvailableError: the provider is reserved for a later phase.
        LLMConfigError: the provider is unknown, a required key is missing, or
            OpenRouter was chosen without a model.
    """
    provider = _parse_provider(cfg.provider or settings.llm_provider)

    if provider is Provider.OLLAMA:
        if settings.public_deployment:
            raise LLMConfigError("Ollama is only available in self-hosted deployments.")
        return ResolvedLLMConfig(
            provider=provider,
            model=cfg.model or settings.ollama_model,
            api_key=None,
            base_url=cfg.base_url or settings.ollama_base_url,
        )

    # Resolve the key before the model so a request missing both (OpenRouter needs
    # both) always fails with the same deterministic error — the missing-key one —
    # rather than one that depends on evaluation order.
    api_key = _require_api_key(provider, cfg.api_key, _settings_api_key(provider))
    model = cfg.model or DEFAULT_MODELS.get(provider)
    if model is None:
        raise LLMConfigError(
            "A model is required for OpenRouter — see https://openrouter.ai/models."
        )
    return ResolvedLLMConfig(provider=provider, model=model, api_key=api_key, base_url=None)


def _parse_provider(name: str) -> Provider:
    key = name.strip().lower()
    if key in RESERVED_PROVIDERS:
        raise ProviderNotAvailableError(f"LLM provider '{key}' is not yet available.")
    try:
        return Provider(key)
    except ValueError:
        raise LLMConfigError(f"Unknown LLM provider: '{name}'.") from None


def _settings_api_key(provider: Provider) -> str | None:
    """The server-configured key for a cloud provider (never called for Ollama)."""
    return {
        Provider.OPENAI: settings.openai_api_key,
        Provider.ANTHROPIC: settings.anthropic_api_key,
        Provider.GEMINI: settings.gemini_api_key,
        Provider.OPENROUTER: settings.openrouter_api_key,
    }[provider]


def _require_api_key(provider: Provider, header_key: str | None, settings_key: str | None) -> str:
    key = header_key or settings_key
    if not key:
        raise LLMConfigError(f"API key required for provider '{provider.value}'.")
    return key
