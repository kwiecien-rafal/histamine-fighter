import { beforeEach, describe, expect, it } from "vitest";

import { formatRemaining, hasSeenBoard, markBoardSeen } from "./daily";

describe("formatRemaining", () => {
  it("drops hours and minutes once they reach zero", () => {
    expect(formatRemaining(9000)).toBe("09s");
    expect(formatRemaining(65_000)).toBe("1m 05s");
    expect(formatRemaining(3_661_000)).toBe("1h 1m 01s");
  });

  it("never goes negative", () => {
    expect(formatRemaining(-5000)).toBe("00s");
  });
});

describe("seen-today tracking", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("remembers only the exact date marked", () => {
    expect(hasSeenBoard("2026-06-16")).toBe(false);

    markBoardSeen("2026-06-16");

    expect(hasSeenBoard("2026-06-16")).toBe(true);
    expect(hasSeenBoard("2026-06-17")).toBe(false);
  });
});
