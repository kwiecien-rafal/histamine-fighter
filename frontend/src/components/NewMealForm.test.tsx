import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createMeal, EditRejectedError } from "../api/admin";
import { NewMealForm } from "./NewMealForm";

// Keep EditRejectedError real (the form checks instanceof); stub only the create call.
vi.mock("../api/admin", async (importActual) => {
  const actual = await importActual<typeof import("../api/admin")>();
  return { ...actual, createMeal: vi.fn() };
});

const createMealMock = vi.mocked(createMeal);

beforeEach(() => {
  vi.clearAllMocks();
});

async function openAndFill(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  await user.click(screen.getByRole("button", { name: "+ New meal" }));
  await user.type(screen.getByLabelText("Name"), "Oat bowl");
  await user.type(screen.getByLabelText("Description"), "warm oats with pear");
  await user.type(screen.getByPlaceholderText("name"), "oats");
}

describe("NewMealForm", () => {
  it("creates a manual meal with the chosen slot and reloads", async () => {
    createMealMock.mockResolvedValueOnce(undefined as never);
    const onCreated = vi.fn();
    const user = userEvent.setup();
    render(<NewMealForm onCreated={onCreated} />);

    await openAndFill(user);
    await user.selectOptions(screen.getByLabelText("Meal type"), "dinner");
    await user.click(screen.getByRole("button", { name: "Create meal" }));

    expect(createMealMock).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "Oat bowl",
        meal_type: "dinner",
        ingredients: [{ name: "oats", category: null }],
      }),
    );
    expect(onCreated).toHaveBeenCalled();
    // The form closes on success, back to the trigger button.
    expect(await screen.findByRole("button", { name: "+ New meal" })).toBeInTheDocument();
  });

  it("keeps create disabled until name, description, and an ingredient are filled", async () => {
    const user = userEvent.setup();
    render(<NewMealForm onCreated={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "+ New meal" }));
    expect(screen.getByRole("button", { name: "Create meal" })).toBeDisabled();

    await user.type(screen.getByLabelText("Name"), "Oat bowl");
    await user.type(screen.getByLabelText("Description"), "warm oats with pear");
    await user.type(screen.getByPlaceholderText("name"), "oats");
    expect(screen.getByRole("button", { name: "Create meal" })).toBeEnabled();
  });

  it("shows the index blocker and stays open when the create is rejected", async () => {
    createMealMock.mockRejectedValueOnce(
      new EditRejectedError({
        message: "The edit introduces an ingredient the index flags.",
        blockers: ["parmesan (avoid)"],
        can_confirm: true,
      }),
    );
    const onCreated = vi.fn();
    const user = userEvent.setup();
    render(<NewMealForm onCreated={onCreated} />);

    await openAndFill(user);
    await user.click(screen.getByRole("button", { name: "Create meal" }));

    expect(await screen.findByText(/parmesan \(avoid\)/)).toBeInTheDocument();
    expect(onCreated).not.toHaveBeenCalled();
    // Still open with the rejected meal so the admin can fix and resubmit.
    expect(screen.getByRole("button", { name: "Create meal" })).toBeInTheDocument();
  });
});
