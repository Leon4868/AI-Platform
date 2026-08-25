import type {
  NodeRunStatus,
  RunSubscription,
  StartWorkflowRunRequest,
  WorkflowNodeRun,
  WorkflowRunError,
  WorkflowRunEvent,
  WorkflowRunSnapshot,
  WorkflowRunTransport,
} from "./types";
import { normalizeProblemResponse } from "../../lib/problem-details";

export type SseFrame = { id?: string; event?: string; data: string };

export class SseFrameParser {
  private buffer = "";

  push(chunk: string): SseFrame[] {
    this.buffer += chunk.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
    const frames: SseFrame[] = [];
    let boundary = this.buffer.indexOf("\n\n");
    while (boundary >= 0) {
      const block = this.buffer.slice(0, boundary);
      this.buffer = this.buffer.slice(boundary + 2);
      const frame = parseSseBlock(block);
      if (frame) frames.push(frame);
      boundary = this.buffer.indexOf("\n\n");
    }
    return frames;
  }
}

function parseSseBlock(block: string): SseFrame | undefined {
  let id: string | undefined;
  let event: string | undefined;
  const data: string[] = [];

  for (const line of block.split("\n")) {
    if (!line || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon < 0 ? line : line.slice(0, colon);
    let value = colon < 0 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "id") id = value;
    else if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }

  return data.length > 0 ? { id, event, data: data.join("\n") } : undefined;
}

export class WorkflowTransportError extends Error {
  readonly detail: WorkflowRunError;

  constructor(detail: WorkflowRunError) {
    super(detail.message);
    this.name = "WorkflowTransportError";
    this.detail = detail;
  }
}

export function normalizeRunError(error: unknown): WorkflowRunError {
  if (error instanceof WorkflowTransportError) return error.detail;
  if (error instanceof DOMException && error.name === "AbortError") {
    return { code: "request_cancelled", message: "运行请求已取消", retryable: true };
  }
  return {
    code: "transport_error",
    message: error instanceof Error ? error.message : "运行服务暂时不可用",
    retryable: true,
  };
}

async function readJson<T>(response: Response): Promise<T> {
  if (response.ok) return response.json() as Promise<T>;
  const problem = await normalizeProblemResponse(
    response,
    `运行服务请求失败（${response.status}）`,
  );
  throw new WorkflowTransportError({
    code: problem.code,
    message: problem.message,
    retryable: response.status >= 500 || response.status === 408 || response.status === 429,
  });
}

