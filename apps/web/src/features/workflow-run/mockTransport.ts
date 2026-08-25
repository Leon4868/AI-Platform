import type {
  RunSubscription,
  StartWorkflowRunRequest,
  WorkflowNodeRun,
  WorkflowRunSnapshot,
  WorkflowRunTransport,
} from "./types";

const defaultNodeIds = ["identity", "intent", "knowledge", "prompt", "model", "safety", "approval", "document", "asset", "notify"];

type MockOptions = {
  stepDelayMs?: number;
  nodeIds?: string[];
  failAtStep?: number;
  failOnStart?: boolean;
};

type Session = {
  snapshot: WorkflowRunSnapshot;
  listeners: Set<RunSubscription>;
  timer?: number;
  nextStep: number;
};

function clone(snapshot: WorkflowRunSnapshot): WorkflowRunSnapshot {
  return structuredClone(snapshot);
}

/** Explicit development transport. It is never selected as an HTTP failure fallback. */
export class MockWorkflowRunTransport implements WorkflowRunTransport {
  readonly kind = "mock" as const;
  private readonly delay: number;
  private readonly nodeIds: string[];
  private readonly failAtStep?: number;
  private readonly failOnStart: boolean;
  private readonly sessions = new Map<string, Session>();

  constructor(options: MockOptions = {}) {
    this.delay = options.stepDelayMs ?? 520;
    this.nodeIds = options.nodeIds ?? defaultNodeIds;
    this.failAtStep = options.failAtStep;
    this.failOnStart = options.failOnStart ?? false;
  }

  async start(workflowDefinitionId: string, request: StartWorkflowRunRequest, signal?: AbortSignal): Promise<WorkflowRunSnapshot> {
    if (signal?.aborted) throw new DOMException("Aborted", "AbortError");
    if (this.failOnStart) throw new Error("Mock 运行器启动失败");

    const now = new Date().toISOString();
    const id = `mock-run-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
    const nodeRuns: WorkflowNodeRun[] = this.nodeIds.map((nodeId) => ({ nodeId, attempt: 1, status: "pending" }));
    const session: Session = {
      snapshot: {
        id,
        workflowDefinitionId,
        workflowDefinitionVersion: request.workflowDefinitionVersion ?? 1,
        status: "queued",
        traceId: `mock-trace-${id.slice(-6)}`,
        nodeRuns,
        createdAt: now,
      },
      listeners: new Set(),
      nextStep: 0,
    };
    this.sessions.set(id, session);
    session.timer = window.setTimeout(() => this.advance(session), this.delay);
    return clone(session.snapshot);
  }

  watch(runId: string, _initialSnapshot: WorkflowRunSnapshot, subscription: RunSubscription): () => void {
    const session = this.sessions.get(runId);
    if (!session) {
      queueMicrotask(() => subscription.onError({ code: "mock_run_not_found", message: "Mock 运行不存在", retryable: false }));
      return () => undefined;
    }
    session.listeners.add(subscription);
    return () => session.listeners.delete(subscription);
  }

  async cancel(runId: string, _reason?: string): Promise<WorkflowRunSnapshot> {
    const session = this.sessions.get(runId);
    if (!session) throw new Error("Mock 运行不存在");
    if (session.timer !== undefined) window.clearTimeout(session.timer);
    session.snapshot = {
      ...session.snapshot,
      status: "cancelled",
      finishedAt: new Date().toISOString(),
      nodeRuns: session.snapshot.nodeRuns.map((node) =>
        node.status === "pending" || node.status === "running" || node.status === "waiting_human"
          ? { ...node, status: "cancelled", finishedAt: new Date().toISOString() }
          : node,
      ),
    };
    this.emit(session);
    return clone(session.snapshot);
  }

  private advance(session: Session) {
    if (session.snapshot.status === "cancelled") return;
    const index = session.nextStep;
    const now = new Date().toISOString();

    if (index > 0) {
      session.snapshot.nodeRuns[index - 1] = {
        ...session.snapshot.nodeRuns[index - 1],
        status: "succeeded",
        finishedAt: now,
      };
    }

    if (index >= session.snapshot.nodeRuns.length) {
      session.snapshot = { ...session.snapshot, status: "succeeded", finishedAt: now };
      this.emit(session);
      return;
    }

    if (this.failAtStep === index) {
      const error = { code: "mock_node_failed", message: "Mock 节点执行失败", retryable: true };
      session.snapshot.nodeRuns[index] = { ...session.snapshot.nodeRuns[index], status: "failed", error, finishedAt: now };
      session.snapshot = { ...session.snapshot, status: "failed", error, finishedAt: now };
      this.emit(session);
      return;
    }

    session.snapshot = { ...session.snapshot, status: "running", startedAt: session.snapshot.startedAt ?? now };
    session.snapshot.nodeRuns[index] = { ...session.snapshot.nodeRuns[index], status: "running", startedAt: now };
    session.nextStep += 1;
    this.emit(session);
    session.timer = window.setTimeout(() => this.advance(session), this.delay);
  }

  private emit(session: Session) {
    const snapshot = clone(session.snapshot);
    session.listeners.forEach((listener) => listener.onSnapshot(snapshot));
  }
}
