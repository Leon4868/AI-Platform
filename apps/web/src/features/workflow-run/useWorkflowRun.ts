import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { normalizeRunError } from "./api";
import type {
  StartWorkflowRunRequest,
  WorkflowRunError,
  WorkflowRunSnapshot,
  WorkflowRunTransport,
  WorkflowRunViewStatus,
} from "./types";
import { activeRunStatuses, currentNodeRun, terminalRunStatuses } from "./types";

type RunState = {
  status: WorkflowRunViewStatus;
  snapshot?: WorkflowRunSnapshot;
  error?: WorkflowRunError;
};

type Options = {
  transport: WorkflowRunTransport;
  workflowDefinitionId: string;
  workflowDefinitionVersion?: number;
};

const initialState: RunState = { status: "idle" };

function snapshotError(snapshot: WorkflowRunSnapshot): WorkflowRunError | undefined {
  if (snapshot.error) return snapshot.error;
  const nodeError = snapshot.nodeRuns.find((node) => node.status === "failed")?.error;
  if (nodeError) return nodeError;
  if (snapshot.status === "failed") {
    return { code: "run_failed", message: "Agent 运行失败，请查看 Trace 后重试", retryable: true };
  }
  return undefined;
}

export function useWorkflowRun({ transport, workflowDefinitionId, workflowDefinitionVersion }: Options) {
  const [state, setState] = useState<RunState>(initialState);
  const lastCommandRef = useRef("");
  const requestSequenceRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const unwatchRef = useRef<(() => void) | null>(null);

  const releaseActiveRequest = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    unwatchRef.current?.();
    unwatchRef.current = null;
  }, []);

  useEffect(() => releaseActiveRequest, [releaseActiveRequest, transport]);

  const start = useCallback(async (command: string) => {
    if (!workflowDefinitionId.trim()) {
      setState({
        status: "failed",
        error: { code: "workflow_id_required", message: "请先填写已保存的 Workflow Definition UUID", retryable: false },
      });
      return;
    }
    const normalizedCommand = command.trim() || "试运行当前企业文档生产流程";
    releaseActiveRequest();
    const sequence = ++requestSequenceRef.current;
    const controller = new AbortController();
    abortRef.current = controller;
    lastCommandRef.current = normalizedCommand;
    setState({ status: "starting" });

    const request: StartWorkflowRunRequest = {
      workflowDefinitionVersion,
      input: { command: normalizedCommand },
    };

    try {
      const snapshot = await transport.start(workflowDefinitionId, request, controller.signal);
      if (sequence !== requestSequenceRef.current) return;
      setState({ status: snapshot.status, snapshot, error: snapshotError(snapshot) });
      if (!terminalRunStatuses.has(snapshot.status)) {
        unwatchRef.current = transport.watch(snapshot.id, snapshot, {
          onSnapshot: (next) => {
            if (sequence !== requestSequenceRef.current) return;
            setState({ status: next.status, snapshot: next, error: snapshotError(next) });
            if (terminalRunStatuses.has(next.status)) {
              unwatchRef.current?.();
              unwatchRef.current = null;
            }
          },
          onError: (error) => {
            if (sequence === requestSequenceRef.current) setState((current) => ({ ...current, status: "failed", error }));
          },
        });
      }
    } catch (error) {
      if (sequence === requestSequenceRef.current) setState({ status: "failed", error: normalizeRunError(error) });
    }
  }, [releaseActiveRequest, transport, workflowDefinitionId, workflowDefinitionVersion]);

  const cancel = useCallback(async () => {
    if (state.status === "starting") {
      requestSequenceRef.current += 1;
      releaseActiveRequest();
      setState({ status: "cancelled" });
      return;
    }
    if (!state.snapshot || !activeRunStatuses.has(state.status)) return;

    const sequence = requestSequenceRef.current;
    setState((current) => ({ ...current, status: "cancelling" }));
    try {
      const snapshot = await transport.cancel(state.snapshot.id, "用户主动停止运行");
      if (sequence === requestSequenceRef.current) {
        unwatchRef.current?.();
        unwatchRef.current = null;
        setState({ status: snapshot.status, snapshot, error: snapshotError(snapshot) });
      }
    } catch (error) {
      if (sequence === requestSequenceRef.current) {
        setState((current) => ({ ...current, status: "failed", error: normalizeRunError(error) }));
      }
    }
  }, [releaseActiveRequest, state.snapshot, state.status, transport]);

  const retry = useCallback(() => start(lastCommandRef.current), [start]);
  const reset = useCallback(() => {
    requestSequenceRef.current += 1;
    releaseActiveRequest();
    setState(initialState);
  }, [releaseActiveRequest]);

  return useMemo(() => ({
    ...state,
    transportKind: transport.kind,
    isRunning: activeRunStatuses.has(state.status),
    currentNode: currentNodeRun(state.snapshot),
    start,
    cancel,
    retry,
    reset,
  }), [cancel, reset, retry, start, state, transport.kind]);
}