function idempotencyKey(): string {
  return globalThis.crypto?.randomUUID?.() ?? `web-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function eventError(payload: Record<string, unknown>, fallback: string): WorkflowRunError {
  const raw = payload.error;
  if (typeof raw === "object" && raw !== null) {
    const candidate = raw as Record<string, unknown>;
    return {
      code: typeof candidate.code === "string" ? candidate.code : "run_failed",
      message: typeof candidate.message === "string" ? candidate.message : fallback,
      retryable: true,
    };
  }
  return { code: "run_failed", message: fallback, retryable: true };
}

function updateNode(snapshot: WorkflowRunSnapshot, nodeId: string, status: NodeRunStatus, occurredAt: string, error?: WorkflowRunError) {
  const existing = snapshot.nodeRuns.find((node) => node.nodeId === nodeId);
  const next: WorkflowNodeRun = existing
    ? { ...existing, status, error: error ?? existing.error }
    : { nodeId, attempt: 1, status, error };
  if (status === "running") next.startedAt = next.startedAt ?? occurredAt;
  if (["succeeded", "failed", "skipped", "cancelled"].includes(status)) next.finishedAt = occurredAt;
  return existing
    ? snapshot.nodeRuns.map((node) => node.nodeId === nodeId ? next : node)
    : [...snapshot.nodeRuns, next];
}

export function applyRunEvent(snapshot: WorkflowRunSnapshot, event: WorkflowRunEvent): WorkflowRunSnapshot {
  let next = { ...snapshot, nodeRuns: snapshot.nodeRuns.map((node) => ({ ...node })) };
  const nodeId = event.nodeId;

  switch (event.type) {
    case "run.queued":
      next.status = "queued";
      break;
    case "run.started":
      next.status = "running";
      next.startedAt = next.startedAt ?? event.occurredAt;
      break;
    case "node.started":
      next.status = "running";
      if (nodeId) next.nodeRuns = updateNode(next, nodeId, "running", event.occurredAt);
      break;
    case "node.completed":
      if (next.status === "waiting_human") next.status = "running";
      if (nodeId) next.nodeRuns = updateNode(next, nodeId, "succeeded", event.occurredAt);
      break;
    case "node.failed": {
      const error = eventError(event.payload, "Agent 节点执行失败");
      if (nodeId) next.nodeRuns = updateNode(next, nodeId, "failed", event.occurredAt, error);
      break;
    }
    case "node.cancelled":
      if (nodeId) next.nodeRuns = updateNode(next, nodeId, "cancelled", event.occurredAt);
      break;
    case "run.waiting_human":
      next.status = "waiting_human";
      if (nodeId) next.nodeRuns = updateNode(next, nodeId, "waiting_human", event.occurredAt);
      break;
    case "run.completed":
      next.status = "succeeded";
      next.finishedAt = event.occurredAt;
      break;
    case "run.failed":
      next.status = "failed";
      next.error = eventError(event.payload, "Agent 运行失败");
      next.finishedAt = event.occurredAt;
      break;
    case "run.cancelled":
      next.status = "cancelled";
      next.finishedAt = event.occurredAt;
      next.nodeRuns = next.nodeRuns.map((node) =>
        node.status === "running" || node.status === "waiting_human" || node.status === "pending"
          ? { ...node, status: "cancelled", finishedAt: event.occurredAt }
          : node,
      );
      break;
  }
  return next;
}

const terminalEventTypes = new Set<WorkflowRunEvent["type"]>(["run.completed", "run.failed", "run.cancelled"]);

function decodeEvent(frame: SseFrame, runId: string, lastSequence: number): WorkflowRunEvent | undefined {
  let event: WorkflowRunEvent;
  try {
    event = JSON.parse(frame.data) as WorkflowRunEvent;
  } catch {
    throw new WorkflowTransportError({ code: "invalid_sse_data", message: "运行事件不是有效 JSON", retryable: true });
  }
  if (event.runId !== runId || !Number.isInteger(event.sequence) || event.sequence < 1) {
    throw new WorkflowTransportError({ code: "invalid_sse_event", message: "运行事件标识或序号无效", retryable: true });
  }
  if (frame.id !== String(event.sequence) || (frame.event && frame.event !== event.type)) {
    throw new WorkflowTransportError({ code: "sse_frame_mismatch", message: "SSE 帧与运行事件内容不一致", retryable: true });
  }
  if (event.sequence <= lastSequence) return undefined;
  if (event.sequence !== lastSequence + 1) {
    throw new WorkflowTransportError({ code: "sse_sequence_gap", message: `运行事件序号出现缺口：${lastSequence} → ${event.sequence}`, retryable: true });
  }
  return event;
}

type HttpTransportOptions = { reconnectDelaysMs?: number[] };

export class HttpWorkflowRunTransport implements WorkflowRunTransport {
  readonly kind = "http" as const;
  private readonly baseUrl: string;
  private readonly reconnectDelaysMs: number[];

  constructor(baseUrl = "/api", options: HttpTransportOptions = {}) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.reconnectDelaysMs = options.reconnectDelaysMs ?? [300, 800, 1800];
  }

  async start(workflowDefinitionId: string, request: StartWorkflowRunRequest, signal?: AbortSignal): Promise<WorkflowRunSnapshot> {
    const response = await fetch(`${this.baseUrl}/v1/workflows/${encodeURIComponent(workflowDefinitionId)}/runs`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey() },
      body: JSON.stringify(request),
      signal,
    });
    return readJson<WorkflowRunSnapshot>(response);
  }

  watch(runId: string, initialSnapshot: WorkflowRunSnapshot, subscription: RunSubscription): () => void {
    const controller = new AbortController();
    let disposed = false;
    let snapshot = initialSnapshot;
    let lastSequence = 0;
    let reconnects = 0;

    const pause = (delay: number) => new Promise<void>((resolve, reject) => {
      if (delay === 0) { resolve(); return; }
      const timer = window.setTimeout(resolve, delay);
      controller.signal.addEventListener("abort", () => {
        window.clearTimeout(timer);
        reject(new DOMException("Aborted", "AbortError"));
      }, { once: true });
    });

    const consume = async (): Promise<boolean> => {
      const headers: Record<string, string> = { Accept: "text/event-stream" };
      if (lastSequence > 0) headers["Last-Event-ID"] = String(lastSequence);
      const response = await fetch(`${this.baseUrl}/v1/workflow-runs/${encodeURIComponent(runId)}/events`, {
        credentials: "same-origin",
        headers,
        signal: controller.signal,
      });
      if (!response.ok) return readJson<never>(response);
      if (!response.headers.get("Content-Type")?.includes("text/event-stream") || !response.body) {
        throw new WorkflowTransportError({ code: "invalid_sse_response", message: "运行服务未返回事件流", retryable: true });
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      const parser = new SseFrameParser();
      while (!disposed) {
        const { done, value } = await reader.read();
        if (done) break;
        for (const frame of parser.push(decoder.decode(value, { stream: true }))) {
          const event = decodeEvent(frame, runId, lastSequence);
          if (!event) continue;
          lastSequence = event.sequence;
          snapshot = applyRunEvent(snapshot, event);
          subscription.onSnapshot(snapshot);
          if (terminalEventTypes.has(event.type)) {
            disposed = true;
            await reader.cancel();
            return true;
          }
        }
      }
      return false;
    };

    const connect = async () => {
      while (!disposed) {
        try {
          const terminal = await consume();
          if (terminal || disposed) return;
          throw new WorkflowTransportError({ code: "sse_disconnected", message: "运行事件流已断开", retryable: true });
        } catch (error) {
          if (disposed || controller.signal.aborted) return;
          if (reconnects >= this.reconnectDelaysMs.length) {
            subscription.onError(normalizeRunError(error));
            return;
          }
          const delay = this.reconnectDelaysMs[reconnects++];
          try {
            await pause(delay);
          } catch {
            return;
          }
        }
      }
    };

    void connect();
    return () => {
      disposed = true;
      controller.abort();
    };
  }

  async cancel(runId: string, reason?: string): Promise<WorkflowRunSnapshot> {
    const response = await fetch(`${this.baseUrl}/v1/workflow-runs/${encodeURIComponent(runId)}/cancel`, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "Idempotency-Key": idempotencyKey() },
      body: JSON.stringify(reason ? { reason } : {}),
    });
    return readJson<WorkflowRunSnapshot>(response);
  }
}
