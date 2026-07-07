import { Link } from "react-router-dom";

import type { SavedMealCard as SavedMealCardData } from "../api/saves";
import { MEAL_TYPE_LABEL } from "../lib/meal";
import { SAVED_TAG_COLORS, SAVED_TAG_LABEL, isColorTag, isSavedMealTag } from "../lib/savedTags";
import { VERDICT_DISPLAY } from "../lib/verdict";

interface SavedMealCardProps {
  meal: SavedMealCardData;
}

// A saved meal in the profile grid, linking to its edit view. Deliberately not
// MealCard: that card infers "✓ Verified" from its data, which must never appear
// on a lookup snapshot or a user-edited copy. Here the badge set is the lookup
// verdict and the edited marker; removal lives on the full edit view.
export function SavedMealCard({ meal }: SavedMealCardProps) {
  const verdict = meal.verdict ? VERDICT_DISPLAY[meal.verdict] : null;
  return (
    <Link
      to={`/profile/meals/${meal.id}`}
      className="flex h-full flex-col rounded border border-stone-200 bg-white p-5 hover:border-forest-300 transition-colors"
    >
      <div className="flex items-start justify-between gap-3 mb-1">
        <h3 className="text-lg font-medium">{meal.name}</h3>
        <div className="flex items-center gap-2 shrink-0">
          {meal.meal_type && (
            <span className="font-mono text-[10px] uppercase tracking-wide text-forest-800 bg-forest-50 border border-forest-200 rounded px-1.5 py-0.5">
              {MEAL_TYPE_LABEL[meal.meal_type]}
            </span>
          )}
          {verdict && (
            <span
              className={`font-mono text-[10px] uppercase tracking-wide border rounded px-1.5 py-0.5 ${verdict.toneClassName}`}
              title="The verdict this dish was assessed at when you saved it."
            >
              {verdict.icon} {verdict.label}
            </span>
          )}
          {meal.edited_at && (
            <span
              className="font-mono text-[10px] uppercase tracking-wide text-stone-600 bg-cream-100 border border-cream-200 rounded px-1.5 py-0.5"
              title="You changed this copy; your edits are not re-checked against the index."
            >
              Edited by you
            </span>
          )}
        </div>
      </div>
      {meal.description && <p className="text-sm text-stone-600 mb-4">{meal.description}</p>}

      {/* mt-auto pins this row to the card's bottom edge, so tags and the
          action label line up across cards of different content heights. */}
      <div className="mt-auto flex items-center justify-between gap-3 pt-4">
        <ul className="flex flex-wrap items-center gap-1.5">
          {meal.tags.map((tag) =>
            isColorTag(tag) ? (
              <li key={tag}>
                <span
                  className={`block h-3.5 w-3.5 rounded-full ${SAVED_TAG_COLORS[tag]}`}
                  title={SAVED_TAG_LABEL[tag]}
                  role="img"
                  aria-label={SAVED_TAG_LABEL[tag]}
                />
              </li>
            ) : (
              <li
                key={tag}
                className="font-mono text-[10px] uppercase tracking-wide text-stone-500 bg-cream-100 border border-cream-200 rounded px-1.5 py-0.5"
              >
                {isSavedMealTag(tag) ? SAVED_TAG_LABEL[tag] : tag}
              </li>
            ),
          )}
        </ul>
        <span className="shrink-0 text-xs text-forest-800 font-medium">View · Edit →</span>
      </div>
    </Link>
  );
}
