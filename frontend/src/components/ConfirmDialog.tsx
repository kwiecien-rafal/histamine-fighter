import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { errorMessage } from "../api/errors";

interface ConfirmDialogProps {
  title: string;
  // The consequence list; rendered above the checkbox so the user reads it first.
  body: React.ReactNode;
  confirmLabel: string;
  // Runs the destructive action; the dialog stays open and shows the error if it throws.
  onConfirm: () => Promise<void>;
  onCancel: () => void;
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input, textarea, [tabindex]:not([tabindex="-1"])';

// Confirmation modal for destructive actions. Same portal + focus-trap shell as
// ReplayDialog; the destructive button stays disabled until the acknowledgement
// checkbox is ticked, so a stray double-click cannot confirm.
export function ConfirmDialog({
  title,
  body,
  confirmLabel,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    panelRef.current?.focus();

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onCancel();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = panelRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE);
      if (!focusable || focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    };

    const { overflow } = document.body.style;
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
      opener?.focus();
    };
  }, [onCancel]);

  async function handleConfirm() {
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  }

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-stone-900/40 p-4"
      onClick={onCancel}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
        className="w-full max-w-md rounded-lg border border-stone-200 bg-white p-5 shadow-xl outline-none"
      >
        <h2 className="text-lg font-semibold text-stone-900">{title}</h2>
        <div className="mt-2 text-sm text-stone-700">{body}</div>
        <label className="mt-4 flex items-start gap-2 text-sm text-stone-700 cursor-pointer">
          <input
            type="checkbox"
            checked={acknowledged}
            onChange={(e) => setAcknowledged(e.target.checked)}
            className="mt-0.5 accent-red-700"
          />
          I understand this can't be undone.
        </label>
        {error && (
          <p role="alert" className="mt-3 text-sm text-red-700">
            <span className="font-medium">Something went wrong —</span> {error}
          </p>
        )}
        <div className="mt-5 flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={onCancel}
            className="rounded border border-stone-300 bg-white px-4 py-2 text-sm text-stone-700 hover:bg-stone-50 cursor-pointer"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void handleConfirm()}
            disabled={!acknowledged || busy}
            className="rounded bg-red-700 px-4 py-2 text-sm text-white hover:bg-red-800 disabled:opacity-50 enabled:cursor-pointer"
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
