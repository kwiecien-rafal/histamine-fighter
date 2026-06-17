import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  AdminAuthError,
  approveMeal,
  listPendingMeals,
  login,
  type AdminMeal,
} from "../api/admin";
import { useAdminAuthStore } from "../store/adminAuth";
import { Admin } from "./Admin";

// Keep AdminAuthError and errorMessage real (instanceof and message formatting
// matter); stub only the network calls.
vi.mock("../api/admin", async (importActual) => {
  const actual = await importActual<typeof import("../api/admin")>();
  return {
    ...actual,
    login: vi.fn(),
    listPendingMeals: vi.fn(),
    approveMeal: vi.fn(),
    rejectMeal: vi.fn(),
  };
});

const loginMock = vi.mocked(login);
const listMock = vi.mocked(listPendingMeals);
const approveMock = vi.mocked(approveMeal);

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
  localStorage.clear();
  useAdminAuthStore.setState({ token: null });
  vi.clearAllMocks();
});

describe("Admin", () => {
  it("signs in and shows the pending queue", async () => {
    loginMock.mockResolvedValueOnce({ access_token: "tok", token_type: "bearer" });
    listMock.mockResolvedValueOnce([meal()]);
    const user = userEvent.setup();
    renderAdmin();

    await user.type(screen.getByLabelText("Email"), "admin@example.com");
    await user.type(screen.getByLabelText("Password"), "supersecret");
    await user.click(screen.getByRole("button", { name: "Log in" }));

    expect(await screen.findByText("Courgette ribbon salad")).toBeInTheDocument();
    // Scope to the meal-type chip: the live-compose selector also renders a "Lunch" option.
    expect(screen.getByText("Lunch", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText(/vegetable/)).toBeInTheDocument();
    expect(loginMock).toHaveBeenCalledWith("admin@example.com", "supersecret");
  });

  it("approves a meal and drops it from the queue", async () => {
    useAdminAuthStore.setState({ token: "tok" });
    listMock.mockResolvedValueOnce([meal()]);
    approveMock.mockResolvedValueOnce({ ...meal(), approval_status: "approved" });
    const user = userEvent.setup();
    renderAdmin();

    await screen.findByText("Courgette ribbon salad");
    await user.click(screen.getByRole("button", { name: "Approve" }));

    expect(await screen.findByText(/Nothing waiting for review/)).toBeInTheDocument();
    expect(screen.queryByText("Courgette ribbon salad")).not.toBeInTheDocument();
    expect(approveMock).toHaveBeenCalledWith("tok", "meal-1");
  });

  it("shows an inline error when the credentials are wrong", async () => {
    loginMock.mockRejectedValueOnce(new Error("Incorrect email or password."));
    const user = userEvent.setup();
    renderAdmin();

    await user.type(screen.getByLabelText("Email"), "admin@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Log in" }));

    expect(await screen.findByText(/Incorrect email or password/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Log in" })).toBeInTheDocument();
  });

  it("returns to the login form when the stored token has expired", async () => {
    useAdminAuthStore.setState({ token: "expired" });
    listMock.mockRejectedValueOnce(new AdminAuthError("Invalid or expired token."));
    renderAdmin();

    expect(await screen.findByRole("button", { name: "Log in" })).toBeInTheDocument();
    expect(screen.queryByText("Courgette ribbon salad")).not.toBeInTheDocument();
  });
});
