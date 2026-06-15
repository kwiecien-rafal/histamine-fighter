"""Tests for the LangChain chat-model factory used by the agentic loop.

Constructing a chat model makes no network call, so these run offline; they
cover provider resolution and that the returned wrapper carries a usable
transparency-badge name (which the raw chat model cannot report uniformly).
"""

import pytest
from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from app.config import settings
from app.llm.config import LLMRequestConfig
from app.llm.errors import LLMConfigError, ProviderNotAvailableError
from app.llm.langchain_factory import build_chat_model


def test_openai_uses_default_model() -> None:
    result = build_chat_model(LLMRequestConfig(provider="openai", api_key="k"))
    assert isinstance(result.model, ChatOpenAI)
    assert result.model.model == "gpt-5.4-mini"
    assert result.model_name == "openai/gpt-5.4-mini"


def test_header_model_overrides_default() -> None:
    result = build_chat_model(LLMRequestConfig(provider="openai", api_key="k", model="gpt-4o"))
    assert isinstance(result.model, ChatOpenAI)
    assert result.model_name == "openai/gpt-4o"


def test_anthropic_wrapper_supplies_badge_name() -> None:
    # The wrapper is what makes the badge work: the raw ChatAnthropic has no
    # model_name attribute at all (its value lives on .model), so the name has to
    # come from the resolver, uniformly across providers.
    result = build_chat_model(LLMRequestConfig(provider="anthropic", api_key="k"))
    assert isinstance(result.model, ChatAnthropic)
    assert result.model_name == "anthropic/claude-sonnet-4-6"


def test_gemini_provider() -> None:
    result = build_chat_model(LLMRequestConfig(provider="gemini", api_key="k"))
    assert isinstance(result.model, ChatGoogleGenerativeAI)
    assert result.model_name == "gemini/gemini-2.5-flash"


def test_openrouter_sets_base_url() -> None:
    result = build_chat_model(
        LLMRequestConfig(provider="openrouter", api_key="k", model="meta/llama")
    )
    assert isinstance(result.model, ChatOpenAI)
    assert result.model.openai_api_base == "https://openrouter.ai/api/v1"


def test_openrouter_requires_a_model() -> None:
    with pytest.raises(LLMConfigError):
        build_chat_model(LLMRequestConfig(provider="openrouter", api_key="k"))


def test_missing_api_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", None)
    with pytest.raises(LLMConfigError):
        build_chat_model(LLMRequestConfig(provider="openai"))


def test_ollama_is_built_when_self_hosted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "public_deployment", False)
    result = build_chat_model(LLMRequestConfig(provider="ollama"))
    assert isinstance(result.model, ChatOllama)


def test_ollama_is_blocked_on_a_public_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "public_deployment", True)
    with pytest.raises(LLMConfigError):
        build_chat_model(LLMRequestConfig(provider="ollama"))


def test_reserved_provider_is_not_available() -> None:
    with pytest.raises(ProviderNotAvailableError):
        build_chat_model(LLMRequestConfig(provider="modal"))


def test_unknown_provider_is_a_config_error() -> None:
    with pytest.raises(LLMConfigError):
        build_chat_model(LLMRequestConfig(provider="banana"))


# A pinned model that accepts an explicit temperature, so these test the factory's
# temperature plumbing rather than the default model's sampling support (GPT-5-class
# models reject a custom temperature, which would null it out here).
_TEMPERATURE_CAPABLE_MODEL = "gpt-4o-mini"


def test_classifier_defaults_to_deterministic_sampling() -> None:
    # A safety classifier must not vary run to run on default sampling.
    result = build_chat_model(
        LLMRequestConfig(provider="openai", api_key="k", model=_TEMPERATURE_CAPABLE_MODEL)
    )
    assert isinstance(result.model, ChatOpenAI)
    assert result.model.temperature == 0.0


def test_temperature_is_overridable_for_creative_agents() -> None:
    result = build_chat_model(
        LLMRequestConfig(provider="openai", api_key="k", model=_TEMPERATURE_CAPABLE_MODEL),
        temperature=0.7,
    )
    assert isinstance(result.model, ChatOpenAI)
    assert result.model.temperature == 0.7


def test_request_timeout_is_set_so_calls_cannot_hang_forever() -> None:
    result = build_chat_model(LLMRequestConfig(provider="openai", api_key="k"))
    assert isinstance(result.model, ChatOpenAI)
    assert result.model.request_timeout == 120.0


def test_every_provider_wraps_the_key_as_a_secret() -> None:
    # Gemini included — secret handling must be uniform within the factory.
    result = build_chat_model(LLMRequestConfig(provider="gemini", api_key="k"))
    assert isinstance(result.model, ChatGoogleGenerativeAI)
    assert isinstance(result.model.google_api_key, SecretStr)
