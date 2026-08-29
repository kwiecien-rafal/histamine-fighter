"""Unit tests for the shared lexical term matcher, with no database.

Covers the two behaviours the composer's recipe scan and the meal-retrieval
exclude both depend on: plural-insensitive tokens, and the directional ``found_in``
(a term must occur in prose) versus the symmetric ``matched`` (two names overlap).
"""

from app.core.term_match import TermMatcher


def test_found_in_matches_a_plural_in_prose_against_a_singular_term() -> None:
    matcher = TermMatcher.from_terms(["tomato", "anchovy"])

    assert matcher.found_in("Add the tomatoes and a few anchovies.") == ["tomato", "anchovy"]


def test_found_in_requires_every_term_token_present() -> None:
    matcher = TermMatcher.from_terms(["red wine"])

    # Only "wine" is present, not the whole term, so "red wine" does not match.
    assert matcher.found_in("Finish with a splash of white wine.") == []
    assert matcher.found_in("Deglaze with red wine.") == ["red wine"]


def test_found_in_is_deduped_and_ordered() -> None:
    matcher = TermMatcher.from_terms(["parmesan", "wine"])

    assert matcher.found_in("Grate parmesan, more parmesan, then wine.") == ["parmesan", "wine"]


def test_matched_is_symmetric_for_two_names() -> None:
    matcher = TermMatcher.from_terms(["wine", "tomato sauce"])

    assert matcher.matched("red wine")  # term {wine} inside the name
    assert matcher.matched("tomato")  # name {tomato} inside the term {tomato, sauce}


def test_matched_does_not_match_a_substring_inside_a_token() -> None:
    matcher = TermMatcher.from_terms(["egg"])

    assert not matcher.matched("eggplant")
