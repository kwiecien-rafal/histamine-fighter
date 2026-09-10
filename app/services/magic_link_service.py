"""Issue and consume magic-link login tokens.

The route builds the signed JWT and the email; this service owns the DB rows
that make a signature insufficient on its own: single use, expiry, and the
guess cap on the 6-digit code. Consumption is a single conditional UPDATE, so
two concurrent redeems of the same login serialize on the row lock and exactly
one wins. The service rides the route's transaction and never commits it, so a
login that fails later (an inactive account) leaves no consumed token behind —
the row stays redeemable, and redemption always re-checks the account, so
nothing is gained by replaying it. One deliberate exception: failed code
attempts are counted in their own committed transaction, because a failed
verify ends in a 401 that rolls the request back, and an increment that rolls
back with it would never cap anything.
"""

import asyncio
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.security import hash_password, verify_password
from app.db.engine import SessionLocal
from app.models.magic_link_token import MagicLinkToken
from app.models.user import normalize_email
from app.schemas.auth import MAGIC_CODE_LENGTH

log = structlog.get_logger(__name__)


class MagicLinkService:
    """Creates and redeems pending passwordless logins. Never commits."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def issue(self, email: str, *, created_from_ip: str | None) -> tuple[UUID, str]:
        """Create a pending login and return its jti and plaintext code."""
        normalized = normalize_email(email)
        await self._session.execute(
            update(MagicLinkToken)
            .where(MagicLinkToken.email == normalized, MagicLinkToken.consumed_at.is_(None))
            .values(consumed_at=datetime.now(UTC))
        )
        code = f"{secrets.randbelow(10**MAGIC_CODE_LENGTH):0{MAGIC_CODE_LENGTH}d}"
        # bcrypt is deliberate CPU; run it off the event loop so concurrent logins
        # do not serialize behind each other's hash.
        code_hash = await asyncio.to_thread(hash_password, code)
        row = MagicLinkToken(
            email=normalized,
            code_hash=code_hash,
            created_from_ip=created_from_ip,
            expires_at=datetime.now(UTC) + timedelta(minutes=settings.magic_link_ttl_minutes),
        )
        self._session.add(row)
        await self._session.flush()
        log.info("magic_link.issued", jti=str(row.id))
        return row.id, code

    async def consume_by_token(self, jti: UUID) -> str | None:
        """Redeem the link's token: return the email, or None if unusable."""
        return await self._consume(jti)

    async def consume_by_code(self, email: str, code: str) -> str | None:
        """Redeem a 6-digit code for an email: return the email, or None."""
        normalized = normalize_email(email)
        stmt = (
            select(MagicLinkToken)
            .where(MagicLinkToken.email == normalized, MagicLinkToken.consumed_at.is_(None))
            .order_by(MagicLinkToken.created_at.desc())
            .limit(1)
        )
        row = (await self._session.execute(stmt)).scalar_one_or_none()
        if row is None or row.expires_at <= datetime.now(UTC):
            return None
        attempts = await self._record_attempt(row.id)
        if attempts > settings.magic_link_max_attempts:
            log.warning("magic_link.attempts_exhausted", jti=str(row.id))
            return None
        if not await asyncio.to_thread(verify_password, code, row.code_hash):
            return None
        return await self._consume(row.id)

    async def _consume(self, jti: UUID) -> str | None:
        """Atomically stamp the row consumed, returning its email; None if lost."""
        stmt = (
            update(MagicLinkToken)
            .where(
                MagicLinkToken.id == jti,
                MagicLinkToken.consumed_at.is_(None),
                MagicLinkToken.expires_at > datetime.now(UTC),
            )
            .values(consumed_at=datetime.now(UTC))
            .returning(MagicLinkToken.email)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def _record_attempt(self, jti: UUID) -> int:
        """Count one code attempt against the row, durably; return the new total."""
        stmt = (
            update(MagicLinkToken)
            .where(MagicLinkToken.id == jti)
            .values(attempts=MagicLinkToken.attempts + 1)
            .returning(MagicLinkToken.attempts)
        )
        async with SessionLocal() as session:
            attempts = (await session.execute(stmt)).scalar_one_or_none()
            await session.commit()
        if attempts is None:
            return settings.magic_link_max_attempts + 1
        return attempts
