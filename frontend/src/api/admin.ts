import type { ProposedIngredient } from "./client";

export type MealType = "breakfast" | "lunch" | "dinner" | "snack";
export type ApprovalStatus = "pending" | "approved" | "rejected";

export interface TraceEvent {
  kind: "draft" | "check" | "swap" | "reject" | "submit" | "verify";
  text: string;
  ingredient: string | null;
  compatibility: string | null;
}

export interface AdminMeal {
  id: string;
  name: string;
  meal_type: MealType;
  description: string;
  ingredients: ProposedIngredient[];
  recipe: string[] | null;
  tags: string[];
  // Ingredients the index could not vouch for, surfaced so the reviewer checks
  // them before approving.
  unverified_ingredients: string[];
  model: string;
  reasoning_trace: TraceEvent[];
  approval_status: ApprovalStatus;
  approved_at: string | null;
  approved_by: string | null;
  created_at: string;
}

// The composer's output as streamed by the live "generate now" demo. Mirrors
// AdminMeal without the persistence fields, since a live composition is not saved.
export interface ComposedMeal {
  name: string;
  meal_type: MealType;
  description: string;
  ingredients: ProposedIngredient[];
  recipe: string[] | null;
  tags: string[];
  unverified_ingredients: string[];
  reasoning_trace: TraceEvent[];
  model: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

// Raised when an admin request comes back 401, i.e. the token is missing or
// expired. The UI treats this as "log in again", distinct from a real failure.
export class AdminAuthError extends Error {}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong.";
}

async function errorDetail(response: Response): Promise<string> {
  // Backend domain errors arrive as {"detail": "<message>"}; fall back to the
  // bare status for validation arrays or non-JSON bodies.
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail) return body.detail;
  } catch {
    // not a JSON body
  }
  return `Request failed: ${response.status}`;
}

async function authedRequest(
  path: string,
  token: string,
  init: RequestInit = {},
): Promise<Response> {
  const response = await fetch(path, {
    ...init,
    headers: { ...init.headers, Authorization: `Bearer ${token}` },
  });
  if (response.status === 401) {
    throw new AdminAuthError(await errorDetail(response));
  }
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  return response;
}

export async function login(email: string, password: string): Promise<TokenResponse> {
  // A 401 here is a wrong credential, not an expired session, so it stays a plain
  // Error the login form shows inline rather than an AdminAuthError.
  const response = await fetch("/admin/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  return (await response.json()) as TokenResponse;
}

export async function listPendingMeals(token: string): Promise<AdminMeal[]> {
  const response = await authedRequest("/admin/meals?status=pending", token);
  return (await response.json()) as AdminMeal[];
}

export async function approveMeal(token: string, mealId: string): Promise<AdminMeal> {
  const response = await authedRequest(`/admin/meals/${mealId}/approve`, token, {
    method: "PATCH",
  });
  return (await response.json()) as AdminMeal;
}

export async function rejectMeal(token: string, mealId: string): Promise<AdminMeal> {
  const response = await authedRequest(`/admin/meals/${mealId}/reject`, token, {
    method: "PATCH",
  });
  return (await response.json()) as AdminMeal;
}
