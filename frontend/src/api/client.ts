import { useLLMProviderStore } from "../store/llmProvider";

export type Verdict = "safe" | "depends" | "avoid";

export interface DishLookupResponse {
  dish: string;
  verdict: Verdict;
  explanation: string;
  model: string;
}

function buildLLMHeaders(): Record<string, string> {
  const { provider, apiKey, model, ollamaBaseUrl } =
    useLLMProviderStore.getState();
  const headers: Record<string, string> = { "X-LLM-Provider": provider };
  if (model.trim()) headers["X-LLM-Model"] = model.trim();
  if (provider === "ollama" && ollamaBaseUrl.trim()) {
    headers["X-LLM-Base-URL"] = ollamaBaseUrl.trim();
  }
  if (apiKey.trim()) headers["X-LLM-API-Key"] = apiKey.trim();
  return headers;
}

export async function lookupDish(dish: string): Promise<DishLookupResponse> {
  const response = await fetch("/api/v1/meals/lookup", {
    method: "POST",
    headers: { "Content-Type": "application/json", ...buildLLMHeaders() },
    body: JSON.stringify({ dish }),
  });
  if (!response.ok) {
    throw new Error(`Lookup failed: ${response.status}`);
  }
  return (await response.json()) as DishLookupResponse;
}
