import { useCallback, useEffect, useState } from "react";

import type { MealType } from "../api/admin";
import { useReasoningStream } from "../hooks/useReasoningStream";
import { MEAL_TYPE_LABEL, MEAL_TYPES } from "../lib/meal";
import { ReasoningReplay } from "./ReasoningReplay";

function mealTypeList(types: MealType[]): string {
  return types.map((type) => MEAL_TYPE_LABEL[type]).join(", ");
}

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
// with replace), and Generate board fills every open slot of the date in one stream,
// clearing the live log as each slot starts. The recorded run is re-watchable per-item on
// the queue card afterwards.
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
  // Whether the running (or last) stream was a full-board run, so the panel knows
  // which success copy and progress header to show.
  const [boardRun, setBoardRun] = useState(false);
  const {
    status,
    events,
    meal,
    error,
    savedId,
    conflict,
    currentSlot,
    slotErrors,
    board,
    start,
    cancel,
  } = useReasoningStream(onExpired);
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
      setBoardRun(false);
      const request =
        mode === "daily"
          ? { endpoint: "/admin/compose/daily", body: { meal_type: mealType, date, replace } }
          : { endpoint: "/admin/compose/curated", body: { meal_type: mealType } };
      void start(request);
    },
    [mode, mealType, date, start],
  );

  const generateBoard = useCallback(() => {
    setBoardRun(true);
    void start({ endpoint: "/admin/compose/daily/board", body: { date } });
  }, [date, start]);

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
          {streaming && !boardRun ? "Composing…" : "Generate"}
        </button>
        {mode === "daily" && (
          <button
            type="button"
            onClick={generateBoard}
            disabled={busy || !canSave}
            className="rounded border border-emerald-800 text-emerald-800 px-4 py-2 text-sm disabled:opacity-50 enabled:cursor-pointer hover:enabled:bg-emerald-50"
          >
            {streaming && boardRun ? "Composing board…" : "Generate full board"}
          </button>
        )}
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

      {streaming && boardRun && currentSlot && (
        <p className="text-sm text-stone-600" aria-live="polite">
          Composing <span className="font-medium">{MEAL_TYPE_LABEL[currentSlot.meal_type]}</span> (
          {currentSlot.index} of {currentSlot.total})…
        </p>
      )}

      {(streaming || events.length > 0) && <ReasoningReplay events={events} live pending={streaming} />}

      {slotErrors.length > 0 && (
        <ul role="alert" className="text-sm text-red-700 space-y-1">
          {slotErrors.map((slotError) => (
            <li key={slotError.meal_type}>
              <span className="font-medium">
                {MEAL_TYPE_LABEL[slotError.meal_type]} failed —
              </span>{" "}
              {slotError.detail}
            </li>
          ))}
        </ul>
      )}

      {savedId && !boardRun && (
        <p className="text-sm text-emerald-700">
          Saved {meal ? <span className="font-medium">{meal.name}</span> : "the meal"} to the queue
          as pending review.
        </p>
      )}

      {board && (
        <p className="text-sm text-emerald-700">
          Board done — saved {mealTypeList(board.composed) || "no meals"} to the queue as pending
          review.
          {board.skipped.length > 0 && ` Skipped ${mealTypeList(board.skipped)} (already filled).`}
        </p>
      )}
    </section>
  );
}
