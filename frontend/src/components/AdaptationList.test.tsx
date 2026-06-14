import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Adaptation } from "../api/client";
import { AdaptationList } from "./AdaptationList";

const adaptations: Adaptation[] = [
  {
    ingredients: ["red wine"],
    role: "supporting",
    action: "swap",
    swap: "beef stock",
    reason: "Stock keeps the savouriness.",
  },
  {
    ingredients: ["onion"],
    role: "seasoning",
    action: "omit",
    swap: null,
    reason: "The dish holds up without it.",
  },
  {
    ingredients: ["parmesan"],
    role: "core",
    action: "no_safe_swap",
    swap: null,
    reason: "Nothing keeps this dish intact.",
  },
];

describe("AdaptationList", () => {
  it("renders each action variant", () => {
    render(<AdaptationList adaptations={adaptations} />);

    expect(screen.getByText("beef stock")).toBeInTheDocument();
    expect(screen.getByText("leave it out")).toBeInTheDocument();
    expect(screen.getByText("no safe swap")).toBeInTheDocument();
  });

  it("strikes through removable ingredients but never a no-safe-swap one", () => {
    render(<AdaptationList adaptations={adaptations} />);

    expect(screen.getByText("red wine")).toHaveClass("line-through");
    expect(screen.getByText("onion")).toHaveClass("line-through");
    expect(screen.getByText("parmesan")).not.toHaveClass("line-through");
  });

  it("labels each entry's culinary role", () => {
    render(<AdaptationList adaptations={adaptations} />);

    expect(screen.getByText("core")).toBeInTheDocument();
    expect(screen.getByText("supporting")).toBeInTheDocument();
    expect(screen.getByText("seasoning")).toBeInTheDocument();
  });
});
