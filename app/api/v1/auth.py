"""Public passwordless auth: magic link, Google/GitHub OAuth, session state, and
account deletion.

Possession of a verified email inbox is the whole credential, however it is
proven: clicking a magic link, entering its 6-digit code, or an OAuth provider
vouching for the address. Every path converges on the same account upsert and
the same httpOnly cookie session the admin gate uses; ``role`` keeps the two
worlds apart. Password login stays at ``/admin/auth`` and is admin-only.
"""

import hashlib
import secrets
from base64 import urlsafe_b64encode
from datetime import timedelta

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.client_ip import client_ip, ip_bucket
from app.core.disposable_domains import is_disposable
from app.core.logging import mask_email
from app.core.ratelimit import auth_rate_limit, limiter
from app.core.security import TokenError, create_purpose_token, decode_purpose_token
from app.core.session_cookie import clear_session_cookie, mint_session
from app.db.session import get_session
from app.dependencies import (
    get_auth_service,
    get_current_user,
    get_http_client,
    get_quota_service,
    get_user_service,
)
from app.enums import Role
from app.models.user import User
from app.schemas.auth import AuthUser, MagicLinkRequest, MagicLinkVerify, QuotaRead
from app.services.auth_service import (
    AuthService,
    DisposableEmailRefused,
    InvalidSignInAttempt,
    SelfServeDeletionRefused,
)
from app.services.oauth_service import (
    PROVIDERS,
    OAuthError,
    OAuthProvider,
    credentials,
    exchange_code,
    fetch_verified_email,
)
from app.services.quota_service import QuotaExceededError, QuotaService
from app.services.user_service import UserService

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

OAUTH_STATE_PURPOSE = "oauth_state"
OAUTH_STATE_TTL = timedelta(minutes=10)


def _state_cookie_name(provider: OAuthProvider) -> str:
    """One state cookie per provider, so a Google attempt in one tab cannot
    clobber a GitHub attempt in another. Two concurrent attempts at the *same*
    provider still last-write-win — accepted; the older tab just retries."""
    return f"hf_oauth_{provider.name}"


# One 200 body for every magic-link request, whatever happened server-side, so
# the response cannot be used to probe the blocklist or the send outcome.
_MAGIC_REQUEST_ACCEPTED = {"detail": "If the address is usable, a sign-in email is on its way."}


