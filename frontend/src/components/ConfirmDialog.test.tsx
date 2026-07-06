import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ConfirmDialog } from "./ConfirmDialog";

function renderDialog(overrides: Partial<React.ComponentProps<typeof ConfirmDialog>> = {}) {
  const props = {
    title: "Delete account?",
    body: <p>Everything goes away.</p>,
    confirmLabel: "Delete my account",
    onConfirm: vi.fn().mockResolvedValue(undefined),
    onCancel: vi.fn(),
    ...overrides,
  };
  render(<ConfirmDialog {...props} />);
  return props;
}

describe("ConfirmDialog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("keeps the destructive button disabled until acknowledged", async () => {
    const user = userEvent.setup();
    const props = renderDialog();

    const confirm = screen.getByRole("button", { name: "Delete my account" });
    expect(confirm).toBeDisabled();

    await user.click(screen.getByRole("checkbox"));
    expect(confirm).toBeEnabled();

    await user.click(confirm);
    expect(props.onConfirm).toHaveBeenCalledOnce();
  });

  it("cancels via the Cancel button and Escape", async () => {
    const user = userEvent.setup();
    const props = renderDialog();

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(props.onCancel).toHaveBeenCalledOnce();

    await user.keyboard("{Escape}");
    expect(props.onCancel).toHaveBeenCalledTimes(2);
  });

  it("shows the error and re-enables when the action fails", async () => {
    const user = userEvent.setup();
    renderDialog({ onConfirm: vi.fn().mockRejectedValue(new Error("nope")) });

    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: "Delete my account" }));

    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent("nope");
    });
    expect(screen.getByRole("button", { name: "Delete my account" })).toBeEnabled();
  });
});
