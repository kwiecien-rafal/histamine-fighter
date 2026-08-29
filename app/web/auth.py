"""The sign-in pages, the emailed-link landing, and the account controls.

The two magic-link steps drive the very handlers the JSON API exposes under
``/api/v1/auth``, so the login policy — Turnstile, the disposable blocklist, the
per-IP caps, the admin refusal, the cookie's lifetime — keeps exactly one
implementation. These routes only turn a form into that call and its failure into
page copy. Erasing an account goes through the API handler for the same reason:
what a deletion purges, and the refusal to self-delete an admin, belong in one
place. Signing out is left inline: it is the shared cookie helper and one service
call, with no policy to share.

OAuth needs no page of its own. ``/api/v1/auth/oauth/{provider}/start`` and its
callback already answer with redirects, so the buttons here are plain links into
them and the callback lands back on ``/login`` or ``/login/complete``.
"""

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from slowapi.errors import RateLimitExceeded
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.cookies import clear_session_cookie
from app.api.v1 import auth as api_auth
from app.config import settings
from app.core.turnstile import TurnstileError
from app.db.session import get_session
from app.dependencies import (
    get_current_user_optional,
    get_email_service,
    get_http_client,
    get_magic_link_service,
    get_quota_service,
    get_user_service,
)
from app.enums import Role
from app.models.user import User
from app.schemas.auth import MAGIC_CODE_LENGTH, MAX_EMAIL_CHARS, MagicLinkRequest, MagicLinkVerify
from app.services.email_service import EmailDeliveryError, EmailService
from app.services.magic_link_service import MagicLinkService
from app.services.oauth_service import PROVIDERS, credentials
from app.services.quota_service import QuotaExceededError, QuotaService
from app.services.user_service import UserService
from app.web.deps import require_user, templates

router = APIRouter()

# Copy for the coarse error flags the OAuth callback redirects back with.
OAUTH_ERRORS = {
    "oauth": "That sign-in didn't complete. Try again, or use your email instead.",
    "signup_limit": "Too many new accounts from this network today. Try again tomorrow.",
}

# Everything the two magic-link handlers raise. HTTPException is their own refusal;
# the rest are domain errors the API boundary would otherwise map to a status code.
_AUTH_FAILURES = (
    HTTPException,
    RateLimitExceeded,
    TurnstileError,
    QuotaExceededError,
    EmailDeliveryError,
)


def _failure_message(exc: BaseException) -> str:
    """Page copy for a failed sign-in attempt.

    An HTTPException's detail is already written for the person reading it (the
    uniform "invalid or expired" line, the disposable-domain refusal); the domain
    errors carry operator wording, so each gets its own sentence.
    """
    if isinstance(exc, HTTPException):
        return str(exc.detail)
    if isinstance(exc, RateLimitExceeded):
        return "Too many attempts from this network. Wait a minute, then try again."
    if isinstance(exc, QuotaExceededError):
        return "Too many new accounts from this network today. Try again tomorrow."
    if isinstance(exc, TurnstileError):
        return "The bot check didn't pass. Reload the page and try again."
    return "We couldn't send that email just now. Try again in a moment."


def _configured_oauth_providers() -> list[str]:
    """The providers this deployment can actually start a round trip with.

    A self-hoster who registered no OAuth apps gets a clean email-only page rather
    than buttons that answer 501.
    """
    return [name for name, provider in PROVIDERS.items() if credentials(provider) is not None]


def _login_form(request: Request, *, email: str = "", error: str | None = None) -> HTMLResponse:
    """The email form, keeping what was typed when something went wrong."""
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "email": email,
            "error": error,
            "max_email_chars": MAX_EMAIL_CHARS,
            "oauth_providers": _configured_oauth_providers(),
            "turnstile_site_key": settings.turnstile_site_key,
        },
    )


def _code_form(request: Request, *, email: str, error: str | None = None) -> HTMLResponse:
    """The 6-digit code form shown once the email is on its way."""
    return templates.TemplateResponse(
        request,
        "login_code.html",
        {"email": email, "error": error, "code_length": MAGIC_CODE_LENGTH},
    )


def _expired_link(request: Request, *, error: str | None = None) -> HTMLResponse:
    """The landing page with no usable token left: explain, and offer a fresh one."""
    return templates.TemplateResponse(
        request,
        "login_verify.html",
        {"token": "", "error": error or "That sign-in link is missing its token."},
    )


@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request,
    error: str = Query(default="", description="Coarse flag set by the OAuth callback."),
    user: User | None = Depends(get_current_user_optional),
) -> Response:
    """The sign-in page: an email address, or one of the configured providers.

    Someone already signed in is sent to their account rather than shown a form
    they do not need.
    """
    if user is not None:
        return RedirectResponse("/account", status_code=status.HTTP_303_SEE_OTHER)
    return _login_form(request, error=OAUTH_ERRORS.get(error))


