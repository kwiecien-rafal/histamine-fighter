import type { Verdict } from "../api/client";
import { VERDICT_DISPLAY } from "../lib/verdict";

interface VerdictBadgeProps {
  verdict: Verdict;
}

export function VerdictBadge({ verdict }: VerdictBadgeProps) {
  const { label, icon, toneClassName } = VERDICT_DISPLAY[verdict];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm font-medium ${toneClassName}`}
    >
      <span aria-hidden>{icon}</span>
      {label}
    </span>
  );
}
