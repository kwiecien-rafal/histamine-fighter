"""Parse and chunk the curated knowledge markdown for embedding.

Pure functions, no database or model loading, so chunking is unit-testable on its
own. A document is YAML-style front matter (``title``, ``slug``, ``source``,
``topic``) followed by markdown. Chunks are heading-aware: each chunk is prefixed
with its heading breadcrumb (``Diagnosis > Symptoms``) so the section's words —
including its parents' — embed alongside the passage and a retrieved chunk reads
in context. ``max_chars`` is a hard bound: a block too big for the budget is split
at the most natural boundary available (lines, then sentences, then words), so no
chunk can silently exceed the embedding model's token window.
"""

import re
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field

# Target chunk size in characters. bge-style models cap around 512 tokens; ~900
# characters (~225 tokens) leaves comfortable headroom. Overlap carries a little
# context across a boundary so a sentence split between chunks is still findable.
DEFAULT_MAX_CHARS = 900
DEFAULT_OVERLAP_CHARS = 150

_FRONT_MATTER = re.compile(r"^---\n(?P<meta>.*?)\n---\n?(?P<body>.*)$", re.DOTALL)
# ATX heading: 1-6 '#' then whitespace then text — '#hashtag' is not a heading.
_HEADING = re.compile(r"^(?P<level>#{1,6})\s+(?P<text>\S.*)$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class KnowledgeFrontMatter(BaseModel):
    """Validated front matter of a knowledge document."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    slug: str = Field(min_length=1)
    source: str = Field(min_length=1)
    topic: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class KnowledgeChunkData:
    """One chunk's ordinal and text, before embedding."""

    chunk_index: int
    content: str


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    front_matter: KnowledgeFrontMatter
    chunks: list[KnowledgeChunkData]


def parse_document(text: str) -> ParsedDocument:
    """Parse a knowledge markdown file into validated front matter and chunks."""
    match = _FRONT_MATTER.match(text)
    if match is None:
        raise ValueError("document is missing the '--- front matter ---' block")
    front_matter = KnowledgeFrontMatter(**_parse_front_matter(match.group("meta")))
    pieces = chunk_body(match.group("body").strip())
    chunks = [KnowledgeChunkData(index, content) for index, content in enumerate(pieces)]
    return ParsedDocument(front_matter=front_matter, chunks=chunks)


def chunk_body(
    body: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP_CHARS,
) -> list[str]:
    """Split a markdown body into heading-prefixed chunks of at most ``max_chars``."""
    if overlap < 1:
        # overlap=0 would make the tail slice (text[-0:]) the whole buffer, not nothing,
        # so every chunk after the first would carry its entire predecessor.
        raise ValueError(f"overlap must be at least 1, got {overlap}")
    chunks: list[str] = []
    for heading, section in _split_into_sections(body):
        prefix = f"{heading}\n\n" if heading else ""
        # The bound covers the whole stored chunk, so the heading prefix and the
        # carried overlap tail both come out of the section's budget.
        section_max = max_chars - len(prefix)
        if section_max - overlap - 2 < 1:
            raise ValueError(
                f"heading {heading!r} leaves no content budget "
                f"(max_chars={max_chars}, overlap={overlap})"
            )
        for piece in _pack_paragraphs(_split_paragraphs(section), section_max, overlap):
            chunks.append(f"{prefix}{piece}")
    return chunks


def _parse_front_matter(raw: str) -> dict[str, str]:
    """Parse simple ``key: value`` lines. A handful of string fields, no nesting."""
    data: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        key, separator, value = stripped.partition(":")
        if not separator:
            raise ValueError(f"front matter line is not 'key: value': {line!r}")
        data[key.strip()] = value.strip()
    return data


def _split_into_sections(body: str) -> list[tuple[str, str]]:
    """Split on ATX headings into (breadcrumb, section-body) pairs.

    The breadcrumb joins the active heading at each level (``Diagnosis > Symptoms``)
    so a chunk under a subsection keeps its parents' context. Fenced code blocks
    are opaque: a ``#`` line inside one is content, not a heading.
    """
    sections: list[tuple[str, str]] = []
    heading_stack: list[tuple[int, str]] = []
    lines: list[str] = []
    in_fence = False

    def flush() -> None:
        breadcrumb = " > ".join(text for _, text in heading_stack)
        sections.append((breadcrumb, "\n".join(lines).strip()))

    for line in body.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
            lines.append(line)
            continue
        heading = None if in_fence else _HEADING.match(line)
        if heading is None:
            lines.append(line)
            continue
        if lines or heading_stack:
            flush()
        lines = []
        level = len(heading.group("level"))
        while heading_stack and heading_stack[-1][0] >= level:
            heading_stack.pop()
        heading_stack.append((level, heading.group("text").strip()))
    if lines or heading_stack:
        flush()
    return sections


def _split_paragraphs(section: str) -> list[str]:
    """Split on blank lines, keeping a fenced code block as a single block."""
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in section.splitlines():
        if _FENCE.match(line):
            in_fence = not in_fence
        elif not line.strip() and not in_fence:
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    return blocks


def _pack_paragraphs(paragraphs: list[str], max_chars: int, overlap: int) -> list[str]:
    """Greedily pack paragraphs up to max_chars, carrying an overlap tail forward."""
    # Any single piece must leave room for a carried tail, or a post-flush buffer
    # (tail + piece) could exceed the bound.
    piece_budget = max_chars - overlap - 2
    pieces = [
        piece for paragraph in paragraphs for piece in _split_oversized(paragraph, piece_budget)
    ]
    chunks: list[str] = []
    buffer = ""
    for piece in pieces:
        candidate = f"{buffer}\n\n{piece}" if buffer else piece
        if not buffer or len(candidate) <= max_chars:
            buffer = candidate
        else:
            chunks.append(buffer)
            tail = _overlap_tail(buffer, overlap)
            buffer = f"{tail}\n\n{piece}" if tail else piece
    if buffer:
        chunks.append(buffer)
    return chunks


def _overlap_tail(text: str, overlap: int) -> str:
    """The last ``overlap`` characters, trimmed to a word boundary.

    The tail is stored as passage text, so a slice landing mid-word would open
    the next chunk with a token fragment; the partial leading word is dropped.
    """
    tail = text[-overlap:]
    if len(text) > overlap and not text[-overlap - 1].isspace() and not tail[0].isspace():
        parts = tail.split(maxsplit=1)
        tail = parts[1] if len(parts) > 1 else ""
    return tail.lstrip()


def _split_oversized(block: str, budget: int) -> list[str]:
    """Break a block that exceeds the budget at the most natural boundary available.

    Lines first (tables, code blocks), then sentences, then words; a single token
    longer than the whole budget is hard-sliced as a last resort. Every returned
    piece fits the budget.
    """
    if len(block) <= budget:
        return [block]
    if "\n" in block:
        return _repack(block.split("\n"), "\n", budget)
    sentences = _SENTENCE_END.split(block)
    if len(sentences) > 1:
        return _repack(sentences, " ", budget)
    words = block.split()
    if len(words) > 1:
        return _repack(words, " ", budget)
    return [block[i : i + budget] for i in range(0, len(block), budget)]


def _repack(pieces: list[str], separator: str, budget: int) -> list[str]:
    """Re-fit split pieces to the budget, then greedily rejoin them up to it."""
    fitted = [fit for piece in pieces if piece.strip() for fit in _split_oversized(piece, budget)]
    packed: list[str] = []
    buffer = ""
    for piece in fitted:
        candidate = f"{buffer}{separator}{piece}" if buffer else piece
        if not buffer or len(candidate) <= budget:
            buffer = candidate
        else:
            packed.append(buffer)
            buffer = piece
    if buffer:
        packed.append(buffer)
    return packed
