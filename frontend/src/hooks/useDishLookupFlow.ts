import { useState } from "react";

import {
  assessDish,
  MAX_INGREDIENTS,
  proposeIngredients,
  type ConfirmedIngredient,
  type DishAssessmentResponse,
} from "../api/client";

export interface EditableIngredient {
  id: string;
  name: string;
  category: string | null;
}

export type FlowState =
  | { phase: "idle"; error: string | null }
  | { phase: "proposing"; dish: string }
  | {
      phase: "editing";
      dish: string;
      ingredients: EditableIngredient[];
      model: string;
      error: string | null;
    }
  | { phase: "assessing"; dish: string; ingredients: EditableIngredient[] }
  | { phase: "result"; dish: string; result: DishAssessmentResponse };

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : "Unknown error";
}

function firstDuplicateName(names: string[]): string | null {
  const seen = new Set<string>();
  for (const name of names) {
    const key = name.trim().toLowerCase();
    if (seen.has(key)) return name.trim();
    seen.add(key);
  }
  return null;
}

export function useDishLookupFlow() {
  const [state, setState] = useState<FlowState>({ phase: "idle", error: null });

  async function propose(dish: string): Promise<void> {
    const trimmed = dish.trim();
    if (!trimmed) return;
    setState({ phase: "proposing", dish: trimmed });
    try {
      const proposal = await proposeIngredients(trimmed);
      setState({
        phase: "editing",
        dish: proposal.dish,
        ingredients: proposal.ingredients.map((item) => ({
          id: crypto.randomUUID(),
          name: item.name,
          category: item.category,
        })),
        model: proposal.model,
        error: null,
      });
    } catch (err) {
      setState({ phase: "idle", error: errorMessage(err) });
    }
  }

  function renameIngredient(id: string, name: string): void {
    setState((current) => {
      if (current.phase !== "editing") return current;
      return {
        ...current,
        // the category described the previous name, so a rename clears it;
        // any list error may describe the previous list, so it clears too
        ingredients: current.ingredients.map((item) =>
          item.id === id ? { ...item, name, category: null } : item,
        ),
        error: null,
      };
    });
  }

  function removeIngredient(id: string): void {
    setState((current) => {
      if (current.phase !== "editing") return current;
      return {
        ...current,
        ingredients: current.ingredients.filter((item) => item.id !== id),
        error: null,
      };
    });
  }

  // Unlike the rename/remove handlers, add and confirm cannot use functional
  // updates: they derive a return value or a request payload from the current
  // state, which only a render-scope read can provide.
  function addIngredient(name: string): boolean {
    const trimmed = name.trim();
    if (
      state.phase !== "editing" ||
      !trimmed ||
      state.ingredients.length >= MAX_INGREDIENTS
    ) {
      return false;
    }
    const duplicate = state.ingredients.some(
      (item) => item.name.trim().toLowerCase() === trimmed.toLowerCase(),
    );
    if (duplicate) {
      setState({ ...state, error: `"${trimmed}" is already in the list` });
      return false;
    }
    setState({
      ...state,
      ingredients: [
        ...state.ingredients,
        { id: crypto.randomUUID(), name: trimmed, category: null },
      ],
      error: null,
    });
    return true;
  }

  async function confirm(): Promise<void> {
    if (state.phase !== "editing") return;
    const { dish, ingredients, model } = state;
    const confirmed: ConfirmedIngredient[] = ingredients
      .filter((item) => item.name.trim())
      .map((item) => ({ name: item.name.trim(), category: item.category }));
    if (confirmed.length === 0) return;
    // adding checks for duplicates, but a rename can still create one
    const duplicate = firstDuplicateName(confirmed.map((item) => item.name));
    if (duplicate) {
      setState({ ...state, error: `"${duplicate}" is in the list twice` });
      return;
    }
    setState({ phase: "assessing", dish, ingredients });
    try {
      const result = await assessDish(dish, confirmed);
      setState({ phase: "result", dish, result });
    } catch (err) {
      // back to editing with the list intact, so the user can retry
      setState({
        phase: "editing",
        dish,
        ingredients,
        model,
        error: errorMessage(err),
      });
    }
  }

  function startOver(): void {
    setState({ phase: "idle", error: null });
  }

  return {
    state,
    propose,
    renameIngredient,
    removeIngredient,
    addIngredient,
    confirm,
    startOver,
  };
}
