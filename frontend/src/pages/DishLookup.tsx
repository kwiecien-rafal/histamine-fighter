import { useState } from "react";

import { MAX_DISH_CHARS } from "../api/client";
import { AlternativesPanel } from "../components/AlternativesPanel";
import { AssessmentResult } from "../components/AssessmentResult";
import { IngredientEditor } from "../components/IngredientEditor";
import { ThinkingBrawl } from "../components/ThinkingBrawl";
import { UsagePanel } from "../components/UsagePanel";
import { useDishLookupFlow } from "../hooks/useDishLookupFlow";
import { pivotTone } from "../lib/assessment";

interface EntryCardProps {
  title: string;
  image: string;
  // Full class strings per card so Tailwind sees them statically.
  cardClassName: string;
  titleClassName: string;
  onClick: () => void;
}

// One of the two big entry choices: tilted at rest like a card tossed on a
// table, straightens and grows on hover to invite the click.
function EntryCard({ title, image, cardClassName, titleClassName, onClick }: EntryCardProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`group flex h-full w-full cursor-pointer flex-col items-center gap-4 border-[3px] bg-white/60 p-6 pb-7 shadow-sm transition-[transform,box-shadow] duration-200 ease-out hover:shadow-xl focus-visible:shadow-xl focus-visible:outline-none focus-visible:ring-4 motion-safe:hover:rotate-0 motion-safe:hover:scale-[1.06] motion-safe:focus-visible:rotate-0 motion-safe:focus-visible:scale-[1.06] ${cardClassName}`}
    >
      <img
        src={image}
        alt=""
        className="h-36 w-auto max-w-full object-contain transition-transform duration-200 motion-safe:group-hover:scale-105"
      />
      <span className={`font-hand text-2xl leading-tight ${titleClassName}`}>{title}</span>
    </button>
  );
}

