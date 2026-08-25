export type SecurityLevel = "internal" | "department_sensitive" | "confidential";
export type DataScope = "personal" | "project" | "department" | "enterprise";

export type KnowledgeBase = {
  id: string;
  name: string;
  description?: string;
  ownerDepartmentId: string;
  securityLevel: SecurityLevel;
  embeddingModelCode: string;
  createdAt: string;
};

export type CreateKnowledgeBaseInput = Omit<KnowledgeBase, "id" | "createdAt">;

export type KnowledgeDocument = {
  id: string;
  knowledgeBaseId: string;
  assetId: string;
  filename: string;
  mimeType: string;
  status: "uploaded" | "parsing" | "indexed" | "failed" | "archived";
  version: number;
  sourceUri?: string;
  indexedAt?: string;
};

export type Citation = {
  knowledgeDocumentId: string;
  chunkId: string;
  assetId: string;
  quote: string;
  page?: number;
  score: number;
};

export type KnowledgeSearchResult = { citations: Citation[]; traceId: string };

export type DocumentTaskStatus = "queued" | "running" | "waiting_human" | "succeeded" | "failed" | "cancelled";

export type DocumentTask = {
  taskId: string;
  status: DocumentTaskStatus;
  draftAssetId?: string;
  workflowRunId: string;
  traceId: string;
  citations: Citation[];
  error?: { code: string; message: string };
  createdAt: string;
  finishedAt?: string;
};

export type CreateDocumentTaskInput = {
  title: string;
  templateAssetId?: string;
  workflowDefinitionId: string;
  knowledgeBaseIds: string[];
  logicalModelCode: string;
  instructions: string;
  sources: Array<{ kind: "asset" | "citation" | "user_input"; id?: string; label: string }>;
  outputFormat: "markdown" | "docx" | "pdf";
};

export type Asset = {
  id: string;
  type: "document" | "image" | "video" | "audio" | "prompt" | "agent" | "workflow" | "dataset" | "report" | "code" | "other";
  name: string;
  description?: string;
  version: number;
  status: "draft" | "pending_review" | "approved" | "published" | "archived";
  mimeType?: string;
  storageUri?: string;
  contentHash?: string;
  creatorId: string;
  ownerDepartmentId: string;
  projectId?: string;
  dataScope: DataScope;
  securityLevel: SecurityLevel;
  lineage: Array<{ assetId: string; version: number; relation: "source" | "derived_from" | "generated_by" | "supersedes" }>;
  workflowRunId?: string;
  traceId?: string;
  createdAt: string;
  updatedAt: string;
};

export interface EnterpriseApi {
  readonly kind: "http" | "mock";
  listKnowledgeBases(signal?: AbortSignal): Promise<KnowledgeBase[]>;
  createKnowledgeBase(input: CreateKnowledgeBaseInput, signal?: AbortSignal): Promise<KnowledgeBase>;
  uploadKnowledgeDocument(knowledgeBaseId: string, file: File, dataScope: DataScope, securityLevel: SecurityLevel, signal?: AbortSignal, projectId?: string): Promise<KnowledgeDocument>;
  searchKnowledge(knowledgeBaseId: string, query: string, topK: number, signal?: AbortSignal): Promise<KnowledgeSearchResult>;
  createDocumentTask(input: CreateDocumentTaskInput, signal?: AbortSignal): Promise<DocumentTask>;
  getDocumentTask(taskId: string, signal?: AbortSignal): Promise<DocumentTask>;
  getAsset(assetId: string, signal?: AbortSignal): Promise<Asset>;
}
