import { afterEach, describe, expect, it, vi } from "vitest";

import { HttpWorkflowRunTransport, SseFrameParser } from "./api";
import type { WorkflowRunEventType, WorkflowRunSnapshot } from "./types";

const responseSnapshot: WorkflowRunSnapshot = {
  id: "run-1",
  workflowDefinitionId: "workflow 1",
  workflowDefinitionVersion: 3,
  status: "queued",
  traceId: "trace-1",
  nodeRuns: [],
  createdAt: "2026-08-24T00:00:00.000Z",
};

function okResponse() {
  return { ok: true, json: async () => responseSnapshot } as Response;
}

function eventFrame(sequence: number, type: WorkflowRunEventType, nodeId?: string) {
  const event = {
    sequence,
    runId: "run-1",
    type,
    occurredAt: `2026-08-24T00:00:0${sequence}.000Z`,
    ...(nodeId ? { nodeId } : {}),
    payload: {},
  };
  return `id: ${sequence}\nevent: ${type}\ndata: ${JSON.stringify(event)}\n\n`;
}

function sseResponse(chunks: string[]) {
  let index = 0;
  const cancel = vi.fn().mockResolvedValue(undefined);
  const reader = {
    read: vi.fn(async () => index < chunks.length
      ? { done: false, value: new TextEncoder().encode(chunks[index++]) }
      : { done: true, value: undefined }),
    cancel,
  };
  return {
    response: {
      ok: true,
      headers: { get: (name: string) => name.toLowerCase() === "content-type" ? "text/event-stream; charset=utf-8" : null },
      body: { getReader: () => reader },
    } as unknown as Response,
    cancel,
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("HttpWorkflowRunTransport", () => {
  it("parses SSE frames incrementally across chunk boundaries", () => {
    const parser = new SseFrameParser();
    expect(parser.push("id: 1\r\nevent: node.started\r\ndata: {\"sequence\":" )).toEqual([]);
    expect(parser.push("1}\r\n\r\n")).toEqual([
      { id: "1", event: "node.started", data: "{\"sequence\":1}" },
    ]);
  });

  it("starts a run on the workflow-scoped endpoint without duplicating the workflow id in the body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(okResponse());
    vi.stubGlobal("fetch", fetchMock);
    const transport = new HttpWorkflowRunTransport("/api/");

    await transport.start("workflow 1", { input: { command: "生成周报" } });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/workflows/workflow%201/runs",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ input: { command: "生成周报" } }),
      }),
    );
  });

  it("sends an optional cancellation reason as JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue(okResponse());
    vi.stubGlobal("fetch", fetchMock);
    const transport = new HttpWorkflowRunTransport();

    await transport.cancel("run/1", "用户停止");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/workflow-runs/run%2F1/cancel",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "Content-Type": "application/json" }),
        body: JSON.stringify({ reason: "用户停止" }),
      }),
    );
  });

  it("reconnects with Last-Event-ID, de-duplicates replayed events, and closes on terminal", async () => {
    const first = sseResponse([eventFrame(1, "run.started")]);
    const second = sseResponse([
      eventFrame(1, "run.started"),
      eventFrame(2, "node.started", "model"),
      eventFrame(3, "run.completed"),
    ]);
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(first.response)
      .mockResolvedValueOnce(second.response);
    vi.stubGlobal("fetch", fetchMock);
    const transport = new HttpWorkflowRunTransport("/api", { reconnectDelaysMs: [0] });
    const onSnapshot = vi.fn();
    const onError = vi.fn();

    transport.watch("run-1", responseSnapshot, { onSnapshot, onError });

    await vi.waitFor(() => expect(onSnapshot).toHaveBeenCalledTimes(3));
    expect(onSnapshot.mock.calls.at(-1)?.[0]).toMatchObject({ status: "succeeded" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][1]).toEqual(expect.objectContaining({
      headers: expect.objectContaining({ "Last-Event-ID": "1" }),
    }));
    expect(second.cancel).toHaveBeenCalledOnce();
    expect(onError).not.toHaveBeenCalled();
  });

  it("projects node.cancelled and run.cancelled into the UI snapshot", async () => {
    const stream = sseResponse([
      eventFrame(1, "node.started", "model") +
      eventFrame(2, "node.cancelled", "model") +
      eventFrame(3, "run.cancelled"),
    ]);
    const fetchMock = vi.fn().mockResolvedValue(stream.response);
    vi.stubGlobal("fetch", fetchMock);
    const transport = new HttpWorkflowRunTransport("/api", { reconnectDelaysMs: [] });
    const onSnapshot = vi.fn();

    transport.watch("run-1", responseSnapshot, { onSnapshot, onError: vi.fn() });

    await vi.waitFor(() => expect(onSnapshot).toHaveBeenCalledTimes(3));
    expect(onSnapshot.mock.calls.at(-1)?.[0]).toMatchObject({
      status: "cancelled",
      nodeRuns: [{ nodeId: "model", status: "cancelled" }],
    });
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(stream.cancel).toHaveBeenCalledOnce();
  });

  it("stops after the bounded reconnect budget and reports the SSE error", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("connection lost"));
    vi.stubGlobal("fetch", fetchMock);
    const transport = new HttpWorkflowRunTransport("/api", { reconnectDelaysMs: [0, 0] });
    const onError = vi.fn();

    transport.watch("run-1", responseSnapshot, { onSnapshot: vi.fn(), onError });

    await vi.waitFor(() => expect(onError).toHaveBeenCalledOnce());
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(onError.mock.calls[0][0]).toMatchObject({ code: "transport_error", retryable: true });
  });
});
