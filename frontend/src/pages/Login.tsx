import { useCallback, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import {
  MAGIC_CODE_LENGTH,
  MAX_EMAIL_CHARS,
  requestMagicLink,
  verifyMagicLink,
} from "../api/auth";
import { errorMessage } from "../api/errors";
import { TURNSTILE_SITE_KEY, TurnstileWidget } from "../components/TurnstileWidget";
import { useSessionStore } from "../store/session";

// Copy for the coarse error flags the OAuth callback redirects back with.
const OAUTH_ERRORS: Record<string, string> = {
  oauth: "That sign-in didn't complete. Please try again, or use your email instead.",
  signup_limit: "Too many new accounts from this network today. Try again tomorrow.",
};

export function Login() {
  const [params] = useSearchParams();
  const oauthError = OAUTH_ERRORS[params.get("error") ?? ""] ?? null;

  const [email, setEmail] = useState("");
  const [turnstileToken, setTurnstileToken] = useState<string | null>(null);
  const [turnstileFailed, setTurnstileFailed] = useState(false);
  // Bumped to remount the widget for a fresh token; a Turnstile token is single
  // use, so a spent one (a failed submit, or a completed one the user backs out
  // of) must be discarded and a retry needs a new widget.
  const [turnstileNonce, setTurnstileNonce] = useState(0);
  const [sending, setSending] = useState(false);
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const resetTurnstile = useCallback(() => {
    setTurnstileToken(null);
    setTurnstileFailed(false);
    setTurnstileNonce((n) => n + 1);
  }, []);

  function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // The disabled button only takes effect on the next render, so guard the
    // in-flight window too: a double submit would reuse one single-use token.
    if (sending) return;
    setSending(true);
    setError(null);
    requestMagicLink(email.trim(), turnstileToken)
      .then(() => setSentTo(email.trim()))
      .catch((err: unknown) => {
        setError(errorMessage(err));
        resetTurnstile();
      })
      .finally(() => setSending(false));
  }

  // With Turnstile configured, hold the submit until the widget hands a token;
  // without it the form works as-is.
  const submitBlocked = sending || !email.trim() || (!!TURNSTILE_SITE_KEY && !turnstileToken);

  return (
    <div className="max-w-sm mx-auto">
      <h1 className="font-serif text-3xl text-forest-900 mb-2">Sign in</h1>
      <p className="text-stone-600 mb-6">
        A free account unlocks the shared AI tier — no password, no key needed.
      </p>

      {oauthError && !sentTo && (
        <p role="alert" className="mb-4 text-sm text-red-700">
          <span className="font-medium">Couldn't sign in —</span> {oauthError}
        </p>
      )}

      {sentTo ? (
        <CodeEntry
          email={sentTo}
          onStartOver={() => {
            setSentTo(null);
            resetTurnstile();
          }}
        />
      ) : (
        <>
          <form onSubmit={submit} className="flex flex-col gap-3">
            <label className="flex flex-col gap-1 text-sm">
              <span className="text-stone-600">Email</span>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
                maxLength={MAX_EMAIL_CHARS}
                required
                className="rounded border border-stone-300 px-3 py-2 focus:outline-none focus:border-forest-700"
              />
            </label>
            <TurnstileWidget
              key={turnstileNonce}
              onToken={setTurnstileToken}
              onError={() => setTurnstileFailed(true)}
            />
            {turnstileFailed && (
              <p role="alert" className="text-sm text-amber-700">
                Couldn't load the bot check. Disable any content blocker and reload, or
                continue with Google or GitHub below.
              </p>
            )}
            {error && (
              <p role="alert" className="text-sm text-red-700">
                <span className="font-medium">Couldn't send the email —</span> {error}
              </p>
            )}
            <button
              type="submit"
              disabled={submitBlocked}
              className="rounded bg-forest-800 hover:bg-forest-700 text-white px-4 py-2 disabled:opacity-50 enabled:cursor-pointer"
            >
              {sending ? "Sending…" : "Email me a sign-in link"}
            </button>
          </form>

          <div className="my-6 flex items-center gap-3 text-xs text-stone-400">
            <span className="h-px flex-1 bg-stone-200" />
            or
            <span className="h-px flex-1 bg-stone-200" />
          </div>

          {/* Full navigations, not fetches: the OAuth round trip is a redirect chain. */}
          <div className="flex flex-col gap-2">
            <OAuthButton href="/api/v1/auth/oauth/google/start">
              Continue with Google
            </OAuthButton>
            <OAuthButton href="/api/v1/auth/oauth/github/start">
              Continue with GitHub
            </OAuthButton>
          </div>

          <p className="mt-6 text-xs text-stone-500">
            By signing in you agree to our <LegalLink to="/terms">terms</LegalLink> and{" "}
            <LegalLink to="/privacy">privacy policy</LegalLink>.
          </p>
        </>
      )}
    </div>
  );
}

function OAuthButton({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a
      href={href}
      className="rounded border border-stone-300 bg-white px-4 py-2 text-center text-stone-800 hover:bg-cream-100"
    >
      {children}
    </a>
  );
}

function LegalLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <Link to={to} className="underline hover:text-stone-700">
      {children}
    </Link>
  );
}

// The cross-device path: the email landed elsewhere, the code is typed here.
function CodeEntry({ email, onStartOver }: { email: string; onStartOver: () => void }) {
  const navigate = useNavigate();
  const setUser = useSessionStore((s) => s.setUser);
  const [code, setCode] = useState("");
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = useCallback(
    (event: React.FormEvent<HTMLFormElement>) => {
      event.preventDefault();
      setVerifying(true);
      setError(null);
      verifyMagicLink({ email, code: code.trim() })
        .then((user) => {
          setUser(user);
          navigate("/", { replace: true });
        })
        .catch((err: unknown) => setError(errorMessage(err)))
        .finally(() => setVerifying(false));
    },
    [email, code, setUser, navigate],
  );

  return (
    <div className="flex flex-col gap-3">
      <p className="text-stone-700">
        Check your inbox — we sent a sign-in link to <span className="font-medium">{email}</span>.
        Click it, or enter the 6-digit code from the email below.
      </p>
      <p className="text-sm text-stone-500">
        Didn't get it? Check your spam folder — the code is in the email's subject line.
      </p>
      <form onSubmit={submit} className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-sm">
          <span className="text-stone-600">6-digit code</span>
          <input
            type="text"
            inputMode="numeric"
            pattern="[0-9]*"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            autoComplete="one-time-code"
            maxLength={MAGIC_CODE_LENGTH}
            required
            className="rounded border border-stone-300 px-3 py-2 tracking-[0.3em] focus:outline-none focus:border-forest-700"
          />
        </label>
        {error && (
          <p role="alert" className="text-sm text-red-700">
            <span className="font-medium">Couldn't sign in —</span> {error}
          </p>
        )}
        <button
          type="submit"
          disabled={verifying || code.trim().length !== MAGIC_CODE_LENGTH}
          className="rounded bg-forest-800 hover:bg-forest-700 text-white px-4 py-2 disabled:opacity-50 enabled:cursor-pointer"
        >
          {verifying ? "Signing in…" : "Sign in with code"}
        </button>
      </form>
      <button
        type="button"
        onClick={onStartOver}
        className="text-sm text-stone-500 underline hover:text-stone-700 self-start cursor-pointer"
      >
        Use a different email
      </button>
    </div>
  );
}
