import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { browseMeals, type PublicMealCard, type PublicMealPage } from "../api/meals";
import { MealsBrowse } from "./MealsBrowse";

vi.mock("../api/meals", () => ({ browseMeals: vi.fn() }));

const browseMock = vi.mocked(browseMeals);

function card(overrides: Partial<PublicMealCard> = {}): PublicMealCard {
  return {
    id: "m1",
    meal_type: "breakfast",
    model: "stub/model",
    name: "Buckwheat porridge",
    description: "warm buckwheat with pear",
    tags: [],
    has_recipe: true,
    has_trace: true,
    ...overrides,
  };
}

function page(items: PublicMealCard[], total = items.length): PublicMealPage {
  return { items, total };
}

function renderBrowse() {
  render(
    <MemoryRouter>
      <MealsBrowse />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("MealsBrowse", () => {
  it("renders approved meal cards with attribution and a detail link", async () => {
    browseMock.mockResolvedValue(page([card()]));
    renderBrowse();

    expect(await screen.findByText("Buckwheat porridge")).toBeInTheDocument();
    expect(screen.getByText("stub/model")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Buckwheat porridge/ })).toHaveAttribute(
      "href",
      "/meals/m1",
    );
  });

  it("refetches when a meal-type filter is chosen", async () => {
    browseMock.mockResolvedValue(page([card()]));
    const user = userEvent.setup();
    renderBrowse();

    await screen.findByText("Buckwheat porridge");
    await user.click(screen.getByRole("button", { name: "Dinner" }));

    expect(browseMock).toHaveBeenCalledWith({ mealType: "dinner", limit: 24 });
  });

  it("appends the next page when Load more is clicked", async () => {
    browseMock.mockResolvedValueOnce(page([card({ id: "m1", name: "First" })], 2));
    browseMock.mockResolvedValueOnce(page([card({ id: "m2", name: "Second" })], 2));
    const user = userEvent.setup();
    renderBrowse();

    await screen.findByText("First");
    await user.click(screen.getByRole("button", { name: /Load more/ }));

    expect(await screen.findByText("Second")).toBeInTheDocument();
    expect(screen.getByText("First")).toBeInTheDocument();
    expect(browseMock).toHaveBeenLastCalledWith({ mealType: undefined, limit: 24, offset: 1 });
  });

  it("shows a filter-aware empty state", async () => {
    browseMock.mockResolvedValue(page([]));
    const user = userEvent.setup();
    renderBrowse();

    expect(await screen.findByText(/No meals to show/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Lunch" }));
    expect(await screen.findByText(/No approved lunch meals yet/)).toBeInTheDocument();
  });

  it("recovers from a failed first load via Try again", async () => {
    browseMock.mockRejectedValueOnce(new Error("boom"));
    browseMock.mockResolvedValueOnce(page([card()]));
    const user = userEvent.setup();
    renderBrowse();

    await user.click(await screen.findByRole("button", { name: "Try again" }));

    expect(await screen.findByText("Buckwheat porridge")).toBeInTheDocument();
  });
});
