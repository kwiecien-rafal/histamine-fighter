import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  getDailyBoard,
  type DailyMealCard,
  type RevealedBoard,
} from "../api/daily";
import { browseMeals } from "../api/meals";
import { Home } from "./Home";

vi.mock("../api/daily", async (importActual) => {
  const actual = await importActual<typeof import("../api/daily")>();
  return { ...actual, getDailyBoard: vi.fn(), getDailyBoardFor: vi.fn() };
});
vi.mock("../api/meals", async (importActual) => {
  const actual = await importActual<typeof import("../api/meals")>();
  return { ...actual, browseMeals: vi.fn() };
});

const boardMock = vi.mocked(getDailyBoard);
const browseMock = vi.mocked(browseMeals);

function mealCard(name: string): DailyMealCard {
  return {
    id: `sug-${name}`,
    meal_type: "breakfast",
    model: "stub/model",
    name,
    description: "warm buckwheat with pear",
    ingredients: [],
    recipe: null,
    tags: [],
    cautioned_ingredients: [],
    trace: [],
  };
}

function revealed(): RevealedBoard {
  return {
    status: "revealed",
    date: "2026-07-03",
    model: "stub/model",
    meals: [mealCard("Buckwheat porridge")],
    usage: { calls: 1, input_tokens: 10, output_tokens: 5, total_tokens: 15, steps: [] },
  };
}

function renderHome() {
  render(
    <MemoryRouter>
      <Home />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  browseMock.mockResolvedValue({ items: [], total: 12 });
});

describe("Home", () => {
  it("shows the hero with the flagship CTAs", () => {
    boardMock.mockResolvedValue({ board: revealed(), serverOffsetMs: 0 });
    renderHome();

    expect(
      screen.getByRole("heading", { name: "Fight back against histamine." }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Check your dish" })).toHaveAttribute(
      "href",
      "/lookup",
    );
    expect(screen.getByRole("link", { name: "See today's board →" })).toHaveAttribute(
      "href",
      "/daily",
    );
  });

  it("sets the page title", () => {
    boardMock.mockResolvedValue({ board: revealed(), serverOffsetMs: 0 });
    renderHome();

    expect(document.title).toBe(
      "Histamine Fighter · Fight back against histamine intolerance",
    );
  });

  it("renders compact cards and the board link when revealed", async () => {
    boardMock.mockResolvedValue({ board: revealed(), serverOffsetMs: 0 });
    renderHome();

    expect(await screen.findByText("Buckwheat porridge")).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "See the full board, recipes and replays →" }),
    ).toHaveAttribute("href", "/daily");
  });

  it("teases a locked board with a countdown instead of meals", async () => {
    boardMock.mockResolvedValue({
      board: {
        status: "locked",
        date: "2026-07-03",
        reveal_at: new Date(Date.now() + 90 * 60_000).toISOString(),
      },
      serverOffsetMs: 0,
    });
    renderHome();

    expect(await screen.findByText(/unlocks in/i)).toBeInTheDocument();
    expect(screen.queryByText("Buckwheat porridge")).not.toBeInTheDocument();
  });

  it("shows the meal pool total", async () => {
    boardMock.mockResolvedValue({ board: revealed(), serverOffsetMs: 0 });
    renderHome();

    expect(
      await screen.findByText("12 meals in the safe corner"),
    ).toBeInTheDocument();
  });

  it("keeps the hero and falls back quietly when the board read fails", async () => {
    boardMock.mockRejectedValue(new Error("boom"));
    renderHome();

    expect(await screen.findByRole("link", { name: "See today's meals →" })).toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Fight back against histamine." }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });
});
