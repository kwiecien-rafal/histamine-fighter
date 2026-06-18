import type { MealType, TraceEvent } from "./admin";
import type { LLMUsage, ProposedIngredient } from "./client";
import { errorDetail } from "./errors";

// The public daily board. A plain GET, no auth and no LLM headers: the meals were
// composed offline and approved, so the page only reads pre-generated rows.

export interface DailyMealCard {
  meal_type: MealType;
  name: string;
  description: string;
  ingredients: ProposedIngredient[];
  recipe: string[] | null;
  tags: string[];
}

export interface LockedBoard {
  status: "locked";
  date: string;
  // ISO timestamp the board unlocks, or null when none is scheduled yet.
  reveal_at: string | null;
}

export interface RevealedBoard {
  status: "revealed";
  date: string;
  model: string;
  meals: DailyMealCard[];
  trace: TraceEvent[];
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
  const response = await fetch("/api/v1/daily/meals");
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
