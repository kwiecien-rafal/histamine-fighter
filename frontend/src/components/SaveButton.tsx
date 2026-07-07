import { useState } from "react";
import { Link } from "react-router-dom";

import type { SaveTarget } from "../api/saves";
import { useDismissableOverlay } from "../hooks/useDismissableOverlay";
import { prefersReducedMotion } from "../lib/daily";
import { saveKey, useSavedMealsStore } from "../store/saves";
import { useSessionStore } from "../store/session";

interface SaveButtonProps {
  target: SaveTarget;
  // The browse-grid overlay is too tight for the text label; the name stays for
  // screen readers via sr-only.
  labelHidden?: boolean;
}

// Transient animation phase: the arrow flying into the bullseye on save, the
// broken arrow fading out on unsave. Cleared on animationend, skipped entirely
// under reduced motion.
type SaveAnim = "hit" | "break" | null;

// The archery target on every final-form dish. Signed in it toggles the save
// optimistically through the shared store; signed out it advertises the feature
// with a small sign-in popover instead of hiding. Click handling always stops
// propagation so the button is safe inside a card that is itself a link.
export function SaveButton({ target, labelHidden = false }: SaveButtonProps) {
  const signedIn = useSessionStore((s) => s.status === "authed");
  const keys = useSavedMealsStore((s) => s.keys);
  const toggle = useSavedMealsStore((s) => s.toggle);
  const [prompting, setPrompting] = useState(false);
  const [anim, setAnim] = useState<SaveAnim>(null);
  const popoverRef = useDismissableOverlay<HTMLDivElement>(prompting, () =>
    setPrompting(false),
  );

  const key =
    target.source === "lookup"
      ? saveKey("lookup", target.payload.lookup_id)
      : saveKey(target.source, target.sourceId);
  const saved = keys.has(key);
  const label = saved ? "Unsave this dish" : "Save this dish";

  function onClick(event: React.MouseEvent) {
    event.preventDefault();
    event.stopPropagation();
    if (!signedIn) {
      setPrompting((open) => !open);
      return;
    }
    // Fires alongside the optimistic flip; a rollback does not re-animate.
    if (!prefersReducedMotion()) setAnim(saved ? "break" : "hit");
    void toggle(target);
  }

  // Feathers form a V whose vertex sits on the shaft and opens outward, so the
  // arrow reads as tip-first in the bullseye (a V at the shaft's end reads as an
  // arrowhead pointing the wrong way).
  const fletching = (
    <>
      <line x1="19.6" y1="4.4" x2="21.6" y2="4.9" className="stroke-forest-700" strokeWidth="1.5" strokeLinecap="round" />
      <line x1="19.6" y1="4.4" x2="19.1" y2="2.4" className="stroke-forest-700" strokeWidth="1.5" strokeLinecap="round" />
    </>
  );

  return (
    <span className="relative inline-flex">
      <button
        type="button"
        onClick={onClick}
        aria-pressed={saved}
        aria-label={label}
        title={label}
        className={`group inline-flex items-center gap-1.5 cursor-pointer transition-colors ${
          saved ? "text-ember-700 hover:text-ember-800" : "text-stone-500 hover:text-ember-700"
        }`}
      >
        <span
          className={
            labelHidden
              ? "sr-only"
              : "relative text-xs whitespace-nowrap after:absolute after:inset-x-0 after:-bottom-0.5 after:h-px after:origin-left after:scale-x-0 after:bg-current after:transition-transform after:duration-200 group-hover:after:scale-x-100"
          }
        >
          {label}
        </span>
        <span className="relative inline-flex shrink-0">
          <svg viewBox="0 0 24 24" className="h-5 w-5" aria-hidden="true">
            <circle cx="12" cy="12" r="9" className="fill-cream-50 stroke-ember-500" strokeWidth="1.5" />
            <circle cx="12" cy="12" r="5.5" fill="none" className="stroke-ember-600" strokeWidth="1.5" />
            <circle cx="12" cy="12" r="2" className="fill-ember-700" />
            {anim === "break" ? (
              <>
                <g className="animate-arrow-break-a [transform-box:fill-box] origin-center">
                  <line x1="12" y1="12" x2="16.5" y2="7.5" className="stroke-forest-700" strokeWidth="1.8" strokeLinecap="round" />
                </g>
                <g
                  className="animate-arrow-break-b [transform-box:fill-box] origin-center"
                  onAnimationEnd={() => setAnim(null)}
                >
                  <line x1="16.5" y1="7.5" x2="21" y2="3" className="stroke-forest-700" strokeWidth="1.8" strokeLinecap="round" />
                  {fletching}
                </g>
              </>
            ) : (
              saved && (
                <g className={anim === "hit" ? "animate-arrow-shoot" : undefined}>
                  <line x1="12" y1="12" x2="21" y2="3" className="stroke-forest-700" strokeWidth="1.8" strokeLinecap="round" />
                  {fletching}
                </g>
              )
            )}
          </svg>
          {anim === "hit" && (
            <span
              aria-hidden="true"
              onAnimationEnd={() => setAnim(null)}
              className="animate-thonk pointer-events-none select-none absolute -top-4 left-1/2 font-serif font-bold text-[10px] uppercase text-ember-700"
            >
              Thonk!
            </span>
          )}
        </span>
      </button>
      {prompting && (
        <div
          ref={popoverRef}
          tabIndex={-1}
          className="absolute right-0 top-full z-20 mt-2 w-52 rounded border border-stone-200 bg-white p-3 text-sm text-stone-700 shadow-lg outline-none"
        >
          <Link
            to="/login"
            onClick={(event) => event.stopPropagation()}
            className="underline hover:text-stone-900"
          >
            Sign in
          </Link>{" "}
          to save dishes for later.
        </div>
      )}
    </span>
  );
}
