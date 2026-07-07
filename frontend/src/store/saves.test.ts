import { beforeEach, describe, expect, it, vi } from "vitest";

import { deleteSave, saveMeal, listSaves, type SavedMealDetail } from "../api/saves";
import { saveKey, useSavedMealsStore } from "./saves";
import { useSessionStore } from "./session";

vi.mock("../api/saves", async (importActual) => ({
  ...(await importActual<typeof import("../api/saves")>()),
  listSaves: vi.fn(),
  saveMeal: vi.fn(),
  deleteSave: vi.fn(),
}));

const listSavesMock = vi.mocked(listSaves);
const saveMealMock = vi.mocked(saveMeal);
const deleteSaveMock = vi.mocked(deleteSave);

function detail(overrides: Partial<SavedMealDetail> = {}): SavedMealDetail {
  return {
    id: "save-1",
    source: "curated",
    source_key: "meal-1",
    meal_type: "lunch",
    name: "Courgette salad",
    description: "fresh",
    tags: [],
    verdict: null,
    edited_at: null,
    created_at: "2026-07-06T00:00:00Z",
    has_recipe: true,
    ingredients: [],
    recipe: null,
    cautioned_ingredients: [],
    model: "fake/test",
    recipe_model: null,
    ...overrides,
  };
}

describe("saved-meals store", () => {
  beforeEach(async () => {
    // mockReset (not clear) drops stale resolutions, so the session subscription's
    // own load during this setup fails fast instead of hydrating a previous test's
    // data; the state is then pinned after the microtasks settle.
    listSavesMock.mockReset();
    saveMealMock.mockReset();
    deleteSaveMock.mockReset();
    useSessionStore.setState({ user: { email: "u@e.com", role: "user" }, status: "authed" });
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    useSavedMealsStore.setState({ status: "idle", keys: new Map() });
    listSavesMock.mockClear();
  });

  it("hydrates the key map from the server once", async () => {
    listSavesMock.mockResolvedValue([detail(), detail({ id: "save-2", source_key: "meal-2" })]);

    await useSavedMealsStore.getState().load();
    await useSavedMealsStore.getState().load();

    expect(listSavesMock).toHaveBeenCalledOnce();
    expect(useSavedMealsStore.getState().keys.get(saveKey("curated", "meal-1"))).toBe("save-1");
  });

  it("does not fetch while signed out", async () => {
    useSessionStore.setState({ user: null, status: "anon" });

    await useSavedMealsStore.getState().load();

    expect(listSavesMock).not.toHaveBeenCalled();
  });

  it("keys a lookup save on its result id, echoed back by the server", async () => {
    saveMealMock.mockResolvedValue(
      detail({ id: "save-9", source: "lookup", source_key: "res-1" }),
    );

    await useSavedMealsStore.getState().toggle({
      source: "lookup",
      payload: {
        lookup_id: "res-1",
        dish: "Spaghetti",
        verdict: "depends",
        description: "",
        ingredients: [{ name: "tomato", category: null }],
        model: "fake/test",
        recipe: null,
        recipe_model: null,
      },
    });

    const { keys } = useSavedMealsStore.getState();
    expect(keys.get(saveKey("lookup", "res-1"))).toBe("save-9");
    expect(keys.size).toBe(1);
  });

  it("rolls the save button back when the save call fails", async () => {
    saveMealMock.mockRejectedValue(new Error("nope"));

    await useSavedMealsStore.getState().toggle({ source: "curated", sourceId: "meal-1" });

    expect(useSavedMealsStore.getState().keys.size).toBe(0);
  });

  it("restores the entry when an unsave fails", async () => {
    useSavedMealsStore.setState({
      status: "ready",
      keys: new Map([[saveKey("curated", "meal-1"), "save-1"]]),
    });
    deleteSaveMock.mockRejectedValue(new Error("nope"));

    await useSavedMealsStore.getState().toggle({ source: "curated", sourceId: "meal-1" });

    expect(useSavedMealsStore.getState().keys.get(saveKey("curated", "meal-1"))).toBe("save-1");
  });

  it("unsaves by row id and reports success", async () => {
    useSavedMealsStore.setState({
      status: "ready",
      keys: new Map([[saveKey("curated", "meal-1"), "save-1"]]),
    });
    deleteSaveMock.mockResolvedValue(undefined);

    const ok = await useSavedMealsStore.getState().unsave("save-1", saveKey("curated", "meal-1"));

    expect(ok).toBe(true);
    expect(deleteSaveMock).toHaveBeenCalledWith("save-1");
    expect(useSavedMealsStore.getState().keys.size).toBe(0);
  });

  it("rolls the unsave back and reports failure when the delete fails", async () => {
    useSavedMealsStore.setState({
      status: "ready",
      keys: new Map([[saveKey("curated", "meal-1"), "save-1"]]),
    });
    deleteSaveMock.mockRejectedValue(new Error("nope"));

    const ok = await useSavedMealsStore.getState().unsave("save-1", saveKey("curated", "meal-1"));

    expect(ok).toBe(false);
    expect(useSavedMealsStore.getState().keys.get(saveKey("curated", "meal-1"))).toBe("save-1");
  });

  it("clears when the session ends", () => {
    useSavedMealsStore.setState({
      status: "ready",
      keys: new Map([[saveKey("curated", "meal-1"), "save-1"]]),
    });

    useSessionStore.setState({ user: null, status: "anon" });

    expect(useSavedMealsStore.getState().keys.size).toBe(0);
    expect(useSavedMealsStore.getState().status).toBe("idle");
  });
});
