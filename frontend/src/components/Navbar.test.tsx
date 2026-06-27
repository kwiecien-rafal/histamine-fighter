import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import type { AuthUser } from "../api/admin";
import { useAdminSession } from "../hooks/useAdminSession";
import { Navbar } from "./Navbar";

// The settings drawer pulls in the provider store and is its own concern; stub it so the
// test isolates the navbar's account slot.
vi.mock("./SettingsDrawer", () => ({ SettingsDrawer: () => null }));
vi.mock("../hooks/useAdminSession");

const useAdminSessionMock = vi.mocked(useAdminSession);

function sessionWith(user: AuthUser | null): ReturnType<typeof useAdminSession> {
  return {
    user,
    status: user ? "authed" : "anon",
    login: vi.fn(),
    logout: vi.fn(),
    expire: vi.fn(),
    loggingIn: false,
    error: null,
  };
}

function renderNavbar() {
  render(
    <MemoryRouter>
      <Navbar />
    </MemoryRouter>,
  );
}

describe("Navbar", () => {
  it("offers Log in when no admin is signed in", () => {
    useAdminSessionMock.mockReturnValue(sessionWith(null));
    renderNavbar();

    expect(screen.getByRole("link", { name: "Log in" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Admin" })).not.toBeInTheDocument();
  });

  it("links to Admin when an admin is signed in", () => {
    useAdminSessionMock.mockReturnValue(sessionWith({ email: "admin@example.com", role: "admin" }));
    renderNavbar();

    const admin = screen.getByRole("link", { name: "Admin" });
    expect(admin).toHaveAttribute("href", "/admin");
    expect(screen.queryByRole("link", { name: "Log in" })).not.toBeInTheDocument();
  });
});
