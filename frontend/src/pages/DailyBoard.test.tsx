import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getDailyBoard,
  type DailyBoard as DailyBoardData,
  type DailyMealCard,
  type RevealedBoard,
} from "../api/daily";
import { hasSeenBoard, markBoardSeen } from "../lib/daily";
import { DailyBoard } from "./DailyBoard";

vi.mock("../api/daily", async (importActual) => {
  const actual = await importActual<typeof import("../api/daily")>();
  return { ...actual, getDailyBoard: vi.fn() };
});

const getMock = vi.mocked(getDailyBoard);

const DATE = "2026-06-16";

// The widest a single poll can be scheduled out (LockedView's base + max jitter);
// advancing by it guarantees the next poll has fired regardless of the jitter.
const POLL_WINDOW_MS = 30_000;

// The hook reads { board, serverOffsetMs }; tests run with no clock skew.
function resolved(board: DailyBoardData) {
  return { board, serverOffsetMs: 0 };
}

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

afterEach(() => {
  vi.useRealTimers();
});

describe("DailyBoard", () => {
  it("counts down to the reveal while locked", async () => {
    const revealAt = new Date(Date.now() + 3_600_000).toISOString();
    getMock.mockResolvedValue(resolved({ status: "locked", date: DATE, reveal_at: revealAt }));
    renderBoard();

    expect(await screen.findByText("Next board in")).toBeInTheDocument();
    expect(screen.queryByText("Buckwheat porridge")).not.toBeInTheDocument();
  });

  it("explains when no board is scheduled yet", async () => {
    getMock.mockResolvedValue(resolved({ status: "locked", date: DATE, reveal_at: null }));
    renderBoard();

    expect(await screen.findByText(/hasn't been set yet/)).toBeInTheDocument();
  });

  it("plays the replay first, then reveals the board on skip", async () => {
    getMock.mockResolvedValue(resolved(revealed()));
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
    getMock.mockResolvedValue(resolved(revealed()));
    renderBoard();

    expect(await screen.findByText("Buckwheat porridge")).toBeInTheDocument();
    expect(screen.queryByText("Composing today's board…")).not.toBeInTheDocument();
  });

  it("re-polls while past the reveal but still awaiting approval", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-16T11:00:00Z"));
    getMock.mockResolvedValue(
      resolved({ status: "locked", date: DATE, reveal_at: "2026-06-16T10:00:00Z" }),
    );
    renderBoard();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const callsAfterLoad = getMock.mock.calls.length;

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_WINDOW_MS);
    });

    expect(getMock.mock.calls.length).toBeGreaterThan(callsAfterLoad);
    expect(screen.getByText("Revealing now…")).toBeInTheDocument();
  });

  it("keeps showing the board when a refresh fails", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-16T11:00:00Z"));
    getMock.mockResolvedValueOnce(
      resolved({ status: "locked", date: DATE, reveal_at: "2026-06-16T10:00:00Z" }),
    );
    getMock.mockRejectedValue(new Error("network down"));
    renderBoard();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_WINDOW_MS);
    });

    // The poll failed, but the locked board it already had stays put.
    expect(screen.getByText("Revealing now…")).toBeInTheDocument();
    expect(screen.queryByText(/Couldn't load the board/)).not.toBeInTheDocument();
  });
});
