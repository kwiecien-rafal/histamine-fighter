import type { DishAssessmentResponse } from "../api/client";

// How far a dish drifts from itself once its adaptations are applied. Ordered by
// severity: a lost identity outranks a merely altered one, which outranks a dish
// that still holds together but has a group with no safe fix.
export type PivotTone = "lost" | "altered" | "unresolved";

// True when any group has no safe fix. Drives the unresolved tone and the
// avoid-name derivation in the hook; a lost dish always has one of these.
export function hasUnresolvedAdaptation(result: DishAssessmentResponse): boolean {
  return result.adaptations.some((entry) => entry.action === "no_safe_swap");
}

// The single source of truth for the pivot: one tone derived once, so the hook
// gate, the App gate, the result callout and the panel header can never drift.
export function pivotTone(result: DishAssessmentResponse): PivotTone | null {
  if (result.integrity === "lost") return "lost";
  if (result.integrity === "altered") return "altered";
  if (hasUnresolvedAdaptation(result)) return "unresolved";
  return null;
}

export function shouldOfferAlternatives(result: DishAssessmentResponse): boolean {
  return pivotTone(result) !== null;
}
