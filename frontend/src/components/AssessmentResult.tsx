import type { DishAssessmentResponse } from "../api/client";
import { pivotTone } from "../lib/assessment";
import { AdaptationList } from "./AdaptationList";
import { AdvisoryList } from "./AdvisoryList";
import { IngredientSafetyChip } from "./IngredientSafetyChip";
import { LLMProviderBadge } from "./LLMProviderBadge";
import { VerdictBadge } from "./VerdictBadge";

interface AssessmentResultProps {
  result: DishAssessmentResponse;
  onStartOver: () => void;
}

export function AssessmentResult({ result, onStartOver }: AssessmentResultProps) {
  const tone = pivotTone(result);
  return (
    <article className="rounded border border-stone-200 bg-white p-5">
      <header className="flex items-start justify-between gap-3 mb-4">
        <h2 className="text-lg font-medium">{result.dish}</h2>
        <LLMProviderBadge model={result.model} />
      </header>

      <section className="mb-4">
        <VerdictBadge verdict={result.verdict} />
      </section>

      <section className="mb-4">
        <h3 className="text-xs uppercase tracking-wide text-stone-500 mb-2">
          Ingredients you confirmed
        </h3>
        <div className="flex flex-wrap gap-1.5">
          {result.ingredients.map((assessment, index) => (
            // a rename can leave duplicate names, so the key needs the index
            <IngredientSafetyChip
              key={`${assessment.name}-${index}`}
              assessment={assessment}
            />
          ))}
        </div>
      </section>

      <section className="mb-4">
        <h3 className="text-xs uppercase tracking-wide text-stone-500 mb-1">
          Why
        </h3>
        <p className="text-stone-700">{result.explanation}</p>
      </section>

      {result.adaptations.length > 0 && (
        <AdaptationList adaptations={result.adaptations} />
      )}

      {result.advisories.length > 0 && (
        <AdvisoryList advisories={result.advisories} />
      )}

      {tone === "lost" ? (
        <section
          role="status"
          className="mb-4 rounded border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900"
        >
          These changes would lose what makes this dish itself — a different
          dish may serve you better.
        </section>
      ) : tone === "altered" ? (
        <section
          role="status"
          className="mb-4 rounded border border-stone-200 bg-stone-50 px-3 py-2 text-sm text-stone-700"
        >
          Adapting this changes a core part of the dish. Prefer something closer
          to the original? The ideas below may help.
        </section>
      ) : (
        tone === "unresolved" && (
          <section
            role="status"
            className="mb-4 rounded border border-stone-200 bg-stone-50 px-3 py-2 text-sm text-stone-700"
          >
            Some of this dish has no safe fix — the suggestions below may help.
          </section>
        )
      )}

      <footer>
        <button
          type="button"
          onClick={onStartOver}
          className="text-sm text-stone-600 hover:text-stone-900 underline underline-offset-4"
        >
          Start over
        </button>
      </footer>
    </article>
  );
}
