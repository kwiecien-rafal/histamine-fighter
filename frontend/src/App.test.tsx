import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { App } from "./App";

describe("App routing", () => {
  it("redirects an unknown path to the dish lookup", () => {
    render(
      <MemoryRouter initialEntries={["/no-such-page"]}>
        <App />
      </MemoryRouter>,
    );

    expect(screen.getByPlaceholderText("e.g. Spaghetti Bolognese")).toBeInTheDocument();
  });
});
