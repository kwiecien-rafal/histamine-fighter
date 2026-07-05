import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getQuota } from "../api/auth";
import { useLLMProviderStore } from "../store/llmProvider";
import { useSessionStore } from "../store/session";
import { SettingsDrawer } from "./SettingsDrawer";

vi.mock("../api/auth", async (importActual) => ({
  ...(await importActual<typeof import("../api/auth")>()),
  getQuota: vi.fn(),
  deleteAccount: vi.fn(),
}));

const getQuotaMock = vi.mocked(getQuota);

function renderDrawer() {
  render(
    <MemoryRouter>
      <SettingsDrawer open onClose={() => undefined} />
    </MemoryRouter>,
  );
}

describe("SettingsDrawer shared tier", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useLLMProviderStore.setState({ provider: "ollama", apiKeys: {}, models: {}, ollamaBaseUrl: "" });
    useSessionStore.setState({ user: null, status: "anon" });
  });

  it("disables the shared row with a sign-in hint when anonymous", () => {
    renderDrawer();

    expect(screen.getByRole("radio", { name: /free tier/i })).toBeDisabled();
    // Two hints (the row and the account section), both to the login page.
    for (const link of screen.getAllByRole("link", { name: "Sign in" })) {
      expect(link).toHaveAttribute("href", "/login");
    }
  });

  it("enables the shared row and shows the quota when signed in", async () => {
    useSessionStore.setState({ user: { email: "u@e.com", role: "user" }, status: "authed" });
    useLLMProviderStore.setState({ provider: "shared" });
    getQuotaMock.mockResolvedValue({
      used: 3,
      limit: 20,
      resets_at: "2026-07-05T00:00:00+00:00",
    });

    renderDrawer();

    expect(screen.getByRole("radio", { name: /free tier/i })).toBeEnabled();
    await waitFor(() => {
      expect(screen.getByText(/3 of 20 free requests used today/i)).toBeInTheDocument();
    });
  });

  it("shows the signed-in account with sign out and delete controls", () => {
    useSessionStore.setState({ user: { email: "u@e.com", role: "user" }, status: "authed" });

    renderDrawer();

    expect(screen.getByText("u@e.com")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out everywhere" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /delete account/i })).toBeInTheDocument();
  });

  it("offers no self-serve deletion for an admin account", () => {
    // Admins are operator-managed via the CLI; the backend answers 403 anyway.
    useSessionStore.setState({ user: { email: "a@e.com", role: "admin" }, status: "authed" });

    renderDrawer();

    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete account/i })).not.toBeInTheDocument();
  });
});
