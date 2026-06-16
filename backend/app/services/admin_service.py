"""Admin account lookup, authentication, and creation.

The auth side of the admin gate: find an operator by email, verify a password,
and create or reset one for the ``create_admin`` CLI. No HTTP concerns and no
commits (the session/route layer owns the transaction).
"""

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.models.admin_user import AdminUser, normalize_email

log = structlog.get_logger(__name__)

# A valid bcrypt hash no password is expected to match. Verifying against it when
# the email is unknown keeps login's timing roughly constant, so a response time
# cannot reveal whether an account exists.
_DUMMY_HASH = "$2b$12$crB67Aj6UoOU7YdzxnSk7uC/vEzUlAJ6c1gbsBgoWkOLWHbmaBPQ."


class AdminService:
    """Reads and writes admin accounts. Never commits."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_email(self, email: str) -> AdminUser | None:
        """Return the account for an email, or None if there is no match."""
        stmt = select(AdminUser).where(AdminUser.email == normalize_email(email))
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def authenticate(self, email: str, password: str) -> AdminUser | None:
        """Return the account when the password matches, else None.

        Runs a throwaway hash check on an unknown email so the wrong-password and
        unknown-email paths cost about the same.
        """
        admin = await self.get_by_email(email)
        if admin is None:
            verify_password(password, _DUMMY_HASH)
            return None
        if not verify_password(password, admin.password_hash):
            return None
        return admin

    async def create_or_update(self, email: str, password: str) -> tuple[AdminUser, bool]:
        """Create an account, or reset its password if the email already exists.

        Returns the account and whether it was newly created. The caller commits.
        """
        admin = await self.get_by_email(email)
        if admin is None:
            admin = AdminUser(email=email, password_hash=hash_password(password))
            self._session.add(admin)
            log.info("admin.created", email=admin.email)
            return admin, True
        admin.password_hash = hash_password(password)
        log.info("admin.password_reset", email=admin.email)
        return admin, False
