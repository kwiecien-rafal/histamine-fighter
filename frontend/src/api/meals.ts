import type { MealType, PublicMeal } from "./domain";
import { errorDetail } from "./errors";

// The public curated browse. Plain GETs, no auth and no LLM headers: every meal is
// verified-safe by construction and admin-approved, so the page only reads the pool.
// The list serves lean summary cards; the recipe and replay trace load from the detail
// endpoint when a visitor opens a meal.

export interface PublicMealCard {
  id: string;
  meal_type: MealType;
  model: string;
  name: string;
  description: string;
  tags: string[];
  // Whether a detail view would have a recipe / a replayable trace, so the card can
  // hint at them without shipping either.
  has_recipe: boolean;
  has_trace: boolean;
}

export type PublicMealDetail = PublicMeal & { id: string };

export interface PublicMealPage {
  items: PublicMealCard[];
  // Total approved meals matching the filter, so the browse knows when to stop paging.
  total: number;
}

export interface BrowseParams {
  mealType?: MealType;
  limit?: number;
  offset?: number;
}

export async function browseMeals(params: BrowseParams = {}): Promise<PublicMealPage> {
  const query = new URLSearchParams();
  if (params.mealType) query.set("meal_type", params.mealType);
  if (params.limit != null) query.set("limit", String(params.limit));
  if (params.offset) query.set("offset", String(params.offset));
  const suffix = query.toString();
  const response = await fetch(`/api/v1/meals${suffix ? `?${suffix}` : ""}`);
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  return (await response.json()) as PublicMealPage;
}

export async function getMeal(id: string): Promise<PublicMealDetail> {
  const response = await fetch(`/api/v1/meals/${id}`);
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  return (await response.json()) as PublicMealDetail;
}
