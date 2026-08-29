"""Purge expired auth data: stale magic-link rows and old usage counters.

Both tables grow with traffic and hold personal data (emails, IP-derived keys),
so unbounded retention would quietly break the privacy policy's deletion
promise. Magic-link rows are dead one TTL after issue; they are kept one extra
day so recent abuse (attempt caps, send bursts) stays inspectable, then dropped.
Usage counters only *drive* quotas for the current UTC day; older rows are kept
30 days for abuse triage (which /64 farmed signups last week), then dropped.

Run it daily from cron, next to the meals generation job:

    uv run python -m app.scripts.purge_auth_data
"""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import structlog
from sqlalchemy import CursorResult, delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import configure_logging
from app.db.engine import SessionLocal
from app.models.magic_link_token import MagicLinkToken
from app.models.usage_counter import UsageCounter

log = structlog.get_logger(__name__)

MAGIC_LINK_RETENTION = timedelta(days=1)
USAGE_COUNTER_RETENTION_DAYS = 30


async def purge(
    session_factory: async_sessionmaker[AsyncSession] = SessionLocal,
    *,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Delete expired rows; return (magic links purged, counters purged)."""
    now = now or datetime.now(UTC)
    counter_cutoff = (now - timedelta(days=USAGE_COUNTER_RETENTION_DAYS)).date()
    async with session_factory() as session:
        # A DELETE always yields a CursorResult; the ORM signature is just wider.
        links = cast(
            CursorResult[Any],
            await session.execute(
                delete(MagicLinkToken).where(MagicLinkToken.expires_at < now - MAGIC_LINK_RETENTION)
            ),
        )
        counters = cast(
            CursorResult[Any],
            await session.execute(delete(UsageCounter).where(UsageCounter.date < counter_cutoff)),
        )
        await session.commit()
    return links.rowcount, counters.rowcount


async def main() -> None:
    magic_links, counters = await purge()
    log.info("purge_auth_data.done", magic_links=magic_links, usage_counters=counters)


if __name__ == "__main__":
    configure_logging()
    asyncio.run(main())
