import { describe, expect, it } from "vitest";

import { formatBoardDate, formatRemaining, shiftIsoDate } from "./daily";

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

describe("shiftIsoDate", () => {
  it("shifts by whole days across a month boundary", () => {
    expect(shiftIsoDate("2026-07-01", -1)).toBe("2026-06-30");
    expect(shiftIsoDate("2026-06-30", 1)).toBe("2026-07-01");
  });

  it("is a no-op for a zero shift", () => {
    expect(shiftIsoDate("2026-06-16", 0)).toBe("2026-06-16");
  });
});
