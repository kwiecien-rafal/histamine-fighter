import { useCallback, useEffect, useRef, useState } from "react";

import {
  AdminAuthError,
  errorMessage,
  type ComposedMeal,
  type MealType,
  type TraceEvent,
} from "../api/admin";

export type StreamStatus = "idle" | "streaming" | "done" | "error" | "expired";

interface ReasoningStream {
  status: StreamStatus;
  events: TraceEvent[];
  meal: ComposedMeal | null;
  error: string | null;
  start: (mealType: MealType) => Promise<void>;
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

// Streams the admin live composition over a POST (EventSource can't carry the
// bearer token, so this reads the response body itself). Trace steps land in
// `events` as they arrive, the terminal meal in `meal`. A 401 means the session
// lapsed, so it calls onExpired rather than surfacing a scary error.
export function useReasoningStream(token: string | null, onExpired: () => void): ReasoningStream {
  const [status, setStatus] = useState<StreamStatus>("idle");
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [meal, setMeal] = useState<ComposedMeal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const start = useCallback(
    async (mealType: MealType) => {
      if (!token) return;
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      setStatus("streaming");
      setEvents([]);
      setMeal(null);
      setError(null);

      try {
        const response = await fetch("/admin/daily/generate", {
          method: "POST",
          headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
          body: JSON.stringify({ meal_type: mealType }),
          signal: controller.signal,
        });
        if (response.status === 401) throw new AdminAuthError("Session expired.");
        if (!response.ok || !response.body) {
          throw new Error(`Request failed: ${response.status}`);
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let streamError: string | null = null;
        let gotMeal = false;
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const frames = buffer.split(/\r?\n\r?\n/);
          buffer = frames.pop() ?? "";
          for (const frame of frames) {
            const parsed = parseFrame(frame);
            if (!parsed) continue;
            if (parsed.event === "trace") {
              setEvents((current) => [...current, parsed.data as TraceEvent]);
            } else if (parsed.event === "meal") {
              setMeal(parsed.data as ComposedMeal);
              gotMeal = true;
            } else if (parsed.event === "error") {
              streamError = (parsed.data as { detail?: string }).detail ?? "Generation failed.";
            }
          }
        }
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
    [token, onExpired],
  );

  // Abort an in-flight stream and return the panel to its pristine state. The
  // running start() loop sees the aborted signal and bails without overwriting.
  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setStatus("idle");
    setEvents([]);
    setMeal(null);
    setError(null);
  }, []);

  return { status, events, meal, error, start, cancel };
}
