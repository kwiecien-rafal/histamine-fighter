import type { AlternativeSource } from "../api/client";

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
