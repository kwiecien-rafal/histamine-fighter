import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";

import type { TraceEvent } from "../api/admin";
import { ReasoningReplay } from "./ReasoningReplay";

interface ReplayDialogProps {
  // The dish whose composition is being replayed, for the dialog label.
  title: string;
  trace: TraceEvent[];
  onClose: () => void;
}

const FOCUSABLE =
  'a[href], button:not([disabled]), input, textarea, [tabindex]:not([tabindex="-1"])';

// A focused modal overlay that replays one meal's recorded reasoning, shared by the
// admin cards and the public board. Portaled to the body so no card's overflow can clip
// it; traps Tab and restores focus to the opener on close so keyboard users keep place.
export function ReplayDialog({ title, trace, onClose }: ReplayDialogProps) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    panelRef.current?.focus();

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
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
  }, [onClose]);

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-stone-900/40 p-4"
      onClick={onClose}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={`How ${title} was composed`}
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
        className="w-full max-w-lg max-h-[85vh] overflow-y-auto outline-none"
      >
        <ReasoningReplay events={trace} />
        <div className="mt-3 flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-stone-300 bg-white px-4 py-2 text-sm text-stone-700 hover:bg-stone-50 cursor-pointer"
          >
            Close
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
