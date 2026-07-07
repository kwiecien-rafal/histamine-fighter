import type {
  AlternativeGoal,
  AlternativeSource,
  DishAssessmentResponse,
} from "../api/client";

// Branded copy for the neutral alternative-source values (CLAUDE section 19). A
// verified pick comes from the approved pool and earns a provenance line; a
// generated one has nothing verified to claim. Either source re-checks the dish
// when tapped, so the tap affordance is shared (see ALTERNATIVE_TAP_HINT) rather
// than standing in for the provenance.
interface SourceBadge {
  // The provenance line, or null when there is no verified claim to make.
  provenance: string | null;
  // Spoken prefix that states what the decorative ✓ glyph means to a screen reader.
  srPrefix: string;
}

export const ALTERNATIVE_SOURCE: Record<AlternativeSource, SourceBadge> = {
  verified: { provenance: "From our kitchen", srPrefix: "Verified: " },
  generated: { provenance: null, srPrefix: "" },
};

// Shown on every card: each suggestion, whatever its source, is re-vetted through
// propose -> confirm -> assess when picked, so each one stays tappable.
export const ALTERNATIVE_TAP_HINT = "Tap to check this dish";

// The core ingredients the dish cannot keep: the ones adaptation gave up on.
// Falls back to every adapted ingredient when no core dead-end exists, so the
// subtitle still names what the alternatives will steer around.
function blockedIngredients(result: DishAssessmentResponse): string[] {
  const coreDeadEnds = result.adaptations
    .filter((entry) => entry.role === "core" && entry.action === "no_safe_swap")
    .flatMap((entry) => entry.ingredients);
  if (coreDeadEnds.length > 0) return coreDeadEnds;
  return result.adaptations.flatMap((entry) => entry.ingredients);
}

// Data-driven subtitles under the goal buttons, composed from what the
// assessment already established — no extra model call. `dish_style` is the
// synthesis step's short descriptor and may be absent on older cached results.
export function goalSubtitle(goal: AlternativeGoal, result: DishAssessmentResponse): string {
  switch (goal) {
    case "same_style": {
      const style = result.dish_style ?? "dish like this";
      const blocked = blockedIngredients(result);
      return blocked.length > 0
        ? `another ${style}, without ${listNames(blocked)}`
        : `another ${style}`;
    }
    case "similar_flavours":
      return "keeps the flavours, maybe in a different format";
    case "any_meal":
      return "a fresh start — anything satisfying that checks out";
  }
}

function listNames(names: string[]): string {
  const shown = names.slice(0, 3);
  const listed =
    shown.length > 1
      ? `${shown.slice(0, -1).join(", ")} or ${shown[shown.length - 1]}`
      : shown[0];
  return names.length > 3 ? `${listed} and more` : listed;
}
