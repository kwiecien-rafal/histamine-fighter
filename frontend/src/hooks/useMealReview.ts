import { useCallback, useEffect, useState } from "react";

import {
  AdminAuthError,
  approveMeal,
  errorMessage,
  listPendingMeals,
  rejectMeal,
  type AdminMeal,
} from "../api/admin";

export type ReviewAction = "approve" | "reject";

interface MealReview {
  meals: AdminMeal[] | null;
  loading: boolean;
  error: string | null;
  decidingId: string | null;
  reload: () => Promise<void>;
  decide: (mealId: string, action: ReviewAction) => Promise<void>;
}

// Manages the pending-review queue for a token. A 401 from any call means the
// session lapsed, so it calls onExpired (which drops the token) instead of
// surfacing a scary error, returning the operator to the login form.
export function useMealReview(token: string | null, onExpired: () => void): MealReview {
  const [meals, setMeals] = useState<AdminMeal[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [decidingId, setDecidingId] = useState<string | null>(null);

  const handled = useCallback(
    (err: unknown): boolean => {
      if (err instanceof AdminAuthError) {
        onExpired();
        return true;
      }
      return false;
    },
    [onExpired],
  );

  const reload = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      setMeals(await listPendingMeals(token));
    } catch (err) {
      if (!handled(err)) setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [token, handled]);

  useEffect(() => {
    if (!token) {
      setMeals(null);
      return;
    }
    void reload();
  }, [token, reload]);

  const decide = useCallback(
    async (mealId: string, action: ReviewAction) => {
      if (!token) return;
      setDecidingId(mealId);
      setError(null);
      try {
        await (action === "approve" ? approveMeal(token, mealId) : rejectMeal(token, mealId));
        // The queue is the pending list, so a decided meal leaves it either way.
        setMeals((current) => current?.filter((meal) => meal.id !== mealId) ?? null);
      } catch (err) {
        if (!handled(err)) setError(errorMessage(err));
      } finally {
        setDecidingId(null);
      }
    },
    [token, handled],
  );

  return { meals, loading, error, decidingId, reload, decide };
}
