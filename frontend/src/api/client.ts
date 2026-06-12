import { useLLMProviderStore } from "../store/llmProvider";

export type Verdict = "safe" | "depends" | "avoid";

// Mirror the backend request caps (app/schemas/meal.py), so inputs can stop
// at the limit instead of letting an overlong edit bounce back as a 422.
export const MAX_INGREDIENTS = 25;
export const MAX_INGREDIENT_CHARS = 80;
export const MAX_DISH_CHARS = 200;

export interface ProposedIngredient {
  name: string;
  category: string | null;
}

export interface IngredientProposalResponse {
  dish: string;
  ingredients: ProposedIngredient[];
  model: string;
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

export interface Replacement {
  ingredient: string;
  swap: string;
  reason: string;
}

export interface DishAssessmentResponse {
  dish: string;
  verdict: Verdict;
  explanation: string;
  replacements: Replacement[];
  ingredients: IngredientAssessment[];
  model: string;
}

function buildLLMHeaders(): Record<string, string> {
  const { provider, apiKeys, models, ollamaBaseUrl } =
    useLLMProviderStore.getState();
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

async function errorDetail(response: Response): Promise<string> {
  // Backend domain errors (bad provider, model failure, rate limit) arrive as
  // {"detail": "<message>"}; anything else — validation detail arrays, proxy
  // HTML — falls back to the bare status.
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail) return body.detail;
  } catch {
    // not a JSON body
  }
  return `Request failed: ${response.status}`;
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...buildLLMHeaders() },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(await errorDetail(response));
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
