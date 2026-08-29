# Experiments

One-off feasibility spikes that back design decisions, kept as the record of how
a choice was made. These are **not** maintained evals or application code — they
are not imported by `app/` and not run in CI.

- `ingredient_embedding_viability.py` — checks whether embedding-based matching
  can safely gate the ingredient lookup. Outcome: no. See
  [ADR 0001](../../docs/adr/0001-no-vector-search-for-the-safety-index.md).
