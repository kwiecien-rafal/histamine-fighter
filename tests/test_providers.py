"""Tests for the provider availability list the operator picks the composer from."""

import pytest

from app.config import settings
from app.llm.providers import Provider, selectable_providers


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start every case from no configured keys, so each test opts its own in."""
    monkeypatch.setattr(settings, "openai_api_key", None)
    monkeypatch.setattr(settings, "anthropic_api_key", None)
    monkeypatch.setattr(settings, "gemini_api_key", None)
    monkeypatch.setattr(settings, "openrouter_api_key", None)
    monkeypatch.setattr(settings, "public_deployment", False)


def test_offers_only_cloud_providers_with_a_configured_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "sk-test")

    assert selectable_providers() == [Provider.OPENAI, Provider.OLLAMA]


def test_offers_ollama_on_a_self_hosted_deployment() -> None:
    assert selectable_providers() == [Provider.OLLAMA]


def test_excludes_ollama_on_a_public_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "public_deployment", True)
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")

    assert selectable_providers() == [Provider.ANTHROPIC]
