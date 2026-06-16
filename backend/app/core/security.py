"""Password hashing and admin JWTs.

Two security primitives behind the admin gate, kept free of HTTP concerns so the
dependency layer maps their failures to status codes (the same separation the LLM
domain errors follow). Passwords are bcrypt-hashed; the access token is a signed
HS256 JWT whose subject is the admin's email.
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.config import settings

# bcrypt only hashes the first 72 bytes and raises above that, so creation is
# bounded here and at the API/CLI boundary rather than silently truncating.
MAX_PASSWORD_BYTES = 72


class TokenError(Exception):
    """A token was missing, malformed, expired, or carried no subject."""


def hash_password(password: str) -> str:
    """Return a bcrypt hash of the password.

    Raises:
        ValueError: the password exceeds bcrypt's 72-byte limit.
    """
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise ValueError(f"password cannot be longer than {MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Check a password against a stored bcrypt hash.

    An over-length password or a malformed stored hash is a non-match, never an
    exception, so a bad input is rejected as wrong rather than surfacing as a 500.
    """
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(subject: str, *, expires_delta: timedelta | None = None) -> str:
    """Issue a signed JWT for the subject (the admin's email).

    ``expires_delta`` overrides the configured TTL; tests use it to mint an
    already-expired token.
    """
    now = datetime.now(UTC)
    ttl = expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict[str, Any] = {"sub": subject, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> str:
    """Return the verified subject of a token, or raise on any problem.

    Raises:
        TokenError: the token is expired, tampered with, or has no subject.
    """
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.InvalidTokenError as exc:  # base class: expired, bad signature, malformed
        raise TokenError("Invalid or expired token.") from exc
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise TokenError("Token carries no subject.")
    return subject
