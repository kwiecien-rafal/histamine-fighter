// Shared HTTP error helpers used across the API client modules.

import { formatResetTime } from "../lib/format";

// A thrown value rendered as a user-facing string; non-Error throwables fall back
// to a generic line. Quota errors get their scope-aware copy here, so every
// surface that renders through this helper explains the right limit.
export function errorMessage(error: unknown): string {
  if (error instanceof QuotaError) return quotaErrorCopy(error);
  return error instanceof Error ? error.message : "Something went wrong.";
}

// The structured sibling a quota-exhausted 429 carries next to its detail string.
// Scopes mirror the backend's QuotaScope literal.
export interface QuotaInfo {
  scope: "user" | "ip" | "global" | "signup_ip" | "magic_send_ip";
  used: number;
  limit: number;
  resets_at: string;
}

// A daily shared-tier quota is exhausted. Distinct from a generic failure so the
// UI can show when the limit resets instead of a red error line.
export class QuotaError extends Error {
  readonly quota: QuotaInfo;

  constructor(detail: string, quota: QuotaInfo) {
    super(detail);
    this.quota = quota;
  }
}

// Parse a failed response body once into the human detail and any structured
// quota, so errorDetail and errorFromResponse share one extraction. Backend
// domain errors arrive as {"detail": "..."}; validation arrays and non-JSON
// bodies fall back to the bare status.
async function parseErrorBody(
  response: Response,
): Promise<{ detail: string; quota: QuotaInfo | null }> {
  let detail = `Request failed: ${response.status}`;
  let quota: QuotaInfo | null = null;
  try {
    const body = (await response.json()) as { detail?: unknown; quota?: unknown };
    if (typeof body.detail === "string" && body.detail) detail = body.detail;
    if (response.status === 429 && isQuotaInfo(body.quota)) quota = body.quota;
  } catch {
    // not a JSON body
  }
  return { detail, quota };
}

// A failed response's message.
export async function errorDetail(response: Response): Promise<string> {
  return (await parseErrorBody(response)).detail;
}

// A failed response as the right Error subclass: a 429 carrying quota info
// becomes QuotaError, anything else a plain Error with the detail string.
export async function errorFromResponse(response: Response): Promise<Error> {
  const { detail, quota } = await parseErrorBody(response);
  return quota !== null ? new QuotaError(detail, quota) : new Error(detail);
}

// Scope-aware copy for an exhausted daily quota. The personal limit names its
// reset time; the network and site-wide caps explain that it is not the user's
// own allowance that ran out, so "come back tomorrow" doesn't read as a bug.
export function quotaErrorCopy(error: QuotaError): string {
  switch (error.quota.scope) {
    case "ip":
      return (
        "This network's free-tier allowance is used up for today. " +
        "Bring your own key in Settings, or come back tomorrow."
      );
    case "global":
      return (
        "The free tier is fully used up site-wide for today. " +
        "Bring your own key in Settings, or come back tomorrow."
      );
    default:
      return `${error.message} Your limit resets at ${formatResetTime(error.quota.resets_at)}.`;
  }
}

function isQuotaInfo(value: unknown): value is QuotaInfo {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.scope === "string" &&
    typeof candidate.used === "number" &&
    typeof candidate.limit === "number" &&
    typeof candidate.resets_at === "string"
  );
}
