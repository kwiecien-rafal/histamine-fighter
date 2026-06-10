"""Tests for the knowledge seed's document loading (filesystem only, no DB)."""

from pathlib import Path

import pytest

from app.scripts.seed_knowledge import load_documents

_GOOD_DOC = """---
title: DAO
slug: dao
source: SIGHI
topic: basics
---

# DAO

Diamine oxidase breaks down histamine.
"""


def _write(directory: Path, name: str, text: str) -> None:
    (directory / name).write_text(text, encoding="utf-8")


def test_parse_failure_names_the_file(tmp_path: Path) -> None:
    _write(tmp_path, "good.md", _GOOD_DOC)
    _write(tmp_path, "broken.md", "no front matter at all")

    with pytest.raises(ValueError, match=r"broken\.md"):
        load_documents(tmp_path)


def test_invalid_front_matter_names_the_file(tmp_path: Path) -> None:
    _write(tmp_path, "missing-slug.md", "---\ntitle: X\nsource: S\ntopic: t\n---\n\nBody.")

    with pytest.raises(ValueError, match=r"missing-slug\.md"):
        load_documents(tmp_path)


def test_readme_is_skipped(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "directory docs, not a corpus document")
    _write(tmp_path, "good.md", _GOOD_DOC)

    documents = load_documents(tmp_path)

    assert [document.front_matter.slug for document in documents] == ["dao"]
