import type { Verdict } from "../api/client";

// The single verdict -> display mapping (CLAUDE section 19): a stable SafetyLevel
// value maps to its label, badge emoji, and tone classes here, so every surface that
// shows a verdict reads from one source. Structured so it can become an i18n
// dictionary later.
export interface VerdictDisplay {
  label: string;
  icon: string;
  toneClassName: string;
}

export const VERDICT_DISPLAY: Record<Verdict, VerdictDisplay> = {
  safe: { label: "Safe", icon: "✅", toneClassName: "bg-forest-50 text-forest-800 border-forest-200" },
  depends: {
    label: "Depends",
    icon: "⚠️",
    toneClassName: "bg-amber-50 text-amber-800 border-amber-200",
  },
  avoid: { label: "Avoid", icon: "🚫", toneClassName: "bg-red-50 text-red-800 border-red-200" },
};
