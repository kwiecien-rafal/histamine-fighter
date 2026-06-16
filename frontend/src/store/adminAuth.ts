import { create } from "zustand";
import { persist } from "zustand/middleware";

// The admin access token, persisted to localStorage per CLAUDE section 10. It is
// short-lived (the backend sets the TTL) and re-read on each admin request, so an
// expired token simply bounces the operator back to the login form.
interface AdminAuthState {
  token: string | null;
  setToken: (token: string) => void;
  clearToken: () => void;
}

export const useAdminAuthStore = create<AdminAuthState>()(
  persist(
    (set) => ({
      token: null,
      setToken: (token) => set({ token }),
      clearToken: () => set({ token: null }),
    }),
    { name: "histamine-fighter:admin", version: 1 },
  ),
);
