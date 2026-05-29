interface LLMProviderBadgeProps {
  model: string;
}

export function LLMProviderBadge({ model }: LLMProviderBadgeProps) {
  return (
    <span
      className="font-mono text-[10px] uppercase tracking-wide text-stone-500 bg-stone-100 border border-stone-200 rounded px-1.5 py-0.5"
      title="Model that produced this response"
    >
      {model}
    </span>
  );
}
