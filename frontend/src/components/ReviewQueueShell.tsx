import type { ReactNode } from "react";

interface ReviewQueueShellProps {
  count: number | null;
  loading: boolean;
  error: string | null;
  emptyMessage: string;
  onReload: () => void;
  children: ReactNode;
  // The noun after the count ("12 approved"); defaults to the pending-review wording.
  countNoun?: string;
  // The label shown before the first load lands, in place of the count.
  idleLabel?: string;
}

// The shared frame for a review queue: the count and refresh control, an error line,
// and an empty-state message, wrapping whatever cards the caller maps in. Each
// concrete queue (curated meals, daily board) differs only in its cards, its count
// wording, and its empty message, so the frame lives here once. count is null until
// the first load lands.
export function ReviewQueueShell({
  count,
  loading,
  error,
  emptyMessage,
  onReload,
  children,
  countNoun = "waiting for review",
  idleLabel = "Pending review",
}: ReviewQueueShellProps) {
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-stone-600">
          {count !== null ? `${count} ${countNoun}` : idleLabel}
        </p>
        <button
          type="button"
          onClick={onReload}
          disabled={loading}
          className="text-sm text-stone-600 hover:text-stone-900 underline underline-offset-4 disabled:opacity-50 enabled:cursor-pointer"
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {error && (
        <p role="alert" className="text-sm text-red-700">
          <span className="font-medium">Something went wrong —</span> {error}
        </p>
      )}

      {count === 0 && !loading && <p className="text-stone-600">{emptyMessage}</p>}

      {children}
    </div>
  );
}
