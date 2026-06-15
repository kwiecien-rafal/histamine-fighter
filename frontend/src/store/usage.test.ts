import { beforeEach, describe, expect, it } from "vitest";

import type { LLMUsage } from "../api/client";
import { useUsageStore } from "./usage";

function usage(overrides: Partial<LLMUsage> = {}): LLMUsage {
  return {
    calls: 1,
    input_tokens: 10,
    output_tokens: 5,
    total_tokens: 15,
    steps: [],
    ...overrides,
  };
}

beforeEach(() => {
  localStorage.clear();
  useUsageStore.getState().reset();
});

describe("useUsageStore", () => {
  it("accumulates session totals and the per-model tally", () => {
    const { record } = useUsageStore.getState();
    record("propose", "openai/gpt-4o-mini", usage());
    record(
      "assess",
      "openai/gpt-4o-mini",
      usage({ calls: 2, input_tokens: 20, output_tokens: 10, total_tokens: 30 }),
    );

    const { totals, byModel } = useUsageStore.getState();
    const expected = {
      calls: 3,
      inputTokens: 30,
      outputTokens: 15,
      totalTokens: 45,
      unreportedCalls: 0,
    };
    expect(totals).toEqual(expected);
    expect(byModel["openai/gpt-4o-mini"]).toEqual(expected);
  });

  it("counts calls whose provider reported no usage", () => {
    const { record } = useUsageStore.getState();
    record("propose", "legacy/local", {
      calls: 1,
      input_tokens: 0,
      output_tokens: 0,
      total_tokens: 0,
      steps: [{ step: "propose", input_tokens: 0, output_tokens: 0, total_tokens: 0, reported: false }],
    });

    expect(useUsageStore.getState().totals.unreportedCalls).toBe(1);
  });

  it("keeps a separate tally per model", () => {
    const { record } = useUsageStore.getState();
    record("propose", "openai/gpt-4o-mini", usage());
    record("propose", "ollama/llama3", usage());

    expect(Object.keys(useUsageStore.getState().byModel)).toEqual([
      "openai/gpt-4o-mini",
      "ollama/llama3",
    ]);
  });

  it("caps the recent-calls log", () => {
    const { record } = useUsageStore.getState();
    for (let i = 0; i < 25; i += 1) {
      record("propose", "openai/gpt-4o-mini", usage());
    }

    expect(useUsageStore.getState().recentCalls).toHaveLength(20);
  });

  it("reset clears the ledger and re-stamps the window", () => {
    const before = useUsageStore.getState().startedAt;
    useUsageStore.getState().record("propose", "openai/gpt-4o-mini", usage());

    useUsageStore.getState().reset();

    const state = useUsageStore.getState();
    expect(state.totals.calls).toBe(0);
    expect(state.byModel).toEqual({});
    expect(state.recentCalls).toEqual([]);
    expect(state.startedAt).toBeGreaterThanOrEqual(before);
  });
});
