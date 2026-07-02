import type { PublicMeal } from "./domain";
import type { LLMUsage } from "./client";
import { errorDetail } from "./errors";

// The public daily board. A plain GET, no auth and no LLM headers: the meals were
// composed offline and approved, so the page only reads pre-generated rows.

// A revealed board card is exactly the shared public meal view; the browse detail uses
// the same shape, so they share one type rather than drifting apart.
export type DailyMealCard = PublicMeal;

export interface LockedBoard {
  status: "locked";
  date: string;
  // ISO timestamp the board unlocks, or null when none is scheduled yet.
  reveal_at: string | null;
}

export interface RevealedBoard {
  status: "revealed";
  date: string;
  // The board's representative model, used only to price the aggregate cost. Per-meal
  // attribution lives on each card's own model.
  model: string;
  meals: DailyMealCard[];
  // Total token usage of composing the day's meals.
  usage: LLMUsage;
}

export type DailyBoard = LockedBoard | RevealedBoard;

export interface DailyBoardResult {
  board: DailyBoard;
  // serverNow - clientNow at fetch time. Added to the device clock so the reveal
  // countdown tracks the server's clock, not a skewed local one.
  serverOffsetMs: number;
}

export async function getDailyBoard(): Promise<DailyBoardResult> {
  return fetchBoard("/api/v1/daily/meals");
}

// A past day within the history window; the endpoint 404s anything outside it.
export async function getDailyBoardFor(date: string): Promise<DailyBoardResult> {
  return fetchBoard(`/api/v1/daily/meals/${date}`);
}

async function fetchBoard(path: string): Promise<DailyBoardResult> {
  const response = await fetch(path);
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  const board = (await response.json()) as DailyBoard;
  return { board, serverOffsetMs: serverClockOffset(response.headers.get("Date")) };
}

// The response Date header in milliseconds, minus the device clock. Zero when the
// header is missing or unparseable, so a client without skew just uses its own clock.
function serverClockOffset(dateHeader: string | null): number {
  if (dateHeader === null) return 0;
  const serverMs = Date.parse(dateHeader);
  return Number.isNaN(serverMs) ? 0 : serverMs - Date.now();
}
