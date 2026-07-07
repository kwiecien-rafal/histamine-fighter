import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { saveMeal, type SavedMealDetail } from "../api/saves";
import { saveKey, useSavedMealsStore } from "../store/saves";
import { useSessionStore } from "../store/session";
import { SaveButton } from "./SaveButton";

vi.mock("../api/saves", async (importActual) => ({
  ...(await importActual<typeof import("../api/saves")>()),
  listSaves: vi.fn(),
  saveMeal: vi.fn(),
  deleteSave: vi.fn(),
}));

const saveMealMock = vi.mocked(saveMeal);

function renderButton() {
  render(
    <MemoryRouter>
      <SaveButton target={{ source: "curated", sourceId: "meal-1" }} />
    </MemoryRouter>,
  );
}

describe("SaveButton", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useSavedMealsStore.setState({ status: "ready", keys: new Map() });
    useSessionStore.setState({ user: { email: "u@e.com", role: "user" }, status: "authed" });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("toggles a save optimistically when signed in", async () => {
    const user = userEvent.setup();
    saveMealMock.mockResolvedValue({
      id: "save-1",
      source: "curated",
      source_key: "meal-1",
    } as SavedMealDetail);
    renderButton();

    await user.click(screen.getByRole("button", { name: "Save this dish" }));

    expect(saveMealMock).toHaveBeenCalledOnce();
    expect(
      screen.getByRole("button", { name: "Unsave this dish" }),
    ).toHaveAttribute("aria-pressed", "true");
  });

  it("shows the arrow in the target for an already-saved meal", () => {
    useSavedMealsStore.setState({
      status: "ready",
      keys: new Map([[saveKey("curated", "meal-1"), "save-1"]]),
    });
    renderButton();

    expect(screen.getByRole("button", { name: "Unsave this dish" })).toBeInTheDocument();
  });

  it("shoots the arrow and pops the THONK burst on save", async () => {
    const user = userEvent.setup();
    saveMealMock.mockResolvedValue({
      id: "save-1",
      source: "curated",
      source_key: "meal-1",
    } as SavedMealDetail);
    renderButton();

    await user.click(screen.getByRole("button", { name: "Save this dish" }));

    const button = screen.getByRole("button", { name: "Unsave this dish" });
    expect(button.querySelector(".animate-arrow-shoot")).not.toBeNull();
    expect(screen.getByText("Thonk!")).toHaveClass("animate-thonk");
  });

  it("skips the animation under reduced motion", async () => {
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true }));
    const user = userEvent.setup();
    saveMealMock.mockResolvedValue({
      id: "save-1",
      source: "curated",
      source_key: "meal-1",
    } as SavedMealDetail);
    renderButton();

    await user.click(screen.getByRole("button", { name: "Save this dish" }));

    const button = screen.getByRole("button", { name: "Unsave this dish" });
    expect(button.querySelector(".animate-arrow-shoot")).toBeNull();
    expect(screen.queryByText("Thonk!")).not.toBeInTheDocument();
  });

  it("offers to save a fresh lookup result even when a same-named save exists", () => {
    // The old name-derived key made a new result of "Spaghetti" light up as
    // already saved; the per-result id must not collide with the older save.
    useSavedMealsStore.setState({
      status: "ready",
      keys: new Map([[saveKey("lookup", "res-older"), "save-1"]]),
    });
    render(
      <MemoryRouter>
        <SaveButton
          target={{
            source: "lookup",
            payload: {
              lookup_id: "res-newer",
              dish: "Spaghetti",
              verdict: "depends",
              description: "",
              ingredients: [{ name: "tomato", category: null }],
              model: "fake/test",
              recipe: null,
              recipe_model: null,
            },
          }}
        />
      </MemoryRouter>,
    );

    expect(screen.getByRole("button", { name: "Save this dish" })).toBeInTheDocument();
  });

  it("hides the visible label when labelHidden but keeps the accessible name", () => {
    render(
      <MemoryRouter>
        <SaveButton target={{ source: "curated", sourceId: "meal-1" }} labelHidden />
      </MemoryRouter>,
    );

    const button = screen.getByRole("button", { name: "Save this dish" });
    expect(button.querySelector(".sr-only")).not.toBeNull();
  });

  it("prompts to sign in instead of saving when anonymous", async () => {
    const user = userEvent.setup();
    useSessionStore.setState({ user: null, status: "anon" });
    renderButton();

    await user.click(screen.getByRole("button", { name: "Save this dish" }));

    expect(saveMealMock).not.toHaveBeenCalled();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });
});
