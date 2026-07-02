import { useCallback, useEffect, useState } from "react";

import type { RevealedBoard } from "../api/daily";
import { ComposeCost } from "../components/ComposeCost";
import { MealCard } from "../components/MealCard";
import { Navbar } from "../components/Navbar";
import { useDailyBoard } from "../hooks/useDailyBoard";
import {
  PAST_BOARD_WINDOW_DAYS,
  formatBoardDate,
  formatRemaining,
  shiftIsoDate,
  todayIsoUtc,
} from "../lib/daily";

export function DailyBoard() {
  const [today] = useState(todayIsoUtc);
  const [date, setDate] = useState(today);
  const isToday = date === today;
  // Today reads the live route (countdown + polling); a past day reads the dated route.
  const { board, serverOffsetMs, loading, error, reload } = useDailyBoard(
    isToday ? undefined : date,
  );
  // Stable, void-returning so the polling effect doesn't re-subscribe each render.
  const refresh = useCallback(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    const previous = document.title;
    document.title = "Today's meals · Histamine Fighter";
    return () => {
      document.title = previous;
    };
  }, []);

  return (
    <>
      <Navbar />
      <main className="min-h-screen bg-stone-50 text-stone-900 px-6 pt-10 pb-24">
        <div className="max-w-2xl mx-auto">
          <header className="mb-6">
            <h1 className="text-3xl font-semibold">
              {isToday ? "Today's meals" : formatBoardDate(date)}
            </h1>
            {isToday && (
              <p className="text-stone-600 mt-1">
                Four histamine-safe meals our agent composes fresh each day.
              </p>
            )}
          </header>

          <DayNav date={date} today={today} onChange={setDate} />

        {/* The error only replaces the page when the first load failed; a failed
            background reload keeps the board it already has. */}
        {board === null && loading && (
          <p className="text-stone-600" aria-live="polite">
            Loading the board…
          </p>
        )}

        {board === null && error && (
          <div role="alert" className="text-sm text-red-700">
            <span className="font-medium">Couldn't load the board —</span> {error}{" "}
            <button
              type="button"
              onClick={refresh}
              className="underline underline-offset-4 cursor-pointer"
            >
              Try again
            </button>
          </div>
        )}

        {board?.status === "locked" &&
          (isToday ? (
            <LockedView
              revealAt={board.reveal_at}
              serverOffsetMs={serverOffsetMs}
              onReady={refresh}
            />
          ) : (
            <NoBoardView date={board.date} />
          ))}

        {board?.status === "revealed" && <RevealedView board={board} />}
        </div>
      </main>
    </>
  );
}

interface DayNavProps {
  date: string;
  today: string;
  onChange: (date: string) => void;
}

// Step one day at a time, capped at the history window back and today forward. The
// viewed day is the page heading, so the nav itself is just the two controls.
function DayNav({ date, today, onChange }: DayNavProps) {
  const earliest = shiftIsoDate(today, -PAST_BOARD_WINDOW_DAYS);
  const navButton =
    "rounded border border-stone-300 px-3 py-1.5 text-sm text-stone-700 hover:bg-stone-100 disabled:opacity-40 enabled:cursor-pointer";
  return (
    <nav className="flex items-center justify-between gap-3 mb-8" aria-label="Browse board days">
      <button
        type="button"
        onClick={() => onChange(shiftIsoDate(date, -1))}
        disabled={date <= earliest}
        className={navButton}
      >
        ← Previous day
      </button>
      <button
        type="button"
        onClick={() => onChange(shiftIsoDate(date, 1))}
        disabled={date >= today}
        className={navButton}
      >
        Next day →
      </button>
    </nav>
  );
}

// A past day that never reached an approval: no countdown, nothing to compose, just the
// honest record that the board was empty that day.
function NoBoardView({ date }: { date: string }) {
  return (
    <section className="rounded border border-stone-200 bg-white p-8 text-center">
      <p className="text-stone-600">No board was published on {formatBoardDate(date)}.</p>
    </section>
  );
}

interface LockedViewProps {
  revealAt: string | null;
  serverOffsetMs: number;
  onReady: () => void;
}

// Past the reveal but still locked, the board is waiting on an admin approval, so it
// is re-polled on a jittered interval until it flips to revealed (which unmounts this
// view) or the cap is hit. The jitter also spreads the reload every client fires at
// the reveal instant, so they don't stampede the read together.
const POLL_BASE_MS = 20_000;
const POLL_JITTER_MS = 10_000;
const MAX_POLLS = 30;

function LockedView({ revealAt, serverOffsetMs, onReady }: LockedViewProps) {
  const target = revealAt ? new Date(revealAt).getTime() : null;
  const [now, setNow] = useState(() => Date.now() + serverOffsetMs);

  // Count down once per second against server-corrected time, and stop ticking the
  // moment the reveal passes — the polling effect takes over from there.
  useEffect(() => {
    if (target === null || Date.now() + serverOffsetMs >= target) {
      setNow(Date.now() + serverOffsetMs);
      return;
    }
    const id = setInterval(() => {
      const current = Date.now() + serverOffsetMs;
      setNow(current);
      if (current >= target) clearInterval(id);
    }, 1000);
    return () => clearInterval(id);
  }, [target, serverOffsetMs]);

  const pastReveal = target !== null && now >= target;

  useEffect(() => {
    if (!pastReveal) return;
    onReady();
    let attempts = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const schedule = () => {
      timer = setTimeout(
        () => {
          attempts += 1;
          onReady();
          if (attempts < MAX_POLLS) schedule();
        },
        POLL_BASE_MS + Math.random() * POLL_JITTER_MS,
      );
    };
    schedule();
    return () => clearTimeout(timer);
  }, [pastReveal, onReady]);

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

// The board shows instantly once revealed; the composer's reasoning is no longer a forced
// premiere but a per-card "watch how it was composed" replay on each MealCard, where the
// per-meal model badge also lives. Only the aggregate token cost stays board-level.
function RevealedView({ board }: { board: RevealedBoard }) {
  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center justify-between gap-3">
        <p className="text-sm text-stone-600">{board.meals.length} meals for today</p>
        <ComposeCost usage={board.usage} model={board.model} />
      </div>
      <div className="grid gap-4 sm:grid-cols-2">
        {board.meals.map((meal) => (
          <MealCard key={meal.meal_type} meal={meal} />
        ))}
      </div>
    </section>
  );
}
