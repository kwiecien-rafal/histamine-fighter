import { useState } from "react";

import { MAX_DISH_CHARS, MAX_INGREDIENT_CHARS, MAX_INGREDIENTS } from "../api/client";
import { MANUAL_MIN_INGREDIENTS, type EditableIngredient } from "../hooks/useDishLookupFlow";
import { ThinkingBrawl } from "./ThinkingBrawl";

interface IngredientEditorProps {
  dish: string;
  ingredients: EditableIngredient[];
  error: string | null;
  busy: boolean;
  // Present only on the manual-entry path, where the dish name is the user's
  // to write. Its presence switches the editor into manual mode: editable
  // title, and MANUAL_MIN_INGREDIENTS ingredients before the check can run.
  onRenameDish?: (name: string) => void;
  onRename: (id: string, name: string) => void;
  onRemove: (id: string) => void;
  onAdd: (name: string) => boolean;
  onConfirm: () => void;
  onStartOver: () => void;
}

export function IngredientEditor({
  dish,
  ingredients,
  error,
  busy,
  onRenameDish,
  onRename,
  onRemove,
  onAdd,
  onConfirm,
  onStartOver,
}: IngredientEditorProps) {
  const [newName, setNewName] = useState("");
  // Set when a check was attempted with the manual requirements unmet; the
  // shortfall copy only shows then, and clears itself once the list is valid.
  const [attempted, setAttempted] = useState(false);
  const atCap = ingredients.length >= MAX_INGREDIENTS;
  const manual = onRenameDish !== undefined;
  const namedCount = ingredients.filter((item) => item.name.trim()).length;
  const missingName = manual && !dish.trim();
  const missingIngredients = namedCount < (manual ? MANUAL_MIN_INGREDIENTS : 1);
  const canSubmit = !missingName && !missingIngredients;

  function submitCheck() {
    if (!canSubmit) {
      setAttempted(true);
      return;
    }
    setAttempted(false);
    onConfirm();
  }

  function submitAdd(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (onAdd(newName)) setNewName("");
  }

  return (
    <section className="rounded border border-stone-200 bg-white p-5">
      <header className="flex items-baseline justify-between mb-1">
        <h2 className="text-lg font-medium">
          {manual ? "Build your dish" : "Confirm the ingredients"}
        </h2>
        <span
          aria-live="polite"
          className={`text-xs ${atCap ? "text-amber-700" : "text-stone-500"}`}
        >
          {ingredients.length} of {MAX_INGREDIENTS} ingredients
        </span>
      </header>
      <p className="text-sm text-stone-600 mb-4">
        {manual
          ? "Name the dish and list what goes in it — an ingredient not on this list cannot be checked."
          : "Fix anything that is wrong — an ingredient not on this list cannot be checked."}
      </p>

      <fieldset disabled={busy}>
        {manual && (
          <div className="mb-4">
            <label
              htmlFor="manual-dish-name"
              className="block text-xs font-medium uppercase tracking-wide text-stone-500 mb-1"
            >
              Dish name
            </label>
            <input
              id="manual-dish-name"
              type="text"
              value={dish}
              onChange={(e) => onRenameDish(e.target.value)}
              placeholder="chicken soup"
              maxLength={MAX_DISH_CHARS}
              className="w-full rounded border border-stone-300 px-3 py-2 focus:outline-none focus:border-forest-700"
            />
          </div>
        )}
        {manual && (
          <p className="text-xs font-medium uppercase tracking-wide text-stone-500 mb-1">
            Ingredients
          </p>
        )}
        {ingredients.length === 0 && (
          <p className="text-sm text-stone-500 mb-4">
            Add each ingredient below. Plain names match our index best —
            "parmesan", not "the cheese from the fridge".
          </p>
        )}
        <ul className="flex flex-col gap-2 mb-4">
          {ingredients.map((item, index) => (
            <li key={item.id} className="flex items-center gap-2">
              <input
                type="text"
                value={item.name}
                onChange={(e) => onRename(item.id, e.target.value)}
                aria-label={`Ingredient ${index + 1}`}
                maxLength={MAX_INGREDIENT_CHARS}
                className="flex-1 rounded border border-stone-300 px-3 py-1.5 text-sm focus:outline-none focus:border-forest-700"
              />
              {item.category && (
                <span className="hidden sm:inline text-xs text-stone-500">
                  {item.category}
                </span>
              )}
              <button
                type="button"
                onClick={() => onRemove(item.id)}
                aria-label={`Remove ${item.name}`}
                className="px-1 text-stone-400 hover:text-red-700 enabled:cursor-pointer"
              >
                ✕
              </button>
            </li>
          ))}
        </ul>

        <form onSubmit={submitAdd} className="flex gap-2 mb-4">
          <input
            type="text"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="e.g. onion"
            aria-label="New ingredient"
            maxLength={MAX_INGREDIENT_CHARS}
            disabled={atCap}
            className="flex-1 rounded border border-stone-300 px-3 py-1.5 text-sm focus:outline-none focus:border-forest-700 disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={atCap || !newName.trim()}
            className="rounded border border-stone-300 px-3 py-1.5 text-sm text-stone-700 disabled:opacity-50 enabled:cursor-pointer"
          >
            Add
          </button>
        </form>

        {error && <p className="text-red-700 text-sm mb-4">{error}</p>}
        {attempted && !canSubmit && (
          <p className="text-red-700 text-sm mb-4">
            {missingName && missingIngredients
              ? `Give the dish a name and add at least ${MANUAL_MIN_INGREDIENTS} ingredients to check it.`
              : missingName
                ? "Give the dish a name to check it."
                : `Add at least ${MANUAL_MIN_INGREDIENTS} ingredients to check it.`}
          </p>
        )}

        {busy && <ThinkingBrawl label="Checking the ingredients…" className="mb-4" />}

        <footer className="flex items-center gap-3">
          <button
            type="button"
            onClick={submitCheck}
            disabled={busy || (!manual && !canSubmit)}
            className="rounded bg-forest-800 hover:bg-forest-700 text-white px-4 py-2 disabled:opacity-50 enabled:cursor-pointer"
          >
            {busy ? "Checking…" : "Check safety"}
          </button>
          {/* On the manual path the way back lives under the card (rendered by
              the page), matching the dish-name path's placement. */}
          {!manual && (
            <button
              type="button"
              onClick={onStartOver}
              className="text-sm text-stone-600 hover:text-stone-900 underline underline-offset-2 enabled:cursor-pointer"
            >
              Start over
            </button>
          )}
        </footer>
      </fieldset>
    </section>
  );
}
