import { useEffect, useRef } from "react";

// Shared modal-overlay keyboard and focus behaviour for the settings drawer and
// the usage panel: Escape closes, focus moves into the panel when it opens and is
// returned to the previously focused element when it closes. The callback is held
// in a ref so the effect only re-runs when `open` flips, not on every render
// (which would otherwise steal focus back to the panel on each state change).
export function useDismissableOverlay<T extends HTMLElement>(
  open: boolean,
  onClose: () => void,
) {
  const ref = useRef<T>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    ref.current?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onCloseRef.current();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previouslyFocused?.focus();
    };
  }, [open]);

  return ref;
}
