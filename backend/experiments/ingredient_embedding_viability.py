"""Is embedding-based matching viable on our ingredient safety index?

This is a one-off feasibility check, not a maintained eval. It backs
ADR 0001 (docs/adr/0001-no-vector-search-for-the-safety-index.md): the result
is the reason the ingredient index is matched lexically, not by vector search.

The question
------------
The index is a *safety* corpus: a confidently-wrong semantic match
("almond milk" -> "Milk") is worse than no match. So the bar is not "do real
synonyms rank well" but "is there a single cosine cutoff that keeps every true
synonym while rejecting every dangerous near-miss?" If no such cutoff exists,
embeddings cannot gate the lookup safely regardless of which model is used.

What it measures, per candidate model
-------------------------------------
- recall@1 on true cross-dialect synonyms (prosciutto -> Cured Ham, ...),
- the lowest similarity a true synonym scores (the floor we'd need to keep them),
- the highest similarity a *dangerous* near-miss scores (the ceiling wrong
  matches reach),
- whether one cosine cutoff separates the two, and by what margin.

A positive, comfortable margin would make embeddings usable on the lookup path.
The recorded outcome was a negative margin for every model (see the ADR).

Run:  uv run python experiments/ingredient_embedding_viability.py
(First run downloads each model; subsequent runs are cached.)
"""

from __future__ import annotations

import numpy as np
from fastembed import TextEmbedding

# Candidate models. The script skips any the installed fastembed cannot load,
# so this list can be trimmed or extended without breaking the run.
CANDIDATE_MODELS: list[str] = [
    "BAAI/bge-small-en-v1.5",  # current default, 384-dim — the baseline to beat
    "BAAI/bge-base-en-v1.5",  # 768-dim, larger
    "sentence-transformers/all-MiniLM-L6-v2",  # 384-dim classic baseline
    "mixedbread-ai/mxbai-embed-large-v1",  # 1024-dim, strong on retrieval
    "snowflake/snowflake-arctic-embed-m",  # 768-dim, retrieval-tuned
]

# True synonyms: the query should retrieve the expected canonical name.
SYNONYMS: list[tuple[str, str]] = [
    ("prosciutto", "cured ham"),
    ("garbanzo beans", "chickpeas"),
    ("aubergine", "eggplant"),
    ("courgette", "zucchini"),
    ("coriander leaves", "cilantro"),
    ("rocket", "arugula"),
    ("capsicum", "bell pepper"),
    ("spring onion", "scallion"),
    ("passata", "tomato sauce"),
    ("soured cream", "sour cream"),
    ("gammon", "ham"),
]

# Dangerous near-misses: lexically/semantically close but a different histamine
# profile. The query must NOT confidently match the forbidden canonical name.
HARD_NEGATIVES: list[tuple[str, str]] = [
    ("almond milk", "milk"),
    ("coconut milk", "milk"),
    ("rice milk", "milk"),
    ("soy sauce", "soy"),
    ("cauliflower rice", "rice"),
]

# Unrelated foods padded into the corpus so wrong matches have somewhere to land.
DISTRACTORS: list[str] = [
    "rice",
    "milk",
    "soy",
    "sugar",
    "chicken breast",
    "white fish",
    "fresh lettuce",
    "olive oil",
    "butter",
    "carrot",
]


def _corpus() -> list[str]:
    seen: dict[str, None] = {}
    for _, expected in SYNONYMS:
        seen.setdefault(expected, None)
    for _, forbidden in HARD_NEGATIVES:
        seen.setdefault(forbidden, None)
    for name in DISTRACTORS:
        seen.setdefault(name, None)
    return list(seen)


def _normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.clip(norms, 1e-12, None)


def _evaluate(model_name: str) -> dict[str, float] | None:
    try:
        model = TextEmbedding(model_name=model_name)
    except Exception as exc:  # noqa: BLE001 — report and skip unsupported models
        print(f"  skipped ({type(exc).__name__}: {exc})")
        return None

    corpus = _corpus()
    index = {name: i for i, name in enumerate(corpus)}
    doc_vectors = _normalize(np.array(list(model.embed(corpus))))
    dimension = doc_vectors.shape[1]

    synonym_queries = [q for q, _ in SYNONYMS]
    negative_queries = [q for q, _ in HARD_NEGATIVES]
    synonym_vectors = _normalize(np.array(list(model.query_embed(synonym_queries))))
    negative_vectors = _normalize(np.array(list(model.query_embed(negative_queries))))

    hits = 0
    positive_sims: list[float] = []
    print(f"  dim={dimension}")
    print("  synonyms (query -> expected):")
    for (query, expected), query_vector in zip(SYNONYMS, synonym_vectors, strict=True):
        sims = doc_vectors @ query_vector
        top = int(np.argmax(sims))
        expected_sim = float(sims[index[expected]])
        positive_sims.append(expected_sim)
        correct = top == index[expected]
        hits += correct
        flag = "ok " if correct else "MISS"
        print(
            f"    [{flag}] {query:<18} expected={expected:<14} "
            f"sim={expected_sim:.3f} top={corpus[top]!r} ({float(sims[top]):.3f})"
        )

    negative_sims: list[float] = []
    print("  hard negatives (query -> forbidden, want LOW):")
    for (query, forbidden), query_vector in zip(
        HARD_NEGATIVES, negative_vectors, strict=True
    ):
        sims = doc_vectors @ query_vector
        forbidden_sim = float(sims[index[forbidden]])
        negative_sims.append(forbidden_sim)
        print(f"    {query:<18} forbidden={forbidden:<8} sim={forbidden_sim:.3f}")

    min_positive = min(positive_sims)
    max_negative = max(negative_sims)
    return {
        "recall_at_1": hits / len(SYNONYMS),
        "min_positive_sim": min_positive,
        "max_negative_sim": max_negative,
        "separation_margin": min_positive - max_negative,
    }


def main() -> None:
    results: dict[str, dict[str, float]] = {}
    for model_name in CANDIDATE_MODELS:
        print(f"\n=== {model_name} ===")
        scores = _evaluate(model_name)
        if scores is not None:
            results[model_name] = scores

    print("\n\n=== summary ===")
    header = f"{'model':<42} {'recall@1':>9} {'min+':>7} {'max-':>7} {'margin':>8}"
    print(header)
    print("-" * len(header))
    for model_name, scores in sorted(
        results.items(), key=lambda kv: kv[1]["separation_margin"], reverse=True
    ):
        verdict = "usable" if scores["separation_margin"] > 0 else "OVERLAP"
        print(
            f"{model_name:<42} {scores['recall_at_1']:>9.2f} "
            f"{scores['min_positive_sim']:>7.3f} {scores['max_negative_sim']:>7.3f} "
            f"{scores['separation_margin']:>8.3f}  {verdict}"
        )
    print(
        "\nmin+ = lowest true-synonym similarity (floor needed to keep them)\n"
        "max- = highest dangerous near-miss similarity (ceiling wrong matches reach)\n"
        "Embeddings can gate the lookup only when margin > 0 with comfortable room;\n"
        "a negative margin means there is no safe cutoff (see ADR 0001)."
    )


if __name__ == "__main__":
    main()
