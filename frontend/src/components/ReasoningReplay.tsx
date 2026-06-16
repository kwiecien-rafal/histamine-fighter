import { useEffect, useState } from "react";

import type { TraceEvent } from "../api/admin";
import { TRACE_KIND_LABEL, isRejectEvent } from "../lib/meal";

interface ReasoningReplayProps {
  events: TraceEvent[];
  // Called when a recorded replay finishes; unused in live mode.
  onComplete?: () => void;
  // Pacing between steps; overridable so tests can drive it with fake timers.
  stepMs?: number;
  // In live mode the events arrive over the wire already, so they are shown as
  // they come (no pacing, no Skip, no completion callback).
  live?: boolean;
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
        {visible.map((event, index) => (
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
