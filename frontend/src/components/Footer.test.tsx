import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { Footer } from "./Footer";

function renderFooter() {
  render(
    <MemoryRouter>
      <Footer />
    </MemoryRouter>,
  );
}

describe("Footer", () => {
  it("carries the medical disclaimer", () => {
    renderFooter();

    expect(
      screen.getByText(/an educational tool, not medical advice/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/doctor or dietitian/i)).toBeInTheDocument();
  });

  it("links to the GitHub repository", () => {
    renderFooter();

    const github = screen.getByRole("link", { name: "GitHub" });
    expect(github).toHaveAttribute(
      "href",
      "https://github.com/kwiecien-rafal/histamine-fighter",
    );
    expect(github).toHaveAttribute("rel", "noreferrer");
  });
});
