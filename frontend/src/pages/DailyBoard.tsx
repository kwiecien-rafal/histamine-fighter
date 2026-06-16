import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import type { RevealedBoard } from "../api/daily";
import { LLMProviderBadge } from "../components/LLMProviderBadge";
import { MealCard } from "../components/MealCard";
import { ReasoningReplay } from "../components/ReasoningReplay";
import { useDailyBoard } from "../hooks/useDailyBoard";
import { formatRemaining, hasSeenBoard, markBoardSeen, prefersReducedMotion } from "../lib/daily";

export function DailyBoard() {
  const { board, loading, error, reload } = useDailyBoard();

  return (
    <main className="min-h-screen bg-stone-50 text-stone-900 px-6 pt-12 pb-24">
      <div className="max-w-2xl mx-auto">
        <header className="mb-8">
          <Link to="/" className="text-sm text-stone-500 hover:text-stone-800">
            Histamine Fighter
          </Link>
          <h1 className="text-3xl font-semibold">Today's meals</h1>
          <p className="text-stone-600 mt-1">
            Four histamine-safe meals our agent composes fresh each day.
          </p>
        </header>

        {error && (
          <div role="alert" className="text-sm text-red-700">
            <span className="font-medium">Couldn't load the board —</span> {error}{" "}
            <button
              type="button"
              onClick={() => void reload()}
              className="underline underline-offset-4 cursor-pointer"
            >
              Try again
            </button>
          </div>
        )}

        {!error && (loading || board === null) && (
          <p className="text-stone-600" aria-live="polite">
            Loading today's board…
          </p>
        )}

        {!error && board?.status === "locked" && (
          <LockedView revealAt={board.reveal_at} onReady={() => void reload()} />
        )}

        {!error && board?.status === "revealed" && <RevealedView board={board} />}
      </div>
    </main>
  );
}

interface LockedViewProps {
  revealAt: string | null;
  onReady: () => void;
}

function LockedView({ revealAt, onReady }: LockedViewProps) {
  const target = revealAt ? new Date(revealAt).getTime() : null;
  const [now, setNow] = useState(() => Date.now());
  const fired = useRef(false);

  useEffect(() => {
    if (target === null) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [target]);

  // When the reveal time passes, pull the board once: it flips to revealed if an
  // admin has approved it, and stays locked (showing "Revealing now") otherwise.
  useEffect(() => {
    if (target !== null && now >= target && !fired.current) {
      fired.current = true;
      onReady();
    }
  }, [target, now, onReady]);

  if (target === null) {
    return (
      <p className="text-stone-600">
        Today's board hasn't been set yet. Check back a little later.
      </p>
    );
  }

  const remaining = target - now;
  return (
    <section className="rounded border border-stone-200 bg-white p-8 text-center">
      <p className="text-sm uppercase tracking-wide text-stone-500 mb-2">Next board in</p>
      {remaining > 0 ? (
        <p className="text-4xl font-semibold tabular-nums">{formatRemaining(remaining)}</p>
      ) : (
        <p className="text-2xl font-semibold" aria-live="polite">
          Revealing now…
        </p>
      )}
      <p className="text-stone-600 mt-4">
        Come back at the reveal to watch the agent compose today's meals.
      </p>
    </section>
  );
}

function RevealedView({ board }: { board: RevealedBoard }) {
  const startOnBoard =
    hasSeenBoard(board.date) || prefersReducedMotion() || board.trace.length === 0;
  const [phase, setPhase] = useState<"replay" | "board">(startOnBoard ? "board" : "replay");

  const finish = useCallback(() => {
    markBoardSeen(board.date);
    setPhase("board");
  }, [board.date]);

  if (phase === "replay") {
    return <ReasoningReplay events={board.trace} onComplete={finish} />;
  }

  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-stone-600">{board.meals.length} meals for today</p>
        <span className="flex items-center gap-2 text-xs text-stone-500">
          Composed by <LLMProviderBadge model={board.model} />
        </span>
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        {board.meals.map((meal) => (
          <MealCard key={meal.meal_type} meal={meal} />
        ))}
      </div>
    </section>
  );
}
