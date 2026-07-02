import { useState } from "react";

import { createMeal, type MealEdit, type MealType } from "../api/admin";
import { MealEditForm } from "./MealEditForm";

interface NewMealFormProps {
  // Refreshes the curated list after a meal is created; it lands pending, so the parent
  // shows the pending tab.
  onCreated: () => void;
}

const EMPTY_MEAL: MealEdit = {
  name: "",
  description: "",
  ingredients: [],
  recipe: null,
  tags: [],
};

// Authors a manual (non-LLM) meal from the curated section. It reuses the shared edit form
// with empty fields plus a meal-type selector, so a hand-written meal clears the same index
// gate a composed one does (a blocker surfaces inline). On success the new pending meal
// joins the queue below.
const DEFAULT_MEAL_TYPE: MealType = "breakfast";

export function NewMealForm({ onCreated }: NewMealFormProps) {
  const [open, setOpen] = useState(false);
  const [mealType, setMealType] = useState<MealType>(DEFAULT_MEAL_TYPE);

  // Drop the in-progress draft: the form's fields reset on remount, so the slot resets
  // with them rather than carrying over to the next meal.
  function close() {
    setMealType(DEFAULT_MEAL_TYPE);
    setOpen(false);
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="rounded border border-stone-300 px-3 py-1.5 text-sm text-stone-700 hover:bg-white cursor-pointer"
      >
        + New meal
      </button>
    );
  }

  return (
    <MealEditForm
      initial={EMPTY_MEAL}
      mealType={{ value: mealType, onChange: setMealType }}
      submitLabel="Create meal"
      onSave={async (edit) => {
        await createMeal({ ...edit, meal_type: mealType });
        close();
        onCreated();
      }}
      onCancel={close}
    />
  );
}
