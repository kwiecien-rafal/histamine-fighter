import type { IngredientAssessment, Verdict } from "../api/client";

// Domain value -> display tone mapping lives at the presentation layer only
// (CLAUDE.md §19), consistent with VerdictBadge's palette.
const CHIP_TONES: Record<Verdict, string> = {
  safe: "bg-emerald-50 text-emerald-800 border-emerald-200",
  depends: "bg-amber-50 text-amber-800 border-amber-200",
  avoid: "bg-red-50 text-red-800 border-red-200",
};

const UNINDEXED_TONE = "bg-stone-50 text-stone-600 border-stone-200";

interface IngredientSafetyChipProps {
  assessment: IngredientAssessment;
}

export function IngredientSafetyChip({ assessment }: IngredientSafetyChipProps) {
  // An errored lookup keeps the cautious amber of its "depends" reading; an
  // ingredient absent from the index is neutral, not "rated safe".
  const note = assessment.error
    ? "check failed"
    : !assessment.found
      ? "no known concern"
      : null;
  const tone =
    !assessment.found && !assessment.error
      ? UNINDEXED_TONE
      : CHIP_TONES[assessment.safety];

  return (
    <span
      className={`inline-flex items-baseline gap-1.5 rounded-full border px-2.5 py-0.5 text-xs ${tone}`}
    >
      <span className="font-medium">{assessment.name}</span>
      {note && <span className="opacity-75">· {note}</span>}
    </span>
  );
}
