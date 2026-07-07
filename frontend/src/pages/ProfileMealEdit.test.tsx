import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  deleteSave,
  generateRecipe,
  getSave,
  updateSave,
  type SavedMealDetail,
} from "../api/saves";
import { useUsageStore } from "../store/usage";
import { ProfileMealEdit } from "./ProfileMealEdit";

vi.mock("../api/saves", async (importActual) => ({
  ...(await importActual<typeof import("../api/saves")>()),
  getSave: vi.fn(),
  updateSave: vi.fn(),
  deleteSave: vi.fn(),
  generateRecipe: vi.fn(),
}));

const getSaveMock = vi.mocked(getSave);
const updateSaveMock = vi.mocked(updateSave);
const deleteSaveMock = vi.mocked(deleteSave);
const generateRecipeMock = vi.mocked(generateRecipe);

const SAVED: SavedMealDetail = {
  id: "save-1",
  source: "curated",
  source_key: "meal-1",
  meal_type: "lunch",
  name: "Courgette salad",
  description: "fresh and simple",
  tags: ["lunch"],
  verdict: null,
  edited_at: null,
  created_at: "2026-07-06T00:00:00Z",
  has_recipe: true,
  ingredients: [{ name: "courgette", category: "vegetable" }],
  recipe: ["Peel.", "Toss."],
  cautioned_ingredients: [],
  model: "fake/test",
  recipe_model: null,
};

