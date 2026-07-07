import type { DishAssessmentResponse } from "../api/client";
import type { SaveTarget } from "../api/saves";
import type { RecipeState } from "../hooks/useDishLookupFlow";
import { pivotTone } from "../lib/assessment";
import { AdaptationList } from "./AdaptationList";
import { AdvisoryList } from "./AdvisoryList";
import { IngredientSafetyChip } from "./IngredientSafetyChip";
import { SaveButton } from "./SaveButton";
import { LLMProviderBadge } from "./LLMProviderBadge";
import { MedicalNote } from "./MedicalNote";
import { ThinkingBrawl } from "./ThinkingBrawl";
import { VerdictBadge } from "./VerdictBadge";

interface AssessmentResultProps {
  result: DishAssessmentResponse;
  // Identity of this one result, minted by the flow; keys the lookup save.
  resultId: string;
  recipe: RecipeState;
  onGenerateRecipe: () => void;
}

// The final assessed dish is the lookup flow's "final form", so the save target is
// built here: the snapshot the server stores is exactly what this card shows,
// generated recipe included.
function saveTarget(
  result: DishAssessmentResponse,
  resultId: string,
  recipe: RecipeState,
): SaveTarget {
  return {
    source: "lookup",
    payload: {
      lookup_id: resultId,
      dish: result.dish,
      verdict: result.verdict,
      description: result.explanation,
      ingredients: result.ingredients.map((item) => ({ name: item.name, category: null })),
      model: result.model,
      recipe: recipe.status === "loaded" ? recipe.steps : null,
      recipe_model: recipe.status === "loaded" ? recipe.model : null,
    },
  };
}

export function AssessmentResult({
  result,
  resultId,
  recipe,
  onGenerateRecipe,
}: AssessmentResultProps) {
  const tone = pivotTone(result);
  return (
    <article className="rounded border border-stone-200 bg-white p-5">
      <header className="flex items-start justify-between gap-3 mb-4">
        <h2 className="text-lg font-medium">{result.dish}</h2>
        <LLMProviderBadge model={result.model} />
      </header>

      <section className="mb-4">
        <VerdictBadge verdict={result.verdict} />
        <div className="mt-2">
          <MedicalNote />
        </div>
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

      {/* The offer stays on every verdict — eating the dish anyway is the
          user's call — but on avoid the wording acknowledges the verdict
          instead of cheerfully contradicting it. */}
      <section className="mt-4 border-t border-stone-100 pt-4">
        {recipe.status === "loaded" ? (
          <>
            <header className="flex items-start justify-between gap-3 mb-2">
              <h3 className="text-xs uppercase tracking-wide text-stone-500">Recipe</h3>
              <LLMProviderBadge model={recipe.model} />
            </header>
            <ol className="list-decimal list-outside ml-5 flex flex-col gap-1.5 text-stone-700">
              {recipe.steps.map((step, index) => (
                <li key={index}>{step}</li>
              ))}
            </ol>
          </>
        ) : recipe.status === "loading" ? (
          <ThinkingBrawl label="Writing the recipe…" />
        ) : (
          <>
            {recipe.status === "error" && (
              <p role="alert" className="text-red-700 text-sm mb-2">
                <span className="font-medium">Couldn't write the recipe —</span>{" "}
                {recipe.message}
              </p>
            )}
            <button
              type="button"
              onClick={onGenerateRecipe}
              className="text-sm text-forest-800 font-medium underline underline-offset-2 hover:text-forest-700 cursor-pointer"
            >
              {recipe.status === "error"
                ? "Try the recipe again →"
                : result.verdict === "avoid"
                  ? "Like the dish, in spite of our verdict? Generate a recipe →"
                  : "Like this dish? Generate a recipe →"}
            </button>
          </>
        )}
      </section>

      <footer className="mt-4 flex justify-end">
        <SaveButton target={saveTarget(result, resultId, recipe)} />
      </footer>
    </article>
  );
}
