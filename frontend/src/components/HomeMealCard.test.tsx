import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DailyMealCard } from "../api/daily";
import { HomeMealCard } from "./HomeMealCard";

function meal(): DailyMealCard {
  return {
    meal_type: "breakfast",
    model: "stub/model",
    name: "Buckwheat porridge",
    description: "warm buckwheat with pear",
    ingredients: [{ name: "buckwheat", category: "grain" }],
    recipe: ["cook it"],
    tags: ["warm"],
    cautioned_ingredients: [],
    trace: [],
  };
}

describe("HomeMealCard", () => {
  it("shows the meal type, name and description", () => {
    render(<HomeMealCard meal={meal()} />);

    expect(screen.getByText("Breakfast")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Buckwheat porridge" })).toBeInTheDocument();
    expect(screen.getByText("warm buckwheat with pear")).toBeInTheDocument();
  });

  it("stays lean: no recipe or replay affordances", () => {
    render(<HomeMealCard meal={meal()} />);

    expect(screen.queryByText(/watch how it was composed/i)).not.toBeInTheDocument();
    expect(screen.queryByText("cook it")).not.toBeInTheDocument();
  });
});
