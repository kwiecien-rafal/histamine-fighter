import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  AdminAuthError,
  approveMeal,
  getCurrentUser,
  listPendingDaily,
  listPendingMeals,
  login,
  type AdminMeal,
  type AuthUser,
} from "../api/admin";
import { Admin } from "./Admin";

// Keep AdminAuthError and errorMessage real (instanceof and message formatting
// matter). Stub only the network calls, including the daily-board queue that runs
// alongside the meal queue for any signed-in admin.
vi.mock("../api/admin", async (importActual) => {
  const actual = await importActual<typeof import("../api/admin")>();
  return {
    ...actual,
    getCurrentUser: vi.fn(),
    login: vi.fn(),
    logout: vi.fn(),
    listPendingMeals: vi.fn(),
    approveMeal: vi.fn(),
    rejectMeal: vi.fn(),
    listPendingDaily: vi.fn(),
    approveDaily: vi.fn(),
    rejectDaily: vi.fn(),
  };
});

const getCurrentUserMock = vi.mocked(getCurrentUser);
const loginMock = vi.mocked(login);
const listMock = vi.mocked(listPendingMeals);
const approveMock = vi.mocked(approveMeal);
const listDailyMock = vi.mocked(listPendingDaily);

const adminUser: AuthUser = { email: "admin@example.com", role: "admin" };

function meal(): AdminMeal {
  return {
    id: "meal-1",
    name: "Courgette ribbon salad",
    meal_type: "lunch",
    description: "raw courgette ribbons with olive oil and fresh herbs",
    ingredients: [{ name: "courgette", category: "vegetable" }],
    recipe: ["Peel into ribbons."],
    tags: ["fresh"],
    unverified_ingredients: [],
    model: "stub/model",
    usage: { calls: 4, input_tokens: 800, output_tokens: 120, total_tokens: 920, steps: [] },
    reasoning_trace: [
      { kind: "reject", text: "Dropped parmesan — avoid.", ingredient: "parmesan", compatibility: "avoid" },
      { kind: "verify", text: "All ingredients cleared the index.", ingredient: null, compatibility: null },
    ],
    approval_status: "pending",
    approved_at: null,
    approved_by: null,
    created_at: "2026-06-16T10:00:00Z",
  };
}

function renderAdmin() {
  render(
    <MemoryRouter>
      <Admin />
    </MemoryRouter>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  // Default: /me finds no session, so the page bootstraps to the login form. Tests
  // that start authed override this with a resolved user.
  getCurrentUserMock.mockRejectedValue(new AdminAuthError("No session."));
  // These tests assert on the curated-meal queue, so the daily queue resolves empty.
  listDailyMock.mockResolvedValue([]);
});

describe("Admin", () => {
  it("signs in and shows the pending queue", async () => {
    loginMock.mockResolvedValueOnce(adminUser);
    listMock.mockResolvedValueOnce([meal()]);
    const user = userEvent.setup();
    renderAdmin();

    await user.type(await screen.findByLabelText("Email"), "admin@example.com");
    await user.type(screen.getByLabelText("Password"), "supersecret");
    await user.click(screen.getByRole("button", { name: "Log in" }));

    expect(await screen.findByText("Courgette ribbon salad")).toBeInTheDocument();
    // Scope to the meal-type chip: the live-compose selector also renders a "Lunch" option.
    expect(screen.getByText("Lunch", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText(/vegetable/)).toBeInTheDocument();
    expect(loginMock).toHaveBeenCalledWith("admin@example.com", "supersecret");
  });

  it("approves a meal and drops it from the queue", async () => {
    getCurrentUserMock.mockResolvedValueOnce(adminUser);
    listMock.mockResolvedValueOnce([meal()]);
    approveMock.mockResolvedValueOnce({ ...meal(), approval_status: "approved" });
    const user = userEvent.setup();
    renderAdmin();

    await screen.findByText("Courgette ribbon salad");
    await user.click(screen.getByRole("button", { name: "Approve" }));

    // The curated-meal queue empties to its own message, distinct from the daily one.
    expect(await screen.findByText(/Compose some meals first/)).toBeInTheDocument();
    expect(screen.queryByText("Courgette ribbon salad")).not.toBeInTheDocument();
    expect(approveMock).toHaveBeenCalledWith("meal-1");
  });

  it("shows an inline error when the credentials are wrong", async () => {
    loginMock.mockRejectedValueOnce(new Error("Incorrect email or password."));
    const user = userEvent.setup();
    renderAdmin();

    await user.type(await screen.findByLabelText("Email"), "admin@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Log in" }));

    expect(await screen.findByText(/Incorrect email or password/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Log in" })).toBeInTheDocument();
  });

  it("returns to the login form when the session has expired", async () => {
    getCurrentUserMock.mockResolvedValueOnce(adminUser);
    listMock.mockRejectedValueOnce(new AdminAuthError("Could not validate credentials."));
    renderAdmin();

    expect(await screen.findByRole("button", { name: "Log in" })).toBeInTheDocument();
    expect(screen.queryByText("Courgette ribbon salad")).not.toBeInTheDocument();
  });
});
