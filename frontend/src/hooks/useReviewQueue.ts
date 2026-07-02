import { useCallback, useEffect, useRef, useState } from "react";

import { AdminAuthError, errorMessage } from "../api/admin";

export type ReviewAction = "approve" | "reject" | "delete";

interface ReviewQueue<T> {
  items: T[] | null;
  loading: boolean;
  error: string | null;
  decidingId: string | null;
  reload: () => Promise<void>;
  decide: (id: string, action: ReviewAction) => Promise<void>;
}

// One pending-review queue, driven by injected list/approve/reject/remove calls so the
// curated-meal and daily-board queues share a single machine. Runs while enabled (the
// operator is an authed admin), removes an item once decided (approve, reject, or hard
// delete all take it out of the pending list), and on a 401 calls onExpired rather than
// surfacing a scary error, returning the operator to the login form. The injected calls
// must be stable (module-level api functions) so the auto-load effect runs once per
// enabled change.
export function useReviewQueue<T extends { id: string }>(
  enabled: boolean,
  onExpired: () => void,
  list: () => Promise<T[]>,
  approve: (id: string) => Promise<unknown>,
  reject: (id: string) => Promise<unknown>,
  remove: (id: string) => Promise<unknown>,
): ReviewQueue<T> {
  const [items, setItems] = useState<T[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [decidingId, setDecidingId] = useState<string | null>(null);
  // Each load takes a token; a stale resolution (an older list call, e.g. from a fast
  // tab switch) checks it against the latest and bows out, so the last load started wins
  // rather than the last to resolve.
  const latestLoad = useRef(0);

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
    const token = ++latestLoad.current;
    setLoading(true);
    setError(null);
    try {
      const next = await list();
      if (token !== latestLoad.current) return;
      setItems(next);
    } catch (err) {
      if (token !== latestLoad.current) return;
      if (!handled(err)) setError(errorMessage(err));
    } finally {
      if (token === latestLoad.current) setLoading(false);
    }
  }, [enabled, handled, list]);

  useEffect(() => {
    if (!enabled) {
      setItems(null);
      return;
    }
    // A new list identity means a different view (a status-tab switch), so clear the
    // previous view's items rather than letting its cards linger under the new tab
    // until the load lands. A plain reload (same list) keeps its items on screen.
    setItems(null);
    void reload();
  }, [enabled, reload]);

  const decide = useCallback(
    async (id: string, action: ReviewAction) => {
      if (!enabled) return;
      const run = action === "approve" ? approve : action === "reject" ? reject : remove;
      setDecidingId(id);
      setError(null);
      try {
        await run(id);
        // Whatever the decision, the row moves out of the status this list is filtered
        // to (approve/reject change it, delete removes it), so drop it locally without a
        // refetch. Holds only while every action takes the row out of the current view.
        setItems((current) => current?.filter((item) => item.id !== id) ?? null);
      } catch (err) {
        if (!handled(err)) setError(errorMessage(err));
      } finally {
        setDecidingId(null);
      }
    },
    [enabled, handled, approve, reject, remove],
  );

  return { items, loading, error, decidingId, reload, decide };
}
