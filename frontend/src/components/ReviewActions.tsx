import { useState } from "react";

interface ReviewActionsProps {
  busy: boolean;
  // Approve/Reject are omitted when they don't apply to the row's current state (an
  // already-approved meal shows no Approve, a rejected one shows no Reject).
  onApprove?: () => void;
  onReject?: () => void;
  // When provided, a Remove control (with an inline confirm) hard-deletes the row.
  onRemove?: () => void;
}

// Approve/Reject controls shared by the review cards. Approval is the human closing
// the gap code can't: the verdict only checks the listed ingredients, so the reviewer
// reads them on the card above before signing off. Remove is the destructive escape
// hatch for an unwanted generation, gated behind a one-step confirm.
export function ReviewActions({ busy, onApprove, onReject, onRemove }: ReviewActionsProps) {
  const [confirmingRemove, setConfirmingRemove] = useState(false);
  return (
    <div className="flex flex-wrap items-center gap-2">
      {onApprove && (
        <button
          type="button"
          onClick={onApprove}
          disabled={busy}
          aria-busy={busy}
          className="rounded bg-emerald-800 text-white px-4 py-2 text-sm disabled:opacity-50 enabled:cursor-pointer"
        >
          Approve
        </button>
      )}
      {onReject && (
        <button
          type="button"
          onClick={onReject}
          disabled={busy}
          aria-busy={busy}
          className="rounded border border-red-300 text-red-700 px-4 py-2 text-sm hover:border-red-500 disabled:opacity-50 enabled:cursor-pointer"
        >
          Reject
        </button>
      )}
      {onRemove &&
        (confirmingRemove ? (
          <span className="flex items-center gap-2 text-sm text-stone-600" role="alert">
            Remove?
            <button
              type="button"
              onClick={() => {
                // Leave the confirm regardless of outcome: on success the card unmounts,
                // on failure it stays put so the operator can retry, not stuck mid-confirm.
                setConfirmingRemove(false);
                onRemove();
              }}
              disabled={busy}
              className="rounded bg-red-700 text-white px-3 py-1.5 disabled:opacity-50 enabled:cursor-pointer"
            >
              Yes
            </button>
            <button
              type="button"
              onClick={() => setConfirmingRemove(false)}
              className="rounded border border-stone-300 px-3 py-1.5 text-stone-700 hover:bg-stone-50 cursor-pointer"
            >
              Cancel
            </button>
          </span>
        ) : (
          <button
            type="button"
            onClick={() => setConfirmingRemove(true)}
            disabled={busy}
            className="text-sm text-stone-500 hover:text-red-700 underline underline-offset-4 disabled:opacity-50 enabled:cursor-pointer"
          >
            Remove
          </button>
        ))}
      {busy && (
        <span className="text-sm text-stone-500" aria-live="polite">
          Saving…
        </span>
      )}
    </div>
  );
}
