"""User account lookup, authentication, and creation.

The auth side of the account gate: find a user by id or email, verify an admin
password, create or reset admins for the ``create_admin`` CLI, and register
passwordless public users once a login flow has verified their email. No HTTP
concerns and no commits (the session/route layer owns the transaction).
"""

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, verify_password
from app.enums import Role
from app.models.user import User, normalize_email
from app.schemas.admin import AdminLoginRequest

log = structlog.get_logger(__name__)

# A valid bcrypt hash no password is expected to match. Verifying against it when
# the email is unknown keeps login's timing roughly constant, so a response time
# cannot reveal whether an account exists.
_DUMMY_HASH = "$2b$12$crB67Aj6UoOU7YdzxnSk7uC/vEzUlAJ6c1gbsBgoWkOLWHbmaBPQ."


class InvalidCredentials(Exception):
    """The single refusal for any failed admin password login.

    Identical for a wrong password, an unknown email, and a disabled account, so the
    answer never reveals which of those it was. The API boundary maps this to 401.
    """


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

    async def authenticate_admin(self, payload: AdminLoginRequest, *, ip: str) -> User:
        """Verify admin credentials and return the account; the caller opens the session."""
        user = await self.authenticate(payload.email, payload.password)
        if user is None:
            # Log the attempted email and source IP so brute force is visible. The
            # password is never logged.
            log.warning("admin.login.failed", email=payload.email, client=ip)
            raise InvalidCredentials("Incorrect email or password.")
        if not user.is_active:
            # Correct credentials on a disabled account. Logged on its own event for the
            # operator, but answered identically so a session never opens and the
            # answer cannot confirm the account exists.
            log.warning("admin.login.inactive", email=user.email, client=ip)
            raise InvalidCredentials("Incorrect email or password.")
        log.info("admin.login.success", email=user.email, client=ip)
        return user

    async def authenticate(self, email: str, password: str) -> User | None:
        """Return the account when the password matches, else None.

        Runs a throwaway hash check on an unknown email so the wrong-password and
        unknown-email paths cost about the same. A passwordless (public) account
        takes the same path as an unknown email: it has no password to match, and
        answering differently would confirm the account exists.

        bcrypt is deliberate CPU; run it off the event loop so concurrent logins
        (and the constant-time unknown-email path) do not serialize the worker.
        """
        user = await self.get_by_email(email)
        if user is None or user.password_hash is None:
            await asyncio.to_thread(verify_password, password, _DUMMY_HASH)
            return None
        if not await asyncio.to_thread(verify_password, password, user.password_hash):
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
        password_hash = await asyncio.to_thread(hash_password, password)
        user = await self.get_by_email(email)
        if user is None:
            user = User(email=email, password_hash=password_hash, role=Role.ADMIN)
            self._session.add(user)
            log.info("admin.created", email=user.email)
            return user, True
        user.password_hash = password_hash
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

    async def register_public_user(self, email: str, *, created_from_ip: str | None) -> User:
        """Create a passwordless public account for an already-verified email.

        The public signup path (magic link, OAuth): the caller has already proven
        control of the email and confirmed no account exists, so possession is the
        whole credential. New accounts are hardcoded ``role=USER`` with no password,
        keeping ``create_or_update`` (the CLI) the only path to ADMIN.

        The insert runs inside a savepoint so a lost race — a concurrent signup of
        the same email — collapses to adopting the winner rather than a 500 on the
        unique-email constraint. The caller commits.
        """
        user = User(email=email, password_hash=None, created_from_ip=created_from_ip)
        self._session.add(user)
        try:
            # Flush inside the savepoint so a unique-email collision rolls back only
            # the insert, leaving the outer login transaction intact. It also
            # populates the server defaults (is_active, token_version) the caller
            # reads to gate the login and mint the session.
            async with self._session.begin_nested():
                await self._session.flush()
        except IntegrityError:
            self._session.expunge(user)
            existing = await self.get_by_email(email)
            if existing is None:
                raise
            log.info("user.register_race_adopted", user_id=str(existing.id))
            return existing
        log.info("user.registered", user_id=str(user.id), client=created_from_ip)
        return user

    async def record_login(self, user: User) -> None:
        """Stamp a successful login. The caller commits."""
        user.last_login_at = datetime.now(UTC)

    async def revoke_sessions(self, user: User) -> None:
        """Invalidate every outstanding session token for the account.

        Bumping ``token_version`` makes the per-request DB recheck refuse any
        token minted before now — "sign out everywhere" for cookie sessions that
        are otherwise irrevocable until they expire. The caller commits.
        """
        user.token_version += 1
        log.info("user.sessions_revoked", user_id=str(user.id))

    async def delete(self, user: User) -> None:
        """Hard-delete an account (GDPR erasure). The caller commits.

        Deliberately not the soft ``is_active`` switch: deletion is the user's
        right to be forgotten, so the row goes away entirely.
        """
        await self._session.delete(user)
        log.info("user.deleted", user_id=str(user.id))
