import type { LLMUsage } from "../api/client";
import { estimateCost } from "./pricing";

// Token and cost formatting shared by the session usage panel and the per-meal
// compose-cost badge, so both render the same way and honour how much of a call
// the provider actually metered.

export function formatTokens(count: number): string {
  if (count < 1000) return String(count);
  if (count < 1_000_000) return `${(count / 1000).toFixed(count < 10_000 ? 1 : 0)}k`;
  return `${(count / 1_000_000).toFixed(count < 10_000_000 ? 1 : 0)}M`;
}

export function formatUsd(usd: number | null): string {
  if (usd === null) return "—";
  if (usd === 0) return "$0";
  if (usd < 0.0001) return "<$0.0001";
  return `~$${usd.toFixed(usd >= 1 ? 2 : 4)}`;
}

// A provider may not meter a call. "none" means every call went unmetered, so the
// token figures are unknown (rendered "—") rather than zero; "partial" means only
// some did, so the figures are a lower bound (marked "~").
export type Reportedness = "full" | "partial" | "none";

export function reportedness(calls: number, unreportedCalls: number): Reportedness {
  if (unreportedCalls === 0) return "full";
  if (unreportedCalls >= calls) return "none";
  return "partial";
}

export function tokenCell(totalTokens: number, rep: Reportedness, unit = "tok"): string {
  if (rep === "none") return "—";
  return `${rep === "partial" ? "~" : ""}${formatTokens(totalTokens)} ${unit}`;
}

export function callsLabel(calls: number): string {
  return `${calls} ${calls === 1 ? "call" : "calls"}`;
}

export interface UsageTotals {
  calls: number;
  inputTokens: number;
  outputTokens: number;
  totalTokens: number;
  unreportedCalls: number;
}

export interface CellTexts {
  tokens: string;
  cost: string;
  title?: string;
}

// The token and cost strings for one model's totals, honouring how much of it the
// provider actually metered. `selfHostedSuffix` widens "$0" to "$0 self-hosted"
// for the roomier by-model rows.
export function cellTexts(
  model: string,
  totals: UsageTotals,
  selfHostedSuffix = false,
): CellTexts {
  const rep = reportedness(totals.calls, totals.unreportedCalls);
  const tokens = tokenCell(totals.totalTokens, rep);
  if (rep === "none") {
    return { tokens, cost: "—", title: "Provider reported no token usage" };
  }
  const cost = estimateCost(model, totals.inputTokens, totals.outputTokens);
  const costText = cost.selfHosted
    ? selfHostedSuffix
      ? "$0 self-hosted"
      : "$0"
    : formatUsd(cost.usd);
  const title =
    rep === "partial"
      ? "Some calls were not metered; figures are a lower bound."
      : cost.usd === null && !cost.selfHosted
        ? "No price set for this model — add it in pricing.ts."
        : undefined;
  return { tokens, cost: costText, title };
}

// Fold one recorded LLMUsage into the totals shape the formatters use.
export function usageTotals(usage: LLMUsage): UsageTotals {
  return {
    calls: usage.calls,
    inputTokens: usage.input_tokens,
    outputTokens: usage.output_tokens,
    totalTokens: usage.total_tokens,
    unreportedCalls: usage.steps.filter((step) => !step.reported).length,
  };
}

export interface UsageSummary {
  calls: string;
  tokens: string;
  cost: string;
  title?: string;
}

// One composition's cost as compact strings: "12 calls", "~3k tok", "~$0.01".
export function summarizeUsage(usage: LLMUsage, model: string): UsageSummary {
  const texts = cellTexts(model, usageTotals(usage));
  return { calls: callsLabel(usage.calls), tokens: texts.tokens, cost: texts.cost, title: texts.title };
}
