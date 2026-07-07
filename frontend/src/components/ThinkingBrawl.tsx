// The waiting-state mascot: a cartoon dust cloud with swords and fists poking
// out, the "fighting histamine" brand made literal while a model call runs.
// Purely decorative (aria-hidden); the label beside it is the announced status,
// and every animation stills under prefers-reduced-motion via motion-reduce.

interface ThinkingBrawlProps {
  label: string;
  className?: string;
}

const STILL = "motion-reduce:animate-none";

export function ThinkingBrawl({ label, className = "" }: ThinkingBrawlProps) {
  return (
    <div className={`flex items-center gap-3 ${className}`}>
      <svg
        viewBox="0 0 120 80"
        aria-hidden="true"
        className="h-12 w-[4.5rem] shrink-0 text-stone-400"
      >
        {/* Sword poking out top-left, jabbing. */}
        <g
          className={`animate-brawl-jab ${STILL}`}
          style={{ transformOrigin: "34px 38px" }}
          stroke="currentColor"
          strokeWidth="3"
          strokeLinecap="round"
        >
          <path d="M32 36 L12 12" />
          <path d="M14 22 L26 12" strokeWidth="2.5" />
        </g>
        {/* Second sword top-right, counter-jabbing. */}
        <g
          className={`animate-brawl-jab-b ${STILL}`}
          style={{ transformOrigin: "84px 38px" }}
          stroke="currentColor"
          strokeWidth="3"
          strokeLinecap="round"
        >
          <path d="M86 36 L108 16" />
          <path d="M96 14 L106 26" strokeWidth="2.5" />
        </g>
        {/* A stick leg kicking out bottom-right. */}
        <g
          className={`animate-brawl-jab ${STILL}`}
          style={{ transformOrigin: "82px 58px", animationDelay: "0.2s" }}
          stroke="currentColor"
          strokeWidth="3"
          strokeLinecap="round"
        >
          <path d="M84 58 L102 68 L110 64" fill="none" />
        </g>
        {/* The dust cloud itself, wobbling. */}
        <g
          className={`animate-brawl-wobble ${STILL}`}
          style={{ transformOrigin: "58px 50px" }}
          fill="currentColor"
        >
          <circle cx="38" cy="52" r="15" />
          <circle cx="56" cy="42" r="18" />
          <circle cx="76" cy="50" r="15" />
          <circle cx="58" cy="58" r="16" />
          <circle cx="44" cy="42" r="11" opacity="0.75" />
          <circle cx="70" cy="40" r="10" opacity="0.75" />
        </g>
        {/* Impact stars popping around the cloud. */}
        <g fill="currentColor">
          <path
            d="M24 60 l2.2 4 4 2.2 -4 2.2 -2.2 4 -2.2 -4 -4 -2.2 4 -2.2 z"
            className={`animate-brawl-star ${STILL}`}
            style={{ transformOrigin: "24px 66px" }}
          />
          <path
            d="M60 14 l2 3.6 3.6 2 -3.6 2 -2 3.6 -2 -3.6 -3.6 -2 3.6 -2 z"
            className={`animate-brawl-star ${STILL}`}
            style={{ transformOrigin: "60px 19px", animationDelay: "0.45s" }}
          />
          <path
            d="M96 46 l1.8 3.2 3.2 1.8 -3.2 1.8 -1.8 3.2 -1.8 -3.2 -3.2 -1.8 3.2 -1.8 z"
            className={`animate-brawl-star ${STILL}`}
            style={{ transformOrigin: "96px 51px", animationDelay: "0.85s" }}
          />
        </g>
      </svg>
      <p className="text-sm text-stone-500" aria-live="polite">
        {label}
      </p>
    </div>
  );
}
