import type { MealType, TraceEvent } from "./admin";
import type { LLMUsage, ProposedIngredient } from "./client";

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

export async function getDailyBoard(): Promise<DailyBoard> {
  const response = await fetch("/api/v1/daily/meals");
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return (await response.json()) as DailyBoard;
}
