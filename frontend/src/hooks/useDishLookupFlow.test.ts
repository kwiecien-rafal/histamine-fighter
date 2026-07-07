import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  assessDish,
  generateLookupRecipe,
  proposeIngredients,
  suggestAlternatives,
  type AlternativeGoal,
  type DishAlternativesResponse,
  type DishAssessmentResponse,
  type IngredientProposalResponse,
  type LLMUsage,
  type RecipeGenerationResponse,
} from "../api/client";
import { useUsageStore } from "../store/usage";
import { useDishLookupFlow } from "./useDishLookupFlow";

const usage: LLMUsage = {
  calls: 1,
  input_tokens: 10,
  output_tokens: 5,
  total_tokens: 15,
  steps: [
    { step: "propose", input_tokens: 10, output_tokens: 5, total_tokens: 15, reported: true },
  ],
};

vi.mock("../api/client", () => ({
  MAX_INGREDIENTS: 25,
  proposeIngredients: vi.fn(),
  assessDish: vi.fn(),
  suggestAlternatives: vi.fn(),
  generateLookupRecipe: vi.fn(),
}));

const proposeMock = vi.mocked(proposeIngredients);
const assessMock = vi.mocked(assessDish);
const alternativesMock = vi.mocked(suggestAlternatives);
const recipeMock = vi.mocked(generateLookupRecipe);

const proposal: IngredientProposalResponse = {
  dish: "Bolognese",
  recognized: true,
  ingredients: [{ name: "tomato", category: "vegetable" }],
  model: "stub/model",
  cached: false,
  usage,
};

function lostAssessment(): DishAssessmentResponse {
  return {
    dish: "Bolognese",
    dish_style: "hearty tomato pasta dish",
    verdict: "avoid",
    explanation: "Tomato is recorded as incompatible.",
    adaptations: [
      { ingredients: ["tomato"], role: "core", action: "no_safe_swap", swap: null, reason: "x" },
    ],
    advisories: [],
    integrity: "lost",
    ingredients: [
      {
        name: "tomato",
        safety: "avoid",
        found: true,
        error: false,
        matched_on: "ingredient",
        mechanisms: ["high_histamine"],
      },
    ],
    model: "stub/model",
    cached: false,
    usage,
  };
}

function altResponse(goal: AlternativeGoal, names: string[]): DishAlternativesResponse {
  return {
    dish: "Bolognese",
    goal,
    alternatives: names.map((name) => ({ name, pitch: "", source: "generated" as const })),
    model: "stub/model",
    usage,
  };
}

function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

async function driveToResult(assessment: DishAssessmentResponse = lostAssessment()) {
  proposeMock.mockResolvedValueOnce(proposal);
  assessMock.mockResolvedValueOnce(assessment);
  const view = renderHook(() => useDishLookupFlow());
  await act(async () => {
    await view.result.current.propose("Bolognese");
  });
  await act(async () => {
    await view.result.current.confirm();
  });
  return view;
}

beforeEach(() => {
  vi.clearAllMocks();
  sessionStorage.clear();
});

