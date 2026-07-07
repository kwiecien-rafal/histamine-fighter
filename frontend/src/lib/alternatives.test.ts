import { describe, expect, it } from "vitest";

import type { Adaptation, DishAssessmentResponse } from "../api/client";
import { goalSubtitle } from "./alternatives";

function assessment(
  adaptations: Adaptation[],
  dishStyle: string | null = "hearty tomato pasta dish",
): DishAssessmentResponse {
  return {
    dish: "Bolognese",
    dish_style: dishStyle,
    verdict: "avoid",
    explanation: "x",
    adaptations,
    advisories: [],
    integrity: "lost",
    ingredients: [],
    model: "stub/model",
    cached: false,
    usage: { calls: 1, input_tokens: 1, output_tokens: 1, total_tokens: 2, steps: [] },
  };
}

function entry(
  ingredients: string[],
  role: Adaptation["role"] = "core",
  action: Adaptation["action"] = "no_safe_swap",
): Adaptation {
  return {
    ingredients,
    role,
    action,
    swap: action === "swap" ? "stand-in" : null,
    reason: "x",
  };
}

describe("goalSubtitle", () => {
  it("names the core dead-end ingredients for same_style", () => {
    const result = assessment([entry(["tomato", "tomato paste"]), entry(["parmesan"])]);

    expect(goalSubtitle("same_style", result)).toBe(
      "another hearty tomato pasta dish, without tomato, tomato paste or parmesan",
    );
  });

  it("prefers core dead-ends over swappable ingredients", () => {
    const result = assessment([
      entry(["red wine"], "supporting", "swap"),
      entry(["parmesan"], "core", "no_safe_swap"),
    ]);

    expect(goalSubtitle("same_style", result)).toBe(
      "another hearty tomato pasta dish, without parmesan",
    );
  });

  it("falls back to all adapted ingredients when nothing is a core dead-end", () => {
    const result = assessment([entry(["red wine"], "supporting", "swap")]);

    expect(goalSubtitle("same_style", result)).toContain("without red wine");
  });

  it("copes with a missing dish_style and empty adaptations", () => {
    const result = assessment([], null);

    expect(goalSubtitle("same_style", result)).toBe("another dish like this");
  });

  it("caps the ingredient list at three names", () => {
    const result = assessment([entry(["a", "b", "c", "d"])]);

    expect(goalSubtitle("same_style", result)).toBe(
      "another hearty tomato pasta dish, without a, b or c and more",
    );
  });

  it("keeps the static subtitles for the other goals", () => {
    const result = assessment([]);

    expect(goalSubtitle("similar_flavours", result)).toMatch(/flavours/);
    expect(goalSubtitle("any_meal", result)).toMatch(/fresh start/);
  });
});
