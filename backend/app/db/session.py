"""Per-request database session.

The session opens when a request comes in, commits if the handler returns
normally, and rolls back if it raises. Services do not commit. Keeping that
decision in one place means the service layer never deals with transactions.
"""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.engine import SessionLocal


async def get_session() -> AsyncIterator[AsyncSession]:
    """Yield a session, committing on success and rolling back on error."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
