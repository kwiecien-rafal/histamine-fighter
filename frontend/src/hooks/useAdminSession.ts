import { useCallback, useState } from "react";

import { errorMessage, login as loginRequest } from "../api/admin";
import { useAdminAuthStore } from "../store/adminAuth";

interface AdminSession {
  token: string | null;
  login: (email: string, password: string) => Promise<void>;
  logout: () => void;
  loggingIn: boolean;
  error: string | null;
}

export function useAdminSession(): AdminSession {
  const token = useAdminAuthStore((s) => s.token);
  const setToken = useAdminAuthStore((s) => s.setToken);
  const clearToken = useAdminAuthStore((s) => s.clearToken);
  const [loggingIn, setLoggingIn] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const login = useCallback(
    async (email: string, password: string) => {
      setLoggingIn(true);
      setError(null);
      try {
        const { access_token } = await loginRequest(email, password);
        setToken(access_token);
      } catch (err) {
        setError(errorMessage(err));
      } finally {
        setLoggingIn(false);
      }
    },
    [setToken],
  );

  return { token, login, logout: clearToken, loggingIn, error };
}
