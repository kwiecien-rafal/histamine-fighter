import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthUser } from "../api/admin";
import { useSessionStore } from "../store/session";
import { Navbar } from "./Navbar";

// The settings drawer pulls in the provider store and is its own concern; stub it so the
// test isolates the navbar's account slot.
vi.mock("./SettingsDrawer", () => ({ SettingsDrawer: () => null }));

function setSession(user: AuthUser | null) {
  useSessionStore.setState({ user, status: user ? "authed" : "anon" });
}

function renderNavbar() {
  render(
    <MemoryRouter>
      <Navbar />
    </MemoryRouter>,
  );
}

describe("Navbar", () => {
  beforeEach(() => {
    setSession(null);
  });

  it("links the flagship dish check", () => {
    renderNavbar();

    expect(screen.getByRole("link", { name: "Check a dish" })).toHaveAttribute(
      "href",
      "/lookup",
    );
  });

  it("links the Learn hub", () => {
    renderNavbar();

    expect(screen.getByRole("link", { name: "Learn" })).toHaveAttribute("href", "/learn");
  });

  it("offers Log in when nobody is signed in", () => {
    renderNavbar();

    expect(screen.getByRole("link", { name: "Log in" })).toHaveAttribute("href", "/login");
    expect(screen.queryByRole("link", { name: "Admin" })).not.toBeInTheDocument();
  });

  it("shows the account email for a signed-in user, without the admin link", () => {
    setSession({ email: "user@example.com", role: "user" });
    renderNavbar();

    expect(screen.getByRole("button", { name: "user@example.com" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Log in" })).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Admin" })).not.toBeInTheDocument();
  });

  it("links to Admin when an admin is signed in", () => {
    setSession({ email: "admin@example.com", role: "admin" });
    renderNavbar();

    const admin = screen.getByRole("link", { name: "Admin" });
    expect(admin).toHaveAttribute("href", "/admin");
    expect(screen.queryByRole("link", { name: "Log in" })).not.toBeInTheDocument();
  });
});
