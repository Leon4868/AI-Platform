import type { Asset, CreateDocumentTaskInput, CreateKnowledgeBaseInput, DataScope, DocumentTask, EnterpriseApi, KnowledgeBase, SecurityLevel } from "../features/enterprise-api/types";

export class FakeEnterpriseApi implements EnterpriseApi {
  readonly kind = "http" as const;
  knowledgeBases: KnowledgeBase[] = [{ id: "kb-1", name: "制度库", description: "员工制度", ownerDepartmentId: "dept-1", securityLevel: "internal", embeddingModelCode: "embed-main", createdAt: "2026-08-25T00:00:00Z" }];
  createdKnowledge?: CreateKnowledgeBaseInput;
  uploadedFile?: File;
  searchedQuery?: string;
  createdTask?: CreateDocumentTaskInput;
  taskReads = 0;
  assetReads = 0;

  async listKnowledgeBases() { return structuredClone(this.knowledgeBases); }
  async createKnowledgeBase(input: CreateKnowledgeBaseInput) {
    this.createdKnowledge = input;
    const created = { ...input, id: "kb-created", createdAt: "2026-08-25T00:00:00Z" };
    this.knowledgeBases.unshift(created);
    return created;
  }
  async uploadKnowledgeDocument(knowledgeBaseId: string, file: File, _dataScope: DataScope, _securityLevel: SecurityLevel) {
    this.uploadedFile = file;
    return { id: "doc-1", knowledgeBaseId, assetId: "source-asset", filename: file.name, mimeType: file.type, status: "indexed" as const, version: 1, indexedAt: "2026-08-25T00:00:00Z" };
  }
  async searchKnowledge(_knowledgeBaseId: string, query: string) {
    this.searchedQuery = query;
    return { traceId: "trace-search", citations: [{ knowledgeDocumentId: "doc-1", chunkId: "chunk-1", assetId: "source-asset", quote: "员工年假为十天。", score: 0.93 }] };
  }
  async createDocumentTask(input: CreateDocumentTaskInput) {
    this.createdTask = input;
    return this.task("queued");
  }
  async getDocumentTask() {
    this.taskReads += 1;
    return this.task(this.taskReads === 1 ? "running" : "succeeded", this.taskReads >= 2);
  }
  async getAsset(): Promise<Asset> {
    this.assetReads += 1;
    return { id: "draft-asset", type: "document", name: "产品周报.md", version: 1, status: "draft", mimeType: "text/markdown", storageUri: "memory://download/token", contentHash: "abc", creatorId: "user-1", ownerDepartmentId: "dept-1", dataScope: "department", securityLevel: "internal", lineage: [], workflowRunId: "run-1", traceId: "trace-1", createdAt: "2026-08-25T00:00:00Z", updatedAt: "2026-08-25T00:00:01Z" };
  }
  private task(status: DocumentTask["status"], completed = false): DocumentTask {
    return { taskId: "task-1", status, workflowRunId: "run-1", traceId: "trace-1", citations: [], createdAt: "2026-08-25T00:00:00Z", ...(completed ? { draftAssetId: "draft-asset", finishedAt: "2026-08-25T00:00:01Z" } : {}) };
  }
}
