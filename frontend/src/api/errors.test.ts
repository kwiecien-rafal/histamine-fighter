import { describe, expect, it } from "vitest";

import { QuotaError, errorFromResponse, quotaErrorCopy, type QuotaInfo } from "./errors";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("errorFromResponse", () => {
  it("turns a 429 with quota info into a QuotaError", async () => {
    const error = await errorFromResponse(
      jsonResponse(429, {
        detail: "Daily free-tier limit reached.",
        quota: { scope: "user", used: 20, limit: 20, resets_at: "2026-07-05T00:00:00+00:00" },
      }),
    );

    expect(error).toBeInstanceOf(QuotaError);
    expect(error.message).toBe("Daily free-tier limit reached.");
    expect((error as QuotaError).quota.limit).toBe(20);
  });

  it("keeps a plain burst-limit 429 an ordinary Error", async () => {
    const error = await errorFromResponse(jsonResponse(429, { detail: "Rate limit exceeded" }));

    expect(error).not.toBeInstanceOf(QuotaError);
    expect(error.message).toBe("Rate limit exceeded");
  });

  it("falls back to the bare status for a non-JSON body", async () => {
    const error = await errorFromResponse(new Response("boom", { status: 500 }));

    expect(error.message).toBe("Request failed: 500");
  });

  it("ignores a malformed quota payload", async () => {
    const error = await errorFromResponse(
      jsonResponse(429, { detail: "nope", quota: { scope: "user" } }),
    );

    expect(error).not.toBeInstanceOf(QuotaError);
  });
});

describe("quotaErrorCopy", () => {
  function quotaError(scope: QuotaInfo["scope"]): QuotaError {
    return new QuotaError("Daily free-tier limit reached.", {
      scope,
      used: 20,
      limit: 20,
      resets_at: "2026-07-06T00:00:00+00:00",
    });
  }

  it("names the reset time for the personal limit", () => {
    const copy = quotaErrorCopy(quotaError("user"));

    expect(copy).toContain("Daily free-tier limit reached.");
    expect(copy).toContain("resets at");
  });

  it("explains the network cap without blaming the user's own allowance", () => {
    expect(quotaErrorCopy(quotaError("ip"))).toContain("This network's");
  });

  it("explains the site-wide cap", () => {
    expect(quotaErrorCopy(quotaError("global"))).toContain("site-wide");
  });
});
