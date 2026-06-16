import type { AlternativeSource } from "../api/client";

// Branded copy for the neutral alternative-source values (CLAUDE section 19): a
// verified pick comes from the approved pool, a generated one is an idea to vet.
interface SourceBadge {
  label: string;
  verified: boolean;
}

export const ALTERNATIVE_SOURCE: Record<AlternativeSource, SourceBadge> = {
  verified: { label: "✓ From our kitchen", verified: true },
  generated: { label: "Tap to check this dish", verified: false },
};
