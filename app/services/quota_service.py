"""Daily quota counters for the shared LLM tier and signup velocity.

The mechanism is one conditional upsert per scope on ``usage_counters``: insert
the day's row at 1, or increment it only while it is under the limit. No row
back means the limit is hit. Charging user, IP, and global in one transaction in
a fixed order keeps concurrent charges deadlock-free, and rolling the whole
transaction back on any refusal means a global-cap rejection never burns the
caller's personal quota.

This service deliberately breaks the "services never commit" rule: it opens its
own short session and commits before the LLM call runs. Riding the request
transaction would hold the row locks (including the single global row every
shared-tier request touches) across a many-second model call, serializing all
shared-tier traffic. The flip side is that a charge is not refunded when the
model call later fails; refunding would need a second commit after a possibly
crashed call, and would let a client farm free retries by aborting requests.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import settings
from app.models.usage_counter import UsageCounter

log = structlog.get_logger(__name__)

# The global counter is one row per day; this is its key.
GLOBAL_KEY = "all"

QuotaScope = Literal["user", "ip", "global", "signup_ip", "magic_send_ip"]


class QuotaExceededError(Exception):
    """A daily quota is exhausted. The API boundary maps this to 429."""

    def __init__(self, scope: QuotaScope, *, used: int, limit: int, resets_at: datetime) -> None:
        self.scope = scope
        self.used = used
        self.limit = limit
        self.resets_at = resets_at
        super().__init__(f"Daily {scope} quota exhausted ({used}/{limit}).")


@dataclass(frozen=True, slots=True)
class QuotaStatus:
    """A read-only snapshot of the caller's shared-tier allowance."""

    used: int
    limit: int
    resets_at: datetime


def _today() -> date:
    """The quota day. UTC everywhere so the reset instant is globally consistent."""
    return datetime.now(UTC).date()


def _next_reset() -> datetime:
    """Next UTC midnight, when every daily counter starts a fresh row."""
    return datetime.combine(_today() + timedelta(days=1), time.min, tzinfo=UTC)


class QuotaService:
    """Charges and reads daily usage counters. Commits its own transactions."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def charge_shared(self, user_id: UUID, ip: str) -> None:
        """Spend one shared-tier call for this user, their IP, and the site.

        All three scopes must clear; the fixed user -> ip -> global order prevents
        deadlocks between concurrent charges. Any refusal rolls back the whole
        charge and raises.

        Raises:
            QuotaExceededError: one of the three daily limits is exhausted.
        """
        day = _today()
        charges: tuple[tuple[QuotaScope, str, int], ...] = (
            ("user", str(user_id), settings.shared_user_daily_limit),
            ("ip", ip, settings.shared_ip_daily_limit),
            ("global", GLOBAL_KEY, settings.shared_global_daily_limit),
        )
        async with self._session_factory() as session:
            for scope, key, limit in charges:
                if not await self._increment_under_limit(session, scope, key, day, limit):
                    await session.rollback()
                    log.info("quota.exhausted", scope=scope, key=key, limit=limit)
                    raise QuotaExceededError(
                        scope, used=limit, limit=limit, resets_at=_next_reset()
                    )
            await session.commit()

    async def charge_signup(self, ip: str) -> None:
        """Spend one account creation for this IP.

        Raises:
            QuotaExceededError: the IP already created today's allowance of accounts.
        """
        await self._charge_single(
            "signup_ip", ip, settings.signup_ip_daily_limit, event="quota.signup_exhausted"
        )

    async def charge_magic_send(self, ip: str) -> None:
        """Spend one magic-link email for this IP.

        Bounds inbox bombing and Resend spend when Turnstile is not configured;
        the per-minute burst limit caps the rate, this caps the daily total.

        Raises:
            QuotaExceededError: the IP already sent today's allowance of emails.
        """
        await self._charge_single(
            "magic_send_ip",
            ip,
            settings.magic_send_ip_daily_limit,
            event="quota.magic_send_exhausted",
        )

    async def _charge_single(self, scope: QuotaScope, key: str, limit: int, *, event: str) -> None:
        """Take one unit of a single daily scope in its own transaction, or raise.

        The one-scope siblings (signup, magic send) share this, while charge_shared
        keeps its own loop to charge three scopes in one transaction.

        Raises:
            QuotaExceededError: this scope's daily limit is exhausted.
        """
        async with self._session_factory() as session:
            if not await self._increment_under_limit(session, scope, key, _today(), limit):
                await session.rollback()
                log.warning(event, client=key, limit=limit)
                raise QuotaExceededError(scope, used=limit, limit=limit, resets_at=_next_reset())
            await session.commit()

    async def read_status(self, user_id: UUID, session: AsyncSession) -> QuotaStatus:
        """The user's shared-tier allowance today, for display.

        Reads on the caller's (request) session: no counters change, so riding
        the request transaction is safe here. The per-user limit is what the UI
        shows; the IP and global caps surface only through a 429's payload.
        """
        stmt = select(UsageCounter.count).where(
            UsageCounter.scope == "user",
            UsageCounter.key == str(user_id),
            UsageCounter.date == _today(),
        )
        used = (await session.execute(stmt)).scalar_one_or_none() or 0
        return QuotaStatus(
            used=used, limit=settings.shared_user_daily_limit, resets_at=_next_reset()
        )

    async def _increment_under_limit(
        self, session: AsyncSession, scope: str, key: str, day: date, limit: int
    ) -> bool:
        """Atomically take one unit of (scope, key, day) if any remains.

        The upsert's WHERE makes check-and-increment a single statement, so two
        concurrent charges cannot both slip under the limit: the second blocks on
        the row lock until the first commits, then sees its count.
        """
        stmt = (
            insert(UsageCounter)
            .values(scope=scope, key=key, date=day, count=1)
            .on_conflict_do_update(
                index_elements=[UsageCounter.scope, UsageCounter.key, UsageCounter.date],
                # ON CONFLICT bypasses the ORM's onupdate hook, so bump updated_at here.
                set_={"count": UsageCounter.count + 1, "updated_at": func.now()},
                where=UsageCounter.count < limit,
            )
            .returning(UsageCounter.count)
        )
        return (await session.execute(stmt)).scalar_one_or_none() is not None
