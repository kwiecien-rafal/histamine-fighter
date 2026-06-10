"""Tests for knowledge document parsing and chunking (pure, no DB)."""

import pytest

from app.knowledge.chunking import chunk_body, parse_document

_DOC = """---
title: Test Doc
slug: test-doc
source: Test Source
topic: basics
---

## First

Para one.

Para two.

## Second

Another paragraph here.
"""


def test_parse_document_reads_front_matter_and_chunks() -> None:
    parsed = parse_document(_DOC)

    assert parsed.front_matter.title == "Test Doc"
    assert parsed.front_matter.slug == "test-doc"
    assert parsed.front_matter.topic == "basics"
    assert parsed.chunks[0].chunk_index == 0
    # The section heading is carried into its chunk for retrieval context.
    assert parsed.chunks[0].content.startswith("First")


def test_missing_front_matter_raises() -> None:
    with pytest.raises(ValueError):
        parse_document("no front matter here")


def test_unknown_front_matter_field_raises() -> None:
    bad = "---\ntitle: t\nslug: s\nsource: x\ntopic: y\nextra: nope\n---\n\nbody"
    with pytest.raises(ValueError):
        parse_document(bad)


def test_chunking_respects_size_budget() -> None:
    body = "## H\n\n" + "\n\n".join(
        f"Paragraph number {i} with some filler words to take up room."
        for i in range(40)
    )
    chunks = chunk_body(body, max_chars=200, overlap=40)

    assert len(chunks) > 1
    # max_chars is a hard bound on the full stored chunk, heading prefix included.
    assert all(len(chunk) <= 200 for chunk in chunks)


def test_each_chunk_keeps_its_heading() -> None:
    body = "## Symptoms\n\nFlushing and headaches.\n\n## Causes\n\nLow DAO activity."
    chunks = chunk_body(body, max_chars=500, overlap=50)

    assert any(chunk.startswith("Symptoms") for chunk in chunks)
    assert any(chunk.startswith("Causes") for chunk in chunks)


def test_oversized_paragraph_is_split_on_sentences_within_budget() -> None:
    body = "## H\n\n" + " ".join(
        f"Sentence number {i} adds a little more prose to the single paragraph."
        for i in range(30)
    )
    chunks = chunk_body(body, max_chars=200, overlap=40)

    assert len(chunks) > 1
    assert all(len(chunk) <= 200 for chunk in chunks)
    assert any("Sentence number 29" in chunk for chunk in chunks)


def test_oversized_sentence_is_split_on_words() -> None:
    body = "## H\n\n" + " ".join(["histamine"] * 60)
    chunks = chunk_body(body, max_chars=200, overlap=40)

    assert len(chunks) > 1
    assert all(len(chunk) <= 200 for chunk in chunks)


def test_giant_token_is_hard_sliced() -> None:
    body = "## H\n\n" + "x" * 600
    chunks = chunk_body(body, max_chars=200, overlap=40)

    assert all(len(chunk) <= 200 for chunk in chunks)


def test_oversized_table_splits_on_lines() -> None:
    rows = "\n".join(f"| food {i} | high in histamine |" for i in range(30))
    body = f"## Foods\n\n| food | level |\n|------|-------|\n{rows}"
    chunks = chunk_body(body, max_chars=200, overlap=40)

    assert all(len(chunk) <= 200 for chunk in chunks)
    # Rows survive intact — line splitting, not mid-row slicing.
    assert any("| food 29 | high in histamine |" in chunk for chunk in chunks)


def test_hash_inside_code_fence_is_not_a_heading() -> None:
    body = "## Setup\n\nIntro text.\n\n```\n# not a heading\n\nvalue = 1\n```\n\nAfter the fence."
    chunks = chunk_body(body, max_chars=500, overlap=50)

    assert all(chunk.startswith("Setup") for chunk in chunks)
    # The fence stays one block despite the blank line inside it.
    assert any("# not a heading\n\nvalue = 1" in chunk for chunk in chunks)


def test_hashtag_without_space_is_not_a_heading() -> None:
    chunks = chunk_body(
        "## Tags\n\n#histamine is a hashtag.", max_chars=500, overlap=50
    )

    assert chunks == ["Tags\n\n#histamine is a hashtag."]


def test_nested_headings_carry_parent_breadcrumb() -> None:
    body = (
        "## Diagnosis\n\nIntro.\n\n"
        "### Symptoms\n\nFlushing and headaches.\n\n"
        "## Treatment\n\nLow-histamine diet."
    )
    chunks = chunk_body(body, max_chars=500, overlap=50)

    assert any(chunk.startswith("Diagnosis > Symptoms\n\n") for chunk in chunks)
    # A sibling section resets the trail instead of stacking onto it.
    assert any(chunk.startswith("Treatment\n\n") for chunk in chunks)


def test_heading_longer_than_budget_raises() -> None:
    with pytest.raises(ValueError):
        chunk_body(f"## {'H' * 200}\n\nBody.", max_chars=200, overlap=40)


def test_zero_overlap_is_rejected() -> None:
    # text[-0:] is the whole string, so overlap=0 must be rejected, not mis-sliced.
    with pytest.raises(ValueError):
        chunk_body("## H\n\nBody.", max_chars=200, overlap=0)


def test_overlap_tail_starts_on_a_word_boundary() -> None:
    # Uniform 6-char words mean a raw 30-char tail slice always lands mid-word;
    # only boundary trimming keeps every token in the chunks a whole word.
    words = [f"word{i:02d}" for i in range(60)]
    body = "## H\n\n" + " ".join(words)
    chunks = chunk_body(body, max_chars=120, overlap=30)

    assert len(chunks) > 1
    vocabulary = set(words) | {"H"}
    assert all(token in vocabulary for chunk in chunks for token in chunk.split())
