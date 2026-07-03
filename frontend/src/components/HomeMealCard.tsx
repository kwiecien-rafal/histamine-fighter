import type { DailyMealCard } from "../api/daily";
import { MEAL_TYPE_LABEL } from "../lib/meal";

// A deliberately lean card for the Home board strip: type, name, a clamped line of
// description. Recipes, traces and replays live on the full board and detail pages.
export function HomeMealCard({ meal }: { meal: DailyMealCard }) {
  return (
    <article className="rounded border border-cream-200 bg-white p-4">
      <p className="font-mono text-[10px] uppercase tracking-wide text-stone-500 mb-1">
        {MEAL_TYPE_LABEL[meal.meal_type]}
      </p>
      <h3 className="font-medium text-stone-900">{meal.name}</h3>
      <p className="text-sm text-stone-600 mt-1 line-clamp-2">{meal.description}</p>
    </article>
  );
}
