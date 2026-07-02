import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { EditRejectedError, type MealEdit } from "../api/admin";
import { MealEditForm } from "./MealEditForm";

function initial(): MealEdit {
  return {
    name: "Courgette ribbon salad",
    description: "raw courgette ribbons with olive oil and fresh herbs",
    ingredients: [{ name: "courgette", category: "vegetable" }],
    recipe: ["Peel into ribbons."],
    tags: ["fresh"],
  };
}

describe("MealEditForm", () => {
  it("surfaces the 422 blocker list when a save is rejected", async () => {
    const onSave = vi.fn().mockRejectedValue(
      new EditRejectedError({
        message: "The edit introduces an ingredient the index flags.",
        blockers: ["parmesan (avoid)"],
        can_confirm: true,
      }),
    );
    const user = userEvent.setup();

    render(<MealEditForm initial={initial()} onSave={onSave} onCancel={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText(/index flags/)).toBeInTheDocument();
    expect(screen.getByText(/parmesan \(avoid\)/)).toBeInTheDocument();
  });

  it("resubmits with confirm_flagged when the operator saves anyway", async () => {
    const onSave = vi
      .fn()
      .mockRejectedValueOnce(
        new EditRejectedError({
          message: "The edit introduces an ingredient the index flags.",
          blockers: ["parmesan (avoid)"],
          can_confirm: true,
        }),
      )
      .mockResolvedValueOnce(undefined);
    const user = userEvent.setup();

    render(<MealEditForm initial={initial()} onSave={onSave} onCancel={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await user.click(await screen.findByRole("button", { name: "Save changes anyway" }));

    expect(onSave).toHaveBeenCalledTimes(2);
    expect(onSave).toHaveBeenLastCalledWith(expect.objectContaining({ confirm_flagged: true }));
  });

  it("offers no save-anyway button when the rejection is not confirmable", async () => {
    const onSave = vi.fn().mockRejectedValue(
      new EditRejectedError({
        message: "Some ingredients could not be checked against the index. Try again.",
        blockers: ["mystery (unverifiable)"],
        can_confirm: false,
      }),
    );
    const user = userEvent.setup();

    render(<MealEditForm initial={initial()} onSave={onSave} onCancel={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText(/could not be checked/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /anyway/ })).not.toBeInTheDocument();
  });

  it("submits the edited fields when the save succeeds", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();

    render(<MealEditForm initial={initial()} onSave={onSave} onCancel={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(onSave).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "Courgette ribbon salad",
        ingredients: [{ name: "courgette", category: "vegetable" }],
        tags: ["fresh"],
      }),
    );
  });

  it("shows a meal-type selector and the create label in create mode", async () => {
    const onSave = vi.fn().mockResolvedValue(undefined);
    const user = userEvent.setup();

    render(
      <MealEditForm
        initial={initial()}
        mealType={{ value: "breakfast", onChange: vi.fn() }}
        submitLabel="Create meal"
        onSave={onSave}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByLabelText("Meal type")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create meal" }));
    expect(onSave).toHaveBeenCalled();
  });
});
