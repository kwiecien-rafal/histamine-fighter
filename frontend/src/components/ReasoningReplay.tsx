import { useEffect, useState } from "react";

import type { TraceEvent } from "../api/admin";
import { TRACE_KIND_LABEL, isRejectEvent } from "../lib/meal";

interface ReasoningReplayProps {
  events: TraceEvent[];
  onComplete: () => void;
  // Pacing between steps; overridable so tests can drive it with fake timers.
  stepMs?: number;
}

const DEFAULT_STEP_MS = 1100;
// A short beat on the final step before handing over to the board.
const HOLD_MS = 900;

// Replays the composer's recorded trace one step at a time, then reveals the
// board. The trace is pre-recorded, not live (the live admin stream is its own
// path), so this is a smooth, deterministic premiere that scales to any audience.
export function ReasoningReplay({ events, onComplete, stepMs = DEFAULT_STEP_MS }: ReasoningReplayProps) {
  const [shown, setShown] = useState(() => (events.length > 0 ? 1 : 0));

  useEffect(() => {
    if (shown >= events.length) {
      const timer = setTimeout(onComplete, HOLD_MS);
      return () => clearTimeout(timer);
    }
    const timer = setTimeout(() => setShown((count) => count + 1), stepMs);
    return () => clearTimeout(timer);
  }, [shown, events.length, stepMs, onComplete]);

  return (
    <section
      className="rounded border border-stone-200 bg-white p-6"
      aria-label="Watch the agent compose today's board"
    >
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-medium">Composing today's board…</h2>
        <button
          type="button"
          onClick={onComplete}
          className="text-sm text-stone-600 hover:text-stone-900 underline underline-offset-4 cursor-pointer"
        >
          Skip
        </button>
      </div>
      <ol className="flex flex-col gap-2 border-l border-stone-200 pl-4" aria-live="polite">
        {events.slice(0, shown).map((event, index) => (
          <li
            key={index}
            className={`text-sm ${isRejectEvent(event) ? "text-red-700" : "text-stone-700"}`}
          >
            <span className="font-mono text-[10px] uppercase tracking-wide text-stone-400">
              {TRACE_KIND_LABEL[event.kind]}
            </span>{" "}
            {event.text}
          </li>
        ))}
      </ol>
    </section>
  );
}