function renderPage() {
  render(
    <MemoryRouter initialEntries={["/profile/meals/save-1"]}>
      <Routes>
        <Route path="/profile/meals/:id" element={<ProfileMealEdit />} />
        <Route path="/profile" element={<p>profile page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ProfileMealEdit", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    useUsageStore.getState().reset();
  });

  it("loads the saved copy into the form and saves via the saves API", async () => {
    const user = userEvent.setup();
    getSaveMock.mockResolvedValue(SAVED);
    updateSaveMock.mockResolvedValue({ ...SAVED, name: "My salad" });
    renderPage();

    const nameInput = await screen.findByDisplayValue("Courgette salad");
    await user.clear(nameInput);
    await user.type(nameInput, "My salad");
    // The tag surface is the closed-vocabulary picker, not a free-text input.
    await user.click(screen.getByRole("button", { name: "Green", pressed: false }));
    await user.click(screen.getByRole("button", { name: "Save my copy" }));

    await waitFor(() => {
      expect(updateSaveMock).toHaveBeenCalledWith(
        "save-1",
        expect.objectContaining({ name: "My salad", tags: ["lunch", "green"] }),
      );
    });
    expect(await screen.findByText("profile page")).toBeInTheDocument();
  });

  it("removes the saved copy and returns to the profile", async () => {
    const user = userEvent.setup();
    getSaveMock.mockResolvedValue(SAVED);
    deleteSaveMock.mockResolvedValue(undefined);
    renderPage();

    await screen.findByDisplayValue("Courgette salad");
    await user.click(
      screen.getByRole("button", { name: "Remove Courgette salad from saved meals" }),
    );

    expect(deleteSaveMock).toHaveBeenCalledWith("save-1");
    expect(await screen.findByText("profile page")).toBeInTheDocument();
  });

  it("stays on the page with an error when removal fails", async () => {
    const user = userEvent.setup();
    getSaveMock.mockResolvedValue(SAVED);
    deleteSaveMock.mockRejectedValue(new Error("network"));
    renderPage();

    await screen.findByDisplayValue("Courgette salad");
    await user.click(
      screen.getByRole("button", { name: "Remove Courgette salad from saved meals" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(/couldn't remove/i);
    expect(screen.queryByText("profile page")).not.toBeInTheDocument();
  });

  it("states that edits drop the verified badge for an untouched copy", async () => {
    getSaveMock.mockResolvedValue(SAVED);
    renderPage();

    expect(
      await screen.findByText(/no longer show as verified/i),
    ).toBeInTheDocument();
  });

  it("offers to write a recipe only when the copy has none", async () => {
    const user = userEvent.setup();
    getSaveMock.mockResolvedValue({ ...SAVED, recipe: null, has_recipe: false });
    generateRecipeMock.mockResolvedValue({
      meal: {
        ...SAVED,
        recipe: ["Peel.", "Toss."],
        has_recipe: true,
        recipe_model: "recipe/model",
      },
      recipe_model: "recipe/model",
      usage: { calls: 1, input_tokens: 1, output_tokens: 1, total_tokens: 2, steps: [] },
    });
    renderPage();

    await screen.findByDisplayValue("Courgette salad");
    await user.click(screen.getByRole("button", { name: "Write the recipe" }));

    expect(generateRecipeMock).toHaveBeenCalledWith("save-1");
    // The generated steps land in the form and the offer disappears for good.
    expect(await screen.findByDisplayValue(/Peel\./)).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Write the recipe" }),
    ).not.toBeInTheDocument();
    // Provenance for the generated steps, from the persisted recipe_model.
    expect(screen.getByText(/recipe written by/i)).toHaveTextContent("recipe/model");
    expect(useUsageStore.getState().totals.calls).toBe(1);
  });

  it("records nothing in the usage ledger when the server returned a stored recipe", async () => {
    const user = userEvent.setup();
    getSaveMock.mockResolvedValue({ ...SAVED, recipe: null, has_recipe: false });
    generateRecipeMock.mockResolvedValue({
      meal: { ...SAVED, has_recipe: true },
      recipe_model: "fake/test",
      usage: { calls: 0, input_tokens: 0, output_tokens: 0, total_tokens: 0, steps: [] },
    });
    renderPage();

    await screen.findByDisplayValue("Courgette salad");
    await user.click(screen.getByRole("button", { name: "Write the recipe" }));

    await waitFor(() => expect(generateRecipeMock).toHaveBeenCalled());
    expect(useUsageStore.getState().totals.calls).toBe(0);
    expect(useUsageStore.getState().recentCalls).toHaveLength(0);
  });

  it("blocks recipe generation while the form holds unsaved edits", async () => {
    const user = userEvent.setup();
    getSaveMock.mockResolvedValue({ ...SAVED, recipe: null, has_recipe: false });
    renderPage();

    const nameInput = await screen.findByDisplayValue("Courgette salad");
    await user.type(nameInput, " deluxe");

    expect(screen.getByRole("button", { name: "Write the recipe" })).toBeDisabled();
    expect(screen.getByText(/save or discard your edits first/i)).toBeInTheDocument();
    expect(generateRecipeMock).not.toHaveBeenCalled();

    // Undoing the edit re-enables generation; no stale dirty flag lingers.
    await user.clear(nameInput);
    await user.type(nameInput, "Courgette salad");
    expect(screen.getByRole("button", { name: "Write the recipe" })).toBeEnabled();
  });

  it("never offers a recipe for a copy that already has one", async () => {
    getSaveMock.mockResolvedValue(SAVED);
    renderPage();

    await screen.findByDisplayValue("Courgette salad");
    expect(
      screen.queryByRole("button", { name: "Write the recipe" }),
    ).not.toBeInTheDocument();
  });

  it("surfaces a recipe failure and lets the user retry", async () => {
    const user = userEvent.setup();
    getSaveMock.mockResolvedValue({ ...SAVED, recipe: null, has_recipe: false });
    generateRecipeMock.mockRejectedValue(new Error("model down"));
    renderPage();

    await screen.findByDisplayValue("Courgette salad");
    await user.click(screen.getByRole("button", { name: "Write the recipe" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/couldn't write the recipe/i);
    expect(screen.getByRole("button", { name: "Write the recipe" })).toBeEnabled();
  });

  it("shows a not-found style error when the save is missing", async () => {
    getSaveMock.mockRejectedValue(new Error("Meal not found."));
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("Meal not found.");
  });
});
