// Display and timing helpers for the daily board. The branded copy and badges
// live here, off the wire, per CLAUDE section 19.

// Remembers the last board date the visitor watched in full, so the premiere
// replay plays once a day and repeat visits go straight to the board (CLAUDE
// section 6: the animation must not become daily friction).
const SEEN_KEY = "hf.daily.seen";

export function hasSeenBoard(date: string): boolean {
  try {
    return localStorage.getItem(SEEN_KEY) === date;
  } catch {
    return false;
  }
}

export function markBoardSeen(date: string): void {
  try {
    localStorage.setItem(SEEN_KEY, date);
  } catch {
    // storage disabled (private mode): the replay simply plays again next visit.
  }
}

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

// Honour the OS "reduce motion" setting by skipping the replay. matchMedia is
// absent in jsdom, so the optional chaining keeps tests on the animated path.
export function prefersReducedMotion(): boolean {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
}
