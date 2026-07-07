import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ThinkingBrawl } from "./ThinkingBrawl";

describe("ThinkingBrawl", () => {
  it("announces the label and hides the drawing from assistive tech", () => {
    const { container } = render(<ThinkingBrawl label="Finding ideas…" />);

    expect(screen.getByText("Finding ideas…")).toHaveAttribute("aria-live", "polite");
    const svg = container.querySelector("svg");
    expect(svg).toHaveAttribute("aria-hidden", "true");
  });

  it("stills every animation under reduced motion", () => {
    const { container } = render(<ThinkingBrawl label="Working…" />);

    const animated = container.querySelectorAll('[class*="animate-brawl"]');
    expect(animated.length).toBeGreaterThan(0);
    for (const node of animated) {
      expect(node.getAttribute("class")).toContain("motion-reduce:animate-none");
    }
  });
});
