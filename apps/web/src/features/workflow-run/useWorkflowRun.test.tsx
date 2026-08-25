import { act, renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type {
  RunSubscription,
  StartWorkflowRunRequest,
  WorkflowRunSnapshot,
  WorkflowRunTransport,
} from "./types";
import { useWorkflowRun } from "./useWorkflowRun";

function snapshot(status: WorkflowRunSnapshot["status"] = "queued"): WorkflowRunSnapshot {
  return {
    id: "run-test",
    workflowDefinitionId: "workflow-test",
    workflowDefinitionVersion: 1,
    status,
    traceId: "trace-test",
    createdAt: "2026-08-24T00:00:00.000Z",
    nodeRuns: [
      { nodeId: "identity", attempt: 1, status: status === "running" ? "running" : "pending" },
      { nodeId: "model", attempt: 1, status: "pending" },
    ],
  };
}

class ControlledTransport implements WorkflowRunTransport {
  readonly kind = "mock" as const;
  subscription?: RunSubscription;
  failStart = false;
  startRequests: StartWorkflowRunRequest[] = [];
  workflowIds: string[] = [];
  cancelReasons: Array<string | undefined> = [];

  async start(workflowDefinitionId: string, request: StartWorkflowRunRequest): Promise<WorkflowRunSnapshot> {
    this.workflowIds.push(workflowDefinitionId);
    this.startRequests.push(request);
    if (this.failStart) throw new Error("运行网关不可用");
    return snapshot();
  }

  watch(_runId: string, _initialSnapshot: WorkflowRunSnapshot, subscription: RunSubscription): () => void {
    this.subscription = subscription;
    return () => { this.subscription = undefined; };
  }

  async cancel(_runId: string, reason?: string): Promise<WorkflowRunSnapshot> {
    this.cancelReasons.push(reason);
    return { ...snapshot("cancelled"), finishedAt: "2026-08-24T00:00:01.000Z" };
  }

  emit(next: WorkflowRunSnapshot) {
    this.subscription?.onSnapshot(next);
  }
}

function setup(transport = new ControlledTransport()) {
  const hook = renderHook(() => useWorkflowRun({
    transport,
    workflowDefinitionId: "workflow-test",
    workflowDefinitionVersion: 1,
  }));
  return { transport, ...hook };
}

describe("useWorkflowRun", () => {
  it("starts a run with the command input", async () => {
    const { result, transport } = setup();
    await act(async () => result.current.start("生成企业周报"));
    expect(result.current.status).toBe("queued");
    expect(result.current.isRunning).toBe(true);
    expect(transport.workflowIds[0]).toBe("workflow-test");
    expect(transport.startRequests[0].input.command).toBe("生成企业周报");
  });

  it("consumes progress snapshots and exposes the current node", async () => {
    const { result, transport } = setup();
    await act(async () => result.current.start("运行"));
    act(() => transport.emit(snapshot("running")));
    expect(result.current.status).toBe("running");
    expect(result.current.currentNode?.nodeId).toBe("identity");
  });

  it("cancels an active run", async () => {
    const { result, transport } = setup();
    await act(async () => result.current.start("运行"));
    await act(async () => result.current.cancel());
    expect(result.current.status).toBe("cancelled");
    expect(result.current.isRunning).toBe(false);
    expect(result.current.snapshot?.nodeRuns[0].status).toBe("pending");
    expect(result.current.transportKind).toBe("mock");
    expect(transport.cancelReasons[0]).toBe("用户主动停止运行");
  });

  it("surfaces transport errors without fallback and can retry", async () => {
    const transport = new ControlledTransport();
    transport.failStart = true;
    const { result } = setup(transport);
    await act(async () => result.current.start("运行"));
    expect(result.current.status).toBe("failed");
    expect(result.current.error?.message).toBe("运行网关不可用");

    transport.failStart = false;
    await act(async () => result.current.retry());
    expect(result.current.status).toBe("queued");
    expect(transport.startRequests).toHaveLength(2);
  });
});
