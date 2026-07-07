import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Adaptation, DishAssessmentResponse, DishIntegrity, Verdict } from "../api/client";
import type { RecipeState } from "../hooks/useDishLookupFlow";
import { AssessmentResult } from "./AssessmentResult";

function assessment(
  integrity: DishIntegrity,
  adaptations: Adaptation[],
  verdict: Verdict = "avoid",
): DishAssessmentResponse {
  return {
    dish: "Bolognese",
    dish_style: "hearty tomato pasta dish",
    verdict,
    explanation: "Tomato is recorded as incompatible.",
    adaptations,
    advisories: [],
    integrity,
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
    usage: {
      calls: 1,
      input_tokens: 10,
      output_tokens: 5,
      total_tokens: 15,
      steps: [],
    },
  };
}

function renderResult(
  result: DishAssessmentResponse,
  recipe: RecipeState = { status: "idle" },
  onGenerateRecipe: () => void = () => {},
) {
  return render(
    <AssessmentResult
      result={result}
      resultId="res-1"
      recipe={recipe}
      onGenerateRecipe={onGenerateRecipe}
    />,
  );
}

const coreSwap: Adaptation = {
  ingredients: ["tomato"],
  role: "core",
  action: "swap",
  swap: "red pepper",
  reason: "x",
};
const coreNoSafeSwap: Adaptation = {
  ingredients: ["tomato"],
  role: "core",
  action: "no_safe_swap",
  swap: null,
  reason: "x",
};
const seasoningNoSafeSwap: Adaptation = {
  ingredients: ["black pepper"],
  role: "seasoning",
  action: "no_safe_swap",
  swap: null,
  reason: "x",
};

describe("AssessmentResult", () => {
  it("keeps the medical note next to the verdict", () => {
    renderResult(assessment("preserved", [coreSwap]));

    expect(
      screen.getByText(/informational only, not medical advice/i),
    ).toBeInTheDocument();
  });

  it("shows the dead-end callout when identity is lost", () => {
    renderResult(assessment("lost", [coreNoSafeSwap]));

    expect(screen.getByText(/a different dish may serve you better/)).toBeInTheDocument();
  });

  it("shows the softer callout when a core ingredient is altered", () => {
    renderResult(assessment("altered", [coreSwap]));

    expect(
      screen.getByText(/Prefer something closer to the original/),
    ).toBeInTheDocument();
  });

  it("shows the no-safe-fix callout for a preserved dish with an unresolved group", () => {
    renderResult(assessment("preserved", [seasoningNoSafeSwap]));

    expect(screen.getByText(/no safe fix/)).toBeInTheDocument();
  });

  it("shows no pivot callout when the dish is preserved and fully resolved", () => {
    renderResult(assessment("preserved", []));

    expect(screen.queryByText(/different dish may serve you better/)).toBeNull();
    expect(screen.queryByText(/Prefer something closer to the original/)).toBeNull();
    expect(screen.queryByText(/no safe fix/)).toBeNull();
  });

  it("offers a recipe on a safe verdict and requests one on click", () => {
    const onGenerate = vi.fn();
    renderResult(assessment("preserved", [], "safe"), { status: "idle" }, onGenerate);

    const button = screen.getByRole("button", { name: /generate a recipe/i });
    button.click();

    expect(onGenerate).toHaveBeenCalledTimes(1);
  });

  it("still offers a recipe on an avoid verdict, acknowledging the verdict", () => {
    const onGenerate = vi.fn();
    renderResult(assessment("lost", [coreNoSafeSwap], "avoid"), { status: "idle" }, onGenerate);

    const button = screen.getByRole("button", {
      name: /like the dish, in spite of our verdict\? generate a recipe/i,
    });
    button.click();

    expect(onGenerate).toHaveBeenCalledTimes(1);
  });

  it("renders the generated steps with the recipe model's badge", () => {
    renderResult(assessment("preserved", [], "safe"), {
      status: "loaded",
      steps: ["Chop the courgette.", "Simmer gently."],
      model: "recipe/model",
    });

    expect(screen.getByText("Chop the courgette.")).toBeInTheDocument();
    expect(screen.getByText("Simmer gently.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /generate a recipe/i })).toBeNull();
  });

  it("surfaces a recipe error with a retry button", () => {
    const onGenerate = vi.fn();
    renderResult(
      assessment("preserved", [], "safe"),
      { status: "error", message: "model down" },
      onGenerate,
    );

    expect(screen.getByRole("alert")).toHaveTextContent(/model down/);
    screen.getByRole("button", { name: /try the recipe again/i }).click();
    expect(onGenerate).toHaveBeenCalledTimes(1);
  });
});
