import type { MealType, TraceEvent } from "../api/admin";

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
