import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Adaptation, DishAssessmentResponse, DishIntegrity } from "../api/client";
import { AssessmentResult } from "./AssessmentResult";

function assessment(
  integrity: DishIntegrity,
  adaptations: Adaptation[],
): DishAssessmentResponse {
  return {
    dish: "Bolognese",
    verdict: "avoid",
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
    usage: {
      calls: 1,
      input_tokens: 10,
      output_tokens: 5,
      total_tokens: 15,
      steps: [],
    },
  };
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
  it("shows the dead-end callout when identity is lost", () => {
    render(
      <AssessmentResult
        result={assessment("lost", [coreNoSafeSwap])}
        onStartOver={() => {}}
      />,
    );

    expect(screen.getByText(/a different dish may serve you better/)).toBeInTheDocument();
  });

  it("shows the softer callout when a core ingredient is altered", () => {
    render(
      <AssessmentResult
        result={assessment("altered", [coreSwap])}
        onStartOver={() => {}}
      />,
    );

    expect(
      screen.getByText(/Prefer something closer to the original/),
    ).toBeInTheDocument();
  });

  it("shows the no-safe-fix callout for a preserved dish with an unresolved group", () => {
    render(
      <AssessmentResult
        result={assessment("preserved", [seasoningNoSafeSwap])}
        onStartOver={() => {}}
      />,
    );

    expect(screen.getByText(/no safe fix/)).toBeInTheDocument();
  });

  it("shows no pivot callout when the dish is preserved and fully resolved", () => {
    render(
      <AssessmentResult
        result={assessment("preserved", [])}
        onStartOver={() => {}}
      />,
    );

    expect(screen.queryByText(/different dish may serve you better/)).toBeNull();
    expect(screen.queryByText(/Prefer something closer to the original/)).toBeNull();
    expect(screen.queryByText(/no safe fix/)).toBeNull();
  });
});
