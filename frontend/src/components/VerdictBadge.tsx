import type { Verdict } from "../api/client";

interface VerdictStyle {
  label: string;
  icon: string;
  className: string;
}

const VERDICT_STYLES: Record<Verdict, VerdictStyle> = {
  safe: {
    label: "Safe",
    icon: "✅",
    className: "bg-emerald-50 text-emerald-800 border-emerald-200",
  },
  depends: {
    label: "Depends",
    icon: "⚠️",
    className: "bg-amber-50 text-amber-800 border-amber-200",
  },
  avoid: {
    label: "Avoid",
    icon: "🚫",
    className: "bg-red-50 text-red-800 border-red-200",
  },
};

interface VerdictBadgeProps {
  verdict: Verdict;
}

export function VerdictBadge({ verdict }: VerdictBadgeProps) {
  const style = VERDICT_STYLES[verdict];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm font-medium ${style.className}`}
    >
      <span aria-hidden>{style.icon}</span>
      {style.label}
    </span>
  );
}
