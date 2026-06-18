// Shared HTTP error helpers used across the API client modules.

// A thrown value rendered as a user-facing string; non-Error throwables fall back
// to a generic line.
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong.";
}

// A failed response's message. Backend domain errors arrive as {"detail": "..."};
// validation arrays and non-JSON bodies fall back to the bare status.
export async function errorDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string" && body.detail) return body.detail;
  } catch {
    // not a JSON body
  }
  return `Request failed: ${response.status}`;
}
