import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { deleteAccount } from "../api/auth";
import { listSaves, type SavedMealCard } from "../api/saves";
import { useSessionStore } from "../store/session";
import { Profile } from "./Profile";

vi.mock("../api/auth", async (importActual) => ({
  ...(await importActual<typeof import("../api/auth")>()),
  deleteAccount: vi.fn(),
}));

vi.mock("../api/saves", async (importActual) => ({
  ...(await importActual<typeof import("../api/saves")>()),
  listSaves: vi.fn(),
}));

const deleteAccountMock = vi.mocked(deleteAccount);
const listSavesMock = vi.mocked(listSaves);

function renderProfile() {
  render(
    <MemoryRouter initialEntries={["/profile"]}>
      <Routes>
        <Route path="/profile" element={<Profile />} />
        <Route path="/" element={<p>home page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("Profile", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listSavesMock.mockResolvedValue([]);
    useSessionStore.setState({ user: null, status: "anon" });
  });

  it("prompts anonymous visitors to sign in", () => {
    renderProfile();

    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
    expect(screen.queryByRole("button", { name: "Sign out" })).not.toBeInTheDocument();
  });

  it("shows the account with sign out, and nests the sharper tools in a disclosure", () => {
    useSessionStore.setState({ user: { email: "u@e.com", role: "user" }, status: "authed" });
    renderProfile();

    expect(screen.getByText("u@e.com")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
    // Inside the (rendered but closed) disclosure, not alongside Sign out.
    const disclosure = screen.getByText("Account & security").closest("details");
    expect(disclosure).not.toBeNull();
    expect(disclosure).toContainElement(
      screen.getByRole("button", { name: "Sign out everywhere" }),
    );
    expect(disclosure).toContainElement(
      screen.getByRole("button", { name: /delete account/i }),
    );
  });

  it("offers no self-serve deletion for an admin account", () => {
    useSessionStore.setState({ user: { email: "a@e.com", role: "admin" }, status: "authed" });
    renderProfile();

    expect(screen.queryByRole("button", { name: /delete account/i })).not.toBeInTheDocument();
  });

  it("lists saved meals with the edited marker and the lookup filter", async () => {
    const user = userEvent.setup();
    const base = {
      description: "",
      tags: [],
      verdict: null,
      edited_at: null,
      created_at: "2026-07-06T00:00:00Z",
      has_recipe: false,
    };
    listSavesMock.mockResolvedValue([
      {
        ...base,
        id: "save-1",
        source: "curated",
        source_key: "meal-1",
        meal_type: "lunch",
        name: "Courgette salad",
        edited_at: "2026-07-06T01:00:00Z",
      },
      {
        ...base,
        id: "save-2",
        source: "lookup",
        source_key: "spaghetti",
        meal_type: null,
        name: "Spaghetti",
        verdict: "depends",
      },
    ] satisfies SavedMealCard[]);
    useSessionStore.setState({ user: { email: "u@e.com", role: "user" }, status: "authed" });
    renderProfile();

    expect(await screen.findByText("Courgette salad")).toBeInTheDocument();
    expect(screen.getByText("Edited by you")).toBeInTheDocument();

    // The lookup bucket filter keeps assessed dishes findable despite their null slot.
    await user.click(screen.getByRole("button", { name: "Dish checks" }));
    expect(screen.queryByText("Courgette salad")).not.toBeInTheDocument();
    expect(screen.getByText("Spaghetti")).toBeInTheDocument();
  });

  it("deletes the account only after the modal acknowledgement", async () => {
    const user = userEvent.setup();
    deleteAccountMock.mockResolvedValue(undefined);
    useSessionStore.setState({ user: { email: "u@e.com", role: "user" }, status: "authed" });
    renderProfile();

    await user.click(screen.getByText("Account & security"));
    await user.click(screen.getByRole("button", { name: /delete account/i }));

    const confirm = screen.getByRole("button", { name: "Delete my account" });
    expect(confirm).toBeDisabled();
    await user.click(screen.getByRole("checkbox"));
    await user.click(confirm);

    await waitFor(() => {
      expect(deleteAccountMock).toHaveBeenCalledOnce();
    });
    // Session cleared and routed home.
    expect(useSessionStore.getState().status).toBe("anon");
    expect(await screen.findByText("home page")).toBeInTheDocument();
  });
});
