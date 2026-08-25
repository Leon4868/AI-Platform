export type WorkflowRunStatus =
  | "queued"
  | "running"
  | "waiting_human"
  | "succeeded"
  | "failed"
  | "cancelled";

export type NodeRunStatus =
  | "pending"
  | "running"
  | "waiting_human"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "skipped";

export type WorkflowRunError = {
  code: string;
  message: string;
  retryable: boolean;
};

export type WorkflowNodeRun = {
  nodeId: string;
  attempt: number;
  status: NodeRunStatus;
  startedAt?: string;
  finishedAt?: string;
  error?: WorkflowRunError;
};

export type WorkflowRunSnapshot = {
  id: string;
  workflowDefinitionId: string;
  workflowDefinitionVersion: number;
  status: WorkflowRunStatus;
  traceId: string;
  nodeRuns: WorkflowNodeRun[];
  createdAt: string;
  startedAt?: string;
  finishedAt?: string;
  error?: WorkflowRunError;
};

export type StartWorkflowRunRequest = {
  workflowDefinitionVersion?: number;
  input: { command: string };
};

export type RunSubscription = {
  onSnapshot: (snapshot: WorkflowRunSnapshot) => void;
  onError: (error: WorkflowRunError) => void;
};

export type WorkflowRunEventType =
  | "run.queued"
  | "run.started"
  | "node.started"
  | "node.completed"
  | "node.failed"
  | "node.cancelled"
  | "run.waiting_human"
  | "run.completed"
  | "run.failed"
  | "run.cancelled";

export type WorkflowRunEvent = {
  sequence: number;
  runId: string;
  type: WorkflowRunEventType;
  occurredAt: string;
  nodeId?: string;
  payload: Record<string, unknown>;
};

export interface WorkflowRunTransport {
  readonly kind: "http" | "mock";
  start(workflowDefinitionId: string, request: StartWorkflowRunRequest, signal?: AbortSignal): Promise<WorkflowRunSnapshot>;
  watch(runId: string, initialSnapshot: WorkflowRunSnapshot, subscription: RunSubscription): () => void;
  cancel(runId: string, reason?: string): Promise<WorkflowRunSnapshot>;
}

export type WorkflowRunViewStatus = "idle" | "starting" | WorkflowRunStatus | "cancelling";

export const activeRunStatuses = new Set<WorkflowRunViewStatus>([
  "starting",
  "queued",
  "running",
  "waiting_human",
  "cancelling",
]);

export const terminalRunStatuses = new Set<WorkflowRunStatus>(["succeeded", "failed", "cancelled"]);

export function currentNodeRun(snapshot?: WorkflowRunSnapshot): WorkflowNodeRun | undefined {
  return snapshot?.nodeRuns.find((node) => node.status === "running" || node.status === "waiting_human");
}

export function completedStepCount(snapshot?: WorkflowRunSnapshot): number {
  return snapshot?.nodeRuns.filter((node) => node.status === "succeeded" || node.status === "skipped").length ?? 0;
}
