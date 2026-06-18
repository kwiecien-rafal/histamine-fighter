import { useCallback, useEffect, useState } from "react";

import { errorMessage } from "../api/admin";
import { getDailyBoard, type DailyBoard } from "../api/daily";

interface DailyBoardState {
  board: DailyBoard | null;
  serverOffsetMs: number;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
}

// Fetches the public board on mount and exposes a reload the page calls as the
// countdown elapses and while it waits for approval. A failed reload keeps the last
// good board, so a transient blip never wipes a working view, and `loading` reflects
// only the first fetch, so background reloads don't flash the loading state.
export function useDailyBoard(): DailyBoardState {
  const [board, setBoard] = useState<DailyBoard | null>(null);
  const [serverOffsetMs, setServerOffsetMs] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      const result = await getDailyBoard();
      setBoard(result.board);
      setServerOffsetMs(result.serverOffsetMs);
      setError(null);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { board, serverOffsetMs, loading, error, reload };
}
