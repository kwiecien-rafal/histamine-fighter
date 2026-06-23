import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AdminAuthError, getCurrentUser, login, logout, type AuthUser } from "../api/admin";
import { useAdminSession } from "./useAdminSession";

vi.mock("../api/admin", async (importActual) => {
  const actual = await importActual<typeof import("../api/admin")>();
  return {
    ...actual,
    getCurrentUser: vi.fn(),
    login: vi.fn(),
    logout: vi.fn(),
  };
});

const getCurrentUserMock = vi.mocked(getCurrentUser);
const loginMock = vi.mocked(login);
const logoutMock = vi.mocked(logout);

const adminUser: AuthUser = { email: "admin@example.com", role: "admin" };

afterEach(() => {
  vi.clearAllMocks();
});

describe("useAdminSession", () => {
  it("bootstraps to authed when /me returns a user", async () => {
    getCurrentUserMock.mockResolvedValueOnce(adminUser);
    const { result } = renderHook(() => useAdminSession());

    await waitFor(() => expect(result.current.status).toBe("authed"));
    expect(result.current.user).toEqual(adminUser);
  });

  it("bootstraps to anon when there is no session", async () => {
    getCurrentUserMock.mockRejectedValueOnce(new AdminAuthError("No session."));
    const { result } = renderHook(() => useAdminSession());

    await waitFor(() => expect(result.current.status).toBe("anon"));
    expect(result.current.user).toBeNull();
  });

  it("sets the user on a successful login", async () => {
    getCurrentUserMock.mockRejectedValueOnce(new AdminAuthError("No session."));
    loginMock.mockResolvedValueOnce(adminUser);
    const { result } = renderHook(() => useAdminSession());
    await waitFor(() => expect(result.current.status).toBe("anon"));

    await act(async () => {
      await result.current.login("admin@example.com", "supersecret");
    });

    expect(result.current.status).toBe("authed");
    expect(result.current.user).toEqual(adminUser);
  });

  it("surfaces a login error and stays anon", async () => {
    getCurrentUserMock.mockRejectedValueOnce(new AdminAuthError("No session."));
    loginMock.mockRejectedValueOnce(new Error("Incorrect email or password."));
    const { result } = renderHook(() => useAdminSession());
    await waitFor(() => expect(result.current.status).toBe("anon"));

    await act(async () => {
      await result.current.login("admin@example.com", "wrong");
    });

    expect(result.current.status).toBe("anon");
    expect(result.current.user).toBeNull();
    expect(result.current.error).toBe("Incorrect email or password.");
  });

  it("clears the session on logout", async () => {
    getCurrentUserMock.mockResolvedValueOnce(adminUser);
    logoutMock.mockResolvedValueOnce(undefined);
    const { result } = renderHook(() => useAdminSession());
    await waitFor(() => expect(result.current.status).toBe("authed"));

    await act(async () => {
      await result.current.logout();
    });

    expect(logoutMock).toHaveBeenCalledTimes(1);
    expect(result.current.status).toBe("anon");
    expect(result.current.user).toBeNull();
  });

  it("drops the local session on expire without a network call", async () => {
    getCurrentUserMock.mockResolvedValueOnce(adminUser);
    const { result } = renderHook(() => useAdminSession());
    await waitFor(() => expect(result.current.status).toBe("authed"));

    act(() => {
      result.current.expire();
    });

    expect(result.current.status).toBe("anon");
    expect(result.current.user).toBeNull();
    expect(logoutMock).not.toHaveBeenCalled();
  });
});
