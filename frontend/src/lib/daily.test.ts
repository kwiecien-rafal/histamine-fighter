import { beforeEach, describe, expect, it } from "vitest";

import { formatBoardDate, formatRemaining, hasSeenBoard, markBoardSeen } from "./daily";

describe("formatBoardDate", () => {
  it("formats a YYYY-MM-DD date on its own calendar day", () => {
    // new Date("2026-06-25") parses as UTC midnight and slips a day west of UTC; the
    // helper builds a local date so the calendar day is preserved.
    expect(formatBoardDate("2026-06-25")).toContain("Jun 25 2026");
  });
});

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
