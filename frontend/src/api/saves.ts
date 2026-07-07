// Saved-meals client. Cookie-authed via sessionRequest, so a 401 flips the UI to
// signed-out through the shared session-expired seam. Curated and daily saves send
// only the source id (the server copies the content); a lookup save carries its
// assessed snapshot, which the server normalizes and never marks verified.

import { sessionRequest } from "./auth";
import { buildLLMHeaders, type LLMUsage, type ProposedIngredient } from "./client";
import type { CautionedIngredient, MealType } from "./domain";

export type SaveSource = "curated" | "daily" | "lookup";
export type SavedVerdict = "safe" | "depends" | "avoid";

export interface SavedMealCard {
  id: string;
  source: SaveSource;
  source_key: string;
  meal_type: MealType | null;
  name: string;
  description: string;
  tags: string[];
  verdict: SavedVerdict | null;
  edited_at: string | null;
  created_at: string;
  has_recipe: boolean;
}

export interface SavedMealDetail extends SavedMealCard {
  ingredients: ProposedIngredient[];
  recipe: string[] | null;
  cautioned_ingredients: CautionedIngredient[];
  model: string;
  // Which model wrote the lazily generated recipe; null until one exists.
  recipe_model: string | null;
}

export interface LookupSavePayload {
  // Client-minted per-result id; becomes the save's source_key, so each
  // assessment result saves as its own row even under a reused dish name.
  lookup_id: string;
  dish: string;
  verdict: SavedVerdict;
  description: string;
  ingredients: ProposedIngredient[];
  model: string;
  // A recipe generated on the result card rides into the save; null when the
  // user never asked for one.
  recipe: string[] | null;
  recipe_model: string | null;
}

// What a heart button points at: a server row for pool content, the assessed
// snapshot for a lookup result.
export type SaveTarget =
  | { source: "curated" | "daily"; sourceId: string }
  | { source: "lookup"; payload: LookupSavePayload };

export interface SavedMealUpdate {
  name: string;
  description: string;
  ingredients: ProposedIngredient[];
  recipe: string[] | null;
  tags: string[];
}

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

export async function listSaves(): Promise<SavedMealCard[]> {
  const response = await sessionRequest("/api/v1/me/meals");
  const page = (await response.json()) as { items: SavedMealCard[] };
  return page.items;
}

export async function getSave(id: string): Promise<SavedMealDetail> {
  const response = await sessionRequest(`/api/v1/me/meals/${id}`);
  return (await response.json()) as SavedMealDetail;
}

export async function saveMeal(target: SaveTarget): Promise<SavedMealDetail> {
  const body =
    target.source === "lookup"
      ? { source: "lookup", ...target.payload }
      : { source: target.source, source_id: target.sourceId };
  const response = await sessionRequest("/api/v1/me/meals", jsonInit("POST", body));
  return (await response.json()) as SavedMealDetail;
}

export async function updateSave(id: string, edit: SavedMealUpdate): Promise<SavedMealDetail> {
  const response = await sessionRequest(`/api/v1/me/meals/${id}`, jsonInit("PATCH", edit));
  return (await response.json()) as SavedMealDetail;
}

export async function deleteSave(id: string): Promise<void> {
  await sessionRequest(`/api/v1/me/meals/${id}`, { method: "DELETE" });
}

export interface SavedRecipeResponse {
  meal: SavedMealDetail;
  recipe_model: string;
  usage: LLMUsage;
}

// Writes a recipe for a saved meal that has none and persists it on the row.
// One LLM call, ever, per saved meal: the server returns an existing recipe
// unchanged. The LLM provider headers ride along like on the lookup calls.
export async function generateRecipe(id: string): Promise<SavedRecipeResponse> {
  const response = await sessionRequest(`/api/v1/me/meals/${id}/recipe`, {
    method: "POST",
    headers: buildLLMHeaders(),
  });
  return (await response.json()) as SavedRecipeResponse;
}
