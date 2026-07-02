import { useEffect, useState } from "react";

import type { TraceEvent } from "../api/admin";
import { prefersReducedMotion } from "../lib/daily";
import { TRACE_KIND_LABEL, isRejectEvent } from "../lib/meal";

interface ReasoningReplayProps {
  events: TraceEvent[];
  // Pacing between steps; overridable so tests can drive it with fake timers.
  stepMs?: number;
  // Live mode: events arrive over the wire, so they are shown as they come (no pacing,
  // no Skip), with a pulsing line marking the wait on the next model call so the
  // multi-second gaps read as the agent thinking, not a freeze.
  live?: boolean;
  pending?: boolean;
}

const DEFAULT_STEP_MS = 1100;

// Reveals the composer's trace step by step. Recorded mode (the watch dialog) paces a
// stored trace, Skip reveals the rest, and prefers-reduced-motion shows it all at once;
// live mode shows steps as the admin stream delivers them.
export function ReasoningReplay({
  events,
  stepMs = DEFAULT_STEP_MS,
  live = false,
  pending = false,
}: ReasoningReplayProps) {
  const revealAllAtOnce = !live && prefersReducedMotion();
  const [shown, setShown] = useState(() =>
    revealAllAtOnce ? events.length : events.length > 0 ? 1 : 0,
  );

  useEffect(() => {
    if (live || revealAllAtOnce || shown >= events.length) return;
    const timer = setTimeout(() => setShown((count) => count + 1), stepMs);
    return () => clearTimeout(timer);
  }, [shown, events.length, stepMs, live, revealAllAtOnce]);

  const visible = live ? events : events.slice(0, shown);
  const atEnd = shown >= events.length;

  return (
    <section
      className="rounded border border-stone-200 bg-white p-6"
      aria-label="Watch the agent compose"
    >
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-medium">
          {live ? "Composing live…" : "How it was composed"}
        </h2>
        {!live && !atEnd && (
          <button
            type="button"
            onClick={() => setShown(events.length)}
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
        {live && pending && (
          <li className="flex items-center gap-2 text-sm text-stone-400">
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-600 animate-pulse" aria-hidden />
            Thinking…
          </li>
        )}
      </ol>
    </section>
  );
}
