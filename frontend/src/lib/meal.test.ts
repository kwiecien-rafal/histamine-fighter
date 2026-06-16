import { describe, expect, it } from "vitest";

import type { TraceEvent } from "../api/admin";
import { MEAL_TYPE_LABEL, TRACE_KIND_LABEL, isRejectEvent } from "./meal";

describe("meal display maps", () => {
  it("labels every meal type", () => {
    expect(MEAL_TYPE_LABEL.breakfast).toBe("Breakfast");
    expect(MEAL_TYPE_LABEL.snack).toBe("Snack");
  });

  it("labels every trace kind", () => {
    expect(TRACE_KIND_LABEL.reject).toBe("Reject");
    expect(TRACE_KIND_LABEL.verify).toBe("Verify");
  });

  it("flags reject events only", () => {
    const reject: TraceEvent = { kind: "reject", text: "x", ingredient: null, compatibility: null };
    const check: TraceEvent = { kind: "check", text: "y", ingredient: null, compatibility: null };
    expect(isRejectEvent(reject)).toBe(true);
    expect(isRejectEvent(check)).toBe(false);
  });
});
