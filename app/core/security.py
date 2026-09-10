"""Password hashing and session JWTs.

Two security primitives behind the auth gate, kept free of HTTP concerns so the
dependency layer maps their failures to status codes (the same separation the LLM
domain errors follow). Passwords are bcrypt-hashed; the access token is a signed
HS256 JWT whose subject is the user's id.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from app.config import settings

# bcrypt only hashes the first 72 bytes and raises above that, so creation is
# bounded here and at the API/CLI boundary rather than silently truncating.
MAX_PASSWORD_BYTES = 72


class TokenError(Exception):
    """A token was missing, malformed, expired, or missing a required claim."""


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """The verified claims carried by an access token."""

    subject: str
    token_version: int


def hash_password(password: str) -> str:
    """Return a bcrypt hash of the password."""
    encoded = password.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise ValueError(f"password cannot be longer than {MAX_PASSWORD_BYTES} bytes")
    return bcrypt.hashpw(encoded, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Check a password against a stored bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(
    subject: str, *, token_version: int, expires_delta: timedelta | None = None
) -> str:
    """Issue a signed JWT for the given subject."""
    now = datetime.now(UTC)
    ttl = expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    payload: dict[str, Any] = {"sub": subject, "ver": token_version, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, settings.jwt_signing_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> TokenClaims:
    """Return the verified claims of a token, or raise on any problem."""
    try:
        payload = jwt.decode(token, settings.jwt_signing_key, algorithms=[settings.jwt_algorithm])
    except jwt.InvalidTokenError as exc:  # base class: expired, bad signature, malformed
        raise TokenError("Invalid or expired token.") from exc
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        raise TokenError("Token carries no subject.")
    version = payload.get("ver")
    # bool is an int subclass, so exclude it explicitly; a token we issued always
    # carries an int, and a pre-version token carries None.
    if not isinstance(version, int) or isinstance(version, bool):
        raise TokenError("Token carries no version.")
    return TokenClaims(subject=subject, token_version=version)


def create_purpose_token(purpose: str, *, jti: str, ttl: timedelta) -> str:
    """Issue a short-lived signed token for a single non-session purpose."""
    now = datetime.now(UTC)
    payload: dict[str, Any] = {"purpose": purpose, "jti": jti, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, settings.jwt_signing_key, algorithm=settings.jwt_algorithm)


def decode_purpose_token(token: str, *, expected_purpose: str) -> str:
    """Return the ``jti`` of a purpose token, verifying signature, TTL, and purpose."""
    try:
        payload = jwt.decode(token, settings.jwt_signing_key, algorithms=[settings.jwt_algorithm])
    except jwt.InvalidTokenError as exc:
        raise TokenError("Invalid or expired token.") from exc
    if payload.get("purpose") != expected_purpose:
        raise TokenError("Token was issued for another purpose.")
    jti = payload.get("jti")
    if not isinstance(jti, str) or not jti:
        raise TokenError("Token carries no id.")
    return jti
