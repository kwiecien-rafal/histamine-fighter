import { useState } from "react";

import type { AdminMeal, MealEdit } from "../api/admin";
import { MEAL_TYPE_LABEL } from "../lib/meal";
import { ComposeCost } from "./ComposeCost";
import { LLMProviderBadge } from "./LLMProviderBadge";
import { MealEditForm } from "./MealEditForm";
import { ReviewActions } from "./ReviewActions";
import { TraceDetails } from "./TraceDetails";
import { UnverifiedNote } from "./UnverifiedNote";

interface MealReviewCardProps {
  meal: AdminMeal;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
  // Hard-deletes the meal (the destructive counterpart to reject).
  onRemove: () => void;
  // When provided, the card offers an inline edit; onSaveEdit persists, onEdited refreshes.
  onSaveEdit?: (edit: MealEdit) => Promise<void>;
  onEdited?: () => void;
}

export function MealReviewCard({
  meal,
  busy,
  onApprove,
  onReject,
  onRemove,
  onSaveEdit,
  onEdited,
}: MealReviewCardProps) {
  const [editing, setEditing] = useState(false);
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
      {meal.usage && (
        <div className="mb-1">
          <ComposeCost usage={meal.usage} model={meal.model} />
        </div>
      )}
      <p className="text-sm text-stone-600 mb-4">{meal.description}</p>

      <h4 className="text-xs font-semibold uppercase tracking-wide text-stone-500 mb-2">
        Ingredients
      </h4>
      <ul className="flex flex-wrap gap-1.5 mb-4">
        {meal.ingredients.map((ingredient, index) => (
          <li
            key={`${ingredient.name}-${index}`}
            className="rounded border border-stone-200 bg-stone-50 px-2 py-0.5 text-sm"
          >
            {ingredient.name}
            {ingredient.category && (
              <span className="text-stone-400"> · {ingredient.category}</span>
            )}
          </li>
        ))}
      </ul>

      <UnverifiedNote ingredients={meal.unverified_ingredients} />

      <TraceDetails trace={meal.reasoning_trace} />

      {editing && onSaveEdit ? (
        <MealEditForm
          initial={{
            name: meal.name,
            description: meal.description,
            ingredients: meal.ingredients,
            recipe: meal.recipe,
            tags: meal.tags,
          }}
          onSave={async (edit) => {
            await onSaveEdit(edit);
            setEditing(false);
            onEdited?.();
          }}
          onCancel={() => setEditing(false)}
        />
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <ReviewActions
            busy={busy}
            onApprove={meal.approval_status !== "approved" ? onApprove : undefined}
            onReject={meal.approval_status !== "rejected" ? onReject : undefined}
            onRemove={onRemove}
          />
          {onSaveEdit && (
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="text-sm text-stone-600 hover:text-stone-900 underline underline-offset-4 cursor-pointer"
            >
              Edit
            </button>
          )}
        </div>
      )}
    </article>
  );
}
