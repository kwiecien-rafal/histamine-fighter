import type { LLMUsage, ProposedIngredient } from "./client";
import { errorDetail } from "./errors";

export { errorMessage } from "./errors";

export type MealType = "breakfast" | "lunch" | "dinner" | "snack";
export type ApprovalStatus = "pending" | "approved" | "rejected";

// Mirror the backend caps (app/schemas/admin.py) so an over-long field stops at
// the input instead of bouncing back as a 422.
export const MAX_EMAIL_CHARS = 320;
export const MAX_PASSWORD_CHARS = 128;

// The stable reading token a trace step carries; the UI maps it to a label.
export type TraceReading = "safe" | "depends" | "avoid" | "unverifiable" | "not_indexed";

export type TraceKind =
  | "draft"
  | "check"
  | "search"
  | "options"
  | "reject"
  | "submit"
  | "verify";

export interface TraceEvent {
  kind: TraceKind;
  text: string;
  ingredient: string | null;
  compatibility: TraceReading | null;
  // Set on the public daily board, where several meals' steps replay together, so
  // the animation can group them by dish; absent on a single-meal stream.
  meal_type?: MealType | null;
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
  // Token usage of the composition; null for meals composed before it was recorded.
  usage: LLMUsage | null;
  reasoning_trace: TraceEvent[];
  approval_status: ApprovalStatus;
  approved_at: string | null;
  approved_by: string | null;
  created_at: string;
}

// The composer's output as streamed by the live "generate now" demo. The trace is
// not on it: the client assembled that from the trace events as they arrived, and a
// live composition is not saved.
export interface ComposedMeal {
  name: string;
  meal_type: MealType;
  description: string;
  ingredients: ProposedIngredient[];
  recipe: string[] | null;
  tags: string[];
  unverified_ingredients: string[];
  model: string;
  usage: LLMUsage;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
}

// Raised when an admin request comes back 401, i.e. the token is missing or
// expired. The UI treats this as "log in again", distinct from a real failure.
export class AdminAuthError extends Error {}

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
