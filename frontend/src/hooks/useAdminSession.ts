import { useCallback, useEffect, useState } from "react";

import {
  errorMessage,
  getCurrentUser,
  login as loginRequest,
  logout as logoutRequest,
  type AuthUser,
} from "../api/admin";

export type SessionStatus = "loading" | "authed" | "anon";

interface AdminSession {
  user: AuthUser | null;
  status: SessionStatus;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  expire: () => void;
  loggingIn: boolean;
  error: string | null;
}

// Cookie-backed admin session. The token lives in an httpOnly cookie the browser
// attaches automatically and JS cannot read, so the hook holds only the public user
// shape and recovers it from /me on mount.
export function useAdminSession(): AdminSession {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [status, setStatus] = useState<SessionStatus>("loading");
  const [loggingIn, setLoggingIn] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getCurrentUser()
      .then((current) => {
        if (!active) return;
        setUser(current);
        setStatus("authed");
      })
      .catch(() => {
        if (!active) return;
        setUser(null);
        setStatus("anon");
      });
    return () => {
      active = false;
    };
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    setLoggingIn(true);
    setError(null);
    try {
      const current = await loginRequest(email, password);
      setUser(current);
      setStatus("authed");
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoggingIn(false);
    }
  }, []);

  // Drop the local session without a network call, for when the server has already
  // rejected the cookie (a 401 from any admin call). Distinct from logout, which also
  // asks the server to clear the cookie.
  const expire = useCallback(() => {
    setUser(null);
    setStatus("anon");
  }, []);

  const logout = useCallback(async () => {
    try {
      await logoutRequest();
    } finally {
      setUser(null);
      setStatus("anon");
    }
  }, []);

  return { user, status, login, logout, expire, loggingIn, error };
}
