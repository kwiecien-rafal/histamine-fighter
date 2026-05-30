import { useState } from "react";

import { lookupDish, type DishLookupResponse } from "./api/client";
import { LLMProviderBadge } from "./components/LLMProviderBadge";
import { SettingsDrawer } from "./components/SettingsDrawer";
import { VerdictBadge } from "./components/VerdictBadge";

export function App() {
  const [dish, setDish] = useState("");
  const [result, setResult] = useState<DishLookupResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!dish.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      setResult(await lookupDish(dish.trim()));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unknown error");
    } finally {
      setLoading(false);
    }
  }

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

        <form onSubmit={onSubmit} className="flex gap-2 mb-6">
          <input
            type="text"
            value={dish}
            onChange={(e) => setDish(e.target.value)}
            placeholder="e.g. Spaghetti Bolognese"
            className="flex-1 rounded border border-stone-300 px-3 py-2 focus:outline-none focus:border-emerald-700"
          />
          <button
            type="submit"
            disabled={loading || !dish.trim()}
            className="rounded bg-emerald-800 text-white px-4 py-2 disabled:opacity-50"
          >
            {loading ? "Checking…" : "Check"}
          </button>
        </form>

        {error && <p className="text-red-700">{error}</p>}

        {result && (
          <article className="rounded border border-stone-200 bg-white p-5">
            <header className="flex items-start justify-between gap-3 mb-4">
              <h2 className="text-lg font-medium">{result.dish}</h2>
              <LLMProviderBadge model={result.model} />
            </header>

            <section className="mb-4">
              <VerdictBadge verdict={result.verdict} />
            </section>

            <section className="mb-4">
              <h3 className="text-xs uppercase tracking-wide text-stone-500 mb-1">
                Why
              </h3>
              <p className="text-stone-700">{result.explanation}</p>
            </section>

            {result.verdict !== "safe" && result.replacements.length > 0 && (
              <section>
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
                      <span className="font-medium text-emerald-800">
                        {r.swap}
                      </span>
                      <p className="text-stone-600 mt-0.5">{r.reason}</p>
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </article>
        )}
      </div>

      <SettingsDrawer
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
      />
    </main>
  );
}
