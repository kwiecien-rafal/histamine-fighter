# Knowledge corpus (Learn-hub RAG)

The `*.md` files here are the curated, sourced knowledge base the `LearnAgent`
retrieves over to answer questions with citations. They are loaded into the
`knowledge_chunks` table by
[`app.scripts.seed_knowledge`](../../app/scripts/seed_knowledge.py): each file is
parsed, chunked, embedded, and stored. Editing a file and re-running the seed
refreshes that document's chunks.

> **DRAFT — pending human accuracy review.** This starter set was drafted from
> well-established, public histamine-intolerance material (see each file's
> `source`) and is written in our own words. It is **educational, not medical
> advice**, and must be reviewed by a qualified person before being relied on.
> Health facts are never AI-generated as the authority — they are curated here so
> the RAG layer can cite them.

## File format

YAML-style front matter followed by markdown:

```markdown
---
title: Human-readable title
slug: url-safe-identifier
source: Attribution / citation for the content
topic: Coarse grouping (basics, mechanism, foods, diet, ...)
---

## A heading

Body paragraphs...
```

`slug` is the document identity the seed upserts against; keep it stable. Chunks
are split on headings and packed to a character budget with a small overlap, so
each chunk carries its section heading for context.

## Sources

Compiled and cross-checked from public references; no single source's document is
reproduced. With thanks to:

- **SIGHI — Swiss Interest Group Histamine Intolerance**: <https://www.histaminintoleranz.ch>
- **British Dietetic Association**, food fact sheet on histamine and vasoactive amines.
- **Histamine Intolerance Awareness (UK)**: <https://www.histamineintolerance.org.uk>
- **Sánchez-Pérez et al. (2021)**, *Nutrients* (CC BY): <https://pmc.ncbi.nlm.nih.gov/articles/PMC8143338/>
- **Comas-Basté et al. (2018)**, *Foods* (CC BY): <https://pmc.ncbi.nlm.nih.gov/articles/PMC6306728/>
- **EFSA (2011)**, Scientific Opinion on biogenic amines in fermented foods: <https://www.efsa.europa.eu/en/efsajournal/pub/2393>
