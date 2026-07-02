import { Link } from "react-router-dom";

import type { PublicMealCard } from "../api/meals";
import { MEAL_TYPE_LABEL } from "../lib/meal";
import { MealAttribution } from "./MealAttribution";

interface BrowseMealCardProps {
  meal: PublicMealCard;
}

// A lean summary card in the browse grid. It links to the meal's detail page, where the
// recipe and the composition replay load; the list itself ships neither, only flags that
// they exist. Every meal here cleared the index and an admin approved it, so the verified
// badge is a constant signal, not a per-row claim.
export function BrowseMealCard({ meal }: BrowseMealCardProps) {
  const hints = [meal.has_recipe && "Recipe", meal.has_trace && "Replay"].filter(Boolean);
  return (
    <Link
      to={`/meals/${meal.id}`}
      className="block rounded border border-stone-200 bg-white p-5 hover:border-emerald-300 transition-colors"
    >
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

      <div className="mt-4 flex flex-wrap items-center justify-between gap-2 text-xs text-stone-500">
        <MealAttribution model={meal.model} />
        <span className="text-emerald-800 font-medium">
          {hints.length > 0 ? `${hints.join(" · ")} →` : "View →"}
        </span>
      </div>
    </Link>
  );
}
