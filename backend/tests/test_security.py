"""Unit tests for password hashing and admin JWTs (no database)."""

from datetime import timedelta

import jwt
import pytest

from app.config import settings
from app.core.security import (
    MAX_PASSWORD_BYTES,
    TokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)

# The token subject is the user's id (a UUID string), not their email.
_SUBJECT = "11111111-1111-1111-1111-111111111111"


def test_hash_then_verify_round_trips() -> None:
    digest = hash_password("correct horse battery")
    assert digest != "correct horse battery"
    assert verify_password("correct horse battery", digest) is True
    assert verify_password("wrong password", digest) is False


def test_hash_is_salted_so_two_hashes_differ() -> None:
    assert hash_password("same-password") != hash_password("same-password")


def test_verify_password_rejects_a_malformed_hash_without_raising() -> None:
    assert verify_password("anything", "not-a-bcrypt-hash") is False


def test_hash_password_refuses_over_72_bytes() -> None:
    with pytest.raises(ValueError, match="72 bytes"):
        hash_password("x" * (MAX_PASSWORD_BYTES + 1))


def test_verify_password_treats_an_over_long_password_as_a_non_match() -> None:
    digest = hash_password("short-enough")
    assert verify_password("x" * (MAX_PASSWORD_BYTES + 1), digest) is False


def test_token_round_trips_the_subject_and_version() -> None:
    token = create_access_token(_SUBJECT, token_version=3)
    claims = decode_access_token(token)
    assert claims.subject == _SUBJECT
    assert claims.token_version == 3


def test_expired_token_is_rejected() -> None:
    token = create_access_token(_SUBJECT, token_version=1, expires_delta=timedelta(minutes=-1))
    with pytest.raises(TokenError):
        decode_access_token(token)


def test_tampered_token_is_rejected() -> None:
    token = create_access_token(_SUBJECT, token_version=1)
    with pytest.raises(TokenError):
        decode_access_token(token + "tamper")


def test_token_without_a_subject_is_rejected() -> None:
    # A correctly signed token that simply carries no subject must still be refused.
    token = jwt.encode({"ver": 1}, settings.secret_key, algorithm=settings.jwt_algorithm)
    with pytest.raises(TokenError):
        decode_access_token(token)


def test_token_without_a_version_is_rejected() -> None:
    # A pre-version token (signed before the claim existed) must not slip through.
    token = jwt.encode({"sub": _SUBJECT}, settings.secret_key, algorithm=settings.jwt_algorithm)
    with pytest.raises(TokenError):
        decode_access_token(token)