def _invalid_login() -> HTTPException:
    """The single 401 for any unusable magic link or code.

    Expired, consumed, tampered, and wrong-code all answer identically, so the
    response never narrows an attacker's search.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="That sign-in link or code is invalid or has expired. Request a new one.",
    )


@router.post("/magic/request")
@limiter.limit(auth_rate_limit)
async def request_magic_link(
    request: Request,
    payload: MagicLinkRequest,
    auth: AuthService = Depends(get_auth_service),
) -> dict[str, str]:
    """Send a sign-in email carrying a single-use link and its 6-digit code.

    Guarded by the burst rate limit and, in the service, by Turnstile, the
    disposable-domain blocklist, and a per-IP daily send cap. The blocklist is the
    one refusal that answers 400 rather than the uniform 200: the caller must be
    told the address cannot work, and disposability is public knowledge, not
    account state. A capped send answers the same uniform 200, so a hit reveals
    nothing.
    """
    try:
        await auth.request_magic_link(payload, ip=client_ip(request))
    except DisposableEmailRefused as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _MAGIC_REQUEST_ACCEPTED


@router.post("/magic/verify")
@limiter.limit(auth_rate_limit)
async def verify_magic_link(
    request: Request,
    response: Response,
    payload: MagicLinkVerify,
    auth: AuthService = Depends(get_auth_service),
) -> AuthUser:
    """Redeem a link token or an email + code, and open the session."""
    try:
        user = await auth.redeem_magic_link(payload, ip=client_ip(request))
    except InvalidSignInAttempt as exc:
        raise _invalid_login() from exc
    mint_session(response, user)
    response.headers["Cache-Control"] = "no-store"
    return AuthUser.model_validate(user)


def _oauth_provider_or_404(name: str) -> OAuthProvider:
    provider = PROVIDERS.get(name)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown sign-in provider."
        )
    return provider


def _oauth_redirect_uri(provider: OAuthProvider) -> str:
    """The callback URL registered at the provider.

    Anchored on the SPA origin, not the Host header: in dev the Vite proxy fronts
    ``/api`` and in production the reverse proxy does, so the browser's whole OAuth
    round trip stays on one origin, and a spoofed Host can never move it.
    """
    return f"{settings.app_base_url}/api/v1/auth/oauth/{provider.name}/callback"


def _clear_oauth_state_cookie(response: Response, provider: OAuthProvider) -> None:
    """Expire the OAuth state cookie, mirroring the attributes it was set with."""
    response.delete_cookie(
        key=_state_cookie_name(provider),
        path="/api/v1/auth/oauth",
        secure=settings.cookie_secure,
        samesite="lax",
    )


def _login_error_redirect(reason: str, provider: OAuthProvider) -> RedirectResponse:
    """Land a failed OAuth round trip back on the login page, mid-navigation.

    The browser is following redirects, not reading JSON, so errors travel as a
    coarse query flag the login page turns into copy. 303 forces a GET. The state
    cookie is expired here too so a failed round trip cannot leave a replayable
    one behind.
    """
    response = RedirectResponse(
        f"{settings.app_base_url}/login?error={reason}", status_code=status.HTTP_303_SEE_OTHER
    )
    _clear_oauth_state_cookie(response, provider)
    return response


@router.get("/oauth/{provider_name}/start")
@limiter.limit(auth_rate_limit)
async def oauth_start(request: Request, provider_name: str) -> RedirectResponse:
    """Send the browser to the provider's consent screen.

    The random ``state`` (and, for Google, the PKCE verifier) rides in a signed,
    short-lived httpOnly cookie; the callback refuses any response that does not
    match it, which is the CSRF gate for the whole round trip.
    """
    provider = _oauth_provider_or_404(provider_name)
    creds = credentials(provider)
    if creds is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"{provider.name} sign-in is not configured on this deployment.",
        )
    client_id, _ = creds
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": _oauth_redirect_uri(provider),
        "scope": provider.scopes,
        "state": state,
    }
    if provider.uses_pkce:
        challenge = urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(
            b"="
        )
        params["code_challenge"] = challenge.decode("ascii")
        params["code_challenge_method"] = "S256"
    url = httpx.URL(provider.auth_url, params=params)
    response = RedirectResponse(str(url), status_code=status.HTTP_302_FOUND)
    # provider:state:verifier, signed. Binding the provider in prevents replaying
    # one provider's state cookie against the other's callback.
    response.set_cookie(
        key=_state_cookie_name(provider),
        value=create_purpose_token(
            OAUTH_STATE_PURPOSE,
            jti=f"{provider.name}:{state}:{verifier}",
            ttl=OAUTH_STATE_TTL,
        ),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/api/v1/auth/oauth",
        max_age=int(OAUTH_STATE_TTL.total_seconds()),
    )
    return response


@router.get("/oauth/{provider_name}/callback")
@limiter.limit(auth_rate_limit)
async def oauth_callback(
    request: Request,
    provider_name: str,
    code: str | None = None,
    state: str | None = None,
    http_client: httpx.AsyncClient = Depends(get_http_client),
    user_service: UserService = Depends(get_user_service),
    quota: QuotaService = Depends(get_quota_service),
) -> RedirectResponse:
    """Finish the provider round trip and open the session.

    Every failure lands back on the login page with a coarse error flag; the
    fixed redirect targets make an open redirect impossible.
    """
    provider = _oauth_provider_or_404(provider_name)
    ip = client_ip(request)
    expected = _consume_state_cookie(request, provider)
    if expected is None or state is None or code is None:
        return _login_error_redirect("oauth", provider)
    expected_provider, expected_state, verifier = expected
    if expected_provider != provider.name or not secrets.compare_digest(state, expected_state):
        log.warning("oauth.state_mismatch", provider=provider.name, client=ip)
        return _login_error_redirect("oauth", provider)
    try:
        access_token = await exchange_code(
            http_client,
            provider,
            code=code,
            redirect_uri=_oauth_redirect_uri(provider),
            pkce_verifier=verifier,
        )
        email = await fetch_verified_email(http_client, provider, access_token)
    except OAuthError:
        return _login_error_redirect("oauth", provider)
    user = await user_service.get_by_email(email)
    if user is None:
        # Disposability gates account farming, so it only bars a new signup. An
        # existing account whose domain was later blocklisted must still sign in.
        if is_disposable(email):
            log.info("oauth.disposable_refused", provider=provider.name, client=ip)
            return _login_error_redirect("oauth", provider)
        try:
            await quota.charge_signup(ip_bucket(ip))
        except QuotaExceededError:
            return _login_error_redirect("signup_limit", provider)
        user = await user_service.register_public_user(email, created_from_ip=ip)
    if user.role is Role.ADMIN:
        # Same stance as the magic-link path: the panel is never one OAuth
        # consent away. The coarse flag keeps the refusal indistinguishable.
        log.warning(
            "auth.login.admin_refused",
            provider=provider.name,
            email=mask_email(email),
            client=ip,
        )
        return _login_error_redirect("oauth", provider)
    if not user.is_active:
        log.warning("auth.login.inactive", email=mask_email(email), client=ip)
        return _login_error_redirect("oauth", provider)
    await user_service.record_login(user)
    response = RedirectResponse(
        f"{settings.app_base_url}/login/complete", status_code=status.HTTP_303_SEE_OTHER
    )
    mint_session(response, user)
    _clear_oauth_state_cookie(response, provider)
    # This redirect plants the session cookie, so keep it out of any shared cache,
    # matching the JSON login paths.
    response.headers["Cache-Control"] = "no-store"
    log.info("auth.login.oauth", provider=provider.name, user_id=str(user.id), client=ip)
    return response


def _consume_state_cookie(request: Request, provider: OAuthProvider) -> tuple[str, str, str] | None:
    """Decode the provider's state cookie into (provider, state, verifier), or None."""
    raw = request.cookies.get(_state_cookie_name(provider))
    if raw is None:
        return None
    try:
        jti = decode_purpose_token(raw, expected_purpose=OAUTH_STATE_PURPOSE)
    except TokenError:
        return None
    parts = jti.split(":", 2)
    if len(parts) != 3:
        return None
    return parts[0], parts[1], parts[2]


