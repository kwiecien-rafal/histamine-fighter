import { useState } from "react";

import { MAX_INGREDIENT_CHARS, MAX_INGREDIENTS } from "../api/client";
import type { EditableIngredient } from "../hooks/useDishLookupFlow";

interface IngredientEditorProps {
  ingredients: EditableIngredient[];
  error: string | null;
  busy: boolean;
  onRename: (id: string, name: string) => void;
  onRemove: (id: string) => void;
  onAdd: (name: string) => boolean;
  onConfirm: () => void;
  onStartOver: () => void;
}

export function IngredientEditor({
  ingredients,
  error,
  busy,
  onRename,
  onRemove,
  onAdd,
  onConfirm,
  onStartOver,
}: IngredientEditorProps) {
  const [newName, setNewName] = useState("");
  const atCap = ingredients.length >= MAX_INGREDIENTS;
  const hasAny = ingredients.some((item) => item.name.trim());

  function submitAdd(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (onAdd(newName)) setNewName("");
  }

  return (
    <section className="rounded border border-stone-200 bg-white p-5">
      <header className="flex items-baseline justify-between mb-1">
        <h2 className="text-lg font-medium">Confirm the ingredients</h2>
        <span
          aria-live="polite"
          className={`text-xs ${atCap ? "text-amber-700" : "text-stone-500"}`}
        >
          {ingredients.length} of {MAX_INGREDIENTS}
        </span>
      </header>
      <p className="text-sm text-stone-600 mb-4">
        Fix anything that is wrong — an ingredient not on this list cannot be
        checked.
      </p>

      <fieldset disabled={busy}>
        {ingredients.length === 0 && (
          <p className="text-sm text-stone-500 mb-4">
            No ingredients were recognised in that dish. Add them yourself
            below, or start over with a different dish.
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
                className="flex-1 rounded border border-stone-300 px-3 py-1.5 text-sm focus:outline-none focus:border-emerald-700"
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
                className="px-1 text-stone-400 hover:text-red-700"
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
            placeholder="Add an ingredient"
            aria-label="New ingredient"
            maxLength={MAX_INGREDIENT_CHARS}
            disabled={atCap}
            className="flex-1 rounded border border-stone-300 px-3 py-1.5 text-sm focus:outline-none focus:border-emerald-700 disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={atCap || !newName.trim()}
            className="rounded border border-stone-300 px-3 py-1.5 text-sm text-stone-700 disabled:opacity-50"
          >
            Add
          </button>
        </form>

        {error && <p className="text-red-700 text-sm mb-4">{error}</p>}

        <footer className="flex items-center gap-3">
          <button
            type="button"
            onClick={onConfirm}
            disabled={!hasAny || busy}
            className="rounded bg-emerald-800 text-white px-4 py-2 disabled:opacity-50"
          >
            {busy ? "Checking…" : "Check safety"}
          </button>
          <button
            type="button"
            onClick={onStartOver}
            className="text-sm text-stone-600 hover:text-stone-900 underline underline-offset-4"
          >
            Start over
          </button>
        </footer>
      </fieldset>
    </section>
  );
}
