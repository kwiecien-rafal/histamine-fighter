import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { getSessionUser, verifyMagicLink } from "../api/auth";
import { useSessionStore } from "../store/session";

// Where the emailed magic link lands. A frontend page rather than a backend GET on
// purpose: email scanners prefetch GET links, and a prefetch must not consume the
// single-use token. This page's JS does the POST, which scanners don't run.
export function LoginVerify() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const setUser = useSessionStore((s) => s.setUser);
  const [failed, setFailed] = useState(false);
  // StrictMode double-fires effects in dev; a second POST would hit an
  // already-consumed token and read as a failure, so latch the first run.
  const started = useRef(false);

  const token = params.get("token");

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    if (!token) {
      setFailed(true);
      return;
    }
    verifyMagicLink({ token })
      .then((user) => {
        setUser(user);
        navigate("/", { replace: true });
      })
      .catch(() => setFailed(true));
  }, [token, setUser, navigate]);

  if (failed) {
    return (
      <div className="max-w-sm mx-auto">
        <h1 className="font-serif text-3xl text-forest-900 mb-2">Sign-in link expired</h1>
        <p className="text-stone-600 mb-4">
          That link is invalid or has already been used. Links work once and expire quickly —
          request a fresh one, or enter the 6-digit code from your newest email.
        </p>
        <Link
          to="/login"
          className="inline-block rounded bg-forest-800 hover:bg-forest-700 text-white px-4 py-2"
        >
          Back to sign in
        </Link>
      </div>
    );
  }
  return <p className="max-w-sm mx-auto text-stone-600">Signing you in…</p>;
}

// Where the OAuth callback redirect lands after the backend set the cookie. The
// session store re-reads /me, so the account shows up without a manual refresh.
export function LoginComplete() {
  const navigate = useNavigate();
  const clear = useSessionStore((s) => s.clear);
  const setUser = useSessionStore((s) => s.setUser);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    // Direct fetch instead of bootstrap(): bootstrap latches after first use, and
    // this page exists precisely because the session just changed.
    getSessionUser()
      .then((user) => setUser(user))
      .catch(() => clear())
      .finally(() => navigate("/", { replace: true }));
  }, [setUser, clear, navigate]);

  return <p className="max-w-sm mx-auto text-stone-600">Finishing sign-in…</p>;
}
