export type Verdict = "safe" | "depends" | "avoid";

export interface DishLookupResponse {
  dish: string;
  verdict: Verdict;
  explanation: string;
  model: string;
}

export async function lookupDish(dish: string): Promise<DishLookupResponse> {
  const response = await fetch("/api/v1/meals/lookup", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ dish }),
  });
  if (!response.ok) {
    throw new Error(`Lookup failed: ${response.status}`);
  }
  return (await response.json()) as DishLookupResponse;
}
