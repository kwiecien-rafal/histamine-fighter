import { create } from "zustand";

import type { AuthUser } from "../api/admin";
import {
  getSessionUser,
  logout as logoutRequest,
  logoutEverywhere as logoutEverywhereRequest,
  setSessionExpiredHandler,
} from "../api/auth";

export type SessionStatus = "loading" | "authed" | "anon";

interface SessionState {
  user: AuthUser | null;
  status: SessionStatus;
  bootstrap: () => Promise<void>;
  setUser: (user: AuthUser) => void;
  clear: () => void;
  logout: () => Promise<void>;
  logoutEverywhere: () => Promise<void>;
}

// One /me bootstrap shared by every consumer (Navbar, SettingsDrawer, Login), and
// a latch so StrictMode's double-fired effect doesn't turn it into two calls.
let bootstrapStarted = false;

// Cookie-backed public session. The token lives in an httpOnly cookie the browser
// attaches automatically and JS cannot read, so the store holds only the public
// user shape and recovers it from /me once on app load. A store, not a hook:
// several components read it at once and must share one truth. The selected LLM
// provider is deliberately untouched on sign-out: it is the user's choice, and a
// signed-out "shared" selection already reads as a clear sign-in prompt.
export const useSessionStore = create<SessionState>()((set, get) => ({
  user: null,
  status: "loading",
  bootstrap: async () => {
    if (bootstrapStarted) return;
    bootstrapStarted = true;
    try {
      const user = await getSessionUser();
      set({ user, status: "authed" });
    } catch {
      // A verify/OAuth flow may have signed the user in while /me was in flight;
      // don't let the stale "no cookie yet" 401 clobber a live session.
      if (get().status !== "authed") set({ user: null, status: "anon" });
    }
  },
  setUser: (user) => set({ user, status: "authed" }),
  // Drop the local session without a network call, for when the server has already
  // rejected the cookie (a 401 from any call). Distinct from logout.
  clear: () => set({ user: null, status: "anon" }),
  logout: async () => {
    try {
      await logoutRequest();
    } finally {
      set({ user: null, status: "anon" });
    }
  },
  logoutEverywhere: async () => {
    try {
      await logoutEverywhereRequest();
    } finally {
      set({ user: null, status: "anon" });
    }
  },
}));

// Any API call that hits a 401 flips the UI to signed-out immediately, so the
// navbar cannot keep showing an account whose 30-day cookie has died. Guarded to
// authed: the bootstrap 401 of a plain anonymous visit is not an expiry.
setSessionExpiredHandler(() => {
  const { status, clear } = useSessionStore.getState();
  if (status === "authed") clear();
});

// Test seam: lets the suite re-run bootstrap in a fresh jsdom without module reloads.
export function resetSessionBootstrapForTests(): void {
  bootstrapStarted = false;
}
