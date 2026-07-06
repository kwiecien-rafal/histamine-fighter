// Public auth client: magic-link login, session state, quota, and account deletion.
// The session rides in an httpOnly cookie, so every call sends credentials and no
// token ever exists in JS. OAuth is deliberately absent here: those flows are full
// page navigations to /api/v1/auth/oauth/{provider}/start, not fetches.

import type { AuthUser } from "./admin";
import { errorDetail, errorFromResponse } from "./errors";

export const MAX_EMAIL_CHARS = 320;
export const MAGIC_CODE_LENGTH = 6;

// Raised when a session call comes back 401: the cookie is missing or expired.
// The UI treats this as "signed out", distinct from a real failure.
export class SessionExpiredError extends Error {}

// The session store registers its clear() here at creation, so any API module
// can flip the UI to signed-out the moment a dead cookie surfaces — without the
// store/api import cycle a direct import would create.
let sessionExpiredHandler: (() => void) | null = null;

export function setSessionExpiredHandler(handler: () => void): void {
  sessionExpiredHandler = handler;
}

export function notifySessionExpired(): void {
  sessionExpiredHandler?.();
}

// The user's shared-tier allowance today, as /me/quota reports it.
export interface QuotaStatus {
  used: number;
  limit: number;
  resets_at: string;
}

// Shared by every cookie-authed API module (auth here, saves in ./saves), so a
// dead session surfaces the same way everywhere.
export async function sessionRequest(path: string, init: RequestInit = {}): Promise<Response> {
  const response = await fetch(path, { ...init, credentials: "include" });
  if (response.status === 401) {
    notifySessionExpired();
    throw new SessionExpiredError(await errorDetail(response));
  }
  if (!response.ok) {
    // errorFromResponse keeps a 429's structured quota (the signup / send caps
    // fire on these routes), so QuotaError's scope-aware copy still applies.
    throw await errorFromResponse(response);
  }
  return response;
}

function jsonInit(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}

// Ask for a sign-in email. Resolves on acceptance; the response body is
// deliberately uniform, so there is nothing to return.
export async function requestMagicLink(
  email: string,
  turnstileToken: string | null,
): Promise<void> {
  await sessionRequest(
    "/api/v1/auth/magic/request",
    jsonInit({ email, turnstile_token: turnstileToken }),
  );
}

// Complete a sign-in with either the emailed link's token or the email + code.
export type MagicVerifyInput = { token: string } | { email: string; code: string };

export async function verifyMagicLink(input: MagicVerifyInput): Promise<AuthUser> {
  const response = await sessionRequest("/api/v1/auth/magic/verify", jsonInit(input));
  return (await response.json()) as AuthUser;
}

// Recover the signed-in user on load; throws SessionExpiredError when anonymous.
export async function getSessionUser(): Promise<AuthUser> {
  const response = await sessionRequest("/api/v1/auth/me");
  return (await response.json()) as AuthUser;
}

export async function getQuota(): Promise<QuotaStatus> {
  const response = await sessionRequest("/api/v1/auth/me/quota");
  return (await response.json()) as QuotaStatus;
}

export async function logout(): Promise<void> {
  await sessionRequest("/api/v1/auth/logout", { method: "POST" });
}

// Revoke every outstanding session for the account (all devices), server-side.
export async function logoutEverywhere(): Promise<void> {
  await sessionRequest("/api/v1/auth/logout/all", { method: "POST" });
}

// GDPR erasure: deletes the account server-side and clears the cookie.
export async function deleteAccount(): Promise<void> {
  await sessionRequest("/api/v1/auth/me", { method: "DELETE" });
}
