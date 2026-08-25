/**
 * AI Platform cross-service contracts.
 *
 * These types describe transport and persisted snapshots, not framework models.
 * JSON Schema files in ../schemas are the language-neutral validation source.
 */

export type EntityId = string;
export type ISODateTime = string;
export type JsonValue =
  | null
  | boolean
  | number
  | string
  | JsonValue[]
  | { [key: string]: JsonValue };

export const CONTRACT_VERSION = "1.0" as const;
export type ContractVersion = typeof CONTRACT_VERSION;

export interface ContractMeta {
  contractVersion: ContractVersion;
  requestId: string;
  occurredAt: ISODateTime;
}

export type SecurityLevel = "internal" | "department_sensitive" | "confidential";
export type DataScope = "personal" | "project" | "department" | "enterprise";

/** Frozen at request start and re-authorized before retrieval/tool execution. */
export interface PermissionSnapshot {
  subjectId: EntityId;
  departmentIds: EntityId[];
  projectIds: EntityId[];
  roles: string[];
  allowedScopes: DataScope[];
  securityClearance: SecurityLevel;
  capturedAt: ISODateTime;
  policyVersion: string;
}

export interface ApiError {
  code: string;
  message: string;
  retryable: boolean;
  details?: Record<string, JsonValue>;
}

export interface ApiEnvelope<T> {
  meta: ContractMeta;
  data?: T;
  error?: ApiError;
}

export type WorkflowNodeType =
  | "input"
  | "knowledge_search"
  | "prompt"
  | "llm"
  | "document_compose"
  | "human_review"
  | "asset_publish"
  | "output";

export interface WorkflowNodePosition {
  x: number;
  y: number;
}

export interface WorkflowNodeConfigByType {
  input: { inputSchema?: JsonValue };
  knowledge_search: { knowledgeBaseIds?: EntityId[]; topK: number };
  prompt: { promptAssetId: EntityId; promptVersion: number };
  llm: { logicalModelCode: string; temperature?: number; maxOutputTokens?: number };
  document_compose: { templateAssetId?: EntityId; outputFormat: "markdown" | "docx" | "pdf" };
  human_review: { reviewerRole: string };
  asset_publish: { targetStatus: "pending_review" | "published" };
  output: { outputKey?: string };
}

export interface WorkflowNodeBase<T extends WorkflowNodeType> {
  id: EntityId;
  type: T;
  name: string;
  version: number;
  position: WorkflowNodePosition;
  config: WorkflowNodeConfigByType[T];
  timeoutSeconds?: number;
  retry?: { maxAttempts: number; backoffSeconds: number };
}

export type WorkflowNode = {
  [T in WorkflowNodeType]: WorkflowNodeBase<T>;
}[WorkflowNodeType];

export interface WorkflowEdgeCondition {
  kind: "always" | "on_success" | "on_failure" | "json_logic";
  expression?: JsonValue;
}

export interface WorkflowEdge {
  id: EntityId;
  sourceNodeId: EntityId;
  targetNodeId: EntityId;
  sourceHandle?: string;
  targetHandle?: string;
  condition: WorkflowEdgeCondition;
}

export type WorkflowDefinitionStatus = "draft" | "published" | "archived";

export interface WorkflowDefinition {
  id: EntityId;
  name: string;
  description?: string;
  definitionVersion: number;
  status: WorkflowDefinitionStatus;
  entryNodeId: EntityId;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  ownerDepartmentId: EntityId;
  createdBy: EntityId;
  createdAt: ISODateTime;
  updatedAt: ISODateTime;
}

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

/**
 * The workflow id is carried by the URL. Pinning a definition version is
 * optional; when omitted, the server resolves the currently published version
 * once and persists that version on the run snapshot.
 */
export interface StartWorkflowRunRequest {
  workflowDefinitionVersion?: number;
  input: JsonValue;
}

export interface CancelWorkflowRunRequest {
  reason?: string;
}

export interface NodeRun {
  nodeId: EntityId;
  attempt: number;
  status: NodeRunStatus;
  startedAt?: ISODateTime;
  finishedAt?: ISODateTime;
  input?: JsonValue;
  output?: JsonValue;
  error?: ApiError;
}

export interface WorkflowRun {
  id: EntityId;
  workflowDefinitionId: EntityId;
  workflowDefinitionVersion: number;
  status: WorkflowRunStatus;
  initiatedBy: EntityId;
  permissionSnapshot: PermissionSnapshot;
  input: JsonValue;
  output?: JsonValue;
  nodeRuns: NodeRun[];
  traceId: EntityId;
  createdAt: ISODateTime;
  startedAt?: ISODateTime;
  finishedAt?: ISODateTime;
}

