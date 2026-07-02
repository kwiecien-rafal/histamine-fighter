import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { errorMessage } from "../api/errors";
import { getMeal, type PublicMealDetail } from "../api/meals";
import { MealCard } from "../components/MealCard";
import { Navbar } from "../components/Navbar";

// One approved meal in full, reached from the browse grid (and deep-linkable). The card
// is the same one the daily board renders, so the recipe and the composition replay live
// here without a second layout. A missing or unapproved id reads as not found.
export function MealDetail() {
  const { id } = useParams<{ id: string }>();
  const [meal, setMeal] = useState<PublicMealDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!id) return;
    setLoading(true);
    setError(null);
    try {
      setMeal(await getMeal(id));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!meal) return;
    const previous = document.title;
    document.title = `${meal.name} · Histamine Fighter`;
    return () => {
      document.title = previous;
    };
  }, [meal]);

  return (
    <>
      <Navbar />
      <main className="min-h-screen bg-stone-50 text-stone-900 px-6 pt-10 pb-24">
        <div className="max-w-2xl mx-auto">
          <Link
            to="/meals"
            className="text-sm text-emerald-800 hover:text-emerald-900 underline underline-offset-4 cursor-pointer"
          >
            ← All meals
          </Link>

          <div className="mt-6">
            {meal === null && loading && (
              <p className="text-stone-600" aria-live="polite">
                Loading meal…
              </p>
            )}

            {meal === null && error && (
              <div role="alert" className="text-sm text-red-700">
                <span className="font-medium">Couldn't load this meal —</span> {error}{" "}
                <button
                  type="button"
                  onClick={() => void load()}
                  className="underline underline-offset-4 cursor-pointer"
                >
                  Try again
                </button>
              </div>
            )}

            {meal && <MealCard meal={meal} />}
          </div>
        </div>
      </main>
    </>
  );
}