export function DishLookup() {
  const [dish, setDish] = useState("");
  const [mode, setMode] = useState<"choose" | "dish">("choose");
  const {
    state,
    propose,
    startManual,
    renameDish,
    renameIngredient,
    removeIngredient,
    addIngredient,
    confirm,
    requestAlternatives,
    generateRecipe,
    startOver,
  } = useDishLookupFlow();

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void propose(dish);
  }

  // Picking a suggestion re-enters the normal flow with that dish, so it gets
  // the same propose -> confirm -> assess vetting as anything typed by hand.
  function pickAlternative(name: string) {
    setDish(name);
    setMode("dish");
    void propose(name);
  }

  function resetToChooser() {
    startOver();
    setMode("choose");
    setDish("");
  }

  const proposing = state.phase === "proposing";
  const manualEntry =
    (state.phase === "editing" || state.phase === "assessing") && state.model === null;
  const resultTone = state.phase === "result" ? pivotTone(state.result) : null;

  return (
    <>
      <div className="max-w-2xl mx-auto">
        <h1 className="font-serif text-3xl font-semibold text-forest-900 mb-1">Is your dish safe?</h1>
        <p className="text-stone-600 mb-8">
          Ask whether a dish is safe for histamine intolerance.
        </p>

        {state.phase === "idle" && mode === "choose" && (
          <div className="grid grid-cols-1 items-stretch gap-4 py-2 sm:grid-cols-[1fr_auto_1fr] sm:gap-5">
            <EntryCard
              title="Start with just a dish name"
              image="/images/dish-name-sign.png"
              cardClassName="-rotate-1 rounded-[2rem_1.2rem_1.8rem_1.4rem] border-ember-700/80 focus-visible:ring-ember-200"
              titleClassName="text-ember-700"
              onClick={() => setMode("dish")}
            />
            <span
              aria-hidden="true"
              className="select-none place-self-center font-hand text-3xl text-stone-400 -rotate-6"
            >
              or
            </span>
            <EntryCard
              title="Enter known ingredients"
              image="/images/ingredient-scroll.png"
              cardClassName="rotate-[1.2deg] rounded-[1.3rem_1.9rem_1.2rem_2rem] border-forest-700/80 focus-visible:ring-forest-200"
              titleClassName="text-forest-800"
              onClick={() => startManual("")}
            />
          </div>
        )}

        {((state.phase === "idle" && mode === "dish") || proposing) && (
          <>
            <form onSubmit={onSubmit} className="flex gap-2 mb-6">
              <input
                type="text"
                value={dish}
                onChange={(e) => setDish(e.target.value)}
                placeholder="e.g. Spaghetti Bolognese"
                maxLength={MAX_DISH_CHARS}
                disabled={proposing}
                className="flex-1 rounded border border-stone-300 px-3 py-2 focus:outline-none focus:border-forest-700 disabled:opacity-50"
              />
              <button
                type="submit"
                disabled={proposing || !dish.trim()}
                className="rounded bg-forest-800 hover:bg-forest-700 text-white px-4 py-2 disabled:opacity-50 enabled:cursor-pointer"
              >
                {proposing ? "Finding…" : "Find ingredients"}
              </button>
            </form>
            {state.phase === "idle" && state.error && (
              <p className="text-red-700">{state.error}</p>
            )}
            {proposing && (
              <ThinkingBrawl label="Fighting through the ingredient list…" className="mb-4" />
            )}
            <p className="text-sm">
              <button
                type="button"
                disabled={proposing}
                onClick={() => setMode("choose")}
                className="text-stone-600 underline underline-offset-2 hover:text-stone-900 disabled:opacity-50 disabled:no-underline enabled:cursor-pointer"
              >
                ← Back to both options
              </button>
            </p>
          </>
        )}

        {state.phase === "unrecognized" && (
          <div role="status" className="rounded border border-amber-300 bg-amber-50 p-5">
            <h2 className="text-lg font-medium text-stone-900 mb-1">
              Unknown dish: {state.dish}
            </h2>
            <p className="text-stone-700 mb-4">
              We couldn't recognise this dish. Try a different
              one, or enter the ingredients yourself.
            </p>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => {
                  startOver();
                  setMode("dish");
                }}
                className="rounded bg-forest-800 hover:bg-forest-700 text-white px-4 py-2 text-sm cursor-pointer"
              >
                Try another name
              </button>
              <button
                type="button"
                onClick={() => startManual(state.dish)}
                className="rounded border border-forest-800 px-4 py-2 text-sm text-forest-800 hover:bg-forest-800/5 cursor-pointer"
              >
                Enter ingredients myself
              </button>
            </div>
          </div>
        )}

        {(state.phase === "editing" || state.phase === "assessing") && (
          <>
            {!manualEntry && <h2 className="text-lg font-medium mb-3">{state.dish}</h2>}
            {state.phase === "editing" && state.cached && (
              <p className="text-xs text-stone-500 -mt-2 mb-3">
                Ingredient list shown from a previous check — review it as usual.
              </p>
            )}
            <IngredientEditor
              dish={state.dish}
              ingredients={state.ingredients}
              error={state.phase === "editing" ? state.error : null}
              busy={state.phase === "assessing"}
              onRenameDish={manualEntry ? renameDish : undefined}
              onRename={renameIngredient}
              onRemove={removeIngredient}
              onAdd={addIngredient}
              onConfirm={() => void confirm()}
              onStartOver={resetToChooser}
            />
            {manualEntry && (
              <p className="mt-4 text-sm">
                <button
                  type="button"
                  disabled={state.phase === "assessing"}
                  onClick={resetToChooser}
                  className="text-stone-600 underline underline-offset-2 hover:text-stone-900 disabled:opacity-50 disabled:no-underline enabled:cursor-pointer"
                >
                  ← Back to both options
                </button>
              </p>
            )}
          </>
        )}

        {state.phase === "result" && (
          <>
            {state.result.cached && (
              <p className="text-xs text-stone-500 mb-2">
                Instant answer — this exact dish and ingredient list was checked
                before, and nothing it relied on has changed in our index.
              </p>
            )}
            <AssessmentResult
              result={state.result}
              resultId={state.resultId}
              recipe={state.recipe}
              onGenerateRecipe={() => void generateRecipe()}
            />
            {resultTone && (
              <AlternativesPanel
                alternatives={state.alternatives}
                tone={resultTone}
                result={state.result}
                onChooseGoal={(goal) => void requestAlternatives(goal)}
                onPick={pickAlternative}
              />
            )}
            <div className="mt-8">
              <button
                type="button"
                onClick={resetToChooser}
                className="rounded border border-red-700 px-4 py-2 text-sm text-red-700 hover:bg-red-50 cursor-pointer"
              >
                Start over
              </button>
            </div>
          </>
        )}
      </div>

      <UsagePanel />
    </>
  );
}