export type RunEventType =
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

export interface WorkflowRunEvent {
  /** Strictly increasing within one run and used as the SSE id. */
  sequence: number;
  runId: EntityId;
  type: RunEventType;
  occurredAt: ISODateTime;
  nodeId?: EntityId;
  payload: JsonValue;
}

export interface ModelUsage {
  logicalModelCode: string;
  provider: "openai" | "anthropic" | "google" | "self_hosted";
  resolvedModel: string;
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
  priceVersion: string;
}

export interface TraceSpan {
  spanId: EntityId;
  parentSpanId?: EntityId;
  name: string;
  status: "ok" | "error";
  startedAt: ISODateTime;
  durationMs: number;
  attributes: Record<string, JsonValue>;
  modelUsage?: ModelUsage;
  error?: ApiError;
}

export interface Trace {
  traceId: EntityId;
  requestId: string;
  runId?: EntityId;
  startedAt: ISODateTime;
  durationMs: number;
  spans: TraceSpan[];
}

export type AssetType =
  | "document"
  | "image"
  | "video"
  | "audio"
  | "prompt"
  | "agent"
  | "workflow"
  | "dataset"
  | "report"
  | "code"
  | "other";

export type AssetStatus = "draft" | "pending_review" | "approved" | "published" | "archived";

export interface AssetLineageRef {
  assetId: EntityId;
  version: number;
  relation: "source" | "derived_from" | "generated_by" | "supersedes";
}

export interface Asset {
  id: EntityId;
  type: AssetType;
  name: string;
  description?: string;
  version: number;
  status: AssetStatus;
  mimeType?: string;
  storageUri?: string;
  contentHash?: string;
  creatorId: EntityId;
  ownerDepartmentId: EntityId;
  projectId?: EntityId;
  dataScope: DataScope;
  securityLevel: SecurityLevel;
  lineage: AssetLineageRef[];
  workflowRunId?: EntityId;
  traceId?: EntityId;
  createdAt: ISODateTime;
  updatedAt: ISODateTime;
}

export interface KnowledgeBase {
  id: EntityId;
  name: string;
  description?: string;
  ownerDepartmentId: EntityId;
  securityLevel: SecurityLevel;
  embeddingModelCode: string;
  createdAt: ISODateTime;
}

export type KnowledgeDocumentStatus = "uploaded" | "parsing" | "indexed" | "failed" | "archived";

export interface KnowledgeDocument {
  id: EntityId;
  knowledgeBaseId: EntityId;
  assetId: EntityId;
  filename: string;
  mimeType: string;
  status: KnowledgeDocumentStatus;
  version: number;
  sourceUri?: string;
  indexedAt?: ISODateTime;
}

export interface KnowledgeChunk {
  id: EntityId;
  knowledgeDocumentId: EntityId;
  ordinal: number;
  text: string;
  headingPath: string[];
  page?: number;
  contentHash: string;
  aclTags: string[];
}

export interface Citation {
  knowledgeDocumentId: EntityId;
  chunkId: EntityId;
  assetId: EntityId;
  quote: string;
  page?: number;
  score: number;
}

export interface KnowledgeSearchRequest {
  query: string;
  topK: number;
  filters?: {
    documentStatus?: KnowledgeDocumentStatus;
    documentIds?: EntityId[];
    assetIds?: EntityId[];
    dataScopes?: DataScope[];
    securityLevels?: SecurityLevel[];
    titleContains?: string;
  };
}

export interface KnowledgeSearchResponse {
  citations: Citation[];
  traceId: EntityId;
}

export type DocumentTaskStatus = WorkflowRunStatus;

export interface DocumentSourceRef {
  kind: "asset" | "citation" | "user_input";
  id?: EntityId;
  label: string;
}

export interface DocumentGenerationRequest {
  title: string;
  templateAssetId?: EntityId;
  workflowDefinitionId: EntityId;
  knowledgeBaseIds: EntityId[];
  logicalModelCode: string;
  instructions: string;
  sources: DocumentSourceRef[];
  outputFormat: "markdown" | "docx" | "pdf";
}

export interface GeneratedDocument {
  taskId: EntityId;
  status: DocumentTaskStatus;
  draftAssetId?: EntityId;
  workflowRunId: EntityId;
  traceId: EntityId;
  citations: Citation[];
  error?: { code: string; message: string };
  createdAt: ISODateTime;
  finishedAt?: ISODateTime;
}
