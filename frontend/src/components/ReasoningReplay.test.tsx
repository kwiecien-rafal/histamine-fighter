import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { TraceEvent } from "../api/admin";
import { ReasoningReplay } from "./ReasoningReplay";

function event(text: string, kind: TraceEvent["kind"] = "check"): TraceEvent {
  return { kind, text, ingredient: null, compatibility: null };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("ReasoningReplay", () => {
  it("paces a recorded trace one step at a time", () => {
    vi.useFakeTimers();
    render(<ReasoningReplay events={[event("first"), event("second")]} stepMs={1000} />);

    expect(screen.getByText("How it was composed")).toBeInTheDocument();
    expect(screen.getByText("first")).toBeInTheDocument();
    expect(screen.queryByText("second")).not.toBeInTheDocument();

    void act(() => {
      vi.advanceTimersByTime(1000);
    });

    expect(screen.getByText("second")).toBeInTheDocument();
  });

  it("reveals the whole recorded trace on Skip, then hides Skip", async () => {
    const user = userEvent.setup();
    render(<ReasoningReplay events={[event("a"), event("b"), event("c")]} />);

    expect(screen.queryByText("c")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Skip" }));

    expect(screen.getByText("a")).toBeInTheDocument();
    expect(screen.getByText("c")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Skip" })).not.toBeInTheDocument();
  });

  it("reveals everything at once under prefers-reduced-motion, with no Skip", () => {
    vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches: true }));
    render(<ReasoningReplay events={[event("a"), event("b"), event("c")]} />);

    expect(screen.getByText("a")).toBeInTheDocument();
    expect(screen.getByText("c")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Skip" })).not.toBeInTheDocument();
  });

  it("shows every event at once in live mode, with no Skip", () => {
    render(<ReasoningReplay events={[event("first"), event("second")]} live />);

    expect(screen.getByText("first")).toBeInTheDocument();
    expect(screen.getByText("second")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Skip" })).not.toBeInTheDocument();
  });

  it("pulses a thinking line while live and pending, and drops it once settled", () => {
    const { rerender } = render(<ReasoningReplay events={[event("first")]} live pending />);

    expect(screen.getByText("first")).toBeInTheDocument();
    expect(screen.getByText(/Thinking/)).toBeInTheDocument();

    rerender(<ReasoningReplay events={[event("first")]} live />);

    expect(screen.queryByText(/Thinking/)).not.toBeInTheDocument();
  });
});
