"""Tests for the operator-set composer settings: defaults, upsert, and cron defense.

The service get/update run on the rolled-back test session. The cron crash-defense
cases drive the real ``compose_all``/``generate`` flow with a no-DB session stand-in,
so they prove the unresolvable-setting path exits non-zero without touching Postgres
or the embedder.
"""

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from structlog.testing import capture_logs

import app.scripts.compose_meals as compose_meals
import app.scripts.generate_daily_meals as generate_daily_meals
from app.config import settings
from app.enums import MealType
from app.models import GenerationSettings
from app.services.generation_settings_service import GenerationSettingsService


async def test_get_falls_back_to_defaults_when_unset(session: AsyncSession) -> None:
    result = await GenerationSettingsService(session).get()

    assert result.composer_provider == settings.llm_provider
    assert result.composer_model is None


async def test_update_creates_a_row_that_get_returns(session: AsyncSession) -> None:
    await GenerationSettingsService(session).update(
        "openai", "gpt-5.4-mini", actor="admin@example.com"
    )
    await session.flush()

    result = await GenerationSettingsService(session).get()
    assert result.composer_provider == "openai"
    assert result.composer_model == "gpt-5.4-mini"
    assert result.updated_by == "admin@example.com"


async def test_update_stays_a_singleton(session: AsyncSession) -> None:
    service = GenerationSettingsService(session)
    await service.update("openai", "gpt-5.4-mini", actor="first@example.com")
    await session.flush()
    await service.update("anthropic", "claude-sonnet-4-6", actor="second@example.com")
    await session.flush()

    rows = (await session.execute(select(GenerationSettings))).scalars().all()
    assert len(rows) == 1
    assert rows[0].composer_provider == "anthropic"
    assert rows[0].updated_by == "second@example.com"


class _NoRowResult:
    """A query result with no rows, enough for ``GenerationSettingsService.get``."""

    def scalars(self) -> "_NoRowResult":
        return self

    def first(self) -> None:
        return None


class _NoDBSession:
    """A session stand-in for the cron entry points that never reaches Postgres."""

    async def __aenter__(self) -> "_NoDBSession":
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def execute(self, *_args: object, **_kwargs: object) -> _NoRowResult:
        return _NoRowResult()


@pytest.fixture
def _unresolvable_composer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the composer at openai with no key, so building the agent raises."""
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", None)
    for module in (compose_meals, generate_daily_meals):
        monkeypatch.setattr(module, "SessionLocal", lambda: _NoDBSession())
        monkeypatch.setattr(module, "get_embedder", lambda: object())


def test_compose_all_exits_nonzero_on_an_unresolvable_setting(
    _unresolvable_composer: None,
) -> None:
    with capture_logs() as logs:
        with pytest.raises(SystemExit):
            asyncio.run(compose_meals.compose_all())

    assert any(entry["event"] == "compose.settings.invalid" for entry in logs)


def test_generate_exits_nonzero_on_an_unresolvable_setting(
    _unresolvable_composer: None,
) -> None:
    target = datetime.now(UTC).date()
    with capture_logs() as logs:
        with pytest.raises(SystemExit):
            asyncio.run(generate_daily_meals.generate([target], list(MealType)))

    assert any(entry["event"] == "compose.settings.invalid" for entry in logs)
