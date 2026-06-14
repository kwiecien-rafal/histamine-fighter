import type { Adaptation, AdaptationAction, CulinaryRole } from "../api/client";

// Branded labels for the neutral action values live here, presentation-only
// (CLAUDE.md §19), mirroring VERDICT_STYLES in VerdictBadge.
const ACTION_TAGS: Record<
  Exclude<AdaptationAction, "swap">,
  { label: string; className: string }
> = {
  omit: {
    label: "leave it out",
    className: "bg-stone-100 text-stone-700 border-stone-300",
  },
  no_safe_swap: {
    label: "no safe swap",
    className: "bg-red-50 text-red-800 border-red-200",
  },
};

// The role explains why a no-safe-swap group can cost the dish its identity, so
// it earns a place on the card. Branded labels, presentation-only (§19).
const ROLE_LABELS: Record<CulinaryRole, string> = {
  core: "core",
  supporting: "supporting",
  seasoning: "seasoning",
};

interface AdaptationListProps {
  adaptations: Adaptation[];
}

export function AdaptationList({ adaptations }: AdaptationListProps) {
  return (
    <section className="mb-4">
      <h3 className="text-xs uppercase tracking-wide text-stone-500 mb-2">
        How to adapt it
      </h3>
      <ul className="flex flex-col gap-2">
        {adaptations.map((entry) => {
          // You cannot cross out what cannot be removed: a no-safe-swap group
          // stays plain, a swapped or omitted one is struck through.
          const removed = entry.action !== "no_safe_swap";
          return (
            <li
              key={entry.ingredients.join("+")}
              className="rounded border border-stone-200 bg-stone-50 px-3 py-2 text-sm"
            >
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span
                  className={
                    removed
                      ? "text-stone-500 line-through"
                      : "font-medium text-stone-700"
                  }
                >
                  {entry.ingredients.join(" + ")}
                </span>
                {entry.action === "swap" ? (
                  <>
                    <span className="text-stone-400">→</span>
                    <span className="font-medium text-emerald-800">
                      {entry.swap}
                    </span>
                  </>
                ) : (
                  <span
                    className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs ${ACTION_TAGS[entry.action].className}`}
                  >
                    {ACTION_TAGS[entry.action].label}
                  </span>
                )}
                <span className="font-mono text-[11px] uppercase tracking-wide text-stone-400">
                  {ROLE_LABELS[entry.role]}
                </span>
              </div>
              <p className="text-stone-600 mt-0.5">{entry.reason}</p>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
