import { useState } from "react";

import { EditRejectedError, errorMessage, type MealEdit } from "../api/admin";
import { MAX_DISH_CHARS, MAX_INGREDIENT_CHARS, MAX_INGREDIENTS } from "../api/client";

interface MealEditFormProps {
  initial: MealEdit;
  // Persists the edit; throws EditRejectedError on a failed index re-check (422).
  onSave: (edit: MealEdit) => Promise<void>;
  onCancel: () => void;
}

interface Row {
  name: string;
  category: string;
}

// Shared edit form for a pending curated meal or daily suggestion. It edits the five
// content fields the backend allows, re-runs the same index check on save, and surfaces
// the 422 blocker list so the admin sees exactly what to fix rather than a bare status.
export function MealEditForm({ initial, onSave, onCancel }: MealEditFormProps) {
  const [name, setName] = useState(initial.name);
  const [description, setDescription] = useState(initial.description);
  const [rows, setRows] = useState<Row[]>(() =>
    initial.ingredients.length > 0
      ? initial.ingredients.map((i) => ({ name: i.name, category: i.category ?? "" }))
      : [{ name: "", category: "" }],
  );
  const [recipeText, setRecipeText] = useState((initial.recipe ?? []).join("\n"));
  const [tagsText, setTagsText] = useState(initial.tags.join(", "));
  const [saving, setSaving] = useState(false);
  const [rejection, setRejection] = useState<EditRejectedError["rejection"] | null>(null);
  const [error, setError] = useState<string | null>(null);

  function setRow(index: number, patch: Partial<Row>) {
    setRows((current) => current.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setRejection(null);
    setError(null);
    const edit: MealEdit = {
      name: name.trim(),
      description: description.trim(),
      ingredients: rows
        .map((row) => ({ name: row.name.trim(), category: row.category.trim() || null }))
        .filter((i) => i.name),
      recipe: lines(recipeText),
      tags: tagsText
        .split(",")
        .map((tag) => tag.trim())
        .filter(Boolean),
    };
    try {
      await onSave(edit);
    } catch (err) {
      if (err instanceof EditRejectedError) setRejection(err.rejection);
      else setError(errorMessage(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form
      onSubmit={(e) => void submit(e)}
      className="mt-3 rounded border border-stone-200 bg-stone-50 p-4 space-y-3"
    >
      <Field label="Name">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          maxLength={MAX_DISH_CHARS}
          required
          className="w-full rounded border border-stone-300 px-2.5 py-1.5 text-sm focus:outline-none focus:border-emerald-700"
        />
      </Field>
      <Field label="Description">
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={2}
          required
          className="w-full rounded border border-stone-300 px-2.5 py-1.5 text-sm focus:outline-none focus:border-emerald-700"
        />
      </Field>

      <Field label="Ingredients">
        <ul className="space-y-1.5">
          {rows.map((row, index) => (
            <li key={index} className="flex gap-1.5">
              <input
                value={row.name}
                onChange={(e) => setRow(index, { name: e.target.value })}
                placeholder="name"
                maxLength={MAX_INGREDIENT_CHARS}
                className="flex-1 rounded border border-stone-300 px-2 py-1 text-sm focus:outline-none focus:border-emerald-700"
              />
              <input
                value={row.category}
                onChange={(e) => setRow(index, { category: e.target.value })}
                placeholder="category (optional)"
                maxLength={MAX_INGREDIENT_CHARS}
                className="flex-1 rounded border border-stone-300 px-2 py-1 text-sm focus:outline-none focus:border-emerald-700"
              />
              <button
                type="button"
                onClick={() => setRows((c) => c.filter((_, i) => i !== index))}
                aria-label="Remove ingredient"
                className="px-2 text-stone-400 hover:text-red-700 cursor-pointer"
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
        <button
          type="button"
          onClick={() => setRows((c) => (c.length < MAX_INGREDIENTS ? [...c, { name: "", category: "" }] : c))}
          className="mt-1.5 text-xs text-emerald-800 hover:text-emerald-900 cursor-pointer"
        >
          + Add ingredient
        </button>
      </Field>

      <Field label="Recipe (one step per line)">
        <textarea
          value={recipeText}
          onChange={(e) => setRecipeText(e.target.value)}
          rows={4}
          className="w-full rounded border border-stone-300 px-2.5 py-1.5 text-sm focus:outline-none focus:border-emerald-700"
        />
      </Field>
      <Field label="Tags (comma separated)">
        <input
          value={tagsText}
          onChange={(e) => setTagsText(e.target.value)}
          className="w-full rounded border border-stone-300 px-2.5 py-1.5 text-sm focus:outline-none focus:border-emerald-700"
        />
      </Field>

      {rejection && (
        <div role="alert" className="text-sm text-red-700">
          <p className="font-medium">{rejection.message}</p>
          {rejection.blockers.length > 0 && (
            <p>Flagged ingredients: {rejection.blockers.join(", ")}.</p>
          )}
          {rejection.recipe_flags.length > 0 && (
            <p>Flagged in the recipe: {rejection.recipe_flags.join(", ")}.</p>
          )}
        </div>
      )}
      {error && (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}

      <div className="flex gap-2">
        <button
          type="submit"
          disabled={saving}
          className="rounded bg-emerald-800 text-white px-3 py-1.5 text-sm disabled:opacity-50 enabled:cursor-pointer"
        >
          {saving ? "Saving…" : "Save changes"}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={saving}
          className="rounded border border-stone-300 px-3 py-1.5 text-sm text-stone-700 hover:bg-white disabled:opacity-50 enabled:cursor-pointer"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="text-xs uppercase tracking-wide text-stone-500">{label}</span>
      <div className="mt-1">{children}</div>
    </label>
  );
}

// Split a textarea into trimmed non-empty lines, or null when there are none, matching
// the recipe shape the backend stores (a list of steps or null).
function lines(text: string): string[] | null {
  const cleaned = text
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  return cleaned.length > 0 ? cleaned : null;
}
