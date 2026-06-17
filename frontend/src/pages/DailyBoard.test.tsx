import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getDailyBoard, type DailyMealCard, type RevealedBoard } from "../api/daily";
import { hasSeenBoard, markBoardSeen } from "../lib/daily";
import { DailyBoard } from "./DailyBoard";

vi.mock("../api/daily", async (importActual) => {
  const actual = await importActual<typeof import("../api/daily")>();
  return { ...actual, getDailyBoard: vi.fn() };
});

const getMock = vi.mocked(getDailyBoard);

const DATE = "2026-06-16";

function mealCard(): DailyMealCard {
  return {
    meal_type: "breakfast",
    name: "Buckwheat porridge",
    description: "warm buckwheat with pear",
    ingredients: [{ name: "buckwheat", category: "grain" }],
    recipe: null,
    tags: [],
  };
}

function revealed(): RevealedBoard {
  return {
    status: "revealed",
    date: DATE,
    model: "stub/model",
    meals: [mealCard()],
    trace: [
      { kind: "check", text: "step one", ingredient: null, compatibility: null },
      { kind: "check", text: "step two", ingredient: null, compatibility: null },
      { kind: "verify", text: "step three", ingredient: null, compatibility: null },
    ],
    usage: { calls: 8, input_tokens: 1600, output_tokens: 240, total_tokens: 1840, steps: [] },
  };
}

function renderBoard() {
  render(
    <MemoryRouter>
      <DailyBoard />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
});

describe("DailyBoard", () => {
  it("counts down to the reveal while locked", async () => {
    const revealAt = new Date(Date.now() + 3_600_000).toISOString();
    getMock.mockResolvedValue({ status: "locked", date: DATE, reveal_at: revealAt });
    renderBoard();

    expect(await screen.findByText("Next board in")).toBeInTheDocument();
    expect(screen.queryByText("Buckwheat porridge")).not.toBeInTheDocument();
  });

  it("explains when no board is scheduled yet", async () => {
    getMock.mockResolvedValue({ status: "locked", date: DATE, reveal_at: null });
    renderBoard();

    expect(await screen.findByText(/hasn't been set yet/)).toBeInTheDocument();
  });

  it("plays the replay first, then reveals the board on skip", async () => {
    getMock.mockResolvedValue(revealed());
    const user = userEvent.setup();
    renderBoard();

    expect(await screen.findByText("Composing today's board…")).toBeInTheDocument();
    expect(screen.queryByText("Buckwheat porridge")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Skip" }));

    expect(await screen.findByText("Buckwheat porridge")).toBeInTheDocument();
    expect(screen.getByText("stub/model")).toBeInTheDocument();
    expect(hasSeenBoard(DATE)).toBe(true);
  });

  it("skips the replay for a repeat visitor who has seen today's board", async () => {
    markBoardSeen(DATE);
    getMock.mockResolvedValue(revealed());
    renderBoard();

    expect(await screen.findByText("Buckwheat porridge")).toBeInTheDocument();
    expect(screen.queryByText("Composing today's board…")).not.toBeInTheDocument();
  });
});
