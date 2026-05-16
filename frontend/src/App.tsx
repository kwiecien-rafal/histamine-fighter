import { useState } from "react";

import { lookupDish, type DishLookupResponse } from "./api/client";

export function App() {
  const [dish, setDish] = useState("");
  const [result, setResult] = useState<DishLookupResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
        <h1 className="text-3xl font-semibold mb-2">Histamine Fighter</h1>
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
          <article className="rounded border border-stone-200 bg-white p-4">
            <header className="flex items-center justify-between mb-2">
              <h2 className="font-medium">{result.dish}</h2>
              <span className="text-xs uppercase tracking-wide text-stone-500">
                {result.verdict} · {result.model}
              </span>
            </header>
            <p className="text-stone-700">{result.explanation}</p>
          </article>
        )}
      </div>
    </main>
  );
}
