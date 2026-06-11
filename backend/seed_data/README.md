# Histamine ingredient seed data

`histamine_ingredients.json` is the curated, human-reviewed source of truth for
the histamine ingredient index. It is loaded into the `histamine_ingredients`
table by [`app.scripts.seed_histamine_db`](../app/scripts/seed_histamine_db.py).
Editing this file and re-running the seed updates the table.

## Scope

Whole foods and basic ingredients people actually cook with: dairy, meat, fish,
seafood, vegetables, fruit, nuts, grains, herbs, spices, and alcohol. Target is
roughly 150–250 high-frequency rows. Food additives (E-numbers) are out of
scope for now: they do not help decompose a dish, and coverage is extended by
fuzzy matching plus the `aliases` array rather than by row count.

## Row format

A flat JSON array of objects. Each row is validated against `IngredientSeedRow`
in the seed script; an unknown field or invalid enum value fails the load.

| Field | Required | Notes |
|-------|----------|-------|
| `name` | yes | Canonical display name, e.g. `"Aged Parmesan"`. The normalized lookup key is derived from this at load time, so do not store it here. |
| `compatibility` | no | One of the levels below. **Omit** when there is no reliable rating; the agent must not assert safety for these. |
| `mechanisms` | no | Any of the mechanism flags below. Defaults to `[]`. |
| `category` | no | Coarse grouping, e.g. `"cheese"`, `"fish"`. Powers substitute lookups. |
| `is_category` | no | `true` marks an umbrella row (e.g. `"Hard Cheese"`) that the dish agent may resolve a category descriptor against when a specific ingredient is not indexed. Defaults to `false`. |
| `aliases` | no | Synonyms and common spellings that should match this row, e.g. `["aubergine", "eggplant"]`. |
| `notes` | no | Plain-language note in our own words, shown in explanations. |
| `sources` | yes | One or more references the rating draws on, e.g. `["Sánchez-Pérez et al. 2021", "SIGHI"]`. At least one is required. |

## What the values mean

**Compatibility** — how well the ingredient tends to be tolerated, by symptom severity:

| Value | Meaning |
|-------|---------|
| `well_tolerated` | No symptoms expected at a normal serving. |
| `moderately_compatible` | Minor symptoms; small amounts are often tolerated. |
| `incompatible` | Clear symptoms at a normal serving. |
| `poorly_tolerated` | Strong symptoms, even in small amounts. |
| *(omitted)* | No reliable rating across sources. Treated as unknown. |

**Mechanisms** — why an ingredient may trigger symptoms (an ingredient can have several):

| Value | Meaning |
|-------|---------|
| `perishable` | Forms histamine quickly as it ages or spoils. |
| `high_histamine` | Naturally high in histamine. |
| `other_amines` | High in other biogenic amines (tyramine, putrescine, and similar). |
| `liberator` | Prompts the body to release its own histamine. |
| `dao_blocker` | Inhibits the enzymes that break histamine down. |

## Curation rules

- One row per ingredient. Fold spelling variants and close synonyms into
  `aliases` rather than adding near-duplicate rows.
- Umbrella rows (`is_category: true`) should carry the phrasings a model would
  use for the group as `aliases` (e.g. `"aged hard cheese"` on Hard Cheese) —
  category descriptors are matched exactly, never fuzzily.
- When in doubt, prefer caution: a too-cautious rating is safer than a
  too-permissive one, and an omitted `compatibility` ("unknown") is honest.
- Keep `notes` short, plain, and your own words.
- Cross-check a rating against more than one source before trusting it, and
  record what you used in `sources`.

## Sources

Ratings are compiled and cross-checked from several public references; no single
source's document or full list is reproduced. Individual facts are not
copyrightable, and the source PDFs are not committed to this repository. With
thanks to the organizations and authors below.

- **SIGHI — Swiss Interest Group Histamine Intolerance**, Food Compatibility
  List: <https://www.histaminintoleranz.ch>
- **Sánchez-Pérez et al. (2021)**, "Low-Histamine Diets: Is the Exclusion of
  Foods Justified by Their Histamine Content?", *Nutrients* (CC BY):
  <https://pmc.ncbi.nlm.nih.gov/articles/PMC8143338/>
- **Comas-Basté et al. (2018)**, "Biogenic Amines in Plant-Origin Foods",
  *Foods* (CC BY): <https://pmc.ncbi.nlm.nih.gov/articles/PMC6306728/>
- **EFSA (2011)**, Scientific Opinion on biogenic amines in fermented foods:
  <https://www.efsa.europa.eu/en/efsajournal/pub/2393>
- **Histamine Intolerance Awareness (UK)**:
  <https://www.histamineintolerance.org.uk>
- **British Dietetic Association**, food fact sheet on histamine and vasoactive
  amines.
