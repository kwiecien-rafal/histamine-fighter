import { useEffect, useRef, useState } from "react";

import {
  assessDish,
  generateLookupRecipe,
  MAX_INGREDIENTS,
  proposeIngredients,
  suggestAlternatives,
  type AlternativeGoal,
  type ConfirmedIngredient,
  type DishAlternative,
  type DishAssessmentResponse,
} from "../api/client";
import { QuotaError, quotaErrorCopy } from "../api/errors";
import { shouldOfferAlternatives } from "../lib/assessment";
import { useUsageStore } from "../store/usage";

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

// The recipe is another refinement of the result phase, like alternatives: the
// card offers it, the steps render beneath the assessment once written.
export type RecipeState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "loaded"; steps: string[]; model: string }
  | { status: "error"; message: string };

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
  | { phase: "unrecognized"; dish: string }
  | {
      phase: "editing";
      dish: string;
      ingredients: EditableIngredient[];
      // Null on the manual-entry path, where no model proposed anything.
      model: string | null;
      // True when the proposal came from the server-side lookup cache.
      cached: boolean;
      error: string | null;
    }
  | {
      phase: "assessing";
      dish: string;
      ingredients: EditableIngredient[];
      // Carried through so the UI keeps manual-entry affordances while busy.
      model: string | null;
    }
  | {
      phase: "result";
      dish: string;
      // Client-minted identity for this one assessment result; a lookup save
      // keys on it, so a fresh result never inherits an older save's state.
      resultId: string;
      result: DishAssessmentResponse;
      alternatives: AlternativesState;
      recipe: RecipeState;
    };

// A fetch that never reached the server rejects with a TypeError ("Failed to
// fetch"), so map that to friendly copy. Backend errors arrive as an Error
// whose message is the already-readable `detail` string, so they pass through;
// a daily-quota 429 gets its scope-aware copy (reset time, network/site caps).
const NETWORK_ERROR_MESSAGE =
  "Couldn't reach the server — check your connection and try again.";

function errorMessage(err: unknown): string {
  if (err instanceof TypeError) return NETWORK_ERROR_MESSAGE;
  if (err instanceof QuotaError) return quotaErrorCopy(err);
  return err instanceof Error ? err.message : "Unknown error";
}

// An aborted fetch is our own doing (start over, new request, unmount), never
// something to show error copy for.
function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

// A finished assessment survives navigation: the result phase (and its fetched
// alternatives) round-trips through sessionStorage, so clicking away does not
// discard a response someone paid an LLM call for. Bump the version suffix
// whenever StoredResult's shape changes, so a stale pre-deploy copy is ignored
// instead of fed into components expecting the new shape.
const RESULT_STORAGE_KEY = "hf.dish-lookup.result.v2";

// The quality bar for manual entries: with no model-vetted context at all, a
// dish name and this many ingredients are required before assessing is worth
// an LLM call. The editor reads it for its copy; confirm() is the backstop.
export const MANUAL_MIN_INGREDIENTS = 2;

interface StoredResult {
  dish: string;
  resultId: string;
  result: DishAssessmentResponse;
  cache: AlternativesCache;
  // Only a finished recipe survives; a loading or failed one restores as idle.
  recipe: { steps: string[]; model: string } | null;
}

function clearStoredResult(): void {
  try {
    sessionStorage.removeItem(RESULT_STORAGE_KEY);
  } catch {
    // Storage unavailable: survival is best-effort.
  }
}

