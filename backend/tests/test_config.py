"""Tests for the admin-secret fail-closed rules in Settings (no database).

Each case constructs Settings directly with ``_env_file=None`` and clears the
relevant env vars, so the result never depends on the developer's .env or shell.
"""

import pytest
from pydantic import ValidationError

from app.config import DEV_SECRET_KEY, Settings

_STRONG_SECRET = "x" * 48


@pytest.fixture(autouse=True)
def _clear_secret_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("SECRET_KEY", "DEBUG", "PUBLIC_DEPLOYMENT"):
        monkeypatch.delenv(var, raising=False)


def test_defaults_use_the_placeholder_in_dev() -> None:
    settings = Settings(_env_file=None)

    assert settings.debug is True
    assert settings.secret_key == DEV_SECRET_KEY


def test_blank_secret_falls_back_to_the_placeholder() -> None:
    assert Settings(_env_file=None, secret_key="   ").secret_key == DEV_SECRET_KEY


def test_public_deployment_rejects_the_dev_secret() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, public_deployment=True)


def test_debug_off_rejects_the_dev_secret() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, debug=False)


def test_production_rejects_a_short_secret() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, public_deployment=True, secret_key="too-short")


def test_production_rejects_a_blank_secret() -> None:
    # Blank coerces to the placeholder, which is then rejected in production.
    with pytest.raises(ValidationError):
        Settings(_env_file=None, debug=False, secret_key="")


def test_strong_secret_boots_in_production() -> None:
    assert Settings(_env_file=None, public_deployment=True, secret_key=_STRONG_SECRET)
    assert Settings(_env_file=None, debug=False, secret_key=_STRONG_SECRET)


def test_cookie_is_secure_only_on_a_public_deployment() -> None:
    # Secure tracks TLS, which only PUBLIC_DEPLOYMENT implies. DEBUG off alone must
    # not force it: that would drop the cookie when running with DEBUG off over http.
    assert Settings(_env_file=None).cookie_secure is False
    assert Settings(_env_file=None, public_deployment=True, secret_key=_STRONG_SECRET).cookie_secure
    assert Settings(_env_file=None, debug=False, secret_key=_STRONG_SECRET).cookie_secure is False


def test_session_cookie_max_age_tracks_the_token_lifetime() -> None:
    settings = Settings(_env_file=None, access_token_expire_minutes=30)
    assert settings.session_cookie_max_age == 30 * 60
