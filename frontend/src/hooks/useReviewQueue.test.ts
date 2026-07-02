import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AdminAuthError } from "../api/admin";
import { useReviewQueue } from "./useReviewQueue";

interface Item {
  id: string;
}

// Stable across renders, the way Admin.tsx passes module-level api functions and a
// memoized onExpired, so the auto-load effect runs once.
const onExpired = vi.fn();
const list = vi.fn(() => Promise.resolve<Item[]>([]));
const approve = vi.fn((id: string) => Promise.resolve(id));
const reject = vi.fn((id: string) => Promise.resolve(id));
const remove = vi.fn((id: string) => Promise.resolve(id));

function renderQueue(enabled = true) {
  return renderHook(() => useReviewQueue<Item>(enabled, onExpired, list, approve, reject, remove));
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("useReviewQueue", () => {
  it("loads the pending list when enabled", async () => {
    list.mockResolvedValueOnce([{ id: "s1" }]);
    const { result } = renderQueue();

    await waitFor(() => expect(result.current.items).toHaveLength(1));
    expect(result.current.items?.[0].id).toBe("s1");
  });

  it("does not load while disabled", async () => {
    const { result } = renderQueue(false);

    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.items).toBeNull();
    expect(list).not.toHaveBeenCalled();
  });

  it("drops an item from the queue once approved", async () => {
    list.mockResolvedValueOnce([{ id: "s1" }, { id: "s2" }]);
    const { result } = renderQueue();
    await waitFor(() => expect(result.current.items).toHaveLength(2));

    await act(async () => {
      await result.current.decide("s1", "approve");
    });

    expect(approve).toHaveBeenCalledWith("s1");
    expect(result.current.items?.map((item) => item.id)).toEqual(["s2"]);
  });

  it("drops an item from the queue once rejected", async () => {
    list.mockResolvedValueOnce([{ id: "s1" }]);
    const { result } = renderQueue();
    await waitFor(() => expect(result.current.items).toHaveLength(1));

    await act(async () => {
      await result.current.decide("s1", "reject");
    });

    expect(reject).toHaveBeenCalledWith("s1");
    expect(result.current.items).toHaveLength(0);
  });

  it("drops an item from the queue once deleted", async () => {
    list.mockResolvedValueOnce([{ id: "s1" }]);
    const { result } = renderQueue();
    await waitFor(() => expect(result.current.items).toHaveLength(1));

    await act(async () => {
      await result.current.decide("s1", "delete");
    });

    expect(remove).toHaveBeenCalledWith("s1");
    expect(result.current.items).toHaveLength(0);
  });

  it("keeps the newest load when an older one resolves later", async () => {
    let resolveOld!: (items: Item[]) => void;
    let resolveNew!: (items: Item[]) => void;
    list.mockReturnValueOnce(new Promise<Item[]>((resolve) => (resolveOld = resolve)));
    list.mockReturnValueOnce(new Promise<Item[]>((resolve) => (resolveNew = resolve)));

    const { result } = renderQueue(); // mount fires the first (old) load, still pending
    act(() => {
      void result.current.reload(); // a second (newer) load supersedes it
    });

    // The newer load resolves first and lands.
    resolveNew([{ id: "new" }]);
    await waitFor(() => expect(result.current.items).toEqual([{ id: "new" }]));

    // The stale older load resolves after it, and must not overwrite the newer result.
    resolveOld([{ id: "old" }]);
    await act(async () => {
      await Promise.resolve();
    });
    expect(result.current.items).toEqual([{ id: "new" }]);
  });

  it("logs the session out on a 401", async () => {
    list.mockRejectedValueOnce(new AdminAuthError("Could not validate credentials."));
    renderQueue();

    await waitFor(() => expect(onExpired).toHaveBeenCalledTimes(1));
  });
});
