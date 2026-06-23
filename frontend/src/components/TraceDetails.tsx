import type { TraceEvent } from "../api/admin";
import { TRACE_KIND_LABEL, isRejectEvent } from "../lib/meal";

interface TraceDetailsProps {
  trace: TraceEvent[];
}

// Collapsed disclosure of the composer's recorded reasoning, shared by the review
// cards so an admin can read what the agent actually did before approving.
export function TraceDetails({ trace }: TraceDetailsProps) {
  if (trace.length === 0) return null;
  return (
    <details className="mb-4">
      <summary className="text-xs font-semibold uppercase tracking-wide text-stone-500 cursor-pointer">
        What the agent did ({trace.length} steps)
      </summary>
      <ol className="mt-2 flex flex-col gap-1 border-l border-stone-200 pl-3">
        {trace.map((event, index) => (
          <li
            key={index}
            className={`text-sm ${isRejectEvent(event) ? "text-red-700" : "text-stone-600"}`}
          >
            <span className="font-mono text-[10px] uppercase tracking-wide text-stone-400">
              {TRACE_KIND_LABEL[event.kind]}
            </span>{" "}
            {event.text}
          </li>
        ))}
      </ol>
    </details>
  );
}
