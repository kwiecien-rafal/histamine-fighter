import { useState } from "react";

import { useDismissableOverlay } from "../hooks/useDismissableOverlay";
import { estimateCost, PRICES_UPDATED } from "../lib/pricing";
import {
  cellTexts,
  callsLabel,
  formatTokens,
  formatUsd,
  reportedness,
  tokenCell,
  usageTotals as asTotals,
} from "../lib/usage";
import { useUsageStore, type TokenTotals } from "../store/usage";

// Session total: sum the metered, priced and self-hosted models; skip the
// unmetered and the unpriced. An empty session reads $0 (nothing spent yet), but
// a session whose every cost is unknown reads "—" so it never implies a false $0.
function sessionCost(byModel: Record<string, TokenTotals>): number | null {
  const entries = Object.entries(byModel);
  if (entries.length === 0) return 0;
  let sum = 0;
  let anyKnown = false;
  for (const [model, totals] of entries) {
    if (reportedness(totals.calls, totals.unreportedCalls) === "none") continue;
    const { usd } = estimateCost(model, totals.inputTokens, totals.outputTokens);
    if (usd !== null) {
      sum += usd;
      anyKnown = true;
    }
  }
  return anyKnown ? sum : null;
}

export function UsagePanel() {
  const [expanded, setExpanded] = useState(false);
  const totals = useUsageStore((s) => s.totals);
  const byModel = useUsageStore((s) => s.byModel);
  const recentCalls = useUsageStore((s) => s.recentCalls);
  const startedAt = useUsageStore((s) => s.startedAt);
  const reset = useUsageStore((s) => s.reset);
  const panelRef = useDismissableOverlay<HTMLElement>(expanded, () => setExpanded(false));

  const cost = sessionCost(byModel);
  const rep = reportedness(totals.calls, totals.unreportedCalls);
  const models = Object.entries(byModel).sort((a, b) => b[1].calls - a[1].calls);
  const since = new Date(startedAt).toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
  });

  const summary = `${callsLabel(totals.calls)} · ${tokenCell(totals.totalTokens, rep)} · ${formatUsd(cost)}`;

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
      <section
        ref={panelRef}
        tabIndex={-1}
        className="max-h-[80vh] overflow-y-auto border-t border-stone-200 bg-white shadow-xl focus:outline-none"
      >
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
                {tokenCell(totals.totalTokens, rep, "tokens")}
              </div>
              <div className="font-mono text-xs text-stone-500">
                {rep === "none"
                  ? "—"
                  : `${formatTokens(totals.inputTokens)} in ▸ ${formatTokens(totals.outputTokens)} out`}
              </div>
            </div>
          </div>

          {models.length > 0 && (
            <ul className="mb-3 space-y-1.5">
              {models.map(([model, modelTotals]) => {
                const texts = cellTexts(model, modelTotals, true);
                return (
                  <li key={model} className="flex items-center justify-between gap-2 text-sm">
                    <span className="truncate rounded border border-stone-200 bg-stone-100 px-1.5 py-0.5 font-mono text-[11px] text-stone-500">
                      {model}
                    </span>
                    <span
                      className="whitespace-nowrap font-mono text-xs text-stone-600"
                      title={texts.title}
                    >
                      {modelTotals.calls}× · {texts.tokens} · {texts.cost}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}

          {recentCalls.length > 0 && (
            <div className="mb-3">
              <h3 className="mb-1.5 text-xs uppercase tracking-wide text-stone-500">Recent calls</h3>
              <ul className="space-y-1.5">
                {recentCalls.map((call) => {
                  const texts = cellTexts(call.model, asTotals(call.usage));
                  return (
                    <li key={call.id} className="flex flex-col gap-1 text-xs text-stone-600">
                      <div className="flex items-center gap-3">
                        <span className="w-20 shrink-0 capitalize">{call.endpoint}</span>
                        <span className="min-w-0 flex-1 truncate font-mono text-stone-400">
                          {call.model}
                        </span>
                        <span className="w-16 shrink-0 text-right font-mono" title={texts.title}>
                          {texts.tokens}
                        </span>
                        <span className="w-16 shrink-0 text-right font-mono">{texts.cost}</span>
                      </div>
                      {call.usage.steps.length > 0 && (
                        <div className="flex flex-wrap gap-1 pl-20">
                          {call.usage.steps.map((step, index) => (
                            <span
                              key={index}
                              title={
                                step.reported
                                  ? `${formatTokens(step.total_tokens)} tok`
                                  : "Provider reported no token usage"
                              }
                              className="rounded bg-stone-100 px-1.5 py-0.5 font-mono text-[10px] text-stone-400"
                            >
                              {step.step}
                            </span>
                          ))}
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          <p className="text-[11px] text-stone-400">
            Approximate list prices (updated {PRICES_UPDATED}); prompt caching is not modelled.
            Self-hosted models show as $0, unmetered calls as —. Counts include calls that errored.
          </p>
        </div>
      </section>
    </div>
  );
}
