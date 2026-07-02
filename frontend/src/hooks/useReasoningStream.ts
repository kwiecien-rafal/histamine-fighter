import { useCallback, useEffect, useRef, useState } from "react";

import {
  AdminAuthError,
  errorDetail,
  errorMessage,
  type ComposedMeal,
  type SavedEvent,
  type SlotConflict,
  type TraceEvent,
} from "../api/admin";

export type StreamStatus = "idle" | "streaming" | "done" | "error" | "expired" | "conflict";

// Which compose endpoint to drive and the body to send. Curated takes just a meal
// type; daily adds the date and an optional replace flag for an overwrite.
export interface ComposeStreamRequest {
  endpoint: string;
  body: Record<string, unknown>;
}

interface ReasoningStream {
  status: StreamStatus;
  events: TraceEvent[];
  meal: ComposedMeal | null;
  error: string | null;
  // Set when a saving stream confirms persistence, so the caller can refresh its queue.
  savedId: string | null;
  // Set when a daily save is refused pre-stream because the slot is taken, so the caller
  // can confirm and re-run with replace:true.
  conflict: SlotConflict | null;
  start: (request: ComposeStreamRequest) => Promise<void>;
  cancel: () => void;
}

interface ParsedFrame {
  event: string;
  data: unknown;
}

// Parse one SSE frame ("event:"/"data:" lines) into its name and JSON payload.
// Comment lines (keep-alive pings) and frames without data are ignored.
function parseFrame(frame: string): ParsedFrame | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of frame.split(/\r?\n/)) {
    if (line.startsWith(":")) continue;
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  }
  if (dataLines.length === 0) return null;
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch {
    return null;
  }
}

// The slot a 409 names, when the body is the structured conflict (not the busy-lock
// 409, whose detail is a plain string). Lets the daily save offer a replace confirm.
async function parseConflict(response: Response): Promise<SlotConflict | null> {
  try {
    const body = (await response.json()) as { detail?: { conflict?: SlotConflict } };
    const conflict = body.detail?.conflict;
    if (conflict?.existing_status) return conflict;
  } catch {
    // not a structured-conflict body
  }
  return null;
}

// Human-friendly copy for the statuses a compose endpoint returns before the stream
// opens. 401 triggers logout and is handled separately; a structured 409 becomes a
// conflict; a 422 carries the backend's own detail (a daily date outside the queue
// window), surfaced verbatim rather than reduced to this generic line.
function startErrorMessage(status: number): string {
  if (status === 409) return "A composition is already running. Wait for it to finish.";
  if (status === 429) return "You've hit the rate limit. Give it a moment, then try again.";
  return `The composer couldn't start (error ${status}).`;
}

// Streams an admin composition over a POST (the endpoint takes a body, so EventSource is
// not an option and this reads the response body itself). Trace steps land in `events` as
// they arrive, the terminal meal in `meal`, and a saving stream ends with a `saved` frame
// that fills `savedId`. A 401 means the session lapsed, so it calls onExpired rather than
// surfacing a scary error; a structured 409 surfaces as `conflict` for a replace confirm.
export function useReasoningStream(onExpired: () => void): ReasoningStream {
  const [status, setStatus] = useState<StreamStatus>("idle");
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [meal, setMeal] = useState<ComposedMeal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savedId, setSavedId] = useState<string | null>(null);
  const [conflict, setConflict] = useState<SlotConflict | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const start = useCallback(
    async ({ endpoint, body }: ComposeStreamRequest) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setStatus("streaming");
      setEvents([]);
      setMeal(null);
      setError(null);
      setSavedId(null);
      setConflict(null);

      try {
        const response = await fetch(endpoint, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          credentials: "include",
          body: JSON.stringify(body),
          signal: controller.signal,
        });
        if (response.status === 401) throw new AdminAuthError("Session expired.");
        if (response.status === 409) {
          const slot = await parseConflict(response);
          if (slot) {
            setConflict(slot);
            setStatus("conflict");
            return;
          }
        }
        // A daily date outside the queue window 422s with an actionable detail; show it
        // rather than the bare status, so the operator can correct the date.
        if (response.status === 422) {
          throw new Error(await errorDetail(response));
        }
        if (!response.ok || !response.body) {
          throw new Error(startErrorMessage(response.status));
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let streamError: string | null = null;
        let gotMeal = false;

        const handleFrame = (frame: string) => {
          const parsed = parseFrame(frame);
          if (!parsed) return;
          if (parsed.event === "trace") {
            setEvents((current) => [...current, parsed.data as TraceEvent]);
          } else if (parsed.event === "meal") {
            setMeal(parsed.data as ComposedMeal);
            gotMeal = true;
          } else if (parsed.event === "saved") {
            setSavedId((parsed.data as SavedEvent).id);
          } else if (parsed.event === "error") {
            streamError = (parsed.data as { detail?: string }).detail ?? "Generation failed.";
          }
        };

        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const frames = buffer.split(/\r?\n\r?\n/);
          buffer = frames.pop() ?? "";
          for (const frame of frames) handleFrame(frame);
        }
        // Flush the decoder and parse a final frame the server left unterminated by a
        // blank line, so a trailing meal or error is never dropped when the stream closes.
        buffer += decoder.decode();
        if (buffer.trim()) handleFrame(buffer);
        if (streamError) {
          setError(streamError);
          setStatus("error");
        } else if (!gotMeal) {
          // A clean close with no terminal meal means the stream dropped mid-run, a
          // reset the server's error backstop cannot turn into an error event. Treat
          // it as a failure instead of reporting success with nothing to show.
          setError("The stream ended before a meal was produced.");
          setStatus("error");
        } else {
          setStatus("done");
        }
      } catch (err) {
        if (controller.signal.aborted) return;
        if (err instanceof AdminAuthError) {
          setStatus("expired");
          onExpired();
          return;
        }
        setError(errorMessage(err));
        setStatus("error");
      }
    },
    [onExpired],
  );

  // Abort an in-flight stream and return the panel to its pristine state. The running
  // start() loop sees the aborted signal and bails without overwriting.
  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStatus("idle");
    setEvents([]);
    setMeal(null);
    setError(null);
    setSavedId(null);
    setConflict(null);
  }, []);

  return { status, events, meal, error, savedId, conflict, start, cancel };
}
