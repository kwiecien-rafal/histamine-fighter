import { useCallback, useEffect, useState } from "react";

import type { MealType } from "../api/admin";
import { useReasoningStream } from "../hooks/useReasoningStream";
import { MEAL_TYPE_LABEL, MEAL_TYPES } from "../lib/meal";
import { ReasoningReplay } from "./ReasoningReplay";

interface ComposePanelProps {
  mode: "curated" | "daily";
  // For daily, the date to prefill (the first incomplete upcoming day, or today).
  defaultDate?: string;
  // True while any composition is streaming, this panel's or its sibling's. The backend
  // serializes runs behind one lock, so a second trigger only 409s; gating both panels on
  // the shared flag keeps the idle panel from starting a run that cannot proceed.
  busy: boolean;
  // Reports this panel's own streaming so the parent can derive the shared busy flag.
  onComposingChange: (mode: "curated" | "daily", streaming: boolean) => void;
  // Called when a save confirms; takes a queue reload directly (its promise is ignored).
  onSaved: () => void | Promise<void>;
  onExpired: () => void;
}

// Drives one live composition: Generate streams the agent live and persists the result as
// a pending row, confirming with a saved frame (which refreshes the parent queue). For
// daily, a taken slot comes back as a conflict the operator confirms to overwrite (re-run
// with replace). The recorded run is re-watchable per-item on the queue card afterwards.
export function ComposePanel({
  mode,
  defaultDate,
  busy,
  onComposingChange,
  onSaved,
  onExpired,
}: ComposePanelProps) {
  const [mealType, setMealType] = useState<MealType>("breakfast");
  const [date, setDate] = useState(defaultDate ?? "");
  const [dateTouched, setDateTouched] = useState(false);
  const { status, events, meal, error, savedId, conflict, start, cancel } =
    useReasoningStream(onExpired);
  const streaming = status === "streaming";

  useEffect(() => {
    if (savedId) void onSaved();
  }, [savedId, onSaved]);

  useEffect(() => {
    onComposingChange(mode, streaming);
  }, [mode, streaming, onComposingChange]);

  // Prefill the date from the parent's suggested slot while the panel is idle and the
  // operator has not chosen one, so it lands on the next sensible date once the queue loads
  // without remounting (which would wipe an in-progress run) or overriding a manual pick.
  useEffect(() => {
    if (mode === "daily" && status === "idle" && !dateTouched && defaultDate) {
      setDate(defaultDate);
    }
  }, [mode, status, dateTouched, defaultDate]);

  const save = useCallback(
    (replace = false) => {
      const request =
        mode === "daily"
          ? { endpoint: "/admin/compose/daily", body: { meal_type: mealType, date, replace } }
          : { endpoint: "/admin/compose/curated", body: { meal_type: mealType } };
      void start(request);
    },
    [mode, mealType, date, start],
  );

  const canSave = mode === "curated" || date !== "";
  // The backend rejects a date before today (UTC); matching that here keeps the picker
  // from offering a value the daily save would only 422.
  const minDate = new Date().toISOString().slice(0, 10);

  return (
    <section className="rounded border border-stone-200 bg-white p-5 space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-sm">
          <span className="block text-xs uppercase tracking-wide text-stone-500">Meal</span>
          <select
            value={mealType}
            onChange={(e) => setMealType(e.target.value as MealType)}
            disabled={streaming}
            className="mt-1 rounded border border-stone-300 px-3 py-2 text-sm focus:outline-none focus:border-emerald-700 disabled:opacity-50"
          >
            {MEAL_TYPES.map((type) => (
              <option key={type} value={type}>
                {MEAL_TYPE_LABEL[type]}
              </option>
            ))}
          </select>
        </label>
        {mode === "daily" && (
          <label className="text-sm">
            <span className="block text-xs uppercase tracking-wide text-stone-500">Date</span>
            <input
              type="date"
              value={date}
              min={minDate}
              onChange={(e) => {
                setDate(e.target.value);
                setDateTouched(true);
              }}
              disabled={streaming}
              className="mt-1 rounded border border-stone-300 px-3 py-2 text-sm focus:outline-none focus:border-emerald-700 disabled:opacity-50"
            />
          </label>
        )}
        <button
          type="button"
          onClick={() => save()}
          disabled={busy || !canSave}
          className="rounded bg-emerald-800 text-white px-4 py-2 text-sm disabled:opacity-50 enabled:cursor-pointer"
        >
          {streaming ? "Composing…" : "Generate"}
        </button>
        {streaming && (
          <button
            type="button"
            onClick={cancel}
            className="rounded border border-stone-300 px-4 py-2 text-sm text-stone-700 hover:bg-stone-50 cursor-pointer"
          >
            Stop
          </button>
        )}
      </div>

      {error && (
        <p role="alert" className="text-sm text-red-700">
          <span className="font-medium">Couldn't compose —</span> {error}
        </p>
      )}

      {status === "conflict" && conflict && (
        <div role="alert" className="rounded border border-amber-200 bg-amber-50 p-3 text-sm">
          <p className="text-amber-800">
            {conflict.existing_status === "approved"
              ? "That slot is already approved and on the public board. Replacing it un-publishes it until you re-approve."
              : "That slot already holds a pending suggestion. Replacing it discards the current one."}
          </p>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              onClick={() => save(true)}
              disabled={busy}
              className="rounded bg-amber-700 text-white px-3 py-1.5 disabled:opacity-50 enabled:cursor-pointer"
            >
              Replace it
            </button>
            <button
              type="button"
              onClick={cancel}
              className="rounded border border-stone-300 px-3 py-1.5 text-stone-700 hover:bg-white cursor-pointer"
            >
              Keep it
            </button>
          </div>
        </div>
      )}

      {(streaming || events.length > 0) && <ReasoningReplay events={events} live pending={streaming} />}

      {savedId && (
        <p className="text-sm text-emerald-700">
          Saved {meal ? <span className="font-medium">{meal.name}</span> : "the meal"} to the queue
          as pending review.
        </p>
      )}
    </section>
  );
}
