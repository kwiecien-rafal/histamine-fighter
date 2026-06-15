import { create } from "zustand";
import { persist } from "zustand/middleware";

import type { LLMUsage } from "../api/client";

export interface TokenTotals {
  calls: number;
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  // Calls whose provider returned no token usage. When this equals `calls` the
  // token figures are unknown, not zero, so the panel shows them as "—".
  unreportedCalls: number;
}

export interface RecordedCall {
  id: string;
  endpoint: string;
  model: string;
  usage: LLMUsage;
  at: number;
}

interface UsageState {
  startedAt: number;
  totals: TokenTotals;
  byModel: Record<string, TokenTotals>;
  recentCalls: RecordedCall[];
  record: (endpoint: string, model: string, usage: LLMUsage) => void;
  reset: () => void;
}

// Cap the per-call log; it only feeds the breakdown view, so older entries add
// nothing but storage.
const RECENT_LIMIT = 20;

const ZERO_TOTALS: TokenTotals = {
  calls: 0,
  inputTokens: 0,
  outputTokens: 0,
  totalTokens: 0,
  unreportedCalls: 0,
};

function add(totals: TokenTotals, usage: LLMUsage): TokenTotals {
  return {
    calls: totals.calls + usage.calls,
    inputTokens: totals.inputTokens + usage.input_tokens,
    outputTokens: totals.outputTokens + usage.output_tokens,
    totalTokens: totals.totalTokens + usage.total_tokens,
    unreportedCalls:
      totals.unreportedCalls + usage.steps.filter((step) => !step.reported).length,
  };
}

// Persisted to localStorage so the tally survives a reload and an accidental tab
// close; only reset() clears it. Counts only, never dish text, so it is safe to
// keep in the browser.
export const useUsageStore = create<UsageState>()(
  persist(
    (set) => ({
      startedAt: Date.now(),
      totals: ZERO_TOTALS,
      byModel: {},
      recentCalls: [],
      record: (endpoint, model, usage) =>
        set((state) => ({
          totals: add(state.totals, usage),
          byModel: {
            ...state.byModel,
            [model]: add(state.byModel[model] ?? ZERO_TOTALS, usage),
          },
          recentCalls: [
            { id: crypto.randomUUID(), endpoint, model, usage, at: Date.now() },
            ...state.recentCalls,
          ].slice(0, RECENT_LIMIT),
        })),
      reset: () =>
        set({
          startedAt: Date.now(),
          totals: ZERO_TOTALS,
          byModel: {},
          recentCalls: [],
        }),
    }),
    {
      name: "histamine-fighter:usage",
      version: 2,
      // v1 had no unreportedCalls; backfill zero (past calls predate the field
      // and are treated as reported) so rehydrated totals stay numeric.
      migrate: (persisted, version) => {
        const state = persisted as UsageState;
        if (version >= 2) return state;
        const withField = (totals: TokenTotals): TokenTotals => ({
          ...totals,
          unreportedCalls: 0,
        });
        return {
          ...state,
          totals: withField(state.totals),
          byModel: Object.fromEntries(
            Object.entries(state.byModel).map(([model, totals]) => [model, withField(totals)]),
          ),
        };
      },
    },
  ),
);
