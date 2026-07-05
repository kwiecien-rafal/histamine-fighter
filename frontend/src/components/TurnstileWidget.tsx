import { useEffect, useRef } from "react";

// Cloudflare's explicit-render API, injected by their script. Only the slice the
// widget uses is typed here.
interface TurnstileApi {
  render: (
    element: HTMLElement,
    options: {
      sitekey: string;
      callback: (token: string) => void;
      "expired-callback": () => void;
      "error-callback": () => void;
    },
  ) => string;
  remove: (widgetId: string) => void;
}

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

const SCRIPT_SRC = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

export const TURNSTILE_SITE_KEY: string | undefined = import.meta.env.VITE_TURNSTILE_SITE_KEY as
  | string
  | undefined;

// Loads the Turnstile script on first use and resolves with the API. One promise
// module-wide, so several mounts share a single script tag.
let apiPromise: Promise<TurnstileApi> | null = null;

function loadTurnstile(): Promise<TurnstileApi> {
  if (apiPromise === null) {
    apiPromise = new Promise((resolve, reject) => {
      if (window.turnstile) {
        resolve(window.turnstile);
        return;
      }
      const script = document.createElement("script");
      script.src = SCRIPT_SRC;
      script.async = true;
      script.onload = () => {
        if (window.turnstile) resolve(window.turnstile);
        else reject(new Error("Turnstile script loaded without its API."));
      };
      script.onerror = () => reject(new Error("Could not load the Turnstile script."));
      document.head.appendChild(script);
    });
  }
  return apiPromise;
}

interface TurnstileWidgetProps {
  // Called with a fresh token, and with null when the token expires or errors.
  onToken: (token: string | null) => void;
  // Called when the widget cannot load or render at all (blocked script, offline).
  // The token callbacks never fire in that case, so without this the form would
  // stay disabled with no way to explain why.
  onError?: () => void;
}

// The bot check on the magic-link form. Renders nothing when no site key is
// configured (dev, self-hosted), matching the backend, which then skips
// verification entirely. The script loads only when this component mounts, so
// the rest of the app never talks to Cloudflare.
export function TurnstileWidget({ onToken, onError }: TurnstileWidgetProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const onTokenRef = useRef(onToken);
  onTokenRef.current = onToken;
  const onErrorRef = useRef(onError);
  onErrorRef.current = onError;

  useEffect(() => {
    const container = containerRef.current;
    if (!TURNSTILE_SITE_KEY || container === null) return;
    let widgetId: string | null = null;
    let unmounted = false;
    loadTurnstile()
      .then((api) => {
        if (unmounted) return;
        widgetId = api.render(container, {
          sitekey: TURNSTILE_SITE_KEY,
          callback: (token) => onTokenRef.current(token),
          "expired-callback": () => onTokenRef.current(null),
          "error-callback": () => onTokenRef.current(null),
        });
      })
      .catch(() => {
        if (!unmounted) onErrorRef.current?.();
      });
    return () => {
      unmounted = true;
      if (widgetId !== null) window.turnstile?.remove(widgetId);
    };
  }, []);

  if (!TURNSTILE_SITE_KEY) return null;
  return <div ref={containerRef} />;
}
