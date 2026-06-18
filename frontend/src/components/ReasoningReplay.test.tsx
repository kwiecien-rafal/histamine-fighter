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
});

describe("ReasoningReplay", () => {
  it("reveals events one at a time, then completes", () => {
    vi.useFakeTimers();
    const onComplete = vi.fn();
    render(
      <ReasoningReplay
        events={[event("first"), event("second")]}
        onComplete={onComplete}
        stepMs={1000}
      />,
    );

    expect(screen.getByText("first")).toBeInTheDocument();
    expect(screen.queryByText("second")).not.toBeInTheDocument();

    void act(() => {
      vi.advanceTimersByTime(1000);
    });

    expect(screen.getByText("second")).toBeInTheDocument();
    expect(onComplete).not.toHaveBeenCalled();

    void act(() => {
      vi.advanceTimersByTime(2000);
    });

    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("completes immediately when there is nothing to replay", () => {
    vi.useFakeTimers();
    const onComplete = vi.fn();
    render(<ReasoningReplay events={[]} onComplete={onComplete} />);

    void act(() => {
      vi.advanceTimersByTime(2000);
    });

    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("skips straight to the board", async () => {
    const onComplete = vi.fn();
    const user = userEvent.setup();
    render(
      <ReasoningReplay
        events={[event("a"), event("b"), event("c")]}
        onComplete={onComplete}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Skip" }));

    expect(onComplete).toHaveBeenCalledTimes(1);
  });

  it("shows every event at once in live mode, with no Skip", () => {
    render(<ReasoningReplay events={[event("first"), event("second")]} live />);

    expect(screen.getByText("first")).toBeInTheDocument();
    expect(screen.getByText("second")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Skip" })).not.toBeInTheDocument();
  });

  it("pulses a thinking line while live and pending, and drops it once settled", () => {
    const { rerender } = render(
      <ReasoningReplay events={[event("first")]} live pending />,
    );

    expect(screen.getByText("first")).toBeInTheDocument();
    expect(screen.getByText(/Thinking/)).toBeInTheDocument();

    rerender(<ReasoningReplay events={[event("first")]} live />);

    expect(screen.queryByText(/Thinking/)).not.toBeInTheDocument();
  });

  it("labels each meal once as its steps begin", () => {
    const events: TraceEvent[] = [
      { ...event("b1"), meal_type: "breakfast" },
      { ...event("b2"), meal_type: "breakfast" },
      { ...event("d1"), meal_type: "dinner" },
    ];
    render(<ReasoningReplay events={events} live />);

    expect(screen.getAllByText("Breakfast")).toHaveLength(1);
    expect(screen.getByText("Dinner")).toBeInTheDocument();
  });
});
