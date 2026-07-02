import { type ReactNode, useCallback, useEffect, useState } from "react";

import type { MealType } from "../api/domain";
import { errorMessage } from "../api/errors";
import { browseMeals, type PublicMealCard } from "../api/meals";
import { BrowseMealCard } from "../components/BrowseMealCard";
import { Navbar } from "../components/Navbar";
import { MEAL_TYPE_LABEL, MEAL_TYPES } from "../lib/meal";

const PAGE_SIZE = 24;

// Browse the curated, admin-approved pool. The grid shows lean cards; opening one loads
// its recipe and the per-card composition replay on the detail page. A visitor can
// explore safe meals beyond just today's four, and page through the whole pool.
export function MealsBrowse() {
  const [mealType, setMealType] = useState<MealType | null>(null);
  const [meals, setMeals] = useState<PublicMealCard[] | null>(null);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadFirstPage = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await browseMeals({ mealType: mealType ?? undefined, limit: PAGE_SIZE });
      setMeals(page.items);
      setTotal(page.total);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [mealType]);

  useEffect(() => {
    void loadFirstPage();
  }, [loadFirstPage]);

  useEffect(() => {
    const previous = document.title;
    document.title = "Safe meals · Histamine Fighter";
    return () => {
      document.title = previous;
    };
  }, []);

  const loadMore = useCallback(async () => {
    if (meals === null) return;
    setLoadingMore(true);
    setError(null);
    try {
      const page = await browseMeals({
        mealType: mealType ?? undefined,
        limit: PAGE_SIZE,
        offset: meals.length,
      });
      setMeals((current) => [...(current ?? []), ...page.items]);
      setTotal(page.total);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoadingMore(false);
    }
  }, [meals, mealType]);

  const hasMore = meals !== null && meals.length < total;

  return (
    <>
      <Navbar />
      <main className="min-h-screen bg-stone-50 text-stone-900 px-6 pt-10 pb-24">
        <div className="max-w-5xl mx-auto">
          <header className="mb-6">
            <h1 className="text-3xl font-semibold">Safe meals</h1>
            <p className="text-stone-600 mt-1">
              Every meal here was composed from the curated safe index and approved by a human.
            </p>
          </header>

          <div className="flex flex-wrap gap-2 mb-8" role="group" aria-label="Filter by meal type">
            <FilterButton active={mealType === null} onClick={() => setMealType(null)}>
              All
            </FilterButton>
            {MEAL_TYPES.map((type) => (
              <FilterButton
                key={type}
                active={mealType === type}
                onClick={() => setMealType(type)}
              >
                {MEAL_TYPE_LABEL[type]}
              </FilterButton>
            ))}
          </div>

          {meals === null && loading && (
            <p className="text-stone-600" aria-live="polite">
              Loading meals…
            </p>
          )}

          {meals === null && error && (
            <div role="alert" className="text-sm text-red-700">
              <span className="font-medium">Couldn't load meals —</span> {error}{" "}
              <button
                type="button"
                onClick={() => void loadFirstPage()}
                className="underline underline-offset-4 cursor-pointer"
              >
                Try again
              </button>
            </div>
          )}

          {meals && meals.length === 0 && (
            <p className="text-stone-600">
              {mealType
                ? `No approved ${MEAL_TYPE_LABEL[mealType].toLowerCase()} meals yet. Try another filter.`
                : "No meals to show here yet. Check back soon."}
            </p>
          )}

          {meals && meals.length > 0 && (
            <>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {meals.map((meal) => (
                  <BrowseMealCard key={meal.id} meal={meal} />
                ))}
              </div>

              {/* A failed load-more keeps the meals already shown; only the line warns. */}
              {error && (
                <p role="alert" className="text-sm text-red-700 mt-6">
                  {error}
                </p>
              )}

              {hasMore && (
                <div className="mt-8 flex justify-center">
                  <button
                    type="button"
                    onClick={() => void loadMore()}
                    disabled={loadingMore}
                    aria-busy={loadingMore}
                    className="rounded border border-stone-300 px-5 py-2 text-sm text-stone-700 hover:bg-stone-100 disabled:opacity-50 enabled:cursor-pointer"
                  >
                    {loadingMore ? "Loading…" : `Load more (${meals.length} of ${total})`}
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </main>
    </>
  );
}

function FilterButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={
        active
          ? "rounded-full border border-emerald-300 bg-emerald-50 px-3 py-1 text-sm text-emerald-800 cursor-pointer"
          : "rounded-full border border-stone-300 px-3 py-1 text-sm text-stone-600 hover:bg-stone-100 cursor-pointer"
      }
    >
      {children}
    </button>
  );
}
