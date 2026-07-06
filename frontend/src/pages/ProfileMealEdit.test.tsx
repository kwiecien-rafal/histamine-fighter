import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { deleteSave, getSave, updateSave, type SavedMealDetail } from "../api/saves";
import { ProfileMealEdit } from "./ProfileMealEdit";

vi.mock("../api/saves", async (importActual) => ({
  ...(await importActual<typeof import("../api/saves")>()),
  getSave: vi.fn(),
  updateSave: vi.fn(),
  deleteSave: vi.fn(),
}));

const getSaveMock = vi.mocked(getSave);
const updateSaveMock = vi.mocked(updateSave);
const deleteSaveMock = vi.mocked(deleteSave);

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

  it("shows a not-found style error when the save is missing", async () => {
    getSaveMock.mockRejectedValue(new Error("Meal not found."));
    renderPage();

    expect(await screen.findByRole("alert")).toHaveTextContent("Meal not found.");
  });
});
