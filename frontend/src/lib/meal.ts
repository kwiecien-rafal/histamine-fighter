import { MANUAL_MODEL } from "../api/domain";
import type { MealType, TraceEvent } from "../api/domain";

// Display labels for the neutral domain enums (CLAUDE section 19): the wire stays
// on stable values, the UI owns the wording.
export const MEAL_TYPE_LABEL: Record<MealType, string> = {
  breakfast: "Breakfast",
  lunch: "Lunch",
  dinner: "Dinner",
  snack: "Snack",
};

// Meal types in their natural order, for selectors.
export const MEAL_TYPES: MealType[] = ["breakfast", "lunch", "dinner", "snack"];

export const TRACE_KIND_LABEL: Record<TraceEvent["kind"], string> = {
  draft: "Draft",
  check: "Check",
  search: "Search",
  options: "Options",
  reject: "Reject",
  submit: "Submit",
  verify: "Verify",
};

// A reject is the agent dropping an unsafe idea — the line worth highlighting when
// an admin reads the trace.
export function isRejectEvent(event: TraceEvent): boolean {
  return event.kind === "reject";
}

export interface ModelAttribution {
  label: string;
  title: string;
  isManual: boolean;
}

// Provenance copy for an AI output's `model`: a real model name shown verbatim, or the
// MANUAL_MODEL sentinel surfaced as "Curated by admin". Centralized so the badge and the
// public attribution line read the sentinel one way (CLAUDE section 19).
export function modelAttribution(model: string): ModelAttribution {
  if (model === MANUAL_MODEL) {
    return {
      label: "Curated by admin",
      title: "Written and curated by an admin, not composed by a model.",
      isManual: true,
    };
  }
  return { label: model, title: "Model that produced this response", isManual: false };
}
