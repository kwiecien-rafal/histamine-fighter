"""Lexical term matching by token-set containment.

One implementation of a fiddly rule used in two places: dropping a retrieved meal
that uses an avoided term (:class:`~app.services.meal_service.MealService`) and
scanning a recipe step for an index-flagged ingredient (the composer's safety
gate). A term matches a text when their token sets are subset-related either way,
so "wine" matches "red wine" and "tomato sauce" matches "fresh tomato". Tokens are
word runs, so punctuation in prose ("deglaze with red wine.") does not hide a
match. Purely lexical, never semantic.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.core.normalization import normalize_ingredient_name

_WORD = re.compile(r"\w+")


@dataclass(frozen=True, slots=True)
class TermMatcher:
    """Matches free text against a set of terms by token-set containment."""

    terms: tuple[tuple[str, frozenset[str]], ...]

    @classmethod
    def from_terms(cls, terms: Iterable[str]) -> "TermMatcher":
        prepared = []
        for term in terms:
            key = normalize_ingredient_name(term)
            tokens = _tokenize(term)
            if key and tokens:
                prepared.append((key, tokens))
        return cls(tuple(prepared))

    def matched(self, text: str) -> bool:
        tokens = _tokenize(text)
        return bool(tokens) and any(term <= tokens or tokens <= term for _, term in self.terms)

    def found_in(self, text: str) -> list[str]:
        """The term keys that match the text, in definition order, deduped."""
        tokens = _tokenize(text)
        if not tokens:
            return []
        seen: set[str] = set()
        hits: list[str] = []
        for key, term in self.terms:
            if (term <= tokens or tokens <= term) and key not in seen:
                seen.add(key)
                hits.append(key)
        return hits


def _tokenize(text: str) -> frozenset[str]:
    return frozenset(_WORD.findall(normalize_ingredient_name(text)))
