import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MANUAL_MODEL } from "../api/domain";
import { LLMProviderBadge } from "./LLMProviderBadge";

describe("LLMProviderBadge", () => {
  it("shows a real model name verbatim", () => {
    render(<LLMProviderBadge model="anthropic/claude" />);

    expect(screen.getByText("anthropic/claude")).toBeInTheDocument();
  });

  it("renders the manual sentinel as 'Curated by admin'", () => {
    render(<LLMProviderBadge model={MANUAL_MODEL} />);

    expect(screen.getByText("Curated by admin")).toBeInTheDocument();
    expect(screen.queryByText(MANUAL_MODEL)).not.toBeInTheDocument();
  });

  it("pins the manual sentinel the backend writes", () => {
    // Backend app/services/meal_service.py MANUAL_MODEL must match; a drift renders a manual
    // meal as its raw model string, and this is the only test that would catch it.
    expect(MANUAL_MODEL).toBe("manual");
  });
});
