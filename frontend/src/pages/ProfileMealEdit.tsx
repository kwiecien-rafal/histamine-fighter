import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import type { MealEdit } from "../api/admin";
import { errorMessage } from "../api/errors";
import { generateRecipe, getSave, updateSave, type SavedMealDetail } from "../api/saves";
import { MealEditForm } from "../components/MealEditForm";
import { ThinkingBrawl } from "../components/ThinkingBrawl";
import { saveKey, useSavedMealsStore } from "../store/saves";
import { useUsageStore } from "../store/usage";

// Edit the user's own saved copy. Reuses the admin MealEditForm (it is
// presentational; only the onSave differs) against PATCH /me/meals/{id}. Saving
// stamps the copy user-modified server-side, so the verified badge drops — that
// honesty is stated up front here, with the lookup flow as the re-check path.
export function ProfileMealEdit() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [meal, setMeal] = useState<SavedMealDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [removeError, setRemoveError] = useState<string | null>(null);
  const [writingRecipe, setWritingRecipe] = useState(false);
  const [recipeError, setRecipeError] = useState<string | null>(null);
  // Unsaved form edits block recipe generation: it writes from the *saved*
  // ingredients and its success remounts the form, which would discard them.
  const [formDirty, setFormDirty] = useState(false);

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      setMeal(await getSave(id));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!meal) return;
    const previous = document.title;
    document.title = `Edit ${meal.name} · Histamine Fighter`;
    return () => {
      document.title = previous;
    };
  }, [meal]);

  async function save(edit: MealEdit) {
    if (!id) return;
    await updateSave(id, {
      name: edit.name,
      description: edit.description,
      ingredients: edit.ingredients,
      recipe: edit.recipe,
      tags: edit.tags,
    });
    void navigate("/profile");
  }

  // One LLM call, ever, per saved meal: the steps persist on the row, so the
  // button disappears for good once they exist.
  async function writeRecipe() {
    if (!id || writingRecipe) return;
    setWritingRecipe(true);
    setRecipeError(null);
    try {
      const response = await generateRecipe(id);
      // A stored recipe returned unchanged made no model call; keep the usage
      // ledger free of zero-call entries, same policy as cached lookups.
      if (response.usage.calls > 0) {
        useUsageStore.getState().record("recipe", response.recipe_model, response.usage);
      }
      setMeal(response.meal);
    } catch (err) {
      setRecipeError(errorMessage(err));
    } finally {
      setWritingRecipe(false);
    }
  }

  // Removal goes through the store so save buttons elsewhere unlight too; only
  // a server-confirmed delete leaves the page.
  async function remove() {
    if (!meal) return;
    setRemoveError(null);
    const ok = await useSavedMealsStore
      .getState()
      .unsave(meal.id, saveKey(meal.source, meal.source_key));
    if (ok) void navigate("/profile");
    else setRemoveError("Couldn't remove this meal. Try again.");
  }

  return (
    <div className="max-w-2xl mx-auto">
      <Link
        to="/profile"
        className="text-sm text-forest-800 hover:text-forest-900 underline underline-offset-4 cursor-pointer"
      >
        ← Your profile
      </Link>

      <div className="mt-6">
        {meal === null && loading && (
          <p className="text-stone-600" aria-live="polite">
            Loading meal…
          </p>
        )}

        {meal === null && !loading && (
          <div role="alert" className="text-sm text-red-700">
            <span className="font-medium">Couldn't load this meal —</span>{" "}
            {error ?? "it may have been removed."}{" "}
            <button
              type="button"
              onClick={() => void load()}
              className="underline underline-offset-4 cursor-pointer"
            >
              Try again
            </button>
          </div>
        )}

        {meal && (
          <>
            <header className="mb-4">
              <h1 className="font-serif text-2xl font-semibold text-forest-900">
                Edit your copy
              </h1>
              <p className="text-sm text-stone-600 mt-1">
                Changes only touch your saved copy
                {meal.edited_at === null && " and it will no longer show as verified"}. Your
                edits aren't re-checked against the index —{" "}
                <Link to="/lookup" className="underline hover:text-stone-900">
                  check the dish
                </Link>{" "}
                to re-verify it.
              </p>
            </header>
            {meal.recipe === null && (
              <div className="mb-4 rounded border border-stone-200 bg-white p-4">
                <p className="text-sm text-stone-600 mb-2">
                  This saved meal has no recipe yet — we can write one from its
                  current ingredient list.
                </p>
                {recipeError && (
                  <p role="alert" className="text-sm text-red-700 mb-2">
                    <span className="font-medium">Couldn't write the recipe —</span>{" "}
                    {recipeError}
                  </p>
                )}
                <button
                  type="button"
                  onClick={() => void writeRecipe()}
                  disabled={writingRecipe || formDirty}
                  className="rounded bg-forest-800 hover:bg-forest-700 text-white px-4 py-2 text-sm disabled:opacity-50 enabled:cursor-pointer"
                >
                  Write the recipe
                </button>
                {formDirty && (
                  <p className="text-xs text-stone-500 mt-2">
                    Save or discard your edits first — the recipe is written
                    from the saved ingredients.
                  </p>
                )}
                {writingRecipe && (
                  <ThinkingBrawl label="Writing the recipe…" className="mt-3" />
                )}
              </div>
            )}
            <MealEditForm
              key={meal.recipe === null ? "without-recipe" : "with-recipe"}
              initial={{
                name: meal.name,
                description: meal.description,
                ingredients: meal.ingredients,
                recipe: meal.recipe,
                tags: meal.tags,
              }}
              onSave={save}
              onCancel={() => void navigate("/profile")}
              submitLabel="Save my copy"
              tagsMode="picker"
              onDirtyChange={setFormDirty}
            />
            {meal.recipe !== null && meal.recipe_model !== null && (
              <p className="mt-2 text-xs text-stone-500">
                Recipe written by <span className="font-mono">{meal.recipe_model}</span>
              </p>
            )}
            <div className="mt-6 flex flex-col items-end gap-1 border-t border-stone-200 pt-4">
              {removeError && (
                <p role="alert" className="text-xs text-red-700">
                  {removeError}
                </p>
              )}
              <button
                type="button"
                onClick={() => void remove()}
                aria-label={`Remove ${meal.name} from saved meals`}
                className="inline-flex items-center gap-1.5 rounded border border-red-700 px-3 py-1.5 text-sm text-red-700 hover:bg-red-50 cursor-pointer"
              >
                <svg
                  viewBox="0 0 24 24"
                  className="h-4 w-4"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.8"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <path d="M4 7h16" />
                  <path d="M9 7V5a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" />
                  <path d="M6 7l1 13a1 1 0 0 0 1 1h8a1 1 0 0 0 1-1l1-13" />
                  <path d="M10 11v6M14 11v6" />
                </svg>
                Remove from saved
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
