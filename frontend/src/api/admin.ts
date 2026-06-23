import type { LLMUsage, ProposedIngredient } from "./client";
import { errorDetail } from "./errors";

export { errorMessage } from "./errors";

export type MealType = "breakfast" | "lunch" | "dinner" | "snack";
export type ApprovalStatus = "pending" | "approved" | "rejected";
export type Role = "user" | "admin";

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

// A composed meal as stored in a daily suggestion. Mirrors DailyMealContent; carries
// unverified_ingredients for the reviewer, which the public board drops.
export interface DailyMealContent {
  name: string;
  description: string;
  ingredients: ProposedIngredient[];
  recipe: string[] | null;
  tags: string[];
  unverified_ingredients: string[];
}

// One daily-board suggestion as the review queue shows it. Mirrors AdminDailyRead:
// the full content and the recorded trace, so the admin checks what the agent
// composed before it can reveal on the public board.
export interface AdminDailySuggestion {
  id: string;
  date: string;
  meal_type: MealType;
  content: DailyMealContent;
  model: string;
  usage: LLMUsage | null;
  reasoning_trace: TraceEvent[];
  reveal_at: string;
  approval_status: ApprovalStatus;
  approved_at: string | null;
  approved_by: string | null;
  created_at: string;
}

// The signed-in user the SPA gates on. Mirrors the backend AuthUser; no token, which
// lives only in the httpOnly session cookie.
export interface AuthUser {
  email: string;
  role: Role;
}

// Raised when an admin request comes back 401, i.e. the session cookie is missing or
// expired. The UI treats this as "log in again", distinct from a real failure.
export class AdminAuthError extends Error {}

// The session rides in an httpOnly cookie the browser attaches automatically, so
// every admin call sends credentials and reads no token in JS.
async function authedRequest(path: string, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(path, { ...init, credentials: "include" });
  if (response.status === 401) {
    throw new AdminAuthError(await errorDetail(response));
  }
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  return response;
}

export async function login(email: string, password: string): Promise<AuthUser> {
  // A 401 here is a wrong credential, not an expired session, so it stays a plain
  // Error the login form shows inline rather than an AdminAuthError. The server sets
  // the session cookie on the response, and credentials:"include" lets the browser
  // keep it.
  const response = await fetch("/admin/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "include",
    body: JSON.stringify({ email, password }),
  });
  if (!response.ok) {
    throw new Error(await errorDetail(response));
  }
  return (await response.json()) as AuthUser;
}

export async function logout(): Promise<void> {
  // Idempotent server-side, so the caller can clear local state regardless of outcome.
  await fetch("/admin/auth/logout", { method: "POST", credentials: "include" });
}

export async function getCurrentUser(): Promise<AuthUser> {
  // How the SPA bootstraps session state: it cannot read the httpOnly cookie, so it
  // asks the server who it is. A 401 (no session) surfaces as AdminAuthError.
  const response = await authedRequest("/admin/auth/me");
  return (await response.json()) as AuthUser;
}

export async function listPendingMeals(): Promise<AdminMeal[]> {
  const response = await authedRequest("/admin/meals?status=pending");
  return (await response.json()) as AdminMeal[];
}

export async function approveMeal(mealId: string): Promise<AdminMeal> {
  const response = await authedRequest(`/admin/meals/${mealId}/approve`, { method: "PATCH" });
  return (await response.json()) as AdminMeal;
}

export async function rejectMeal(mealId: string): Promise<AdminMeal> {
  const response = await authedRequest(`/admin/meals/${mealId}/reject`, { method: "PATCH" });
  return (await response.json()) as AdminMeal;
}

export async function listPendingDaily(): Promise<AdminDailySuggestion[]> {
  const response = await authedRequest("/admin/daily?status=pending");
  return (await response.json()) as AdminDailySuggestion[];
}

export async function approveDaily(suggestionId: string): Promise<AdminDailySuggestion> {
  const response = await authedRequest(`/admin/daily/${suggestionId}/approve`, { method: "PATCH" });
  return (await response.json()) as AdminDailySuggestion;
}

export async function rejectDaily(suggestionId: string): Promise<AdminDailySuggestion> {
  const response = await authedRequest(`/admin/daily/${suggestionId}/reject`, { method: "PATCH" });
  return (await response.json()) as AdminDailySuggestion;
}
