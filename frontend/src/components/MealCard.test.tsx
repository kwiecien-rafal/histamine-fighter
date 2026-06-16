import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DailyMealCard } from "../api/daily";
import { MealCard } from "./MealCard";

function meal(): DailyMealCard {
  return {
    meal_type: "breakfast",
    name: "Buckwheat porridge",
    description: "warm buckwheat with pear and a drizzle of maple",
    ingredients: [{ name: "buckwheat", category: "grain" }],
    recipe: ["Simmer the buckwheat.", "Top with pear."],
    tags: ["warm"],
  };
}

describe("MealCard", () => {
  it("renders the meal with its branded type label and a verified badge", () => {
    render(<MealCard meal={meal()} />);

    expect(screen.getByText("Buckwheat porridge")).toBeInTheDocument();
    expect(screen.getByText("Breakfast")).toBeInTheDocument();
    expect(screen.getByText("✓ Verified")).toBeInTheDocument();
    expect(screen.getByText(/grain/)).toBeInTheDocument();
  });

  it("shows the recipe steps", () => {
    render(<MealCard meal={meal()} />);

    expect(screen.getByText(/Recipe \(2 steps\)/)).toBeInTheDocument();
    expect(screen.getByText("Top with pear.")).toBeInTheDocument();
  });
});