function initialState(): FlowState {
  try {
    const raw = sessionStorage.getItem(RESULT_STORAGE_KEY);
    if (!raw) return { phase: "idle", error: null };
    const saved = JSON.parse(raw) as Partial<StoredResult>;
    if (
      typeof saved.dish !== "string" ||
      typeof saved.resultId !== "string" ||
      typeof saved.result?.verdict !== "string"
    ) {
      throw new Error("unexpected shape");
    }
    return {
      phase: "result",
      dish: saved.dish,
      resultId: saved.resultId,
      result: saved.result,
      alternatives: { status: "idle", cache: saved.cache ?? {} },
      recipe: saved.recipe ? { status: "loaded", ...saved.recipe } : { status: "idle" },
    };
  } catch {
    clearStoredResult();
    return { phase: "idle", error: null };
  }
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
  const [state, setState] = useState<FlowState>(initialState);
  // Every new request (or start over) bumps the epoch and aborts the previous
  // in-flight call; an async completion whose captured epoch is stale must not
  // touch state. This generalizes the identity trick resolveAlternatives uses,
  // and the abort genuinely stops server-side LLM spend: uvicorn cancels the
  // request task on client disconnect. Switching LLM provider mid-flight needs
  // no guard: headers are read per request, and every displayed output carries
  // the model that actually produced it.
  const epochRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  // The recipe rides beside the flow requests, not through them: fetching
  // alternatives must not cancel a recipe already being written (both are paid
  // calls), but leaving the result — start over, a new propose — cancels it.
  const recipeAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      recipeAbortRef.current?.abort();
    };
  }, []);

  // Keep the stored copy in sync with whatever the result phase holds, fetched
  // alternatives included, so a restore comes back with its goal cache intact.
  useEffect(() => {
    if (state.phase !== "result") return;
    try {
      const saved: StoredResult = {
        dish: state.dish,
        resultId: state.resultId,
        result: state.result,
        cache: state.alternatives.cache,
        recipe:
          state.recipe.status === "loaded"
            ? { steps: state.recipe.steps, model: state.recipe.model }
            : null,
      };
      sessionStorage.setItem(RESULT_STORAGE_KEY, JSON.stringify(saved));
    } catch {
      // Storage full or unavailable: survival is best-effort.
    }
  }, [state]);

  function beginRequest(): { epoch: number; signal: AbortSignal } {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    epochRef.current += 1;
    return { epoch: epochRef.current, signal: controller.signal };
  }

  function abortRecipe(): void {
    recipeAbortRef.current?.abort();
    recipeAbortRef.current = null;
  }

  async function propose(dish: string): Promise<void> {
    const trimmed = dish.trim();
    if (!trimmed) return;
    const { epoch, signal } = beginRequest();
    abortRecipe();
    clearStoredResult();
    setState({ phase: "proposing", dish: trimmed });
    try {
      const proposal = await proposeIngredients(trimmed, signal);
      if (epochRef.current !== epoch) return;
      // A cache hit made no model call, so it never shows up in the usage panel.
      if (!proposal.cached) {
        useUsageStore.getState().record("propose", proposal.model, proposal.usage);
      }
      if (!proposal.recognized || proposal.ingredients.length === 0) {
        // No dish recognisable in the text: announce it instead of dropping
        // into an empty editor that would dead-end (or worse, assess junk).
        setState({ phase: "unrecognized", dish: trimmed });
        return;
      }
      setState({
        phase: "editing",
        dish: proposal.dish,
        ingredients: proposal.ingredients.map((item) => ({
          id: crypto.randomUUID(),
          name: item.name,
          category: item.category,
        })),
        model: proposal.model,
        cached: proposal.cached,
        error: null,
      });
    } catch (err) {
      if (isAbortError(err) || epochRef.current !== epoch) return;
      setState({ phase: "idle", error: errorMessage(err) });
    }
  }

  // The propose-free path: the user types the ingredients themselves, either
  // from the entry card chooser (blank dish, named on the editor card) or
  // after an unrecognized announcement (dish prefilled).
  function startManual(dish: string): void {
    beginRequest(); // no request follows; this cancels any in-flight one
    abortRecipe();
    clearStoredResult();
    setState({
      phase: "editing",
      dish: dish.trim(),
      ingredients: [],
      model: null,
      cached: false,
      error: null,
    });
  }

  // Manual path only, enforced here: a proposed dish name is the thing the
  // model's ingredient list describes, so it stays fixed on that path.
  function renameDish(name: string): void {
    setState((current) => {
      if (current.phase !== "editing" || current.model !== null) return current;
      return { ...current, dish: name, error: null };
    });
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
    const { ingredients, model, cached } = state;
    const dish = state.dish.trim();
    const confirmed: ConfirmedIngredient[] = ingredients
      .filter((item) => item.name.trim())
      .map((item) => ({ name: item.name.trim(), category: item.category }));
    // The editor blocks the shortfall with copy; this is the backstop.
    if (!dish || confirmed.length < (model === null ? MANUAL_MIN_INGREDIENTS : 1)) return;
    // adding checks for duplicates, but a rename can still create one
    const duplicate = firstDuplicateName(confirmed.map((item) => item.name));
    if (duplicate) {
      setState({ ...state, error: `"${duplicate}" is in the list twice` });
      return;
    }
    const { epoch, signal } = beginRequest();
    setState({ phase: "assessing", dish, ingredients, model });
    try {
      const result = await assessDish(dish, confirmed, signal);
      if (epochRef.current !== epoch) return;
      if (!result.cached) {
        useUsageStore.getState().record("assess", result.model, result.usage);
      }
      setState({
        phase: "result",
        dish,
        resultId: crypto.randomUUID(),
        result,
        alternatives: { status: "idle", cache: {} },
        recipe: { status: "idle" },
      });
    } catch (err) {
      if (isAbortError(err) || epochRef.current !== epoch) return;
      // back to editing with the list intact, so the user can retry
      setState({
        phase: "editing",
        dish,
        ingredients,
        model,
        cached,
        error: errorMessage(err),
      });
    }
  }

  async function generateRecipe(): Promise<void> {
    if (state.phase !== "result") return;
    // "loaded" is final: the card renders the steps instead of the button, and
    // a repeat call would only spend a second model call on the same dish.
    if (state.recipe.status === "loading" || state.recipe.status === "loaded") return;
    const { result } = state;
    recipeAbortRef.current?.abort();
    const controller = new AbortController();
    recipeAbortRef.current = controller;
    setState((prev) =>
      prev.phase === "result" && prev.result === result
        ? { ...prev, recipe: { status: "loading" } }
        : prev,
    );
    try {
      const response = await generateLookupRecipe(
        result.dish,
        result.explanation,
        result.ingredients.map((item) => ({ name: item.name, category: null })),
        result.advisories,
        controller.signal,
      );
      useUsageStore.getState().record("recipe", response.model, response.usage);
      setState((prev) =>
        prev.phase === "result" && prev.result === result
          ? {
              ...prev,
              recipe: { status: "loaded", steps: response.steps, model: response.model },
            }
          : prev,
      );
    } catch (err) {
      if (isAbortError(err)) return;
      setState((prev) =>
        prev.phase === "result" && prev.result === result
          ? { ...prev, recipe: { status: "error", message: errorMessage(err) } }
          : prev,
      );
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
    // The dish's own safe parts anchor the suggestions toward what already worked,
    // rather than the backend reverse-engineering anchors from the avoid list.
    const preferIngredients = result.ingredients
      .filter((item) => item.safety === "safe")
      .map((item) => item.name);

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

    const { signal } = beginRequest();
    setState((prev) =>
      prev.phase === "result" && prev.result === result
        ? { ...prev, alternatives: { ...prev.alternatives, status: "loading", goal } }
        : prev,
    );
    try {
      const response = await suggestAlternatives(
        dish,
        goal,
        avoidIngredients,
        preferIngredients,
        signal,
      );
      useUsageStore.getState().record("alternatives", response.model, response.usage);
      setState((prev) =>
        resolveAlternatives(prev, result, goal, {
          suggestions: response.alternatives,
          model: response.model,
        }),
      );
    } catch (err) {
      if (isAbortError(err)) return;
      setState((prev) =>
        resolveAlternatives(prev, result, goal, { message: errorMessage(err) }),
      );
    }
  }

  function startOver(): void {
    abortRef.current?.abort();
    abortRecipe();
    epochRef.current += 1;
    clearStoredResult();
    setState({ phase: "idle", error: null });
  }

  return {
    state,
    propose,
    startManual,
    renameDish,
    renameIngredient,
    removeIngredient,
    addIngredient,
    confirm,
    requestAlternatives,
    generateRecipe,
    startOver,
  };
}
