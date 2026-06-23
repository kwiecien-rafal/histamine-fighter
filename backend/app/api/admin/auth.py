"""Admin session: log in, log out, and read the current user.

The access token rides in an httpOnly cookie set on login, never in the response
body, so JavaScript cannot read it and XSS cannot exfiltrate it. The SPA cannot
read the cookie either, so ``/me`` is how it bootstraps session state on load. When
real public users land, this router moves to a neutral ``/api/v1/auth`` prefix while
``require_admin`` stays the gate. Deferred to avoid churn now.
"""

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.config import settings
from app.core.ratelimit import auth_rate_limit, limiter
from app.core.security import create_access_token
from app.dependencies import get_current_user, get_user_service
from app.models.user import User
from app.schemas.admin import AdminLoginRequest, AuthUser
from app.services.user_service import UserService

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/auth", tags=["admin"])


def _set_session_cookie(response: Response, token: str) -> None:
    """Plant the session token in an httpOnly cookie.

    httpOnly keeps it unreadable from JavaScript. Secure is on in production and off
    in dev so it still sets over http on localhost. SameSite=Lax blunts CSRF on
    cross-site requests.
    """
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
        max_age=settings.session_cookie_max_age,
    )


def _clear_session_cookie(response: Response) -> None:
    """Expire the session cookie, mirroring the attributes it was set with.

    Browsers match a deletion on name, path, and domain, so the path and the
    Secure/SameSite flags are repeated here to keep the overwrite reliable.
    """
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        secure=settings.cookie_secure,
        samesite="lax",
    )


def _invalid_credentials() -> HTTPException:
    """The single 401 for any failed login.

    Identical for a wrong password, an unknown email, and a disabled account, so the
    response never reveals which of those it was.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect email or password.",
    )


@router.post("/login")
@limiter.limit(auth_rate_limit)
async def login(
    request: Request,
    response: Response,
    payload: AdminLoginRequest,
    user_service: UserService = Depends(get_user_service),
) -> AuthUser:
    """Verify credentials and open a session by setting the httpOnly cookie.

    A wrong email and a wrong password give the same 401, so the response never
    reveals which accounts exist. The token rides in the cookie, never the body.
    """
    client = request.client.host if request.client else None
    user = await user_service.authenticate(payload.email, payload.password)
    if user is None:
        # Log the attempted email and source IP so brute force is visible. The
        # password is never logged.
        log.warning("admin.login.failed", email=payload.email, client=client)
        raise _invalid_credentials()
    if not user.is_active:
        # Correct credentials on a disabled account. Logged on its own event for the
        # operator, but answered with the same 401 so a session never opens and the
        # response cannot confirm the account exists.
        log.warning("admin.login.inactive", email=user.email, client=client)
        raise _invalid_credentials()
    log.info("admin.login.success", email=user.email, client=client)
    token = create_access_token(str(user.id), token_version=user.token_version)
    _set_session_cookie(response, token)
    # The login response opens the session, so keep it out of any shared cache.
    response.headers["Cache-Control"] = "no-store"
    return AuthUser.model_validate(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    """Delete the session cookie. Idempotent, so it is safe without a session.

    This clears the browser's copy of the token. It does not revoke the token
    server-side, so one captured before logout stays valid until it expires. A
    password reset, which bumps token_version, is the revoke-all.
    """
    _clear_session_cookie(response)


@router.get("/me")
async def me(response: Response, user: User = Depends(get_current_user)) -> AuthUser:
    """Return the signed-in user, or 401.

    The SPA calls this on load to recover session state, since it cannot read the
    httpOnly cookie itself. The response carries the user's identity, so it stays out
    of any shared cache.
    """
    response.headers["Cache-Control"] = "no-store"
    return AuthUser.model_validate(user)
