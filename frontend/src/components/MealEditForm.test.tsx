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
  it("surfaces the 422 blocker and recipe-flag lists when a save is rejected", async () => {
    const onSave = vi.fn().mockRejectedValue(
      new EditRejectedError({
        message: "The edit introduces an ingredient or recipe step the index flags.",
        blockers: ["parmesan (avoid)"],
        recipe_flags: ["parmesan"],
      }),
    );
    const user = userEvent.setup();

    render(<MealEditForm initial={initial()} onSave={onSave} onCancel={vi.fn()} />);

    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText(/index flags/)).toBeInTheDocument();
    expect(screen.getByText(/parmesan \(avoid\)/)).toBeInTheDocument();
    expect(screen.getByText(/Flagged in the recipe/)).toBeInTheDocument();
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
});
