# 1. No vector search for the histamine safety index

- **Status:** Accepted
- **Date:** 2026-06-08

## Context

The dish-lookup agent grounds its safety verdict in the `histamine_ingredients`
table. Today that table is reached by **lexical** matching only: exact normalized
name, alias-array membership, and `pg_trgm` trigram similarity. Trigrams bridge
spelling variants ("tomatos" → tomato) but not semantically-distant synonyms that
aren't hand-listed as aliases ("prosciutto" ↛ "cured ham", "garbanzo" ↛
"chickpea").

The proposal was to add a vector embedding column and union semantic matches into
the lookup, so the agent could recover those synonyms. Because the embedding
model's dimension is baked into the database column, the model is a deploy-time
commitment that is expensive to reverse — so we checked it on evidence first.

## Decision

**The ingredient index is not vectorized.** Lexical matching (exact + alias +
trigram) stays the sole, authoritative path for both `find_candidates` (lookup)
and `find_substitutes`. Cross-dialect synonyms are covered by curating the
`aliases` array.

## Evidence

A viability check
([backend/experiments/ingredient_embedding_viability.py](../../backend/experiments/ingredient_embedding_viability.py))
embedded a set of true cross-dialect synonyms (`prosciutto` → Cured Ham) and
*dangerous near-misses* (`almond milk` → Milk) and asked: is there a single
cosine cutoff that keeps every true synonym while rejecting every wrong match?

Across five models the separation margin was **always negative** — the
lowest-scoring true synonym always scored *below* the highest-scoring dangerous
near-miss:

```
model                                   recall@1   min+    max-   margin
snowflake/snowflake-arctic-embed-m          0.64   0.810   0.888  -0.078  OVERLAP
mixedbread-ai/mxbai-embed-large-v1          0.64   0.497   0.819  -0.322  OVERLAP
BAAI/bge-small-en-v1.5                      0.36   0.505   0.839  -0.334  OVERLAP
BAAI/bge-base-en-v1.5                       0.45   0.405   0.801  -0.396  OVERLAP
sentence-transformers/all-MiniLM-L6-v2      0.64   0.186   0.781  -0.595  OVERLAP
```

`min+` = lowest true-synonym similarity; `max-` = highest dangerous near-miss
similarity. A usable cutoff exists only when `margin > 0`.

The failure is **structural, not a capacity gap**:

- General embeddings are *correct* that "almond milk" is semantically near "milk".
  The distinction that matters here — histamine profile — is domain knowledge no
  general model carries.
- Bigger did not help: 1024-dim `mxbai-large` (−0.322) lost to 768-dim
  `arctic-embed-m`, and 768-dim `bge-base` was worse than the 384-dim baseline.
- A fuzzy similarity score is the wrong primitive for a safety gate, which wants
  determinism and auditability.

## Consequences

- **Positive.** The lookup path stays deterministic and auditable; no embedding
  model sits on the hot lookup loop; the safety verdict cannot be silently
  downgraded by a spurious semantic neighbour.
- **Cost.** Cross-dialect synonym coverage now depends on curating the `aliases`
  array — deterministic and zero-inference, but manual. Curation effort replaces
  a similarity threshold as the recall lever.
- **Embeddings and RAG are not abandoned** — they move to where they are a
  correct fit: a knowledge layer for the Learn hub (curated, sourced corpus →
  chunk → embed → retrieve → cited generation). There the model retrieves prose
  passages (the task embeddings suit) and the LLM grounds, cites, and can decline,
  so no global safety threshold is required. That model is chosen by its own
  prose-retrieval eval, not by this check.

## Revisit when

- A domain-tuned or fine-tuned embedding demonstrates clean separation on the
  same check, **or**
- Alias-curation effort grows large enough to outweigh the determinism benefit.

Re-run the check to refresh the evidence before reversing this decision.