describe("useDishLookupFlow", () => {
  it("moves to editing after a successful propose", async () => {
    proposeMock.mockResolvedValueOnce(proposal);
    const { result } = renderHook(() => useDishLookupFlow());

    await act(async () => {
      await result.current.propose("Bolognese");
    });

    expect(result.current.state.phase).toBe("editing");
  });

  it("announces an unrecognized dish instead of opening the editor", async () => {
    proposeMock.mockResolvedValueOnce({ ...proposal, recognized: false, ingredients: [] });
    const { result } = renderHook(() => useDishLookupFlow());

    await act(async () => {
      await result.current.propose("asdkjhqwe");
    });

    expect(result.current.state).toEqual({ phase: "unrecognized", dish: "asdkjhqwe" });
  });

  it("treats an empty proposed list as unrecognized", async () => {
    proposeMock.mockResolvedValueOnce({ ...proposal, ingredients: [] });
    const { result } = renderHook(() => useDishLookupFlow());

    await act(async () => {
      await result.current.propose("qwzzt");
    });

    expect(result.current.state.phase).toBe("unrecognized");
  });

  it("drops a propose response that lands after start over", async () => {
    const slow = deferred<IngredientProposalResponse>();
    proposeMock.mockReturnValueOnce(slow.promise);
    const { result } = renderHook(() => useDishLookupFlow());

    let pending!: Promise<void>;
    act(() => {
      pending = result.current.propose("Bolognese");
    });
    act(() => {
      result.current.startOver();
    });
    slow.resolve(proposal);
    await act(async () => {
      await pending;
    });

    expect(result.current.state).toEqual({ phase: "idle", error: null });
  });

  it("shows no error copy when a request is aborted", async () => {
    proposeMock.mockRejectedValueOnce(new DOMException("aborted", "AbortError"));
    const { result } = renderHook(() => useDishLookupFlow());

    await act(async () => {
      await result.current.propose("Bolognese");
    });

    expect(result.current.state.phase).toBe("proposing");
  });

  it("restores a stored result on mount, alternatives cache and recipe included", () => {
    const cached = { suggestions: [{ name: "Risotto", pitch: "", source: "generated" }], model: "stub/model" };
    sessionStorage.setItem(
      "hf.dish-lookup.result.v2",
      JSON.stringify({
        dish: "Bolognese",
        resultId: "res-1",
        result: lostAssessment(),
        cache: { any_meal: cached },
        recipe: { steps: ["Chop.", "Simmer."], model: "recipe/model" },
      }),
    );

    const { result } = renderHook(() => useDishLookupFlow());

    const state = result.current.state;
    expect(state.phase).toBe("result");
    if (state.phase !== "result") return;
    expect(state.dish).toBe("Bolognese");
    expect(state.resultId).toBe("res-1");
    expect(state.result.verdict).toBe("avoid");
    expect(state.alternatives).toEqual({ status: "idle", cache: { any_meal: cached } });
    expect(state.recipe).toEqual({
      status: "loaded",
      steps: ["Chop.", "Simmer."],
      model: "recipe/model",
    });
  });

  it("restores a stored result without a recipe as recipe idle", () => {
    sessionStorage.setItem(
      "hf.dish-lookup.result.v2",
      JSON.stringify({
        dish: "Bolognese",
        resultId: "res-1",
        result: lostAssessment(),
        cache: {},
        recipe: null,
      }),
    );

    const { result } = renderHook(() => useDishLookupFlow());

    expect(result.current.state.phase).toBe("result");
    if (result.current.state.phase !== "result") return;
    expect(result.current.state.recipe).toEqual({ status: "idle" });
  });

  it("ignores a pre-resultId stored copy, so old saves cannot key new results", () => {
    sessionStorage.setItem(
      "hf.dish-lookup.result.v2",
      JSON.stringify({ dish: "Bolognese", result: lostAssessment(), cache: {} }),
    );

    const { result } = renderHook(() => useDishLookupFlow());

    expect(result.current.state).toEqual({ phase: "idle", error: null });
  });

  it("falls back to idle on corrupt stored state", () => {
    sessionStorage.setItem("hf.dish-lookup.result.v2", "{not json");

    const { result } = renderHook(() => useDishLookupFlow());

    expect(result.current.state).toEqual({ phase: "idle", error: null });
    expect(sessionStorage.getItem("hf.dish-lookup.result.v2")).toBeNull();
  });

  it("persists a finished result and clears it on start over", async () => {
    const { result } = await driveToResult();

    expect(sessionStorage.getItem("hf.dish-lookup.result.v2")).not.toBeNull();

    act(() => {
      result.current.startOver();
    });

    expect(sessionStorage.getItem("hf.dish-lookup.result.v2")).toBeNull();
  });

  it("startManual opens an empty editor without calling propose", () => {
    const { result } = renderHook(() => useDishLookupFlow());

    act(() => {
      result.current.startManual("  Grandma's stew  ");
    });

    expect(result.current.state).toEqual({
      phase: "editing",
      dish: "Grandma's stew",
      ingredients: [],
      model: null,
      cached: false,
      error: null,
    });
    expect(proposeMock).not.toHaveBeenCalled();
  });

  it("startManual accepts an empty dish, to be named on the editor card", () => {
    const { result } = renderHook(() => useDishLookupFlow());

    act(() => {
      result.current.startManual("");
    });

    expect(result.current.state.phase).toBe("editing");
    if (result.current.state.phase !== "editing") return;
    expect(result.current.state.dish).toBe("");
    expect(result.current.state.model).toBeNull();
  });

  it("renameDish updates the dish while editing", () => {
    const { result } = renderHook(() => useDishLookupFlow());

    act(() => {
      result.current.startManual("");
    });
    act(() => {
      result.current.renameDish("Grandma's stew");
    });

    expect(result.current.state.phase).toBe("editing");
    if (result.current.state.phase !== "editing") return;
    expect(result.current.state.dish).toBe("Grandma's stew");
  });

  it("renameDish is ignored on the proposed path, where the name is the model's", async () => {
    proposeMock.mockResolvedValueOnce(proposal);
    const { result } = renderHook(() => useDishLookupFlow());

    await act(async () => {
      await result.current.propose("Bolognese");
    });
    act(() => {
      result.current.renameDish("Something else");
    });

    expect(result.current.state.phase).toBe("editing");
    if (result.current.state.phase !== "editing") return;
    expect(result.current.state.dish).toBe("Bolognese");
  });

  it("ignores a stored result under the old unversioned key", () => {
    sessionStorage.setItem(
      "hf.dish-lookup.result",
      JSON.stringify({ dish: "Bolognese", result: lostAssessment(), cache: {} }),
    );

    const { result } = renderHook(() => useDishLookupFlow());

    expect(result.current.state).toEqual({ phase: "idle", error: null });
  });

  it("refuses to assess a manual entry without a name and two ingredients", async () => {
    const { result } = renderHook(() => useDishLookupFlow());

    act(() => {
      result.current.startManual("");
    });
    act(() => {
      result.current.addIngredient("carrot");
    });
    act(() => {
      result.current.addIngredient("rice");
    });
    await act(async () => {
      await result.current.confirm(); // two ingredients, still no name
    });
    expect(assessMock).not.toHaveBeenCalled();

    act(() => {
      result.current.renameDish("Carrot rice");
    });
    act(() => {
      result.current.removeIngredient(
        result.current.state.phase === "editing"
          ? result.current.state.ingredients[0].id
          : "",
      );
    });
    await act(async () => {
      await result.current.confirm(); // named, but one ingredient
    });
    expect(assessMock).not.toHaveBeenCalled();
  });

  it("assesses a named manual entry with two ingredients", async () => {
    assessMock.mockResolvedValueOnce(lostAssessment());
    const { result } = renderHook(() => useDishLookupFlow());

    act(() => {
      result.current.startManual("");
    });
    act(() => {
      result.current.renameDish("Carrot rice");
    });
    act(() => {
      result.current.addIngredient("carrot");
    });
    act(() => {
      result.current.addIngredient("rice");
    });
    await act(async () => {
      await result.current.confirm();
    });

    expect(assessMock).toHaveBeenCalledWith(
      "Carrot rice",
      [
        { name: "carrot", category: null },
        { name: "rice", category: null },
      ],
      expect.any(AbortSignal),
    );
    expect(result.current.state.phase).toBe("result");
  });

  it("skips the usage panel for cached responses", async () => {
    proposeMock.mockResolvedValueOnce({ ...proposal, cached: true });
    assessMock.mockResolvedValueOnce({ ...lostAssessment(), cached: true });
    const callsBefore = useUsageStore.getState().totals.calls;
    const { result } = renderHook(() => useDishLookupFlow());

    await act(async () => {
      await result.current.propose("Bolognese");
    });
    await act(async () => {
      await result.current.confirm();
    });

    expect(result.current.state.phase).toBe("result");
    expect(useUsageStore.getState().totals.calls).toBe(callsBefore);
  });

  it("assesses the confirmed list and lands on the result", async () => {
    const { result } = await driveToResult();

    expect(result.current.state.phase).toBe("result");
  });

  it("does not request alternatives when nothing is unresolved", async () => {
    const resolved = lostAssessment();
    resolved.adaptations = [
      { ingredients: ["onion"], role: "supporting", action: "swap", swap: "leek", reason: "x" },
    ];
    resolved.integrity = "preserved";
    const { result } = await driveToResult(resolved);

    await act(async () => {
      await result.current.requestAlternatives("any_meal");
    });

    expect(alternativesMock).not.toHaveBeenCalled();
  });

  it("offers alternatives when a core ingredient was altered, with no dead end", async () => {
    const altered = lostAssessment();
    altered.adaptations = [
      { ingredients: ["tomato"], role: "core", action: "swap", swap: "red pepper", reason: "x" },
    ];
    altered.integrity = "altered";
    const { result } = await driveToResult(altered);
    alternativesMock.mockResolvedValueOnce(altResponse("any_meal", ["Courgette Pasta"]));

    await act(async () => {
      await result.current.requestAlternatives("any_meal");
    });

    expect(alternativesMock).toHaveBeenCalledWith(
      "Bolognese",
      "any_meal",
      ["tomato"],
      [],
      expect.any(AbortSignal),
    );
  });

  it("passes the dish's safe ingredients as anchors", async () => {
    const assessment = lostAssessment();
    assessment.ingredients = [
      ...assessment.ingredients,
      {
        name: "olive oil",
        safety: "safe",
        found: true,
        error: false,
        matched_on: "ingredient",
        mechanisms: [],
      },
      {
        name: "basil",
        safety: "safe",
        found: true,
        error: false,
        matched_on: "ingredient",
        mechanisms: [],
      },
    ];
    const { result } = await driveToResult(assessment);
    alternativesMock.mockResolvedValueOnce(altResponse("any_meal", ["Courgette Pasta"]));

    await act(async () => {
      await result.current.requestAlternatives("any_meal");
    });

    expect(alternativesMock).toHaveBeenCalledWith(
      "Bolognese",
      "any_meal",
      ["tomato"],
      ["olive oil", "basil"],
      expect.any(AbortSignal),
    );
  });

  it("loads alternatives for a goal", async () => {
    const { result } = await driveToResult();
    alternativesMock.mockResolvedValueOnce(altResponse("any_meal", ["Courgette Pasta"]));

    await act(async () => {
      await result.current.requestAlternatives("any_meal");
    });

    const { state } = result.current;
    if (state.phase !== "result" || state.alternatives.status !== "loaded") {
      throw new Error(`unexpected state: ${state.phase}`);
    }
    expect(state.alternatives.suggestions).toEqual([
      { name: "Courgette Pasta", pitch: "", source: "generated" },
    ]);
  });

  it("serves a re-picked goal from cache without refetching", async () => {
    const { result } = await driveToResult();
    alternativesMock.mockResolvedValue(altResponse("any_meal", ["Courgette Pasta"]));

    await act(async () => {
      await result.current.requestAlternatives("any_meal");
    });
    await act(async () => {
      await result.current.requestAlternatives("same_style");
    });
    await act(async () => {
      await result.current.requestAlternatives("any_meal");
    });

    expect(alternativesMock).toHaveBeenCalledTimes(2);
  });

  it("drops a stale alternatives response after start over", async () => {
    const { result } = await driveToResult();
    const pending = deferred<DishAlternativesResponse>();
    alternativesMock.mockReturnValueOnce(pending.promise);

    act(() => {
      void result.current.requestAlternatives("any_meal");
    });
    act(() => {
      result.current.startOver();
    });
    expect(result.current.state.phase).toBe("idle");

    await act(async () => {
      pending.resolve(altResponse("any_meal", ["Courgette Pasta"]));
      await pending.promise;
    });

    expect(result.current.state.phase).toBe("idle");
  });

  it("caches a superseded goal without flipping the visible status", async () => {
    const { result } = await driveToResult();
    const slow = deferred<DishAlternativesResponse>();
    alternativesMock.mockReturnValueOnce(slow.promise);
    alternativesMock.mockResolvedValueOnce(altResponse("same_style", ["Risotto"]));

    act(() => {
      void result.current.requestAlternatives("any_meal");
    });
    await act(async () => {
      await result.current.requestAlternatives("same_style");
    });
    await act(async () => {
      slow.resolve(altResponse("any_meal", ["Caponata"]));
      await slow.promise;
    });

    const { state } = result.current;
    if (state.phase !== "result" || state.alternatives.status !== "loaded") {
      throw new Error(`unexpected state: ${state.phase}`);
    }
    // The newer goal stays on screen; the stale one only filled the cache.
    expect(state.alternatives.goal).toBe("same_style");
    expect(state.alternatives.cache.any_meal).toEqual({
      suggestions: [{ name: "Caponata", pitch: "", source: "generated" }],
      model: "stub/model",
    });

    await act(async () => {
      await result.current.requestAlternatives("any_meal");
    });
    expect(alternativesMock).toHaveBeenCalledTimes(2);
  });

  it("surfaces an alternatives error", async () => {
    const { result } = await driveToResult();
    alternativesMock.mockRejectedValueOnce(new Error("network down"));

    await act(async () => {
      await result.current.requestAlternatives("any_meal");
    });

    const { state } = result.current;
    if (state.phase !== "result" || state.alternatives.status !== "error") {
      throw new Error(`unexpected state: ${state.phase}`);
    }
    expect(state.alternatives.message).toBe("network down");
  });

  it("mints a fresh resultId per result, so a new result never inherits a save", async () => {
    const first = await driveToResult();
    const firstState = first.result.current.state;
    if (firstState.phase !== "result") throw new Error("expected a result");

    const second = await driveToResult();
    const secondState = second.result.current.state;
    if (secondState.phase !== "result") throw new Error("expected a result");

    expect(firstState.resultId).not.toBe(secondState.resultId);
  });

  it("generates a recipe for the result and persists it to storage", async () => {
    const { result } = await driveToResult();
    const generation: RecipeGenerationResponse = {
      steps: ["Chop.", "Simmer."],
      model: "recipe/model",
      usage,
    };
    recipeMock.mockResolvedValueOnce(generation);

    await act(async () => {
      await result.current.generateRecipe();
    });

    const { state } = result.current;
    if (state.phase !== "result") throw new Error("expected a result");
    expect(state.recipe).toEqual({
      status: "loaded",
      steps: ["Chop.", "Simmer."],
      model: "recipe/model",
    });
    expect(recipeMock).toHaveBeenCalledWith(
      "Bolognese",
      "Tomato is recorded as incompatible.",
      [{ name: "tomato", category: null }],
      [],
      expect.any(AbortSignal),
    );
    const stored = JSON.parse(sessionStorage.getItem("hf.dish-lookup.result.v2") ?? "{}") as {
      recipe: unknown;
    };
    expect(stored.recipe).toEqual({ steps: ["Chop.", "Simmer."], model: "recipe/model" });
  });

  it("does not re-request a recipe that is already loaded", async () => {
    const { result } = await driveToResult();
    recipeMock.mockResolvedValue({ steps: ["Chop."], model: "recipe/model", usage });

    await act(async () => {
      await result.current.generateRecipe();
    });
    await act(async () => {
      await result.current.generateRecipe();
    });

    expect(recipeMock).toHaveBeenCalledTimes(1);
  });

  it("surfaces a recipe error and allows a retry", async () => {
    const { result } = await driveToResult();
    recipeMock.mockRejectedValueOnce(new Error("model down"));
    recipeMock.mockResolvedValueOnce({ steps: ["Chop."], model: "recipe/model", usage });

    await act(async () => {
      await result.current.generateRecipe();
    });
    let { state } = result.current;
    if (state.phase !== "result") throw new Error("expected a result");
    expect(state.recipe).toEqual({ status: "error", message: "model down" });

    await act(async () => {
      await result.current.generateRecipe();
    });
    ({ state } = result.current);
    if (state.phase !== "result") throw new Error("expected a result");
    expect(state.recipe.status).toBe("loaded");
  });

  it("drops a recipe response that lands after start over", async () => {
    const { result } = await driveToResult();
    const slow = deferred<RecipeGenerationResponse>();
    recipeMock.mockReturnValueOnce(slow.promise);

    act(() => {
      void result.current.generateRecipe();
    });
    act(() => {
      result.current.startOver();
    });
    await act(async () => {
      slow.resolve({ steps: ["Chop."], model: "recipe/model", usage });
      await slow.promise;
    });

    expect(result.current.state).toEqual({ phase: "idle", error: null });
  });
});
