"""Lookup-key normalization.

The seed loader derives each row's lookup key from these functions, and the
lookup services normalize incoming queries the same way. Sharing one
implementation keeps the stored key and the query key in step.
"""

import re

_WHITESPACE = re.compile(r"\s+")
_TRAILING_PUNCTUATION = re.compile(r"[?!.\s]+$")


def normalize_ingredient_name(name: str) -> str:
    """Return a stable lookup key: lowercased, trimmed, single-spaced."""
    return _WHITESPACE.sub(" ", name.strip().lower())


def normalize_question(question: str) -> str:
    """Return a cache key for a free-text question."""
    return _TRAILING_PUNCTUATION.sub("", _WHITESPACE.sub(" ", question.strip().lower()))
