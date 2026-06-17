import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  assessDish,
  proposeIngredients,
  suggestAlternatives,
  type AlternativeGoal,
  type DishAlternativesResponse,
  type DishAssessmentResponse,
  type IngredientProposalResponse,
  type LLMUsage,
} from "../api/client";
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
}));

const proposeMock = vi.mocked(proposeIngredients);
const assessMock = vi.mocked(assessDish);
const alternativesMock = vi.mocked(suggestAlternatives);

const proposal: IngredientProposalResponse = {
  dish: "Bolognese",
  ingredients: [{ name: "tomato", category: "vegetable" }],
  model: "stub/model",
  usage,
};

function lostAssessment(): DishAssessmentResponse {
  return {
    dish: "Bolognese",
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

    expect(alternativesMock).toHaveBeenCalledWith("Bolognese", "any_meal", ["tomato"], []);
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

    expect(alternativesMock).toHaveBeenCalledWith("Bolognese", "any_meal", ["tomato"], [
      "olive oil",
      "basil",
    ]);
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
});
