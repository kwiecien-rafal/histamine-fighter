import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { AdminDailySuggestion } from "../api/admin";
import { DailySuggestionCard } from "./DailySuggestionCard";

function suggestion(overrides: Partial<AdminDailySuggestion> = {}): AdminDailySuggestion {
  return {
    id: "s1",
    date: "2026-06-22",
    meal_type: "breakfast",
    content: {
      name: "Oat porridge",
      description: "warm oats with pear",
      ingredients: [{ name: "oats", category: "grain" }],
      recipe: null,
      tags: [],
      unverified_ingredients: ["pear"],
    },
    model: "stub/model",
    usage: null,
    reasoning_trace: [
      { kind: "verify", text: "All ingredients cleared the index.", ingredient: null, compatibility: null },
    ],
    reveal_at: "2026-06-22T06:00:00Z",
    approval_status: "pending",
    approved_at: null,
    approved_by: null,
    created_at: "2026-06-21T10:00:00Z",
    ...overrides,
  };
}

describe("DailySuggestionCard", () => {
  it("shows the composed content, reveal time, and flags unverified ingredients", () => {
    render(
      <DailySuggestionCard
        suggestion={suggestion()}
        busy={false}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />,
    );

    expect(screen.getByText("Oat porridge")).toBeInTheDocument();
    expect(screen.getByText("warm oats with pear")).toBeInTheDocument();
    expect(screen.getByText(/grain/)).toBeInTheDocument();
    expect(screen.getByText("Breakfast", { selector: "span" })).toBeInTheDocument();
    expect(screen.getByText(/Reveals/)).toBeInTheDocument();
    expect(screen.getByText(/check before approving/i)).toBeInTheDocument();
  });

  it("fires the decision callbacks", async () => {
    const onApprove = vi.fn();
    const onReject = vi.fn();
    const user = userEvent.setup();
    render(
      <DailySuggestionCard
        suggestion={suggestion()}
        busy={false}
        onApprove={onApprove}
        onReject={onReject}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Approve" }));
    await user.click(screen.getByRole("button", { name: "Reject" }));

    expect(onApprove).toHaveBeenCalledTimes(1);
    expect(onReject).toHaveBeenCalledTimes(1);
  });

  it("disables the actions while a decision is in flight", () => {
    render(
      <DailySuggestionCard suggestion={suggestion()} busy onApprove={vi.fn()} onReject={vi.fn()} />,
    );

    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
  });
});
