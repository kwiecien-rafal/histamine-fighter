import { useState } from "react";

import {
  assessDish,
  MAX_INGREDIENTS,
  proposeIngredients,
  suggestAlternatives,
  type AlternativeGoal,
  type ConfirmedIngredient,
  type DishAlternative,
  type DishAssessmentResponse,
} from "../api/client";
import { shouldOfferAlternatives } from "../lib/assessment";

export interface EditableIngredient {
  id: string;
  name: string;
  category: string | null;
}

// One goal's fetched suggestions plus the model that produced them. The model
// rides along so the transparency badge (CLAUDE.md §6) shows on a cache hit too,
// and stays correct if the user switches provider between goals.
type GoalAlternatives = { suggestions: DishAlternative[]; model: string };

// One result's alternatives, keyed by goal: a goal fetched once is shown from
// here on a second visit, no repeat call.
export type AlternativesCache = Partial<Record<AlternativeGoal, GoalAlternatives>>;

// The pivot is a refinement of the result phase: the assessment stays on
// screen while alternatives load (or fail) beneath it. The cache rides along on
// every variant so switching goals back and forth stays free after the first try.
export type AlternativesState = { cache: AlternativesCache } & (
  | { status: "idle" }
  | { status: "loading"; goal: AlternativeGoal }
  | { status: "loaded"; goal: AlternativeGoal; suggestions: DishAlternative[]; model: string }
  | { status: "error"; goal: AlternativeGoal; message: string }
);

type AlternativesOutcome = GoalAlternatives | { message: string };

function resolveAlternatives(
  prev: FlowState,
  result: DishAssessmentResponse,
  goal: AlternativeGoal,
  outcome: AlternativesOutcome,
): FlowState {
  // Two independent guards on a response that landed late: the result-identity
  // check drops it once the user started over; the loading+goal check keeps a
  // stale goal's response from clobbering a newer one. A success is cached
  // regardless, so the superseded goal is instant if the user comes back to it.
  if (prev.phase !== "result" || prev.result !== result) return prev;
  const cache =
    "suggestions" in outcome
      ? { ...prev.alternatives.cache, [goal]: outcome }
      : prev.alternatives.cache;
  const isCurrent =
    prev.alternatives.status === "loading" && prev.alternatives.goal === goal;
  if (!isCurrent) {
    return { ...prev, alternatives: { ...prev.alternatives, cache } };
  }
  return {
    ...prev,
    alternatives:
      "suggestions" in outcome
        ? { cache, status: "loaded", goal, ...outcome }
        : { cache, status: "error", goal, message: outcome.message },
  };
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
  | {
      phase: "result";
      dish: string;
      result: DishAssessmentResponse;
      alternatives: AlternativesState;
    };

// A fetch that never reached the server rejects with a TypeError ("Failed to
// fetch"), so map that to friendly copy. Backend errors arrive as an Error
// whose message is the already-readable `detail` string, so they pass through.
const NETWORK_ERROR_MESSAGE =
  "Couldn't reach the server — check your connection and try again.";

function errorMessage(err: unknown): string {
  if (err instanceof TypeError) return NETWORK_ERROR_MESSAGE;
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
      setState({
        phase: "result",
        dish,
        result,
        alternatives: { status: "idle", cache: {} },
      });
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

  async function requestAlternatives(goal: AlternativeGoal): Promise<void> {
    if (state.phase !== "result" || !shouldOfferAlternatives(state.result)) return;
    const { dish, result } = state;
    // Exclude exactly the avoid-level ingredients the adaptations cover. Every
    // case that passes the gate has at least one adaptation entry (a core change
    // or a no-safe-swap), so this list is never empty. Reading them off a
    // separate filter would be a second, drift-prone notion of "avoid-level".
    const avoidIngredients = result.adaptations.flatMap((entry) => entry.ingredients);

    const cached = state.alternatives.cache[goal];
    if (cached) {
      // Already fetched for this result: show it straight away, no second call.
      setState((prev) =>
        prev.phase === "result" && prev.result === result
          ? {
              ...prev,
              alternatives: {
                cache: prev.alternatives.cache,
                status: "loaded",
                goal,
                ...cached,
              },
            }
          : prev,
      );
      return;
    }

    setState((prev) =>
      prev.phase === "result" && prev.result === result
        ? { ...prev, alternatives: { ...prev.alternatives, status: "loading", goal } }
        : prev,
    );
    try {
      const response = await suggestAlternatives(dish, goal, avoidIngredients);
      setState((prev) =>
        resolveAlternatives(prev, result, goal, {
          suggestions: response.alternatives,
          model: response.model,
        }),
      );
    } catch (err) {
      setState((prev) =>
        resolveAlternatives(prev, result, goal, { message: errorMessage(err) }),
      );
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
    requestAlternatives,
    startOver,
  };
}
