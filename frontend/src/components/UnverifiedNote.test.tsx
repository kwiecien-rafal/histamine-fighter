import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { UnverifiedNote } from "./UnverifiedNote";

describe("UnverifiedNote", () => {
  it("lists the ingredients the index could not vouch for", () => {
    render(<UnverifiedNote ingredients={["dragon fruit", "yuzu"]} />);

    expect(screen.getByText(/check before approving/i)).toBeInTheDocument();
    expect(screen.getByText(/dragon fruit, yuzu/)).toBeInTheDocument();
  });

  it("renders nothing when there is nothing unverified", () => {
    const { container } = render(<UnverifiedNote ingredients={[]} />);

    expect(container).toBeEmptyDOMElement();
  });
});
