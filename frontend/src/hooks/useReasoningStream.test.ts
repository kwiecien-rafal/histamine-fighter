import { renderHook, waitFor } from "@testing-library/react";
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
  vi.restoreAllMocks();
});

describe("useReasoningStream", () => {
  it("parses trace steps and the terminal meal from the event stream", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      sseResponse([
        'event: trace\ndata: {"kind":"check","text":"Checking parmesan","ingredient":null,"compatibility":null}\n\n',
        'event: meal\ndata: {"name":"Courgette salad","meal_type":"lunch","description":"fresh","ingredients":[],"recipe":null,"tags":[],"reasoning_trace":[],"model":"stub/model"}\n\n',
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

  it("logs the session out on a 401", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));
    const onExpired = vi.fn();

    const { result } = renderHook(() => useReasoningStream("tok", onExpired));
    await result.current.start("snack");

    await waitFor(() => expect(onExpired).toHaveBeenCalledTimes(1));
    expect(result.current.status).toBe("streaming");
  });
});
