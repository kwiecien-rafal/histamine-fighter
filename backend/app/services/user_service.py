"""User account lookup, authentication, and creation.

The auth side of the account gate: find a user by id or email, verify a password,
and create or reset one for the ``create_admin`` CLI (which stamps the admin role).
No HTTP concerns and no commits (the session/route layer owns the transaction).
"""

from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.enums import Role
from app.models.user import User, normalize_email

log = structlog.get_logger(__name__)

# A valid bcrypt hash no password is expected to match. Verifying against it when
# the email is unknown keeps login's timing roughly constant, so a response time
# cannot reveal whether an account exists.
_DUMMY_HASH = "$2b$12$crB67Aj6UoOU7YdzxnSk7uC/vEzUlAJ6c1gbsBgoWkOLWHbmaBPQ."


class UserService:
    """Reads and writes user accounts. Never commits."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: UUID) -> User | None:
        """Return the account for an id, or None if there is no match.

        The auth gate resolves the JWT subject (the user's id) through here.
        """
        return await self._session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        """Return the account for an email, or None if there is no match."""
        stmt = select(User).where(User.email == normalize_email(email))
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def authenticate(self, email: str, password: str) -> User | None:
        """Return the account when the password matches, else None.

        Runs a throwaway hash check on an unknown email so the wrong-password and
        unknown-email paths cost about the same.
        """
        user = await self.get_by_email(email)
        if user is None:
            verify_password(password, _DUMMY_HASH)
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user

    async def create_or_update(self, email: str, password: str) -> tuple[User, bool]:
        """Create an admin account, or reset an existing account's password.

        This is the admin-elevation path (the ``create_admin`` CLI), so the account
        ends up ``role=ADMIN`` whether it is created or updated. Running it for an
        existing non-admin email therefore both resets the password and grants admin.
        Returns the account and whether it was newly created. A reset bumps the token
        version so any token issued under the old password stops working. The caller
        commits.
        """
        user = await self.get_by_email(email)
        if user is None:
            user = User(email=email, password_hash=hash_password(password), role=Role.ADMIN)
            self._session.add(user)
            log.info("admin.created", email=user.email)
            return user, True
        user.password_hash = hash_password(password)
        user.token_version += 1
        user.role = Role.ADMIN
        log.info("admin.password_reset", email=user.email)
        return user, False

    async def set_active(self, email: str, *, active: bool) -> User | None:
        """Enable or disable an account, or return None if the email is unknown.

        The auth gate re-reads is_active on every request, so disabling an account
        locks it out on its next call without waiting for the token to expire. The
        caller commits.
        """
        user = await self.get_by_email(email)
        if user is None:
            return None
        user.is_active = active
        log.info("admin.active_changed", email=user.email, active=active)
        return user

    # A future public register_user() would live here, hardcoding role=Role.USER so
    # the only path to ADMIN stays create_or_update (the CLI). Deferred: no public
    # account feature exists yet.
