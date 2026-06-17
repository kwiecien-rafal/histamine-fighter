import type { AdminMeal } from "../api/admin";
import { MEAL_TYPE_LABEL, TRACE_KIND_LABEL, isRejectEvent } from "../lib/meal";
import { LLMProviderBadge } from "./LLMProviderBadge";

interface MealReviewCardProps {
  meal: AdminMeal;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
}

export function MealReviewCard({ meal, busy, onApprove, onReject }: MealReviewCardProps) {
  return (
    <article className="rounded border border-stone-200 bg-white p-5">
      <div className="flex items-start justify-between gap-3 mb-1">
        <h3 className="text-lg font-medium">{meal.name}</h3>
        <div className="flex items-center gap-2 shrink-0">
          <span className="font-mono text-[10px] uppercase tracking-wide text-emerald-800 bg-emerald-50 border border-emerald-200 rounded px-1.5 py-0.5">
            {MEAL_TYPE_LABEL[meal.meal_type]}
          </span>
          <LLMProviderBadge model={meal.model} />
        </div>
      </div>
      <p className="text-sm text-stone-600 mb-4">{meal.description}</p>

      <h4 className="text-xs font-semibold uppercase tracking-wide text-stone-500 mb-2">
        Ingredients
      </h4>
      <ul className="flex flex-wrap gap-1.5 mb-4">
        {meal.ingredients.map((ingredient) => (
          <li
            key={ingredient.name}
            className="rounded border border-stone-200 bg-stone-50 px-2 py-0.5 text-sm"
          >
            {ingredient.name}
            {ingredient.category && (
              <span className="text-stone-400"> · {ingredient.category}</span>
            )}
          </li>
        ))}
      </ul>

      {meal.unverified_ingredients.length > 0 && (
        <div className="mb-4 rounded border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
          <span className="font-semibold">Not in the index — check before approving:</span>{" "}
          {meal.unverified_ingredients.join(", ")}
        </div>
      )}

      {meal.reasoning_trace.length > 0 && (
        <details className="mb-4">
          <summary className="text-xs font-semibold uppercase tracking-wide text-stone-500 cursor-pointer">
            What the agent did ({meal.reasoning_trace.length} steps)
          </summary>
          <ol className="mt-2 flex flex-col gap-1 border-l border-stone-200 pl-3">
            {meal.reasoning_trace.map((event, index) => (
              <li
                key={index}
                className={`text-sm ${isRejectEvent(event) ? "text-red-700" : "text-stone-600"}`}
              >
                <span className="font-mono text-[10px] uppercase tracking-wide text-stone-400">
                  {TRACE_KIND_LABEL[event.kind]}
                </span>{" "}
                {event.text}
              </li>
            ))}
          </ol>
        </details>
      )}

      {/* Approval is the human closing the gap code can't: the verdict only
          checks the listed ingredients, so the reviewer reads them above. */}
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={onApprove}
          disabled={busy}
          className="rounded bg-emerald-800 text-white px-4 py-2 text-sm disabled:opacity-50 enabled:cursor-pointer"
        >
          Approve
        </button>
        <button
          type="button"
          onClick={onReject}
          disabled={busy}
          className="rounded border border-red-300 text-red-700 px-4 py-2 text-sm hover:border-red-500 disabled:opacity-50 enabled:cursor-pointer"
        >
          Reject
        </button>
        {busy && (
          <span className="text-sm text-stone-500" aria-live="polite">
            Saving…
          </span>
        )}
      </div>
    </article>
  );
}
