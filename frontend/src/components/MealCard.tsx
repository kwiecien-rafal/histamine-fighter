import { useState } from "react";

import type { PublicMeal } from "../api/domain";
import { MEAL_TYPE_LABEL } from "../lib/meal";
import { MealAttribution } from "./MealAttribution";
import { ReplayDialog } from "./ReplayDialog";

interface MealCardProps {
  meal: PublicMeal;
}

// The full view of an approved meal, shared by the daily board and the browse detail.
// Every meal here cleared the curated index and an admin approved it, so the verified
// badge is a constant signal, not a per-row claim. The model badge is per-card: a board
// can mix models when an admin regenerates a single slot.
export function MealCard({ meal }: MealCardProps) {
  const [watching, setWatching] = useState(false);
  return (
    <article className="rounded border border-stone-200 bg-white p-5">
      <div className="flex items-start justify-between gap-3 mb-1">
        <h3 className="text-lg font-medium">{meal.name}</h3>
        <div className="flex items-center gap-2 shrink-0">
          <span className="font-mono text-[10px] uppercase tracking-wide text-emerald-800 bg-emerald-50 border border-emerald-200 rounded px-1.5 py-0.5">
            {MEAL_TYPE_LABEL[meal.meal_type]}
          </span>
          <span
            className="font-mono text-[10px] uppercase tracking-wide text-emerald-800 bg-emerald-50 border border-emerald-200 rounded px-1.5 py-0.5"
            title="Composed from the curated safe index and approved by a human."
          >
            ✓ Verified
          </span>
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

      {meal.tags.length > 0 && (
        <ul className="flex flex-wrap gap-1.5 mb-4">
          {meal.tags.map((tag) => (
            <li
              key={tag}
              className="font-mono text-[10px] uppercase tracking-wide text-stone-500 bg-stone-100 border border-stone-200 rounded px-1.5 py-0.5"
            >
              {tag}
            </li>
          ))}
        </ul>
      )}

      {meal.recipe && meal.recipe.length > 0 && (
        <details>
          <summary className="text-xs font-semibold uppercase tracking-wide text-stone-500 cursor-pointer">
            Recipe ({meal.recipe.length} steps)
          </summary>
          <ol className="mt-2 flex flex-col gap-1 list-decimal list-inside text-sm text-stone-600">
            {meal.recipe.map((step, index) => (
              <li key={index}>{step}</li>
            ))}
          </ol>
        </details>
      )}

      <div className="mt-4 flex flex-wrap items-center justify-between gap-2 text-xs text-stone-500">
        <MealAttribution model={meal.model} />
        {meal.trace.length > 0 && (
          <button
            type="button"
            onClick={() => setWatching(true)}
            className="text-sm text-emerald-800 hover:text-emerald-900 underline underline-offset-4 cursor-pointer"
          >
            Watch how it was composed
          </button>
        )}
      </div>

      {watching && (
        <ReplayDialog title={meal.name} trace={meal.trace} onClose={() => setWatching(false)} />
      )}
    </article>
  );
}
