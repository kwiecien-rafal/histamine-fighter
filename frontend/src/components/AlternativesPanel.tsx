import type { AlternativeGoal } from "../api/client";
import type { AlternativesState } from "../hooks/useDishLookupFlow";
import type { PivotTone } from "../lib/assessment";
import { LLMProviderBadge } from "./LLMProviderBadge";

// Branded goal labels for the neutral enum values, presentation-only.
const GOAL_OPTIONS: { value: AlternativeGoal; label: string }[] = [
  { value: "any_meal", label: "Just a good meal" },
  { value: "same_style", label: "Same style of dish" },
  { value: "similar_flavours", label: "Similar flavours" },
];

interface AlternativesPanelProps {
  alternatives: AlternativesState;
  tone: PivotTone;
  onChooseGoal: (goal: AlternativeGoal) => void;
  onPick: (dish: string) => void;
}

export function AlternativesPanel({
  alternatives,
  tone,
  onChooseGoal,
  onPick,
}: AlternativesPanelProps) {
  const busy = alternatives.status === "loading";
  const chosenGoal =
    alternatives.status === "idle" ? null : alternatives.goal;

  return (
    <section className="mt-4 rounded border border-stone-200 bg-white p-5">
      <div className="flex items-start justify-between gap-3 mb-1">
        <h2 className="text-lg font-medium">Find something else</h2>
        {alternatives.status === "loaded" && (
          <LLMProviderBadge model={alternatives.model} />
        )}
      </div>
      <p className="text-sm text-stone-600 mb-3">
        {tone === "lost"
          ? "This dish is hard to save — tell us what you're after and we'll suggest dishes to check instead."
          : "Want something closer to the original, or just different? Tell us what you're after."}
      </p>

      <div className="flex flex-wrap gap-2 mb-4">
        {GOAL_OPTIONS.map((option) => (
          <button
            key={option.value}
            type="button"
            disabled={busy}
            aria-pressed={option.value === chosenGoal}
            onClick={() => onChooseGoal(option.value)}
            className={`rounded border px-3 py-1.5 text-sm disabled:opacity-50 enabled:cursor-pointer ${
              option.value === chosenGoal
                ? "border-emerald-800 bg-emerald-800 text-white"
                : "border-stone-300 text-stone-700 hover:border-emerald-700"
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>

      {alternatives.status === "loading" && (
        <p className="text-sm text-stone-500" aria-live="polite">
          Finding ideas…
        </p>
      )}

      {alternatives.status === "error" && (
        // role="alert" announces assertively; the bold lead-in is a non-colour
        // cue so the error does not read as red text alone (WCAG AA).
        <p role="alert" className="text-sm text-red-700">
          <span className="font-medium">Couldn't load suggestions —</span>{" "}
          {alternatives.message}
        </p>
      )}

      {alternatives.status === "loaded" &&
        (alternatives.suggestions.length === 0 ? (
          <p className="text-sm text-stone-600">
            No good alternatives came back — try a different goal, or start
            over with another dish.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {alternatives.suggestions.map((suggestion) => (
              <li key={suggestion.name}>
                <button
                  type="button"
                  onClick={() => onPick(suggestion.name)}
                  className="w-full text-left rounded border border-stone-200 bg-stone-50 px-3 py-2 hover:border-emerald-700 cursor-pointer"
                >
                  <span className="block font-medium text-emerald-800">
                    {suggestion.name}
                  </span>
                  {suggestion.pitch && (
                    <span className="block text-sm text-stone-600 mt-0.5">
                      {suggestion.pitch}
                    </span>
                  )}
                  <span className="block text-xs text-stone-400 mt-1">
                    Tap to check this dish
                  </span>
                </button>
              </li>
            ))}
          </ul>
        ))}
    </section>
  );
}