@router.get("/me")
async def me(response: Response, user: User = Depends(get_current_user)) -> AuthUser:
    """Return the signed-in user, or 401; how the SPA bootstraps session state."""
    response.headers["Cache-Control"] = "no-store"
    return AuthUser.model_validate(user)


@router.get("/me/quota")
async def my_quota(
    response: Response,
    user: User = Depends(get_current_user),
    quota: QuotaService = Depends(get_quota_service),
    session: AsyncSession = Depends(get_session),
) -> QuotaRead:
    """The user's shared-tier allowance today, for the settings UI."""
    response.headers["Cache-Control"] = "no-store"
    status_ = await quota.read_status(user.id, session)
    return QuotaRead(used=status_.used, limit=status_.limit, resets_at=status_.resets_at)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    """Delete the session cookie. Idempotent, so it is safe without a session."""
    clear_session_cookie(response)


@router.post("/logout/all", status_code=status.HTTP_204_NO_CONTENT)
@limiter.limit(auth_rate_limit)
async def logout_all(
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
    user_service: UserService = Depends(get_user_service),
) -> None:
    """Sign out everywhere: revoke every outstanding session for the account.

    A plain logout only deletes this browser's cookie; the 30-day tokens on
    other devices stay valid until they expire. Bumping the token version makes
    the per-request DB recheck refuse all of them from the next call on.
    """
    await user_service.revoke_sessions(user)
    clear_session_cookie(response)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(
    response: Response,
    user: User = Depends(get_current_user),
    auth: AuthService = Depends(get_auth_service),
) -> None:
    """Erase the account (GDPR): the user row, its saved meals, its quota counters,
    its magic-link rows, then the cookie.
    """
    try:
        await auth.erase_account(user)
    except SelfServeDeletionRefused as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    clear_session_cookie(response)
