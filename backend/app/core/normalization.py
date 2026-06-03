"""Ingredient-name normalization.

The seed loader derives each row's lookup key from this function, and the
lookup service normalizes incoming queries the same way. Sharing one
implementation keeps the stored key and the query key in step.
"""

import re

_WHITESPACE = re.compile(r"\s+")


def normalize_ingredient_name(name: str) -> str:
    """Return a stable lookup key: lowercased, trimmed, single-spaced.

    Args:
        name: The display name as written, e.g. "  Aged   Parmesan ".

    Returns:
        The normalized key, e.g. "aged parmesan".
    """
    return _WHITESPACE.sub(" ", name.strip().lower())
