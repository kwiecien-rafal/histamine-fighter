// Shared presentation formatting helpers.

// A quota reset instant as local wall-clock time; the fallback covers a
// malformed timestamp without surfacing "Invalid Date" to the user.
export function formatResetTime(isoTimestamp: string): string {
  const date = new Date(isoTimestamp);
  if (Number.isNaN(date.getTime())) return "midnight UTC";
  return date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
}
