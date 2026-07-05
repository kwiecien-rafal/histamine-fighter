"""Session cookie helpers shared by admin and public auth routes.

One place owns the cookie attributes so the admin login, the public logins
(magic link, OAuth), and every logout stay in agreement on name, path, and
flags. Only the lifetime differs per caller: admins get the short TTL, public
users the long one.
"""

from datetime import timedelta

from fastapi import Response

from app.config import settings
from app.core.security import create_access_token
from app.enums import Role
from app.models.user import User


def mint_session(response: Response, user: User) -> None:
    """Issue a public user's month-long session: mint the JWT, set the cookie.

    Public sessions only. The login routes refuse admin accounts before reaching
    here, so this guard should be unreachable — it exists so a future call site
    can never quietly hand a month-long cookie to an admin, whose password login
    at /admin/auth mints the short TTL instead.
    """
    if user.role is Role.ADMIN:
        raise ValueError("Admins do not get public sessions; use the admin password login.")
    ttl = timedelta(days=settings.user_session_expire_days)
    token = create_access_token(str(user.id), token_version=user.token_version, expires_delta=ttl)
    set_session_cookie(response, token, max_age=settings.user_session_cookie_max_age)


def set_session_cookie(response: Response, token: str, *, max_age: int) -> None:
    """Plant the session token in an httpOnly cookie.

    httpOnly keeps it unreadable from JavaScript. Secure is on in production and off
    in dev so it still sets over http on localhost. SameSite=Lax blunts CSRF on
    cross-site requests. ``max_age`` must match the TTL of the JWT it carries so the
    cookie and the token expire together.
    """
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
        max_age=max_age,
    )


def clear_session_cookie(response: Response) -> None:
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
