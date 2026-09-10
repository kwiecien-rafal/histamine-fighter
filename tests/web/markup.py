"""Whitespace normalization for assertions that read the rendered markup.

djLint reflows the templates, and an attribute long enough to cross its
``max_attribute_length`` is wrapped onto its own line. Whitespace between
attributes is insignificant per the HTML syntax rules, so an assertion that
names two of them in a row is describing the formatter, not the page. Collapsing
runs of whitespace first keeps those assertions about the markup itself.
"""

import re

WHITESPACE_RUN = re.compile(r"\s+")


def markup(page: str) -> str:
    """A rendered page with its whitespace runs collapsed to single spaces."""
    return WHITESPACE_RUN.sub(" ", page)
