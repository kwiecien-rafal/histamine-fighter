import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { TagPicker } from "./TagPicker";

describe("TagPicker", () => {
  it("renders every vocabulary tag with its pressed state", () => {
    render(<TagPicker value={["lunch", "green"]} onChange={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Lunch" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "Green" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByRole("button", { name: "From dish check" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    // 4 meal slots + dish check + 6 colors.
    expect(screen.getAllByRole("button")).toHaveLength(11);
  });

  it("adds a tag in vocabulary order regardless of click order", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<TagPicker value={["green"]} onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: "Breakfast" }));

    expect(onChange).toHaveBeenCalledWith(["breakfast", "green"]);
  });

  it("removes a selected tag on a second click", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<TagPicker value={["breakfast", "green"]} onChange={onChange} />);

    await user.click(screen.getByRole("button", { name: "Green" }));

    expect(onChange).toHaveBeenCalledWith(["breakfast"]);
  });
});
