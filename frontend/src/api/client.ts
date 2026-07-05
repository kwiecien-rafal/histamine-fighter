import { useLLMProviderStore } from "../store/llmProvider";
import { notifySessionExpired } from "./auth";
import { errorFromResponse } from "./errors";

export type Verdict = "safe" | "depends" | "avoid";

// Mirror the backend request caps (app/schemas/meal.py), so inputs can stop
// at the limit instead of letting an overlong edit bounce back as a 422.
export const MAX_INGREDIENTS = 25;
export const MAX_INGREDIENT_CHARS = 80;
export const MAX_DISH_CHARS = 200;

export interface StepUsage {
  step: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  reported: boolean;
}

export interface LLMUsage {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  steps: StepUsage[];
}

export interface ProposedIngredient {
  name: string;
  category: string | null;
}

export interface IngredientProposalResponse {
  dish: string;
  ingredients: ProposedIngredient[];
  model: string;
  usage: LLMUsage;
}

export interface ConfirmedIngredient {
  name: string;
  category: string | null;
}

export interface IngredientAssessment {
  name: string;
  safety: Verdict;
  found: boolean;
  error: boolean;
  matched_on: "ingredient" | "category" | null;
  mechanisms: string[];
}

export type CulinaryRole = "core" | "supporting" | "seasoning";
export type AdaptationAction = "swap" | "omit" | "no_safe_swap";
export type DishIntegrity = "preserved" | "altered" | "lost";
export type AlternativeGoal = "any_meal" | "same_style" | "similar_flavours";

export interface Adaptation {
  ingredients: string[];
  role: CulinaryRole;
  action: AdaptationAction;
  swap: string | null;
  reason: string;
}

export interface Advisory {
  ingredient: string;
  note: string;
}

export interface DishAssessmentResponse {
  dish: string;
  verdict: Verdict;
  explanation: string;
  adaptations: Adaptation[];
  advisories: Advisory[];
  integrity: DishIntegrity;
  ingredients: IngredientAssessment[];
  model: string;
  usage: LLMUsage;
}

export type AlternativeSource = "verified" | "generated";

export interface DishAlternative {
  name: string;
  pitch: string;
  // Neutral domain value: "verified" is a member of the approved pool, "generated"
  // is a fresh idea the user re-vets on click. Branded copy lives in the display map.
  source: AlternativeSource;
}

export interface DishAlternativesResponse {
  dish: string;
  goal: AlternativeGoal;
  alternatives: DishAlternative[];
  model: string;
  usage: LLMUsage;
}

export function buildLLMHeaders(): Record<string, string> {
  const { provider, apiKeys, models, ollamaBaseUrl } =
    useLLMProviderStore.getState();
  // The shared tier is server-configured: the backend pins the model and its own
  // key, so only the provider header travels — no client input can steer what
  // the operator pays for.
  if (provider === "shared") {
    return { "X-LLM-Provider": provider };
  }
  const apiKey = (apiKeys[provider] ?? "").trim();
  const model = (models[provider] ?? "").trim();
  const headers: Record<string, string> = { "X-LLM-Provider": provider };
  if (model) headers["X-LLM-Model"] = model;
  if (provider === "ollama" && ollamaBaseUrl.trim()) {
    headers["X-LLM-Base-URL"] = ollamaBaseUrl.trim();
  }
  if (apiKey) headers["X-LLM-API-Key"] = apiKey;
  return headers;
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  // credentials: the shared tier authenticates by session cookie; on BYO-key and
  // Ollama calls the cookie rides along unused.
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...buildLLMHeaders() },
    body: JSON.stringify(body),
    credentials: "include",
  });
  if (!response.ok) {
    // A 401 on the shared tier means the session cookie died mid-session; flip
    // the UI to signed-out now instead of letting the navbar lie until reload.
    if (response.status === 401 && useLLMProviderStore.getState().provider === "shared") {
      notifySessionExpired();
    }
    throw await errorFromResponse(response);
  }
  return (await response.json()) as T;
}

export function proposeIngredients(
  dish: string,
): Promise<IngredientProposalResponse> {
  return postJSON("/api/v1/meals/propose", { dish });
}

export function assessDish(
  dish: string,
  ingredients: ConfirmedIngredient[],
): Promise<DishAssessmentResponse> {
  return postJSON("/api/v1/meals/assess", { dish, ingredients });
}

export function suggestAlternatives(
  dish: string,
  goal: AlternativeGoal,
  avoidIngredients: string[],
  preferIngredients: string[],
): Promise<DishAlternativesResponse> {
  return postJSON("/api/v1/meals/alternatives", {
    dish,
    goal,
    avoid_ingredients: avoidIngredients,
    prefer_ingredients: preferIngredients,
  });
}
