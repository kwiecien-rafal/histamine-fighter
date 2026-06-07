"""Tests for the custom LLM client factory's provider resolution.

These guard the shared rules in app.llm.providers from the custom-client side;
the LangChain side is covered by test_langchain_factory. The factory raises
domain errors, never HTTPException — the API boundary maps those to status codes
(see test_llm_error_boundary).
"""

import pytest

from app.config import settings
from app.llm.config import LLMRequestConfig
from app.llm.errors import LLMConfigError, ProviderNotAvailableError
from app.llm.factory import build_llm_client
from app.llm.langchain_factory import build_chat_model


def test_openai_uses_default_model() -> None:
    client = build_llm_client(LLMRequestConfig(provider="openai", api_key="k"))
    assert client.model_name == "openai/gpt-4o-mini"


def test_openrouter_requires_a_model() -> None:
    with pytest.raises(LLMConfigError):
        build_llm_client(LLMRequestConfig(provider="openrouter", api_key="k"))


def test_missing_api_key_is_a_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", None)
    with pytest.raises(LLMConfigError):
        build_llm_client(LLMRequestConfig(provider="openai"))


def test_reserved_provider_is_not_available() -> None:
    with pytest.raises(ProviderNotAvailableError):
        build_llm_client(LLMRequestConfig(provider="modal"))


def test_unknown_provider_is_a_config_error() -> None:
    with pytest.raises(LLMConfigError):
        build_llm_client(LLMRequestConfig(provider="banana"))


def test_both_factories_agree_on_openrouter_missing_key_and_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # OpenRouter needs both a key and a model. Both factories share one resolver,
    # so the same bad input must raise the same error from each (no drift).
    monkeypatch.setattr(settings, "openrouter_api_key", None)
    cfg = LLMRequestConfig(provider="openrouter")

    with pytest.raises(LLMConfigError) as custom_error:
        build_llm_client(cfg)
    with pytest.raises(LLMConfigError) as chat_error:
        build_chat_model(cfg)

    assert str(custom_error.value) == str(chat_error.value)
