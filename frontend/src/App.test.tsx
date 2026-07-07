import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";
import { getDailyBoard } from "./api/daily";
import { browseMeals } from "./api/meals";

// Routing tests only need pages to mount; the network reads behind Home are stubbed
// and the settings drawer (provider store) is out of scope here.
vi.mock("./api/daily", async (importActual) => {
  const actual = await importActual<typeof import("./api/daily")>();
  return { ...actual, getDailyBoard: vi.fn(), getDailyBoardFor: vi.fn() };
});
vi.mock("./api/meals", async (importActual) => {
  const actual = await importActual<typeof import("./api/meals")>();
  return { ...actual, browseMeals: vi.fn() };
});
vi.mock("./components/SettingsDrawer", () => ({ SettingsDrawer: () => null }));
vi.mock("./api/learn", async (importActual) => {
  const actual = await importActual<typeof import("./api/learn")>();
  return { ...actual, askLearn: vi.fn(), listLearnArticles: vi.fn().mockResolvedValue([]) };
});

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(getDailyBoard).mockResolvedValue({
    board: { status: "locked", date: "2026-07-03", reveal_at: null },
    serverOffsetMs: 0,
  });
  vi.mocked(browseMeals).mockResolvedValue({ items: [], total: 0 });
});

function renderAt(path: string) {
  render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

describe("App routing", () => {
  it("renders the Home hero at the root", async () => {
    renderAt("/");

    expect(
      await screen.findByRole("heading", { name: "Fight back against histamine." }),
    ).toBeInTheDocument();
  });

  it("serves the dish lookup at /lookup", () => {
    renderAt("/lookup");

    // The lookup now opens on the two-card entry chooser, not the search form.
    expect(
      screen.getByRole("button", { name: /start with just a dish name/i }),
    ).toBeInTheDocument();
  });

  it("redirects an unknown path to Home", async () => {
    renderAt("/no-such-page");

    expect(
      await screen.findByRole("heading", { name: "Fight back against histamine." }),
    ).toBeInTheDocument();
  });

  it("serves the Learn hub at /learn", async () => {
    renderAt("/learn");

    expect(
      await screen.findByRole("heading", { name: "Know your enemy" }),
    ).toBeInTheDocument();
  });

  it("shows the footer disclaimer on public pages", async () => {
    renderAt("/");

    expect(
      await screen.findByText(/an educational tool, not medical advice/i),
    ).toBeInTheDocument();
  });
});
