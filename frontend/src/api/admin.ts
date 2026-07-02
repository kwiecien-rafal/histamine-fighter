import type { LLMUsage, ProposedIngredient } from "./client";
import type { MealType, TraceEvent } from "./domain";
import { errorDetail } from "./errors";

export { errorDetail, errorMessage } from "./errors";
// The neutral domain types live in ./domain; re-exported so existing admin imports
// keep working while the public clients depend on ./domain directly.
export type { MealType, TraceEvent, TraceKind, TraceReading } from "./domain";

export type ApprovalStatus = "pending" | "approved" | "rejected";
export type Role = "user" | "admin";

// Mirror the backend caps (app/schemas/admin.py) so an over-long field stops at
// the input instead of bouncing back as a 422.
export const MAX_EMAIL_CHARS = 320;
export const MAX_PASSWORD_CHARS = 128;

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

// The session rides in an httpOnly cookie the browser attaches automatically, so every
// admin call sends credentials and reads no token in JS. Throws AdminAuthError on a 401
// (expired session) but returns the raw response otherwise, so a caller that must read a
// non-2xx body (a 422 edit rejection) can inspect it.
async function rawAuthedRequest(path: string, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(path, { ...init, credentials: "include" });
  if (response.status === 401) {
    throw new AdminAuthError(await errorDetail(response));
  }
  return response;
}

// As rawAuthedRequest, but throws on any non-2xx so the common callers stay one-liners.
async function authedRequest(path: string, init: RequestInit = {}): Promise<Response> {
  const response = await rawAuthedRequest(path, init);
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

export async function listMeals(status: ApprovalStatus): Promise<AdminMeal[]> {
  const response = await authedRequest(`/admin/meals?status=${status}`);
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

export async function deleteMeal(mealId: string): Promise<void> {
  await authedRequest(`/admin/meals/${mealId}`, { method: "DELETE" });
}

export async function approveDaily(suggestionId: string): Promise<AdminDailySuggestion> {
  const response = await authedRequest(`/admin/daily/${suggestionId}/approve`, { method: "PATCH" });
  return (await response.json()) as AdminDailySuggestion;
}

export async function rejectDaily(suggestionId: string): Promise<AdminDailySuggestion> {
  const response = await authedRequest(`/admin/daily/${suggestionId}/reject`, { method: "PATCH" });
  return (await response.json()) as AdminDailySuggestion;
}

export async function deleteDaily(suggestionId: string): Promise<void> {
  await authedRequest(`/admin/daily/${suggestionId}`, { method: "DELETE" });
}

// The operator-set composer model. Mirrors ComposeSettingsRead: the current choice
// plus the providers an admin may switch to (those with a configured key, plus Ollama
// off public deployments). The key itself never travels — it stays in the server env.
export interface ComposeSettings {
  provider: string | null;
  model: string | null;
  available_providers: string[];
}

export async function getComposeSettings(): Promise<ComposeSettings> {
  const response = await authedRequest("/admin/compose/settings");
  return (await response.json()) as ComposeSettings;
}

export async function updateComposeSettings(
  provider: string,
  model: string | null,
): Promise<ComposeSettings> {
  const response = await authedRequest("/admin/compose/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider, model }),
  });
  return (await response.json()) as ComposeSettings;
}

// One upcoming date in the daily queue. Mirrors QueuedDay: the slots present, the meal
// types still missing, and the counts the UI warns on (an upcoming day not yet approved).
export interface QueuedDay {
  date: string;
  slots: AdminDailySuggestion[];
  missing_meal_types: MealType[];
  pending_count: number;
  approved_count: number;
}

export async function listDailyQueue(): Promise<QueuedDay[]> {
  const response = await authedRequest("/admin/daily/queue");
  return (await response.json()) as QueuedDay[];
}

// The terminal frame a saving compose stream emits once the row is persisted.
export interface SavedEvent {
  id: string;
}

// The body of a daily compose-and-save: which slot, which date, and whether to overwrite
// a slot already taken (set only after the operator confirms the conflict).
export interface ComposeDailyRequest {
  meal_type: MealType;
  date: string;
  replace?: boolean;
}

// The slot a daily compose-and-save would overwrite, reported by a pre-stream 409 so the
// UI can word the confirm by stakes (replacing an approved row un-publishes it).
export interface SlotConflict {
  date: string;
  meal_type: MealType;
  existing_status: ApprovalStatus;
}

// The body of a full-board compose-and-save: the date whose open slots to fill. Board
// mode never replaces a pending or approved slot, so there is no replace flag.
export interface ComposeBoardRequest {
  date: string;
}

// Announces the board slot about to compose (1-based index of total); the live
// composing log clears when it arrives.
export interface SlotStartEvent {
  meal_type: MealType;
  index: number;
  total: number;
}

// A non-terminal per-slot failure on a board run: the slot's compose or save failed
// and the run moved on to the remaining slots.
export interface SlotErrorEvent {
  meal_type: MealType;
  detail: string;
}

// The terminal summary of a board run. Skipped slots already held a pending or
// approved suggestion, which a board run never replaces.
export interface BoardSummary {
  composed: MealType[];
  failed: MealType[];
  skipped: MealType[];
}

// The five admin-editable content fields, mirroring AdminMealUpdate / AdminDailyUpdate.
// confirm_flagged is the request-only override: set on resubmit after the operator
// confirms a flagged-ingredient warning, never persisted server-side.
export interface MealEdit {
  name: string;
  description: string;
  ingredients: ProposedIngredient[];
  recipe: string[] | null;
  tags: string[];
  confirm_flagged?: boolean;
}

// A hand-authored manual meal, mirroring AdminMealCreate: the editable fields plus the slot
// a new meal needs (an edit cannot change a meal's type, so MealEdit omits it).
export interface MealCreate extends MealEdit {
  meal_type: MealType;
}

// The offending items a 422 carries when an edit fails the index re-check, so the edit
// form can show exactly what to fix rather than a bare status. can_confirm is false when
// a lookup itself failed, in which case the save-anyway override is not offered.
export interface EditRejection {
  message: string;
  blockers: string[];
  can_confirm: boolean;
}

export class EditRejectedError extends Error {
  constructor(readonly rejection: EditRejection) {
    super(rejection.message);
  }
}

// Shared writer for a meal create or edit: both hit the same index gate, so both share the
// composer-style 422 handling that surfaces the offending items for the form to display.
async function writeMeal<T>(
  method: "POST" | "PATCH",
  path: string,
  body: MealEdit | MealCreate,
): Promise<T> {
  const response = await rawAuthedRequest(path, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (response.status === 422) {
    const errorBody = (await response.json().catch(() => null)) as { detail?: EditRejection } | null;
    const detail = errorBody?.detail;
    // The composer-style 422 carries the offending items; a plain schema 422 (e.g. an
    // oversized list the form should have blocked) falls back to a generic line.
    if (detail && Array.isArray(detail.blockers)) throw new EditRejectedError(detail);
    throw new Error("The meal was rejected.");
  }
  if (!response.ok) throw new Error(await errorDetail(response));
  return (await response.json()) as T;
}

export function createMeal(meal: MealCreate): Promise<AdminMeal> {
  return writeMeal("POST", "/admin/meals", meal);
}

export function updateMeal(mealId: string, edit: MealEdit): Promise<AdminMeal> {
  return writeMeal("PATCH", `/admin/meals/${mealId}`, edit);
}

export function updateDaily(suggestionId: string, edit: MealEdit): Promise<AdminDailySuggestion> {
  return writeMeal("PATCH", `/admin/daily/${suggestionId}`, edit);
}
