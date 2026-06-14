import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { AlternativeGoal } from "../api/client";
import type { AlternativesState } from "../hooks/useDishLookupFlow";
import type { PivotTone } from "../lib/assessment";
import { AlternativesPanel } from "./AlternativesPanel";

function renderPanel(
  alternatives: AlternativesState,
  handlers: Partial<{
    onChooseGoal: (goal: AlternativeGoal) => void;
    onPick: (dish: string) => void;
  }> = {},
  tone: PivotTone = "lost",
) {
  render(
    <AlternativesPanel
      alternatives={alternatives}
      tone={tone}
      onChooseGoal={handlers.onChooseGoal ?? vi.fn()}
      onPick={handlers.onPick ?? vi.fn()}
    />,
  );
}

describe("AlternativesPanel", () => {
  it("offers the goal buttons when idle", () => {
    renderPanel({ status: "idle", cache: {} });

    expect(screen.getByRole("button", { name: "Just a good meal" })).toBeEnabled();
  });

  it("keeps the hard-to-save header when identity is lost", () => {
    renderPanel({ status: "idle", cache: {} }, {}, "lost");

    expect(screen.getByText(/hard to save/)).toBeInTheDocument();
  });

  it("softens the header when the dish is only altered", () => {
    renderPanel({ status: "idle", cache: {} }, {}, "altered");

    expect(
      screen.getByText(/Want something closer to the original/),
    ).toBeInTheDocument();
  });

  it("shows a loading line and disables the goals while loading", () => {
    renderPanel({ status: "loading", goal: "any_meal", cache: {} });

    expect(screen.getByText("Finding ideas…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Just a good meal" })).toBeDisabled();
  });

  it("shows the error message", () => {
    renderPanel({ status: "error", goal: "any_meal", message: "network down", cache: {} });

    expect(screen.getByText("network down")).toBeInTheDocument();
  });

  it("explains an empty result", () => {
    renderPanel({ status: "loaded", goal: "any_meal", suggestions: [], model: "stub/model", cache: {} });

    expect(screen.getByText(/no good alternatives/i)).toBeInTheDocument();
  });

  it("badges which model produced the suggestions", () => {
    renderPanel({
      status: "loaded",
      goal: "any_meal",
      suggestions: [{ name: "Courgette Pasta", pitch: "Fresh and herby." }],
      model: "stub/model",
      cache: {},
    });

    expect(screen.getByText("stub/model")).toBeInTheDocument();
  });

  it("fires onChooseGoal with the picked goal", async () => {
    const onChooseGoal = vi.fn();
    const user = userEvent.setup();
    renderPanel({ status: "idle", cache: {} }, { onChooseGoal });

    await user.click(screen.getByRole("button", { name: "Same style of dish" }));

    expect(onChooseGoal).toHaveBeenCalledWith("same_style");
  });

  it("fires onPick with a chosen suggestion", async () => {
    const onPick = vi.fn();
    const user = userEvent.setup();
    renderPanel(
      {
        status: "loaded",
        goal: "any_meal",
        suggestions: [{ name: "Courgette Pasta", pitch: "Fresh and herby." }],
        model: "stub/model",
        cache: {},
      },
      { onPick },
    );

    await user.click(screen.getByRole("button", { name: /Courgette Pasta/ }));

    expect(onPick).toHaveBeenCalledWith("Courgette Pasta");
  });
});
