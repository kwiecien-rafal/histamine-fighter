import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { DailyMealCard } from "../api/daily";
import { MANUAL_MODEL } from "../api/domain";
import { MealCard } from "./MealCard";

function meal(): DailyMealCard {
  return {
    meal_type: "breakfast",
    model: "stub/model",
    name: "Buckwheat porridge",
    description: "warm buckwheat with pear and a drizzle of maple",
    ingredients: [{ name: "buckwheat", category: "grain" }],
    recipe: ["Simmer the buckwheat.", "Top with pear."],
    tags: ["warm"],
    trace: [
      { kind: "verify", text: "All ingredients cleared.", ingredient: null, compatibility: null },
    ],
  };
}

describe("MealCard", () => {
  it("renders the meal with its branded type label, verified badge, and model", () => {
    render(<MealCard meal={meal()} />);

    expect(screen.getByText("Buckwheat porridge")).toBeInTheDocument();
    expect(screen.getByText("Breakfast")).toBeInTheDocument();
    expect(screen.getByText("✓ Verified")).toBeInTheDocument();
    expect(screen.getByText(/grain/)).toBeInTheDocument();
    expect(screen.getByText("stub/model")).toBeInTheDocument();
  });

  it("shows the recipe steps", () => {
    render(<MealCard meal={meal()} />);

    expect(screen.getByText(/Recipe \(2 steps\)/)).toBeInTheDocument();
    expect(screen.getByText("Top with pear.")).toBeInTheDocument();
  });

  it("opens the per-card composition replay", async () => {
    const user = userEvent.setup();
    render(<MealCard meal={meal()} />);

    await user.click(screen.getByRole("button", { name: "Watch how it was composed" }));

    expect(screen.getByText("How it was composed")).toBeInTheDocument();
    expect(screen.getByText("All ingredients cleared.")).toBeInTheDocument();
  });

  it("hides the replay for a hand-authored meal with no recorded trace", () => {
    render(<MealCard meal={{ ...meal(), model: MANUAL_MODEL, trace: [] }} />);

    expect(screen.getByText("Curated by admin")).toBeInTheDocument();
    expect(screen.queryByText("Composed by")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Watch how it was composed" }),
    ).not.toBeInTheDocument();
  });
});
