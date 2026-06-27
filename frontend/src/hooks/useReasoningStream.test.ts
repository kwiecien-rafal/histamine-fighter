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

// The preview request shape the hook now takes; the endpoint and body are parametrized.
function preview(mealType: string) {
  return { endpoint: "/admin/compose/preview", body: { meal_type: mealType } };
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

    const { result } = renderHook(() => useReasoningStream(vi.fn()));
    await result.current.start(preview("lunch"));

    await waitFor(() => expect(result.current.status).toBe("done"));
    expect(result.current.events).toHaveLength(1);
    expect(result.current.events[0].text).toBe("Checking parmesan");
    expect(result.current.meal?.name).toBe("Courgette salad");

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("POST");
    // The session rides in the httpOnly cookie, sent because credentials are included;
    // no bearer token is read in JS.
    expect(init.credentials).toBe("include");
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
  });

  it("surfaces a streamed error event", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse(['event: error\ndata: {"detail":"The composer could not finish."}\n\n']),
      ),
    );

    const { result } = renderHook(() => useReasoningStream(vi.fn()));
    await result.current.start(preview("dinner"));

    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.error).toBe("The composer could not finish.");
  });

  it("logs the session out on a 401 and lands in a terminal state", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 401 })));
    const onExpired = vi.fn();

    const { result } = renderHook(() => useReasoningStream(onExpired));
    await result.current.start(preview("snack"));

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

    const { result } = renderHook(() => useReasoningStream(vi.fn()));
    let pending: Promise<void> | undefined;
    act(() => {
      pending = result.current.start(preview("lunch"));
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

    const { result } = renderHook(() => useReasoningStream(vi.fn()));
    await result.current.start(preview("lunch"));

    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.events).toHaveLength(1);
    expect(result.current.meal).toBeNull();
    expect(result.current.error).toBe("The stream ended before a meal was produced.");
  });

  it("parses a final frame the server left unterminated by a blank line", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([
          'event: trace\ndata: {"kind":"check","text":"Checking parmesan","ingredient":null,"compatibility":null}\n\n',
          // No trailing "\n\n": the meal sits in the buffer the read loop never re-splits.
          'event: meal\ndata: {"name":"Courgette salad","meal_type":"lunch","description":"fresh","ingredients":[],"recipe":null,"tags":[],"unverified_ingredients":[],"model":"stub/model","usage":{"calls":3,"input_tokens":100,"output_tokens":20,"total_tokens":120,"steps":[]}}',
        ]),
      ),
    );

    const { result } = renderHook(() => useReasoningStream(vi.fn()));
    await result.current.start(preview("lunch"));

    await waitFor(() => expect(result.current.status).toBe("done"));
    expect(result.current.meal?.name).toBe("Courgette salad");
  });

  const preStreamCases: Array<[number, string]> = [
    [409, "A composition is already running. Wait for it to finish."],
    [429, "You've hit the rate limit. Give it a moment, then try again."],
  ];
  it.each(preStreamCases)("maps a %i response to a friendly message", async (status, message) => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status })));

    const { result } = renderHook(() => useReasoningStream(vi.fn()));
    await result.current.start(preview("lunch"));

    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.error).toBe(message);
  });

  it("fills savedId from a terminal saved frame", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        sseResponse([
          'event: meal\ndata: {"name":"Saved dish","meal_type":"lunch","description":"x","ingredients":[],"recipe":null,"tags":[],"unverified_ingredients":[],"model":"stub/model","usage":{"calls":1,"input_tokens":1,"output_tokens":1,"total_tokens":2,"steps":[]}}\n\n',
          'event: saved\ndata: {"id":"meal-123"}\n\n',
        ]),
      ),
    );

    const { result } = renderHook(() => useReasoningStream(vi.fn()));
    await result.current.start({
      endpoint: "/admin/compose/curated",
      body: { meal_type: "lunch" },
    });

    await waitFor(() => expect(result.current.status).toBe("done"));
    expect(result.current.savedId).toBe("meal-123");
  });

  it("surfaces a structured 409 as a slot conflict, not a generic error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: {
              message: "That daily slot already holds a suggestion.",
              conflict: { date: "2026-06-25", meal_type: "lunch", existing_status: "approved" },
            },
          }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    const { result } = renderHook(() => useReasoningStream(vi.fn()));
    await result.current.start({
      endpoint: "/admin/compose/daily",
      body: { meal_type: "lunch", date: "2026-06-25" },
    });

    await waitFor(() => expect(result.current.status).toBe("conflict"));
    expect(result.current.conflict?.existing_status).toBe("approved");
    expect(result.current.error).toBeNull();
  });

  it("re-runs with replace after a slot conflict and streams to saved", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            detail: {
              message: "That daily slot already holds a suggestion.",
              conflict: { date: "2026-06-25", meal_type: "lunch", existing_status: "approved" },
            },
          }),
          { status: 409, headers: { "Content-Type": "application/json" } },
        ),
      )
      .mockResolvedValueOnce(
        sseResponse([
          'event: meal\ndata: {"name":"Replacement","meal_type":"lunch","description":"x","ingredients":[],"recipe":null,"tags":[],"unverified_ingredients":[],"model":"stub/model","usage":{"calls":1,"input_tokens":1,"output_tokens":1,"total_tokens":2,"steps":[]}}\n\n',
          'event: saved\ndata: {"id":"daily-9"}\n\n',
        ]),
      );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useReasoningStream(vi.fn()));
    await result.current.start({
      endpoint: "/admin/compose/daily",
      body: { meal_type: "lunch", date: "2026-06-25" },
    });
    await waitFor(() => expect(result.current.status).toBe("conflict"));

    // The operator confirms the overwrite: the same slot re-runs with replace and saves.
    await result.current.start({
      endpoint: "/admin/compose/daily",
      body: { meal_type: "lunch", date: "2026-06-25", replace: true },
    });

    await waitFor(() => expect(result.current.status).toBe("done"));
    expect(result.current.savedId).toBe("daily-9");
    expect(result.current.conflict).toBeNull();
    const [, retry] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(JSON.parse(retry.body as string)).toMatchObject({ replace: true });
  });

  it("surfaces a 422 detail as the error, not a bare status", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({ detail: "date must be between 2026-06-25 and 2026-07-09." }),
          { status: 422, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    const { result } = renderHook(() => useReasoningStream(vi.fn()));
    await result.current.start({
      endpoint: "/admin/compose/daily",
      body: { meal_type: "lunch", date: "2026-09-01" },
    });

    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.error).toBe("date must be between 2026-06-25 and 2026-07-09.");
  });
});
