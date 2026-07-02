// Display and timing helpers for the daily board. The branded copy and badges
// live here, off the wire, per CLAUDE section 19.

// Whole seconds left until the reveal as a compact "3h 12m 05s"; the hours and
// minutes segments drop off once they reach zero so a near reveal stays readable.
export function formatRemaining(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  const parts: string[] = [];
  if (hours > 0) parts.push(`${hours}h`);
  if (hours > 0 || minutes > 0) parts.push(`${minutes}m`);
  parts.push(`${seconds.toString().padStart(2, "0")}s`);
  return parts.join(" ");
}

// Honour the OS "reduce motion" setting by skipping the paced replay. matchMedia is
// absent in jsdom, so the optional chaining keeps tests on the animated path.
export function prefersReducedMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
}

// Format a YYYY-MM-DD calendar date in local time. new Date("2026-06-25") parses as UTC
// midnight, which toDateString() then renders a day early west of UTC; building the date
// from its parts keeps it on the intended day everywhere.
export function formatBoardDate(iso: string): string {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(year, month - 1, day).toDateString();
}

// How many days back the past-board view can step. Mirrors the backend's
// daily_history_days default; the dated endpoint 404s anything older, so this only
// caps the navigation, it does not own the bound.
export const PAST_BOARD_WINDOW_DAYS = 7;

// Today as YYYY-MM-DD in UTC, matching the backend's UTC "today" so the navigation
// bounds line up with the window the dated endpoint accepts.
export function todayIsoUtc(): string {
  return new Date().toISOString().slice(0, 10);
}

// Shift a YYYY-MM-DD date by whole days, in UTC so it never drifts across a timezone
// boundary. Lexical string comparison of two such dates is also a valid date order.
export function shiftIsoDate(iso: string, days: number): string {
  const [year, month, day] = iso.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day + days)).toISOString().slice(0, 10);
}
