"""The real QuotaService SQL: atomic conditional upserts against Postgres.

These tests run outside the rollback isolation on purpose — the service commits
its own transactions, which is exactly the behavior under test — so each test
cleans the counters table explicitly.
"""

from collections.abc import AsyncIterator
from datetime import date
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.models.usage_counter import UsageCounter
from app.services.quota_service import QuotaExceededError, QuotaService
from tests.conftest import TEST_DATABASE_URL

IP = "203.0.113.7"


@pytest_asyncio.fixture
async def quota_db(
    _database_schema: None,
) -> AsyncIterator[tuple[QuotaService, async_sessionmaker[AsyncSession]]]:
    """A QuotaService on its own committing session factory, with cleanup."""
    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield QuotaService(factory), factory
    finally:
        async with factory() as session:
            await session.execute(delete(UsageCounter))
            await session.commit()
        await engine.dispose()


async def _counts(
    factory: async_sessionmaker[AsyncSession],
) -> dict[tuple[str, str], int]:
    async with factory() as session:
        rows = (await session.execute(select(UsageCounter))).scalars().all()
        return {(row.scope, row.key): row.count for row in rows}


async def test_charges_all_three_scopes_and_stops_at_the_user_limit(
    quota_db: tuple[QuotaService, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "shared_user_daily_limit", 2)
    monkeypatch.setattr(settings, "shared_ip_daily_limit", 10)
    monkeypatch.setattr(settings, "shared_global_daily_limit", 10)
    service, factory = quota_db
    user_id = uuid4()

    await service.charge_shared(user_id, IP)
    await service.charge_shared(user_id, IP)
    with pytest.raises(QuotaExceededError) as exc:
        await service.charge_shared(user_id, IP)

    assert exc.value.scope == "user"
    assert exc.value.limit == 2
    counts = await _counts(factory)
    assert counts[("user", str(user_id))] == 2
    # The refused third charge rolled back entirely: ip and global stayed at 2.
    assert counts[("ip", IP)] == 2
    assert counts[("global", "all")] == 2


async def test_ip_limit_binds_across_accounts(
    quota_db: tuple[QuotaService, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "shared_user_daily_limit", 10)
    monkeypatch.setattr(settings, "shared_ip_daily_limit", 2)
    monkeypatch.setattr(settings, "shared_global_daily_limit", 10)
    service, _ = quota_db

    await service.charge_shared(uuid4(), IP)
    await service.charge_shared(uuid4(), IP)
    with pytest.raises(QuotaExceededError) as exc:
        await service.charge_shared(uuid4(), IP)

    # Fresh accounts gain nothing behind one connection: min(user, ip) binds.
    assert exc.value.scope == "ip"


async def test_global_rejection_does_not_burn_the_user_quota(
    quota_db: tuple[QuotaService, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "shared_user_daily_limit", 10)
    monkeypatch.setattr(settings, "shared_ip_daily_limit", 10)
    monkeypatch.setattr(settings, "shared_global_daily_limit", 1)
    service, factory = quota_db
    first, second = uuid4(), uuid4()

    await service.charge_shared(first, IP)
    with pytest.raises(QuotaExceededError) as exc:
        await service.charge_shared(second, "198.51.100.9")

    assert exc.value.scope == "global"
    counts = await _counts(factory)
    # The second user's rolled-back charge left no user or ip row behind.
    assert ("user", str(second)) not in counts
    assert ("ip", "198.51.100.9") not in counts
    assert counts[("global", "all")] == 1


async def test_signup_charges_cap_per_ip(
    quota_db: tuple[QuotaService, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "signup_ip_daily_limit", 2)
    service, _ = quota_db

    await service.charge_signup(IP)
    await service.charge_signup(IP)
    with pytest.raises(QuotaExceededError) as exc:
        await service.charge_signup(IP)

    assert exc.value.scope == "signup_ip"


async def test_counters_roll_over_by_utc_day(
    quota_db: tuple[QuotaService, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "shared_user_daily_limit", 1)
    monkeypatch.setattr(settings, "shared_ip_daily_limit", 10)
    monkeypatch.setattr(settings, "shared_global_daily_limit", 10)
    service, _ = quota_db
    user_id = uuid4()

    monkeypatch.setattr("app.services.quota_service._today", lambda: date(2026, 7, 4))
    await service.charge_shared(user_id, IP)
    with pytest.raises(QuotaExceededError):
        await service.charge_shared(user_id, IP)

    # A new UTC day starts fresh rows; yesterday's exhaustion does not carry over.
    monkeypatch.setattr("app.services.quota_service._today", lambda: date(2026, 7, 5))
    await service.charge_shared(user_id, IP)


async def test_read_status_reports_the_user_scope(
    quota_db: tuple[QuotaService, async_sessionmaker[AsyncSession]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "shared_user_daily_limit", 20)
    service, factory = quota_db
    user_id = uuid4()
    await service.charge_shared(user_id, IP)

    async with factory() as session:
        status = await service.read_status(user_id, session)
        fresh = await service.read_status(uuid4(), session)

    assert (status.used, status.limit) == (1, 20)
    assert fresh.used == 0
    assert status.resets_at.tzinfo is not None
