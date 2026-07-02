import { useCallback, useEffect, useRef, useState } from "react";

import { errorMessage } from "../api/admin";
import { getDailyBoard, getDailyBoardFor, type DailyBoard } from "../api/daily";

interface DailyBoardState {
  board: DailyBoard | null;
  serverOffsetMs: number;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
}

// Fetches a public board on mount and exposes a reload the page calls as the countdown
// elapses and while it waits for approval. With no date it reads today (the live route);
// with a date it reads that past day. A failed reload keeps the last good board, so a
// transient blip never wipes a working view, and `loading` reflects only a fresh fetch,
// so background reloads don't flash the loading state.
export function useDailyBoard(date?: string): DailyBoardState {
  const [board, setBoard] = useState<DailyBoard | null>(null);
  const [serverOffsetMs, setServerOffsetMs] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Each load takes a token; a stale resolution (a slow fetch for a day the user has
  // already navigated away from) checks it against the latest and bows out, so the day
  // started last wins rather than the one that resolves last.
  const latestLoad = useRef(0);

  const reload = useCallback(async () => {
    const token = ++latestLoad.current;
    try {
      const result = date ? await getDailyBoardFor(date) : await getDailyBoard();
      if (token !== latestLoad.current) return;
      setBoard(result.board);
      setServerOffsetMs(result.serverOffsetMs);
      setError(null);
    } catch (err) {
      if (token !== latestLoad.current) return;
      setError(errorMessage(err));
    } finally {
      if (token === latestLoad.current) setLoading(false);
    }
  }, [date]);

  useEffect(() => {
    // A date change is a different day, so clear the old board and show loading rather
    // than leaving the previous day's meals on screen until the new ones land. A plain
    // poll calls reload directly and keeps its board.
    setBoard(null);
    setLoading(true);
    void reload();
  }, [reload]);

  return { board, serverOffsetMs, loading, error, reload };
}
