import { useCallback, useEffect, useState } from "react";

import { AdminAuthError, errorMessage } from "../api/admin";

export type ReviewAction = "approve" | "reject";

interface ReviewQueue<T> {
  items: T[] | null;
  loading: boolean;
  error: string | null;
  decidingId: string | null;
  reload: () => Promise<void>;
  decide: (id: string, action: ReviewAction) => Promise<void>;
}

// One pending-review queue, driven by injected list/approve/reject calls so the
// curated-meal and daily-board queues share a single machine. Runs while enabled (the
// operator is an authed admin), removes an item once decided, and on a 401 calls
// onExpired rather than surfacing a scary error, returning the operator to the login
// form. The injected calls must be stable (module-level api functions) so the
// auto-load effect runs once per enabled change.
export function useReviewQueue<T extends { id: string }>(
  enabled: boolean,
  onExpired: () => void,
  list: () => Promise<T[]>,
  approve: (id: string) => Promise<unknown>,
  reject: (id: string) => Promise<unknown>,
): ReviewQueue<T> {
  const [items, setItems] = useState<T[] | null>(null);
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
    if (!enabled) return;
    setLoading(true);
    setError(null);
    try {
      setItems(await list());
    } catch (err) {
      if (!handled(err)) setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [enabled, handled, list]);

  useEffect(() => {
    if (!enabled) {
      setItems(null);
      return;
    }
    void reload();
  }, [enabled, reload]);

  const decide = useCallback(
    async (id: string, action: ReviewAction) => {
      if (!enabled) return;
      setDecidingId(id);
      setError(null);
      try {
        await (action === "approve" ? approve(id) : reject(id));
        // The queue is the pending list, so a decided item leaves it either way.
        setItems((current) => current?.filter((item) => item.id !== id) ?? null);
      } catch (err) {
        if (!handled(err)) setError(errorMessage(err));
      } finally {
        setDecidingId(null);
      }
    },
    [enabled, handled, approve, reject],
  );

  return { items, loading, error, decidingId, reload, decide };
}
