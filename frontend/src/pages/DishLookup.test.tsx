import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { FlowState } from "../hooks/useDishLookupFlow";
import { DishLookup } from "./DishLookup";

vi.mock("../api/client", () => ({
  MAX_DISH_CHARS: 80,
  MAX_INGREDIENT_CHARS: 60,
  MAX_INGREDIENTS: 25,
}));

vi.mock("../components/UsagePanel", () => ({ UsagePanel: () => null }));

const flow = {
  state: { phase: "idle", error: null } as FlowState,
  propose: vi.fn(),
  startManual: vi.fn(),
  renameDish: vi.fn(),
  renameIngredient: vi.fn(),
  removeIngredient: vi.fn(),
  addIngredient: vi.fn(),
  confirm: vi.fn(),
  requestAlternatives: vi.fn(),
  startOver: vi.fn(),
};

vi.mock("../hooks/useDishLookupFlow", () => ({
  MANUAL_MIN_INGREDIENTS: 2,
  useDishLookupFlow: () => flow,
}));

beforeEach(() => {
  vi.clearAllMocks();
  flow.state = { phase: "idle", error: null };
});

describe("DishLookup entry chooser", () => {
  it("offers the two entry cards on arrival, no search form yet", () => {
    render(<DishLookup />);

    expect(
      screen.getByRole("button", { name: /start with just a dish name/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /enter known ingredients/i }),
    ).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/spaghetti bolognese/i)).not.toBeInTheDocument();
  });

  it("reveals the dish-name form when that card is chosen", () => {
    render(<DishLookup />);

    fireEvent.click(screen.getByRole("button", { name: /start with just a dish name/i }));

    expect(screen.getByPlaceholderText(/spaghetti bolognese/i)).toBeInTheDocument();
    expect(flow.propose).not.toHaveBeenCalled();
    expect(flow.startManual).not.toHaveBeenCalled();
  });

  it("starts a blank manual entry when the ingredients card is chosen", () => {
    render(<DishLookup />);

    fireEvent.click(screen.getByRole("button", { name: /enter known ingredients/i }));

    expect(flow.startManual).toHaveBeenCalledWith("");
    expect(flow.propose).not.toHaveBeenCalled();
  });

  it("returns to the chooser from the dish-name form", () => {
    render(<DishLookup />);

    fireEvent.click(screen.getByRole("button", { name: /start with just a dish name/i }));
    fireEvent.click(screen.getByRole("button", { name: /back to both options/i }));

    expect(
      screen.getByRole("button", { name: /enter known ingredients/i }),
    ).toBeInTheDocument();
  });
});

describe("DishLookup manual editing", () => {
  it("shows an editable dish name and complains only on an invalid check attempt", () => {
    flow.state = {
      phase: "editing",
      dish: "",
      ingredients: [{ id: "1", name: "carrot", category: null }],
      model: null,
      cached: false,
      error: null,
    };
    render(<DishLookup />);

    const nameInput = screen.getByLabelText(/dish name/i);
    fireEvent.change(nameInput, { target: { value: "Carrot rice" } });
    expect(flow.renameDish).toHaveBeenCalledWith("Carrot rice");

    // No nagging before the user tries to run the check.
    expect(screen.queryByText(/give the dish a name/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /check safety/i }));
    expect(flow.confirm).not.toHaveBeenCalled();
    expect(
      screen.getByText(/give the dish a name and add at least 2 ingredients/i),
    ).toBeInTheDocument();
  });

  it("shows the thinking brawl while an assessment runs", () => {
    flow.state = {
      phase: "assessing",
      dish: "Carrot rice",
      ingredients: [
        { id: "1", name: "carrot", category: null },
        { id: "2", name: "rice", category: null },
      ],
      model: null,
    };
    render(<DishLookup />);

    expect(screen.getByText("Checking the ingredients…")).toBeInTheDocument();
  });

  it("runs the check once a name and two ingredients are in", () => {
    flow.state = {
      phase: "editing",
      dish: "Carrot rice",
      ingredients: [
        { id: "1", name: "carrot", category: null },
        { id: "2", name: "rice", category: null },
      ],
      model: null,
      cached: false,
      error: null,
    };
    render(<DishLookup />);

    fireEvent.click(screen.getByRole("button", { name: /check safety/i }));
    expect(flow.confirm).toHaveBeenCalled();
  });

  it("labels both sections and offers the way back to the chooser", () => {
    flow.state = {
      phase: "editing",
      dish: "",
      ingredients: [],
      model: null,
      cached: false,
      error: null,
    };
    render(<DishLookup />);

    expect(screen.getByText("Ingredients")).toBeInTheDocument();
    expect(screen.getByText(/0 of 25 ingredients/i)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /back to both options/i }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /start over/i })).not.toBeInTheDocument();
  });

  it("keeps the proposed dish name fixed on the model path", () => {
    flow.state = {
      phase: "editing",
      dish: "Bolognese",
      ingredients: [{ id: "1", name: "tomato", category: "vegetable" }],
      model: "stub/model",
      cached: false,
      error: null,
    };
    render(<DishLookup />);

    expect(screen.queryByLabelText(/dish name/i)).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Bolognese" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /check safety/i })).toBeEnabled();
  });
});
