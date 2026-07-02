import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getMeal, type PublicMealDetail } from "../api/meals";
import { MealDetail } from "./MealDetail";

vi.mock("../api/meals", () => ({ getMeal: vi.fn() }));

const getMealMock = vi.mocked(getMeal);

function detail(overrides: Partial<PublicMealDetail> = {}): PublicMealDetail {
  return {
    id: "m1",
    meal_type: "breakfast",
    model: "stub/model",
    name: "Buckwheat porridge",
    description: "warm buckwheat with pear",
    ingredients: [{ name: "buckwheat", category: "grain" }],
    recipe: ["Simmer the buckwheat.", "Top with pear."],
    tags: ["warm"],
    trace: [{ kind: "verify", text: "All cleared.", ingredient: null, compatibility: null }],
    ...overrides,
  };
}

function renderDetail(id = "m1") {
  render(
    <MemoryRouter initialEntries={[`/meals/${id}`]}>
      <Routes>
        <Route path="/meals/:id" element={<MealDetail />} />
      </Routes>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("MealDetail", () => {
  it("loads the meal by id and shows its recipe", async () => {
    getMealMock.mockResolvedValue(detail());
    renderDetail();

    expect(await screen.findByText("Buckwheat porridge")).toBeInTheDocument();
    expect(screen.getByText("Top with pear.")).toBeInTheDocument();
    expect(getMealMock).toHaveBeenCalledWith("m1");
  });

  it("opens the per-card composition replay", async () => {
    getMealMock.mockResolvedValue(detail());
    const user = userEvent.setup();
    renderDetail();

    await user.click(await screen.findByRole("button", { name: "Watch how it was composed" }));

    expect(await screen.findByText("How it was composed")).toBeInTheDocument();
    expect(screen.getByText("All cleared.")).toBeInTheDocument();
  });

  it("surfaces a not-found error for an unapproved or missing meal", async () => {
    getMealMock.mockRejectedValue(new Error("Meal not found."));
    renderDetail("missing");

    expect(await screen.findByText(/Meal not found/)).toBeInTheDocument();
  });
});
