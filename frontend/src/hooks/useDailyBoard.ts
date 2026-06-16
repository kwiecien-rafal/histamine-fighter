import { useCallback, useEffect, useState } from "react";

import { errorMessage } from "../api/admin";
import { getDailyBoard, type DailyBoard } from "../api/daily";

interface DailyBoardState {
  board: DailyBoard | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
}

// Fetches the public board once on mount and exposes a reload the page calls when
// the countdown elapses, so a locked board can flip to revealed without a refresh.
export function useDailyBoard(): DailyBoardState {
  const [board, setBoard] = useState<DailyBoard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setBoard(await getDailyBoard());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { board, loading, error, reload };
}
