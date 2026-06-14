import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import type { LLMUsage } from "../api/client";
import { useUsageStore } from "../store/usage";
import { UsagePanel } from "./UsagePanel";

const MODEL = "anthropic/claude-haiku-4-5";

function assessUsage(): LLMUsage {
  return {
    calls: 2,
    input_tokens: 1000,
    output_tokens: 500,
    total_tokens: 1500,
    steps: [
      { step: "disambiguate", input_tokens: 400, output_tokens: 200, total_tokens: 600, reported: true },
      { step: "synthesize", input_tokens: 600, output_tokens: 300, total_tokens: 900, reported: true },
    ],
  };
}

function openBar() {
  return screen.getByRole("button", { name: /show llm usage/i });
}

beforeEach(() => {
  localStorage.clear();
  useUsageStore.getState().reset();
});

describe("UsagePanel", () => {
  it("summarizes the session on the collapsed bar", () => {
    useUsageStore.getState().record("assess", MODEL, assessUsage());
    render(<UsagePanel />);

    expect(openBar()).toHaveTextContent("2 calls");
  });

  it("lists each call's model and cost, not the step labels, when expanded", async () => {
    const user = userEvent.setup();
    useUsageStore.getState().record("assess", MODEL, assessUsage());
    render(<UsagePanel />);

    await user.click(openBar());

    // The model shows in both the per-model breakdown and the recent-calls row.
    expect(screen.getAllByText(MODEL)).toHaveLength(2);
    // The recent-calls row is labelled by endpoint; the old greyed step column is gone.
    expect(screen.getByText(/^assess$/i)).toBeInTheDocument();
    expect(screen.queryByText(/synthesize/)).toBeNull();
    // A per-call cost is rendered (haiku is priced).
    expect(screen.getAllByText(/^~\$/).length).toBeGreaterThan(0);
  });

  it("clears the ledger from the Reset button", async () => {
    const user = userEvent.setup();
    useUsageStore.getState().record("assess", MODEL, assessUsage());
    render(<UsagePanel />);

    await user.click(openBar());
    await user.click(screen.getByRole("button", { name: "Reset" }));

    expect(useUsageStore.getState().totals.calls).toBe(0);
  });
});
