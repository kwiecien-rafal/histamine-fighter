// Neutral domain types shared by the public and admin API clients. They live here, not
// in the admin client, so the public pages (daily board, meal browse) don't have to
// reach into admin code for a plain domain enum. Branded labels stay in the UI layer
// (CLAUDE section 19); these are the stable wire values.

import type { ProposedIngredient } from "./client";

export type MealType = "breakfast" | "lunch" | "dinner" | "snack";

// The `model` sentinel a hand-authored meal carries in place of a producing model. The UI
// renders it as "Curated by admin"; an empty trace alongside it means no replay offers.
// Must stay in sync with MANUAL_MODEL in the backend (app/services/meal_service.py); both
// pin the literal in a test so a one-sided change fails rather than silently regressing.
export const MANUAL_MODEL = "manual";

// The stable reading token a trace step carries; the UI maps it to a label.
export type TraceReading = "safe" | "depends" | "avoid" | "unverifiable" | "not_indexed";

export type TraceKind =
  | "draft"
  | "check"
  | "search"
  | "options"
  | "reject"
  | "submit"
  | "verify";

export interface TraceEvent {
  kind: TraceKind;
  text: string;
  ingredient: string | null;
  compatibility: TraceReading | null;
}

// The full public view of a composed meal, shared by the daily board card and the
// curated browse detail (the same shape on both surfaces). The model's prose trace
// steps are filtered out server-side; the browse *list* ships a lean summary instead.
export interface PublicMeal {
  meal_type: MealType;
  // Per-card so attribution stays truthful when a board mixes models.
  model: string;
  name: string;
  description: string;
  ingredients: ProposedIngredient[];
  recipe: string[] | null;
  tags: string[];
  // This meal's own code-authored reasoning, for the per-card "watch how it was
  // composed" replay.
  trace: TraceEvent[];
}
