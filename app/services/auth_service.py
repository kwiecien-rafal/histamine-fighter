"""Sign-in and account erasure, shared by the JSON auth routes and the login pages.

Both presentations run the same steps: prove the request is human and the address
usable, spend the per-IP caps, issue or redeem the single-use token, and resolve it
to an account. Only the wording of a refusal differs — a status code or a line on
the form — so refusals raise domain errors here and each boundary words them.

The session cookie stays with the caller: this service never sees a ``Response``, so
minting and clearing remain where the HTTP lives.
"""

from datetime import timedelta
from uuid import UUID

import httpx
import structlog
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.client_ip import ip_bucket
from app.core.disposable_domains import is_disposable
from app.core.logging import mask_email
from app.core.security import TokenError, create_purpose_token, decode_purpose_token
from app.core.turnstile import verify_turnstile
from app.enums import Role
from app.models.magic_link_token import MagicLinkToken
from app.models.saved_meal import SavedMeal
from app.models.usage_counter import UsageCounter
from app.models.user import User
from app.schemas.auth import MagicLinkRequest, MagicLinkVerify
from app.services.email_service import EmailService
from app.services.magic_link_service import MagicLinkService
from app.services.quota_service import QuotaExceededError, QuotaService
from app.services.user_service import UserService

log = structlog.get_logger(__name__)

MAGIC_LINK_PURPOSE = "magic_link"


class DisposableEmailRefused(Exception):
    """The address is on the disposable-domain blocklist.

    The one refusal that is not answered uniformly: the caller must be told the
    address cannot work, and disposability is public knowledge, not account state.
    The API boundary maps this to 400.
    """


class InvalidSignInAttempt(Exception):
    """The single refusal for any unusable magic link or code.

    Expired, consumed, tampered, wrong-code, an admin address, and a deactivated
    account all raise this identically, so the answer never narrows an attacker's
    search. The API boundary maps this to 401.
    """


class SelfServeDeletionRefused(Exception):
    """A non-public account tried to erase itself. The API boundary maps this to 403."""


class AuthService:
    """Magic-link sign-in and account erasure."""

    def __init__(
        self,
        session: AsyncSession,
        magic_links: MagicLinkService,
        users: UserService,
        quota: QuotaService,
        emails: EmailService,
        http_client: httpx.AsyncClient,
    ) -> None:
        self._session = session
        self._magic_links = magic_links
        self._users = users
        self._quota = quota
        self._emails = emails
        self._http_client = http_client

    async def request_magic_link(self, payload: MagicLinkRequest, *, ip: str) -> None:
        """Send a sign-in email carrying a single-use link and its 6-digit code.

        Guarded by Turnstile (when configured), the disposable-domain blocklist, and
        a per-IP daily send cap that bounds inbox bombing even when Turnstile is not
        configured. A capped caller returns normally without sending, so a hit
        reveals nothing.
        """
        await verify_turnstile(self._http_client, payload.turnstile_token, ip)
        if is_disposable(payload.email):
            log.info("magic_link.disposable_refused", client=ip)
            raise DisposableEmailRefused("Disposable email addresses can't be used to sign in.")
        try:
            await self._quota.charge_magic_send(ip_bucket(ip))
        except QuotaExceededError:
            log.warning("magic_link.send_cap_reached", client=ip)
            return
        jti, code = await self._magic_links.issue(payload.email, created_from_ip=ip)
        token = create_purpose_token(
            MAGIC_LINK_PURPOSE,
            jti=str(jti),
            ttl=timedelta(minutes=settings.magic_link_ttl_minutes),
        )
        link_url = f"{settings.app_base_url}/login/verify?token={token}"
        # Persist the pending login before the external send: it releases the row locks
        # issue() took (invalidating prior tokens) and guarantees the emailed link
        # points at a committed row, so Resend latency is never held across the open
        # request transaction and a send never outlives a rolled-back token.
        await self._session.commit()
        await self._emails.send_magic_link(payload.email, link_url=link_url, code=code)

    async def redeem_magic_link(self, payload: MagicLinkVerify, *, ip: str) -> User:
        """Redeem a link token or an email + code, and return the account it proves.

        First-ever login creates the account (charged against the signup velocity cap
        first, so a refused signup consumes nothing else). The caller mints the
        session cookie, whose TTL follows the account's role.
        """
        email = await self._redeem(payload)
        if email is None:
            raise InvalidSignInAttempt
        user = await self._users.get_by_email(email)
        if user is None:
            # The signup charge commits on its own before the account exists, so a
            # failure after it wastes one signup slot for the IP. Acceptable: the
            # alternative is an uncharged path that farms accounts via induced errors.
            await self._quota.charge_signup(ip_bucket(ip))
            user = await self._users.register_public_user(email, created_from_ip=ip)
        if user.role is Role.ADMIN:
            # Admin auth is the password at /admin/auth, deliberately: inbox
            # possession must never be enough for the panel. The uniform refusal
            # means it does not confirm the address belongs to an admin.
            log.warning("auth.login.admin_refused", email=mask_email(email), client=ip)
            raise InvalidSignInAttempt
        if not user.is_active:
            log.warning("auth.login.inactive", email=mask_email(email), client=ip)
            raise InvalidSignInAttempt
        await self._users.record_login(user)
        log.info("auth.login.magic", user_id=str(user.id), client=ip)
        return user

    async def erase_account(self, user: User) -> None:
        """Erase the account and everything attached to it; the caller clears the cookie.

        Hard delete, not deactivation: the point is that no personal data remains.
        Saved meals also cascade at the database level; the explicit delete keeps the
        erasure visible here alongside the other purges.
        """
        if user.role is not Role.USER:
            # Admin accounts are operator-managed (manage_admin CLI). Self-serve
            # erasure from the public drawer must not be able to take the panel down.
            raise SelfServeDeletionRefused(
                "Admin accounts are managed via the CLI, not self-serve deletion."
            )
        await self._session.execute(delete(SavedMeal).where(SavedMeal.user_id == user.id))
        await self._session.execute(
            delete(UsageCounter).where(
                UsageCounter.scope == "user", UsageCounter.key == str(user.id)
            )
        )
        await self._session.execute(
            delete(MagicLinkToken).where(MagicLinkToken.email == user.email)
        )
        await self._users.delete(user)

    async def _redeem(self, payload: MagicLinkVerify) -> str | None:
        """Resolve either verify path to the proven email, or None."""
        if payload.token is not None:
            try:
                jti = UUID(decode_purpose_token(payload.token, expected_purpose=MAGIC_LINK_PURPOSE))
            except (TokenError, ValueError):
                return None
            return await self._magic_links.consume_by_token(jti)
        if payload.email is not None and payload.code is not None:
            return await self._magic_links.consume_by_code(payload.email, payload.code)
        return None  # pragma: no cover - schema validator guarantees one path
