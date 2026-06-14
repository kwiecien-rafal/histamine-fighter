import { describe, expect, it } from "vitest";

import { estimateCost } from "./pricing";

describe("estimateCost", () => {
  it("prices a known model from its input/output split", () => {
    // claude-haiku-4-5 is $1 in + $5 out per million tokens.
    const { usd, selfHosted } = estimateCost(
      "anthropic/claude-haiku-4-5",
      1_000_000,
      1_000_000,
    );

    expect(selfHosted).toBe(false);
    expect(usd).toBeCloseTo(6, 10);
  });

  it("treats self-hosted providers as free", () => {
    expect(estimateCost("ollama/llama3", 1000, 1000)).toEqual({
      usd: 0,
      selfHosted: true,
    });
    expect(estimateCost("modal/mistral-7b", 1000, 1000)).toEqual({
      usd: 0,
      selfHosted: true,
    });
  });

  it("returns no cost for an unpriced model", () => {
    expect(estimateCost("acme/secret-model", 1000, 1000)).toEqual({
      usd: null,
      selfHosted: false,
    });
  });
});
