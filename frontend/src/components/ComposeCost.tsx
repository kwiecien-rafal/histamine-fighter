import type { LLMUsage } from "../api/client";
import { summarizeUsage } from "../lib/usage";

interface ComposeCostProps {
  usage: LLMUsage;
  model: string;
}

// A compact transparency line for one composition: how many model calls it took,
// the token count, and the approximate cost. Reads "—" where the provider did not
// meter the call, never a misleading $0.
export function ComposeCost({ usage, model }: ComposeCostProps) {
  if (usage.calls === 0) return null;
  const summary = summarizeUsage(usage, model);
  return (
    <span className="font-mono text-[11px] text-stone-500" title={summary.title}>
      {summary.calls} · {summary.tokens} · {summary.cost}
    </span>
  );
}
