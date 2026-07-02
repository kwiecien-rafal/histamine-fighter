import { modelAttribution } from "../lib/meal";

interface LLMProviderBadgeProps {
  model: string;
}

// The provenance chip on an AI output: the model that produced it, or "Curated by admin"
// for a hand-authored meal. The wording lives in modelAttribution so the badge and the
// public card read the sentinel one way.
export function LLMProviderBadge({ model }: LLMProviderBadgeProps) {
  const { label, title } = modelAttribution(model);
  return (
    <span
      className="font-mono text-[10px] uppercase tracking-wide text-stone-500 bg-stone-100 border border-stone-200 rounded px-1.5 py-0.5"
      title={title}
    >
      {label}
    </span>
  );
}
