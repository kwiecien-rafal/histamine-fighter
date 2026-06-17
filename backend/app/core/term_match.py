"""Lexical term matching by token-set containment.

One implementation of a fiddly rule used in two places: dropping a retrieved meal
that uses an avoided term (:class:`~app.services.meal_service.MealService`) and
scanning a recipe step for an index-flagged ingredient (the composer's safety
gate). Tokens are word runs, naively singularized, so plural prose ("add the
tomatoes") still matches a singular term ("tomato") and punctuation ("deglaze with
red wine.") does not hide a match. Purely lexical, never semantic.

Two directions for two jobs: :meth:`matched` is symmetric (a term and a name match
when either's tokens contain the other's, so "wine" matches "red wine" and
"tomato sauce" matches "fresh tomato"), used to compare two ingredient names.
:meth:`found_in` is one-directional (the term's tokens must all appear in the
text), the right test for "does this risky term occur in this recipe prose".
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
        """The terms whose tokens all appear in the text, in order, deduped.

        One-directional on purpose: prose is matched by whether a term occurs in
        it, not by the symmetric containment :meth:`matched` uses for two names.
        """
        tokens = _tokenize(text)
        if not tokens:
            return []
        seen: set[str] = set()
        hits: list[str] = []
        for key, term in self.terms:
            if term <= tokens and key not in seen:
                seen.add(key)
                hits.append(key)
        return hits


def _tokenize(text: str) -> frozenset[str]:
    return frozenset(_singularize(word) for word in _WORD.findall(normalize_ingredient_name(text)))


def _singularize(word: str) -> str:
    """Strip a common English plural so "tomatoes" and "tomato" share a token.

    Conservative and applied to both sides of every comparison, so it never needs
    to be correct English, only consistent: "molasses" folding to "molass" matches
    nothing and harms nothing.
    """
    if len(word) <= 3:
        return word
    if word.endswith("ies"):
        return f"{word[:-3]}y"
    if word.endswith(("ches", "shes", "sses", "xes", "zes", "oes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word
