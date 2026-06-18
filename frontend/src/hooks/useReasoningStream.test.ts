import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useReasoningStream } from "./useReasoningStream";

// Build a fetch Response whose body streams the given SSE frames in chunks, so the
// hook's incremental parser is exercised the way a real stream delivers it.
function sseResponse(frames: string[]): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      const encoder = new TextEncoder();
      for (const frame of frames) controller.enqueue(encoder.encode(frame));
      controller.close();
    },
  });
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

afterEach(() => {
  // unstubAllGlobals undoes vi.stubGlobal("fetch", ...); restoreAllMocks does not,
  // so without it the stubbed fetch would leak into other suites.
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("useReasoningStream", () => {
  it("parses trace steps and the terminal meal from the event stream", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse([
        'event: trace\ndata: {"kind":"check","text":"Checking parmesan","ingredient":null,"compatibility":null}\n\n',
        'event: meal\ndata: {"name":"Courgette salad","meal_type":"lunch","description":"fresh","ingredients":[],"recipe":null,"tags":[],"unverified_ingredients":[],"model":"stub/model","usage":{"calls":3,"input_tokens":100,"output_tokens":20,"total_tokens":120,"steps":[]}}\n\n',
      ]),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useReasoningStream("tok", vi.fn()));
    await result.current.start("lunch");

    await waitFor(() => expect(result.current.status).toBe("done"));
    expect(result.current.events).toHaveLength(1);
    expect(result.current.events[0].text).toBe("Checking parmesan");
    expect(result.current.meal?.name).toBe("Courgette salad");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok");
  });

  it("surfaces a streamed error event", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse(['event: error\ndata: {"detail":"The composer could not finish."}\n\n']),
      ),
    );

    const { result } = renderHook(() => useReasoningStream("tok", vi.fn()));
    await result.current.start("dinner");

    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.error).toBe("The composer could not finish.");
  });

  it("logs the session out on a 401 and lands in a terminal state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));
    const onExpired = vi.fn();

    const { result } = renderHook(() => useReasoningStream("tok", onExpired));
    await result.current.start("snack");

    // The auth path bails to logout, but settles on a terminal status rather than
    // dangling at "streaming", and never surfaces a scary error.
    await waitFor(() => expect(result.current.status).toBe("expired"));
    expect(onExpired).toHaveBeenCalledTimes(1);
    expect(result.current.error).toBeNull();
  });

  it("cancel aborts an in-flight stream and resets to idle", async () => {
    // A body that emits one trace frame then stays open until the request aborts,
    // so the stream is genuinely mid-run when cancel() fires.
    let body: ReadableStreamDefaultController<Uint8Array> | null = null;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        body = controller;
        controller.enqueue(
          new TextEncoder().encode(
            'event: trace\ndata: {"kind":"check","text":"Checking","ingredient":null,"compatibility":null}\n\n',
          ),
        );
      },
    });
    const fetchMock = vi.fn().mockImplementation((_url: string, init: RequestInit) => {
      init.signal?.addEventListener("abort", () => body?.error(new Error("aborted")));
      return Promise.resolve(
        new Response(stream, { status: 200, headers: { "Content-Type": "text/event-stream" } }),
      );
    });
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useReasoningStream("tok", vi.fn()));
    let pending: Promise<void> | undefined;
    act(() => {
      pending = result.current.start("lunch");
    });
    await waitFor(() => expect(result.current.events).toHaveLength(1));
    expect(result.current.status).toBe("streaming");

    act(() => result.current.cancel());
    // Let the aborted run settle so it leaves no dangling promise behind.
    await pending;

    expect(result.current.status).toBe("idle");
    expect(result.current.events).toHaveLength(0);
    expect(result.current.meal).toBeNull();
    expect(result.current.error).toBeNull();
  });

  it("fails when the stream ends without a terminal meal", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([
          'event: trace\ndata: {"kind":"check","text":"Checking parmesan","ingredient":null,"compatibility":null}\n\n',
        ]),
      ),
    );

    const { result } = renderHook(() => useReasoningStream("tok", vi.fn()));
    await result.current.start("lunch");

    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.events).toHaveLength(1);
    expect(result.current.meal).toBeNull();
    expect(result.current.error).toBe("The stream ended before a meal was produced.");
  });

  const preStreamCases: Array<[number, string]> = [
    [409, "A composition is already running. Wait for it to finish."],
    [429, "You've hit the rate limit. Give it a moment, then try again."],
  ];
  it.each(preStreamCases)("maps a %i response to a friendly message", async (status, message) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status })));

    const { result } = renderHook(() => useReasoningStream("tok", vi.fn()));
    await result.current.start("lunch");

    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.error).toBe(message);
  });
});
