"""The retention purge: stale magic-link rows and old usage counters go, fresh stay.

Like the quota-service tests, these commit outside the rollback isolation (the
purge commits its own transaction), so each test cleans up explicitly.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.magic_link_token import MagicLinkToken
from app.models.usage_counter import UsageCounter
from app.scripts.purge_auth_data import USAGE_COUNTER_RETENTION_DAYS, purge
from tests.conftest import TEST_DATABASE_URL


@pytest_asyncio.fixture
async def purge_db(_database_schema: None) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        async with factory() as session:
            await session.execute(delete(MagicLinkToken))
            await session.execute(delete(UsageCounter))
            await session.commit()
        await engine.dispose()


def _magic_row(email: str, *, expires_at: datetime) -> MagicLinkToken:
    return MagicLinkToken(email=email, code_hash="x", expires_at=expires_at)


async def test_purge_drops_only_stale_rows(
    purge_db: async_sessionmaker[AsyncSession],
) -> None:
    now = datetime.now(UTC)
    async with purge_db() as session:
        session.add(_magic_row("stale@example.com", expires_at=now - timedelta(days=2)))
        session.add(_magic_row("fresh@example.com", expires_at=now + timedelta(minutes=10)))
        # Expired but within the one-day inspection window: kept.
        session.add(_magic_row("recent@example.com", expires_at=now - timedelta(hours=2)))
        session.add(
            UsageCounter(
                scope="ip",
                key="203.0.113.7",
                date=(now - timedelta(days=USAGE_COUNTER_RETENTION_DAYS + 10)).date(),
                count=5,
            )
        )
        session.add(UsageCounter(scope="ip", key="203.0.113.7", date=now.date(), count=1))
        await session.commit()

    links_purged, counters_purged = await purge(purge_db, now=now)

    assert (links_purged, counters_purged) == (1, 1)
    async with purge_db() as session:
        emails = (await session.execute(select(MagicLinkToken.email))).scalars().all()
        dates = (await session.execute(select(UsageCounter.date))).scalars().all()
    assert sorted(emails) == ["fresh@example.com", "recent@example.com"]
    assert dates == [now.date()]
