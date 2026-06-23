import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ReviewQueueShell } from "./ReviewQueueShell";

describe("ReviewQueueShell", () => {
  it("shows the count and renders its cards", () => {
    render(
      <ReviewQueueShell
        count={2}
        loading={false}
        error={null}
        emptyMessage="Nothing here."
        onReload={vi.fn()}
      >
        <p>card</p>
      </ReviewQueueShell>,
    );

    expect(screen.getByText("2 waiting for review")).toBeInTheDocument();
    expect(screen.getByText("card")).toBeInTheDocument();
    expect(screen.queryByText("Nothing here.")).not.toBeInTheDocument();
  });

  it("shows a placeholder before the first load, not the empty message", () => {
    render(
      <ReviewQueueShell
        count={null}
        loading={false}
        error={null}
        emptyMessage="Nothing here."
        onReload={vi.fn()}
      >
        {null}
      </ReviewQueueShell>,
    );

    expect(screen.getByText("Pending review")).toBeInTheDocument();
    expect(screen.queryByText("Nothing here.")).not.toBeInTheDocument();
  });

  it("shows the empty message once a loaded queue is empty", () => {
    render(
      <ReviewQueueShell
        count={0}
        loading={false}
        error={null}
        emptyMessage="Nothing here."
        onReload={vi.fn()}
      >
        {null}
      </ReviewQueueShell>,
    );

    expect(screen.getByText("Nothing here.")).toBeInTheDocument();
  });

  it("disables the refresh control while loading", () => {
    render(
      <ReviewQueueShell
        count={null}
        loading
        error={null}
        emptyMessage="Nothing here."
        onReload={vi.fn()}
      >
        {null}
      </ReviewQueueShell>,
    );

    expect(screen.getByRole("button", { name: "Refreshing…" })).toBeDisabled();
  });

  it("surfaces an error and refreshes on demand", async () => {
    const onReload = vi.fn();
    const user = userEvent.setup();
    render(
      <ReviewQueueShell
        count={null}
        loading={false}
        error="boom"
        emptyMessage="Nothing here."
        onReload={onReload}
      >
        {null}
      </ReviewQueueShell>,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("boom");
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(onReload).toHaveBeenCalledTimes(1);
  });
});
