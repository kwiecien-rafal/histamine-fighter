import type { AdminDailySuggestion } from "../api/admin";
import { MEAL_TYPE_LABEL } from "../lib/meal";
import { ComposeCost } from "./ComposeCost";
import { LLMProviderBadge } from "./LLMProviderBadge";
import { ReviewActions } from "./ReviewActions";
import { TraceDetails } from "./TraceDetails";
import { UnverifiedNote } from "./UnverifiedNote";

interface DailySuggestionCardProps {
  suggestion: AdminDailySuggestion;
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
}

export function DailySuggestionCard({
  suggestion,
  busy,
  onApprove,
  onReject,
}: DailySuggestionCardProps) {
  const { content } = suggestion;
  return (
    <article className="rounded border border-stone-200 bg-white p-5">
      <div className="flex items-start justify-between gap-3 mb-1">
        <h3 className="text-lg font-medium">{content.name}</h3>
        <div className="flex items-center gap-2 shrink-0">
          <span className="font-mono text-[10px] uppercase tracking-wide text-emerald-800 bg-emerald-50 border border-emerald-200 rounded px-1.5 py-0.5">
            {MEAL_TYPE_LABEL[suggestion.meal_type]}
          </span>
          <LLMProviderBadge model={suggestion.model} />
        </div>
      </div>
      <p className="text-xs text-stone-500 mb-1">
        Reveals {new Date(suggestion.reveal_at).toLocaleString()}
      </p>
      {suggestion.usage && (
        <div className="mb-1">
          <ComposeCost usage={suggestion.usage} model={suggestion.model} />
        </div>
      )}
      <p className="text-sm text-stone-600 mb-4">{content.description}</p>

      <h4 className="text-xs font-semibold uppercase tracking-wide text-stone-500 mb-2">
        Ingredients
      </h4>
      <ul className="flex flex-wrap gap-1.5 mb-4">
        {content.ingredients.map((ingredient, index) => (
          <li
            key={`${ingredient.name}-${index}`}
            className="rounded border border-stone-200 bg-stone-50 px-2 py-0.5 text-sm"
          >
            {ingredient.name}
            {ingredient.category && (
              <span className="text-stone-400"> · {ingredient.category}</span>
            )}
          </li>
        ))}
      </ul>

      <UnverifiedNote ingredients={content.unverified_ingredients} />

      <TraceDetails trace={suggestion.reasoning_trace} />

      <ReviewActions busy={busy} onApprove={onApprove} onReject={onReject} />
    </article>
  );
}
