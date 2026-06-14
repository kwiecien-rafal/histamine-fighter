import { useState } from "react";

import { estimateCost, PRICES_UPDATED } from "../lib/pricing";
import { useUsageStore, type TokenTotals } from "../store/usage";

function formatTokens(count: number): string {
  if (count < 1000) return String(count);
  return `${(count / 1000).toFixed(count < 10_000 ? 1 : 0)}k`;
}

function formatUsd(usd: number | null): string {
  if (usd === null) return "—";
  if (usd === 0) return "$0";
  if (usd < 0.0001) return "<$0.0001";
  return `~$${usd.toFixed(usd >= 1 ? 2 : 4)}`;
}

function costInfo(model: string, inputTokens: number, outputTokens: number) {
  const cost = estimateCost(model, inputTokens, outputTokens);
  return {
    label: cost.selfHosted ? "$0" : formatUsd(cost.usd),
    selfHosted: cost.selfHosted,
    unpriced: cost.usd === null && !cost.selfHosted,
  };
}

// Session total: sum the priced and self-hosted models, ignore the unpriced ones.
// "—" only when no model has a known price, so a partly-priced session still
// shows a figure and the per-model rows reveal any gaps.
function sessionCost(byModel: Record<string, TokenTotals>): number | null {
  let sum = 0;
  let anyKnown = false;
  for (const [model, totals] of Object.entries(byModel)) {
    const { usd } = estimateCost(model, totals.inputTokens, totals.outputTokens);
    if (usd !== null) {
      sum += usd;
      anyKnown = true;
    }
  }
  return anyKnown ? sum : null;
}

function callsLabel(calls: number): string {
  return `${calls} ${calls === 1 ? "call" : "calls"}`;
}

export function UsagePanel() {
  const [expanded, setExpanded] = useState(false);
  const totals = useUsageStore((s) => s.totals);
  const byModel = useUsageStore((s) => s.byModel);
  const recentCalls = useUsageStore((s) => s.recentCalls);
  const startedAt = useUsageStore((s) => s.startedAt);
  const reset = useUsageStore((s) => s.reset);

  const cost = sessionCost(byModel);
  const models = Object.entries(byModel).sort((a, b) => b[1].calls - a[1].calls);
  const since = new Date(startedAt).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });

  const summary = `${callsLabel(totals.calls)} · ${formatTokens(totals.totalTokens)} tok · ${formatUsd(cost)}`;

  if (!expanded) {
    return (
      <button
        type="button"
        onClick={() => setExpanded(true)}
        aria-label="Show LLM usage details"
        className="fixed inset-x-0 bottom-0 z-40 flex cursor-pointer items-center justify-center gap-2 border-t border-stone-200 bg-white/95 px-4 py-2 text-sm text-stone-600 backdrop-blur hover:text-stone-900"
      >
        <span aria-hidden className="text-lg leading-none">
          ▴
        </span>
        <span className="font-mono">{summary}</span>
      </button>
    );
  }

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col justify-end"
      role="dialog"
      aria-modal="true"
      aria-label="LLM usage details"
    >
      <button
        type="button"
        aria-label="Close usage details"
        className="flex-1 bg-stone-900/30"
        onClick={() => setExpanded(false)}
      />
      <section className="max-h-[80vh] overflow-y-auto border-t border-stone-200 bg-white shadow-xl">
        <div className="mx-auto max-w-xl px-5 py-4">
          <header className="mb-3 flex items-center justify-between">
            <h2 className="text-sm font-semibold">
              Usage{" "}
              <span className="font-normal text-stone-500">· since {since}</span>
            </h2>
            <div className="flex items-center gap-3">
              <button
                type="button"
                onClick={reset}
                className="cursor-pointer rounded border border-red-200 bg-red-50 px-2 py-0.5 text-xs font-medium text-red-700 hover:bg-red-100"
              >
                Reset
              </button>
              <button
                type="button"
                aria-label="Minimize usage details"
                onClick={() => setExpanded(false)}
                className="cursor-pointer text-2xl leading-none text-stone-500 hover:text-stone-900"
              >
                ▾
              </button>
            </div>
          </header>

          <div className="mb-3 flex items-baseline justify-between border-b border-stone-100 pb-3">
            <div className="text-sm text-stone-700">
              <div>
                <span className="font-semibold">{callsLabel(totals.calls)}</span> this session
              </div>
              <div className="text-xs text-stone-500">≈ {formatUsd(cost)} total</div>
            </div>
            <div className="text-right">
              <div className="font-mono text-sm text-stone-700">
                {formatTokens(totals.totalTokens)} tokens
              </div>
              <div className="font-mono text-xs text-stone-500">
                {formatTokens(totals.inputTokens)} in ▸ {formatTokens(totals.outputTokens)} out
              </div>
            </div>
          </div>

          {models.length > 0 && (
            <ul className="mb-3 space-y-1.5">
              {models.map(([model, modelTotals]) => {
                const { label, selfHosted, unpriced } = costInfo(
                  model,
                  modelTotals.inputTokens,
                  modelTotals.outputTokens,
                );
                return (
                  <li key={model} className="flex items-center justify-between gap-2 text-sm">
                    <span className="truncate rounded border border-stone-200 bg-stone-100 px-1.5 py-0.5 font-mono text-[11px] text-stone-500">
                      {model}
                    </span>
                    <span
                      className="whitespace-nowrap font-mono text-xs text-stone-600"
                      title={unpriced ? "No price set for this model — add it in pricing.ts" : undefined}
                    >
                      {modelTotals.calls}× · {formatTokens(modelTotals.totalTokens)} tok ·{" "}
                      {selfHosted ? "$0 self-hosted" : label}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}

          {recentCalls.length > 0 && (
            <div className="mb-3">
              <h3 className="mb-1.5 text-xs uppercase tracking-wide text-stone-500">
                Recent calls
              </h3>
              <ul className="space-y-1">
                {recentCalls.map((call) => (
                  <li
                    key={call.id}
                    className="flex items-center gap-3 text-xs text-stone-600"
                  >
                    <span className="w-20 shrink-0 capitalize">{call.endpoint}</span>
                    <span className="min-w-0 flex-1 truncate font-mono text-stone-400">
                      {call.model}
                    </span>
                    <span className="w-16 shrink-0 text-right font-mono">
                      {formatTokens(call.usage.total_tokens)} tok
                    </span>
                    <span className="w-16 shrink-0 text-right font-mono">
                      {costInfo(call.model, call.usage.input_tokens, call.usage.output_tokens).label}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <p className="text-[11px] text-stone-400">
            Approximate, from public list prices (updated {PRICES_UPDATED}). Self-hosted models
            are shown as $0. Counts include calls that errored.
          </p>
        </div>
      </section>
    </div>
  );
}
