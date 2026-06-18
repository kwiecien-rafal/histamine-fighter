import { Fragment, useEffect, useState } from "react";

import type { TraceEvent } from "../api/admin";
import { MEAL_TYPE_LABEL, TRACE_KIND_LABEL, isRejectEvent } from "../lib/meal";

interface ReasoningReplayProps {
  events: TraceEvent[];
  // Called when a recorded replay finishes; unused in live mode.
  onComplete?: () => void;
  // Pacing between steps; overridable so tests can drive it with fake timers.
  stepMs?: number;
  // In live mode the events arrive over the wire already, so they are shown as
  // they come (no pacing, no Skip, no completion callback).
  live?: boolean;
  // In live mode, a pulsing line marks the wait on the next model call, so the
  // multi-second gaps between steps read as the agent thinking, not a freeze.
  pending?: boolean;
}

const DEFAULT_STEP_MS = 1100;
// A short beat on the final step before handing over to the board.
const HOLD_MS = 900;

// Reveals the composer's trace step by step. In recorded mode it paces a stored
// trace and then hands over to the board (a smooth, deterministic premiere that
// scales to any audience); in live mode it shows steps as the admin stream
// delivers them.
export function ReasoningReplay({
  events,
  onComplete,
  stepMs = DEFAULT_STEP_MS,
  live = false,
  pending = false,
}: ReasoningReplayProps) {
  const [shown, setShown] = useState(() => (events.length > 0 ? 1 : 0));

  useEffect(() => {
    if (live) return;
    if (shown >= events.length) {
      const timer = setTimeout(() => onComplete?.(), HOLD_MS);
      return () => clearTimeout(timer);
    }
    const timer = setTimeout(() => setShown((count) => count + 1), stepMs);
    return () => clearTimeout(timer);
  }, [shown, events.length, stepMs, onComplete, live]);

  const visible = live ? events : events.slice(0, shown);

  return (
    <section
      className="rounded border border-stone-200 bg-white p-6"
      aria-label="Watch the agent compose"
    >
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-medium">
          {live ? "Composing live…" : "Composing today's board…"}
        </h2>
        {!live && (
          <button
            type="button"
            onClick={() => onComplete?.()}
            className="text-sm text-stone-600 hover:text-stone-900 underline underline-offset-4 cursor-pointer"
          >
            Skip
          </button>
        )}
      </div>
      <ol className="flex flex-col gap-2 border-l border-stone-200 pl-4" aria-live="polite">
        {visible.map((event, index) => {
          // The daily board replays several meals in one trace; label each as its
          // steps begin. A single-meal stream carries no meal_type, so no headers.
          const meal = event.meal_type;
          return (
            <Fragment key={index}>
              {meal != null && meal !== visible[index - 1]?.meal_type && (
                <li className="text-xs font-semibold uppercase tracking-wide text-stone-500 mt-3 first:mt-0">
                  {MEAL_TYPE_LABEL[meal]}
                </li>
              )}
              <li
                className={`text-sm ${isRejectEvent(event) ? "text-red-700" : "text-stone-700"}`}
              >
                <span className="font-mono text-[10px] uppercase tracking-wide text-stone-400">
                  {TRACE_KIND_LABEL[event.kind]}
                </span>{" "}
                {event.text}
              </li>
            </Fragment>
          );
        })}
        {live && pending && (
          <li className="flex items-center gap-2 text-sm text-stone-400">
            <span
              className="h-1.5 w-1.5 rounded-full bg-emerald-600 animate-pulse"
              aria-hidden
            />
            Thinking…
          </li>
        )}
      </ol>
    </section>
  );
}
