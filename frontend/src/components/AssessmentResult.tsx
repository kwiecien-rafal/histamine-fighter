import type { DishAssessmentResponse } from "../api/client";
import { IngredientSafetyChip } from "./IngredientSafetyChip";
import { LLMProviderBadge } from "./LLMProviderBadge";
import { VerdictBadge } from "./VerdictBadge";

interface AssessmentResultProps {
  result: DishAssessmentResponse;
  onStartOver: () => void;
}

export function AssessmentResult({ result, onStartOver }: AssessmentResultProps) {
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

      {result.verdict !== "safe" && result.replacements.length > 0 && (
        <section className="mb-4">
          <h3 className="text-xs uppercase tracking-wide text-stone-500 mb-2">
            Safer swaps
          </h3>
          <ul className="flex flex-col gap-2">
            {result.replacements.map((r) => (
              <li
                key={`${r.ingredient}-${r.swap}`}
                className="rounded border border-stone-200 bg-stone-50 px-3 py-2 text-sm"
              >
                <span className="text-stone-500 line-through">
                  {r.ingredient}
                </span>
                <span className="mx-2 text-stone-400">→</span>
                <span className="font-medium text-emerald-800">{r.swap}</span>
                <p className="text-stone-600 mt-0.5">{r.reason}</p>
              </li>
            ))}
          </ul>
        </section>
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
