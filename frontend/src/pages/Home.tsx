import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import type { LockedBoard } from "../api/daily";
import { HomeMealCard } from "../components/HomeMealCard";
import { MedicalNote } from "../components/MedicalNote";
import { useDailyBoard } from "../hooks/useDailyBoard";
import { useMealPoolTotal } from "../hooks/useMealPoolTotal";
import { formatRemaining } from "../lib/daily";

// The landing page: hero, a light strip of today's board, how the flow works, and
// teasers into the meal pool and the Learn hub. It stays data-light — two reads, no
// LLM calls — so the first impression is instant.
export function Home() {
  const { board, serverOffsetMs, loading } = useDailyBoard();
  const { total } = useMealPoolTotal();

  useEffect(() => {
    const previous = document.title;
    document.title = "Histamine Fighter · Fight back against histamine intolerance";
    return () => {
      document.title = previous;
    };
  }, []);

  return (
    <div className="max-w-5xl mx-auto flex flex-col gap-16">
      <section className="pt-6 sm:pt-12">
        <h1 className="font-serif text-4xl sm:text-5xl font-bold tracking-tight text-forest-900">
          Fight back against histamine.
        </h1>
        <p className="text-lg text-stone-600 mt-4 max-w-2xl">
          Check any dish in seconds. Get an honest verdict, smart swaps, and meals that
          won't fight back — grounded in a curated ingredient index, never a model's guess.
        </p>
        <div className="mt-8 flex flex-wrap items-center gap-5">
          <Link
            to="/lookup"
            className="rounded bg-ember-600 hover:bg-ember-700 text-white px-5 py-2.5 font-medium"
          >
            Check your dish
          </Link>
          <Link to="/daily" className="text-forest-800 hover:text-forest-900 font-medium">
            See today's board →
          </Link>
        </div>
      </section>

      <section>
        <h2 className="font-serif text-2xl font-semibold text-forest-900">
          Today's game plan
        </h2>
        <p className="text-stone-600 mt-1">
          Four safe meals, composed fresh every day and approved by a human.
        </p>
        <div className="mt-1">
          <MedicalNote />
        </div>

        <div className="mt-5">
          {board === null && loading && (
            <p className="text-stone-600" aria-live="polite">
              Loading today's board…
            </p>
          )}

          {/* A strip failure must not turn the landing page into an error page; the
              board page owns retries. */}
          {board === null && !loading && <BoardLinkCard />}

          {board?.status === "locked" && (
            <LockedTeaser board={board} serverOffsetMs={serverOffsetMs} />
          )}

          {board?.status === "revealed" && (
            <>
              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {board.meals.map((meal) => (
                  <HomeMealCard key={meal.meal_type} meal={meal} />
                ))}
              </div>
              <p className="mt-4">
                <Link
                  to="/daily"
                  className="text-sm text-forest-800 hover:text-forest-900 underline underline-offset-4"
                >
                  See the full board, recipes and replays →
                </Link>
              </p>
            </>
          )}
        </div>
      </section>

      <section>
        <h2 className="font-serif text-2xl font-semibold text-forest-900">
          How the fight is won
        </h2>
        <div className="mt-5 grid gap-4 sm:grid-cols-3">
          <HowStep number="1" title="Name the dish">
            Type anything — the AI proposes the ingredients it expects inside.
          </HowStep>
          <HowStep number="2" title="Confirm the lineup">
            You know your plate. Add, rename or drop ingredients before anything is judged.
          </HowStep>
          <HowStep number="3" title="Get the verdict">
            Safety is computed from a curated ingredient index — the model never decides.
            Risky dish? You get swaps and alternatives.
          </HowStep>
        </div>
      </section>

      <section className="grid gap-4 sm:grid-cols-2">
        <TeaserCard
          title={
            total !== null
              ? `${total} meals in the safe corner`
              : "An open cookbook of verified-safe meals"
          }
          body="Every one composed from the curated index and approved by a human."
          to="/meals"
          linkLabel="Browse the safe meals →"
        />
        <TeaserCard
          title="Know your enemy"
          body="What histamine is, why it hits, and which foods pick the fight — every answer cited from curated sources."
          to="/learn"
          linkLabel="Ask in the Learn hub →"
        />
      </section>
    </div>
  );
}

// A quiet fallback strip when the board read failed: keep the promise, link the page.
function BoardLinkCard() {
  return (
    <div className="rounded border border-cream-200 bg-white p-6">
      <Link
        to="/daily"
        className="text-forest-800 hover:text-forest-900 underline underline-offset-4"
      >
        See today's meals →
      </Link>
    </div>
  );
}

function LockedTeaser({
  board,
  serverOffsetMs,
}: {
  board: LockedBoard;
  serverOffsetMs: number;
}) {
  const target = board.reveal_at ? new Date(board.reveal_at).getTime() : null;
  const [now, setNow] = useState(() => Date.now() + serverOffsetMs);

  // Tick the countdown once per second against server-corrected time. Unlike the
  // board page, Home never re-polls at the reveal; it just points there.
  useEffect(() => {
    if (target === null) return;
    const id = setInterval(() => setNow(Date.now() + serverOffsetMs), 1000);
    return () => clearInterval(id);
  }, [target, serverOffsetMs]);

  if (target === null) {
    return (
      <div className="rounded border border-cream-200 bg-cream-100 p-6">
        <p className="text-stone-700">
          Today's board hasn't been set yet —{" "}
          <Link
            to="/meals"
            className="text-forest-800 hover:text-forest-900 underline underline-offset-4"
          >
            browse the safe meals instead
          </Link>
          .
        </p>
      </div>
    );
  }

  const remaining = target - now;
  return (
    <div className="rounded border border-cream-200 bg-cream-100 p-6">
      <p className="text-stone-700">
        Today's board unlocks in{" "}
        <span className="font-semibold tabular-nums">
          {formatRemaining(Math.max(remaining, 0))}
        </span>
        .
      </p>
      <p className="mt-2">
        <Link
          to="/daily"
          className="text-sm text-forest-800 hover:text-forest-900 underline underline-offset-4"
        >
          Watch the reveal on the board →
        </Link>
      </p>
    </div>
  );
}

function HowStep({
  number,
  title,
  children,
}: {
  number: string;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded border border-cream-200 bg-white p-5">
      <p className="font-serif text-3xl text-ember-600">{number}</p>
      <h3 className="font-medium text-stone-900 mt-2">{title}</h3>
      <p className="text-sm text-stone-600 mt-1">{children}</p>
    </div>
  );
}

function TeaserCard({
  title,
  body,
  to,
  linkLabel,
}: {
  title: string;
  body: string;
  to: string;
  linkLabel: string;
}) {
  return (
    <div className="rounded border border-cream-200 bg-white p-6">
      <h3 className="font-serif text-xl font-semibold text-forest-900">{title}</h3>
      <p className="text-sm text-stone-600 mt-2">{body}</p>
      <p className="mt-4">
        <Link
          to={to}
          className="text-sm text-forest-800 hover:text-forest-900 underline underline-offset-4"
        >
          {linkLabel}
        </Link>
      </p>
    </div>
  );
}