@router.post("/login", response_class=HTMLResponse)
async def request_link(
    request: Request,
    email: str = Form(),
    turnstile_token: str | None = Form(default=None, alias="cf-turnstile-response"),
    magic_links: MagicLinkService = Depends(get_magic_link_service),
    email_service: EmailService = Depends(get_email_service),
    http_client: httpx.AsyncClient = Depends(get_http_client),
    quota: QuotaService = Depends(get_quota_service),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Send the sign-in email, then show the code form.

    The code form is rendered straight from the POST rather than redirected to, so
    the address stays out of the URL and the browser history.
    """
    email = email.strip()
    try:
        payload = MagicLinkRequest(email=email, turnstile_token=turnstile_token)
    except ValidationError:
        return _login_form(request, email=email, error="That doesn't look like an email address.")
    try:
        await api_auth.request_magic_link(
            request=request,
            payload=payload,
            magic_links=magic_links,
            email_service=email_service,
            http_client=http_client,
            quota=quota,
            session=session,
        )
    except _AUTH_FAILURES as exc:
        return _login_form(request, email=email, error=_failure_message(exc))
    return _code_form(request, email=payload.email)


@router.post("/login/code")
async def submit_code(
    request: Request,
    email: str = Form(),
    code: str = Form(),
    magic_links: MagicLinkService = Depends(get_magic_link_service),
    user_service: UserService = Depends(get_user_service),
    quota: QuotaService = Depends(get_quota_service),
) -> Response:
    """Redeem the 6-digit code from the email and open the session."""
    email, code = email.strip(), code.strip()
    try:
        payload = MagicLinkVerify(email=email, code=code)
    except ValidationError:
        return _code_form(
            request, email=email, error=f"Enter the {MAGIC_CODE_LENGTH}-digit code from the email."
        )
    signed_in = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    try:
        await api_auth.verify_magic_link(
            request=request,
            response=signed_in,
            payload=payload,
            magic_links=magic_links,
            user_service=user_service,
            quota=quota,
        )
    except _AUTH_FAILURES as exc:
        return _code_form(request, email=email, error=_failure_message(exc))
    return signed_in


@router.get("/login/verify", response_class=HTMLResponse)
async def verify_page(
    request: Request, token: str = Query(default="", description="The emailed link's token.")
) -> HTMLResponse:
    """Where the emailed sign-in link lands: a button that posts its token back.

    Deliberately not a GET that redeems the token. Mail scanners and link
    previewers follow GET links, and a single-use token must survive that — only
    the POST behind the button spends it.
    """
    if not token:
        return _expired_link(request)
    return templates.TemplateResponse(request, "login_verify.html", {"token": token})


@router.post("/login/verify")
async def confirm_link(
    request: Request,
    token: str = Form(),
    magic_links: MagicLinkService = Depends(get_magic_link_service),
    user_service: UserService = Depends(get_user_service),
    quota: QuotaService = Depends(get_quota_service),
) -> Response:
    """Redeem the emailed link's token and open the session."""
    try:
        payload = MagicLinkVerify(token=token)
    except ValidationError:
        return _expired_link(request)
    signed_in = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    try:
        await api_auth.verify_magic_link(
            request=request,
            response=signed_in,
            payload=payload,
            magic_links=magic_links,
            user_service=user_service,
            quota=quota,
        )
    except _AUTH_FAILURES as exc:
        return _expired_link(request, error=_failure_message(exc))
    return signed_in


@router.get("/login/complete")
async def login_complete() -> RedirectResponse:
    """Where the OAuth callback lands once it has planted the session cookie.

    The cookie is already set, so there is nothing left to do but hand the visitor
    back to the site signed in.
    """
    return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/account", response_class=HTMLResponse)
async def account_page(
    request: Request,
    user: User = Depends(require_user),
    quota: QuotaService = Depends(get_quota_service),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """The account: the signed-in address, today's shared-tier allowance, and the
    two ways out of the session."""
    response = templates.TemplateResponse(
        request, "account.html", {"quota": await quota.read_status(user.id, session)}
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/account/delete", response_class=HTMLResponse)
async def confirm_account_deletion(
    request: Request, user: User = Depends(require_user)
) -> HTMLResponse:
    """Spell out what erasing the account removes, before anything is erased.

    A page of its own rather than a dialog: without it the only thing between a
    stray click and an irreversible delete would be a script.
    """
    return templates.TemplateResponse(
        request, "account_delete.html", {"is_admin": user.role is not Role.USER}
    )


@router.post("/account/delete")
async def delete_account(
    user: User = Depends(require_user),
    user_service: UserService = Depends(get_user_service),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Erase the account and everything attached to it, then sign out."""
    erased = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    await api_auth.delete_me(response=erased, user=user, user_service=user_service, session=session)
    return erased


@router.post("/logout")
async def logout() -> RedirectResponse:
    """Sign out of this browser. Idempotent, so it is safe without a session."""
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response)
    return response


@router.post("/logout/all")
async def logout_everywhere(
    user: User = Depends(require_user),
    user_service: UserService = Depends(get_user_service),
) -> RedirectResponse:
    """Sign out of every browser by revoking the account's outstanding sessions.

    A plain sign-out only drops this browser's cookie; the month-long tokens on
    other devices stay valid until they expire.
    """
    await user_service.revoke_sessions(user)
    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    clear_session_cookie(response)
    return response
