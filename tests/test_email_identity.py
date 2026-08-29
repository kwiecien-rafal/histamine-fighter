"""normalize_email (account identity) and mask_email (log redaction)."""

import pytest

from app.core.logging import mask_email
from app.models.user import normalize_email


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Gerald@Example.COM ", "gerald@example.com"),
        ("gerald+news@example.com", "gerald@example.com"),
        ("gerald+a+b@example.com", "gerald@example.com"),
        # Dots are meaningful on most providers; only the plus-tag collapses.
        ("first.last@example.com", "first.last@example.com"),
        # A local part that is nothing but a tag stays whole rather than emptying.
        ("+tag@example.com", "+tag@example.com"),
        ("no-at-sign", "no-at-sign"),
    ],
)
def test_normalize_email(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected


def test_mask_email_keeps_one_leading_character_and_the_domain() -> None:
    assert mask_email("gerald@example.com") == "g***@example.com"


def test_mask_email_survives_degenerate_input() -> None:
    assert mask_email("a@b") == "a***@b"
    assert mask_email("@example.com") == "***@example.com"
    assert mask_email("garbage") == "***"
