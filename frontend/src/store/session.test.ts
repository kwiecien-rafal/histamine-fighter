import { beforeEach, describe, expect, it, vi } from "vitest";

import { getSessionUser, logout, logoutEverywhere, notifySessionExpired } from "../api/auth";
import { resetSessionBootstrapForTests, useSessionStore } from "./session";

vi.mock("../api/auth", async (importActual) => ({
  ...(await importActual<typeof import("../api/auth")>()),
  getSessionUser: vi.fn(),
  logout: vi.fn(),
  logoutEverywhere: vi.fn(),
}));

const getSessionUserMock = vi.mocked(getSessionUser);
const logoutMock = vi.mocked(logout);
const logoutEverywhereMock = vi.mocked(logoutEverywhere);

describe("session store", () => {
  beforeEach(() => {
    resetSessionBootstrapForTests();
    useSessionStore.setState({ user: null, status: "loading" });
    vi.clearAllMocks();
  });

  it("bootstraps to authed from /me", async () => {
    getSessionUserMock.mockResolvedValue({ email: "user@example.com", role: "user" });

    await useSessionStore.getState().bootstrap();

    expect(useSessionStore.getState().status).toBe("authed");
    expect(useSessionStore.getState().user?.email).toBe("user@example.com");
  });

  it("bootstraps to anon when /me rejects", async () => {
    getSessionUserMock.mockRejectedValue(new Error("401"));

    await useSessionStore.getState().bootstrap();

    expect(useSessionStore.getState().status).toBe("anon");
    expect(useSessionStore.getState().user).toBeNull();
  });

  it("runs the /me bootstrap only once, even when double-fired", async () => {
    getSessionUserMock.mockResolvedValue({ email: "user@example.com", role: "user" });

    await Promise.all([
      useSessionStore.getState().bootstrap(),
      useSessionStore.getState().bootstrap(),
    ]);

    expect(getSessionUserMock).toHaveBeenCalledTimes(1);
  });

  it("clears the session locally even when the logout call fails", async () => {
    useSessionStore.setState({ user: { email: "u@e.com", role: "user" }, status: "authed" });
    logoutMock.mockRejectedValue(new Error("network"));

    await useSessionStore.getState().logout().catch(() => undefined);

    expect(useSessionStore.getState().status).toBe("anon");
    expect(useSessionStore.getState().user).toBeNull();
  });

  it("signs out everywhere through the revocation endpoint", async () => {
    useSessionStore.setState({ user: { email: "u@e.com", role: "user" }, status: "authed" });
    logoutEverywhereMock.mockResolvedValue();

    await useSessionStore.getState().logoutEverywhere();

    expect(logoutEverywhereMock).toHaveBeenCalledTimes(1);
    expect(useSessionStore.getState().status).toBe("anon");
  });

  it("drops an authed session when any API call reports the cookie expired", () => {
    useSessionStore.setState({ user: { email: "u@e.com", role: "user" }, status: "authed" });

    notifySessionExpired();

    expect(useSessionStore.getState().status).toBe("anon");
    expect(useSessionStore.getState().user).toBeNull();
  });

  it("ignores the expiry signal while anonymous or still loading", () => {
    useSessionStore.setState({ user: null, status: "loading" });

    notifySessionExpired();

    expect(useSessionStore.getState().status).toBe("loading");
  });
});
