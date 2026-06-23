interface ReviewActionsProps {
  busy: boolean;
  onApprove: () => void;
  onReject: () => void;
}

// Approve/Reject controls shared by the review cards. Approval is the human closing
// the gap code can't: the verdict only checks the listed ingredients, so the reviewer
// reads them on the card above before signing off.
export function ReviewActions({ busy, onApprove, onReject }: ReviewActionsProps) {
  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={onApprove}
        disabled={busy}
        aria-busy={busy}
        className="rounded bg-emerald-800 text-white px-4 py-2 text-sm disabled:opacity-50 enabled:cursor-pointer"
      >
        Approve
      </button>
      <button
        type="button"
        onClick={onReject}
        disabled={busy}
        aria-busy={busy}
        className="rounded border border-red-300 text-red-700 px-4 py-2 text-sm hover:border-red-500 disabled:opacity-50 enabled:cursor-pointer"
      >
        Reject
      </button>
      {busy && (
        <span className="text-sm text-stone-500" aria-live="polite">
          Saving…
        </span>
      )}
    </div>
  );
}
