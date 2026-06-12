import { useState } from "react";

import { MAX_DISH_CHARS } from "./api/client";
import { AssessmentResult } from "./components/AssessmentResult";
import { IngredientEditor } from "./components/IngredientEditor";
import { SettingsDrawer } from "./components/SettingsDrawer";
import { useDishLookupFlow } from "./hooks/useDishLookupFlow";

export function App() {
  const [dish, setDish] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false);
  const {
    state,
    propose,
    renameIngredient,
    removeIngredient,
    addIngredient,
    confirm,
    startOver,
  } = useDishLookupFlow();

  function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void propose(dish);
  }

  const proposing = state.phase === "proposing";

  return (
    <main className="min-h-screen bg-stone-50 text-stone-900 px-6 py-12">
      <div className="max-w-xl mx-auto">
        <header className="flex items-start justify-between mb-2">
          <h1 className="text-3xl font-semibold">Histamine Fighter</h1>
          <button
            type="button"
            onClick={() => setSettingsOpen(true)}
            className="text-sm text-stone-600 hover:text-stone-900 underline underline-offset-4"
          >
            Settings
          </button>
        </header>
        <p className="text-stone-600 mb-8">
          Ask whether a dish is safe for histamine intolerance.
        </p>

        {(state.phase === "idle" || proposing) && (
          <>
            <form onSubmit={onSubmit} className="flex gap-2 mb-6">
              <input
                type="text"
                value={dish}
                onChange={(e) => setDish(e.target.value)}
                placeholder="e.g. Spaghetti Bolognese"
                maxLength={MAX_DISH_CHARS}
                disabled={proposing}
                className="flex-1 rounded border border-stone-300 px-3 py-2 focus:outline-none focus:border-emerald-700 disabled:opacity-50"
              />
              <button
                type="submit"
                disabled={proposing || !dish.trim()}
                className="rounded bg-emerald-800 text-white px-4 py-2 disabled:opacity-50"
              >
                {proposing ? "Finding…" : "Find ingredients"}
              </button>
            </form>
            {state.phase === "idle" && state.error && (
              <p className="text-red-700">{state.error}</p>
            )}
          </>
        )}

        {(state.phase === "editing" || state.phase === "assessing") && (
          <>
            <h2 className="text-lg font-medium mb-3">{state.dish}</h2>
            <IngredientEditor
              ingredients={state.ingredients}
              error={state.phase === "editing" ? state.error : null}
              busy={state.phase === "assessing"}
              onRename={renameIngredient}
              onRemove={removeIngredient}
              onAdd={addIngredient}
              onConfirm={() => void confirm()}
              onStartOver={startOver}
            />
          </>
        )}

        {state.phase === "result" && (
          <AssessmentResult result={state.result} onStartOver={startOver} />
        )}
      </div>

      <SettingsDrawer
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
      />
    </main>
  );
}
