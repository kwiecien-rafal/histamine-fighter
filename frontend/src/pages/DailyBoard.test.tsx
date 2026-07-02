import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  getDailyBoard,
  getDailyBoardFor,
  type DailyBoard as DailyBoardData,
  type DailyMealCard,
  type RevealedBoard,
} from "../api/daily";
import { DailyBoard } from "./DailyBoard";

vi.mock("../api/daily", async (importActual) => {
  const actual = await importActual<typeof import("../api/daily")>();
  return { ...actual, getDailyBoard: vi.fn(), getDailyBoardFor: vi.fn() };
});

const getMock = vi.mocked(getDailyBoard);
const getForMock = vi.mocked(getDailyBoardFor);

const DATE = "2026-06-16";

// Today and a day offset in UTC, matching how the page derives its navigation bounds.
function isoDaysFromToday(days: number): string {
  const now = new Date();
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + days))
    .toISOString()
    .slice(0, 10);
}

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
    model: "stub/model",
    name: "Buckwheat porridge",
    description: "warm buckwheat with pear",
    ingredients: [{ name: "buckwheat", category: "grain" }],
    recipe: null,
    tags: [],
    trace: [
      { kind: "check", text: "step one", ingredient: null, compatibility: null },
      { kind: "verify", text: "step two", ingredient: null, compatibility: null },
    ],
  };
}

function revealed(): RevealedBoard {
  return {
    status: "revealed",
    date: DATE,
    model: "stub/model",
    meals: [mealCard()],
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

  it("sets the page title", async () => {
    getMock.mockResolvedValue(resolved({ status: "locked", date: DATE, reveal_at: null }));
    renderBoard();

    await screen.findByText(/hasn't been set yet/);
    expect(document.title).toBe("Today's meals · Histamine Fighter");
  });

  it("shows the board immediately once revealed, with no forced premiere", async () => {
    getMock.mockResolvedValue(resolved(revealed()));
    renderBoard();

    expect(await screen.findByText("Buckwheat porridge")).toBeInTheDocument();
    expect(screen.getByText("stub/model")).toBeInTheDocument();
    expect(screen.queryByText("Composing today's board…")).not.toBeInTheDocument();
  });

  it("opens a per-card replay from the Watch button", async () => {
    getMock.mockResolvedValue(resolved(revealed()));
    const user = userEvent.setup();
    renderBoard();

    await user.click(await screen.findByRole("button", { name: "Watch how it was composed" }));

    expect(await screen.findByText("How it was composed")).toBeInTheDocument();
    expect(screen.getByText("step one")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByText("How it was composed")).not.toBeInTheDocument();
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

  it("disables forward navigation on today", async () => {
    getMock.mockResolvedValue(resolved(revealed()));
    renderBoard();

    await screen.findByText("Buckwheat porridge");
    expect(screen.getByRole("button", { name: /next day/i })).toBeDisabled();
  });

  it("steps back to a past day's board through the dated route", async () => {
    const yesterday = isoDaysFromToday(-1);
    getMock.mockResolvedValue(resolved(revealed()));
    getForMock.mockResolvedValue(
      resolved({ ...revealed(), meals: [{ ...mealCard(), name: "Yesterday's stew" }] }),
    );
    const user = userEvent.setup();
    renderBoard();

    await screen.findByText("Buckwheat porridge");
    await user.click(screen.getByRole("button", { name: /previous day/i }));

    expect(await screen.findByText("Yesterday's stew")).toBeInTheDocument();
    expect(getForMock).toHaveBeenCalledWith(yesterday);
  });

  it("shows 'no board published' for a past day with nothing approved", async () => {
    const yesterday = isoDaysFromToday(-1);
    getMock.mockResolvedValue(resolved(revealed()));
    getForMock.mockResolvedValue(
      resolved({ status: "locked", date: yesterday, reveal_at: "2026-06-15T10:00:00Z" }),
    );
    const user = userEvent.setup();
    renderBoard();

    await screen.findByText("Buckwheat porridge");
    await user.click(screen.getByRole("button", { name: /previous day/i }));

    expect(await screen.findByText(/No board was published on/)).toBeInTheDocument();
    expect(screen.queryByText("Buckwheat porridge")).not.toBeInTheDocument();
  });
});
