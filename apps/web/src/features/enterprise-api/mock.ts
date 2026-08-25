import type {
  Asset,
  CreateDocumentTaskInput,
  CreateKnowledgeBaseInput,
  DataScope,
  DocumentTask,
  EnterpriseApi,
  KnowledgeBase,
  KnowledgeDocument,
  KnowledgeSearchResult,
  SecurityLevel,
} from "./types";
import type { AgentSummary, AgentWorkflowDefinition, AgentWorkflowDraft, CreateAgentInput } from "../agent-center/types";

const now = () => new Date().toISOString();
const id = (prefix: string) => `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;

/** Explicit demo transport. It is never selected as an HTTP error fallback. */
export class MockEnterpriseApi implements EnterpriseApi {
  readonly kind = "mock" as const;
  private knowledgeBases: KnowledgeBase[] = [{
    id: "mock-kb-company",
    name: "企业制度库",
    description: "显式 Mock 数据，用于无后端演示",
    ownerDepartmentId: "dept-platform",
    securityLevel: "internal",
    embeddingModelCode: "embedding-main",
    createdAt: now(),
  }];
  private tasks = new Map<string, { task: DocumentTask; reads: number }>();
  private assets = new Map<string, Asset>();
  private agents: AgentSummary[] = [];
  private agentWorkflows = new Map<string, AgentWorkflowDraft>();

  async listAgents(query: { page: number; pageSize: number }) {
    const start = (query.page - 1) * query.pageSize;
    return {
      items: structuredClone(this.agents.slice(start, start + query.pageSize)),
      page: query.page,
      pageSize: query.pageSize,
      total: this.agents.length,
    };
  }

  async listManageableDepartments() {
    return [{ id: "dept-platform", name: "平台研发部" }];
  }

  async createAgent(input: CreateAgentInput) {
    const timestamp = now();
    const created: AgentSummary = {
      id: id("mock-agent"),
      ...input,
      createdBy: "mock-user",
      lifecycleStatus: "active",
      aggregateRevision: 1,
      hasUnpublishedChanges: true,
      publishedVersion: null,
      ownedWorkflowDraftId: id("mock-workflow"),
      createdAt: timestamp,
      updatedAt: timestamp,
    };
    this.agents = [created, ...this.agents];
    this.agentWorkflows.set(created.id, {
      agentId: created.id,
      workflowDraftId: created.ownedWorkflowDraftId,
      aggregateRevision: 1,
      definition: { nodes: [], edges: [] },
    });
    return structuredClone(created);
  }

  async getAgentWorkflowDraft(agentId: string) {
    const draft = this.agentWorkflows.get(agentId);
    if (!draft) throw new Error("Mock Agent 画布不存在");
    return structuredClone(draft);
  }

  async saveAgentWorkflowDraft(agentId: string, aggregateRevision: number, definition: AgentWorkflowDefinition) {
    const current = this.agentWorkflows.get(agentId);
    if (!current || current.aggregateRevision !== aggregateRevision) throw new Error("Mock Agent 画布版本冲突");
    const saved = { ...current, aggregateRevision: aggregateRevision + 1, definition: structuredClone(definition) };
    this.agentWorkflows.set(agentId, saved);
    return structuredClone(saved);
  }

  async listKnowledgeBases() { return structuredClone(this.knowledgeBases); }

  async createKnowledgeBase(input: CreateKnowledgeBaseInput) {
    const created = { ...input, id: id("mock-kb"), createdAt: now() };
    this.knowledgeBases = [created, ...this.knowledgeBases];
    return structuredClone(created);
  }

  async uploadKnowledgeDocument(knowledgeBaseId: string, file: File, _dataScope: DataScope, _securityLevel: SecurityLevel) {
    const document: KnowledgeDocument = {
      id: id("mock-doc"), knowledgeBaseId, assetId: id("mock-asset"), filename: file.name,
      mimeType: file.type || "text/plain", status: "indexed", version: 1, indexedAt: now(),
    };
    return document;
  }

  async searchKnowledge(_knowledgeBaseId: string, query: string): Promise<KnowledgeSearchResult> {
    return {
      traceId: id("mock-trace"),
      citations: query.trim() ? [{
        knowledgeDocumentId: "mock-doc", chunkId: "mock-chunk", assetId: "mock-source-asset",
        quote: `与“${query.trim()}”相关的显式 Mock 知识片段。`, score: 0.92,
      }] : [],
    };
  }

  async createDocumentTask(_input: CreateDocumentTaskInput) {
    const taskId = id("mock-task");
    const task: DocumentTask = {
      taskId, status: "queued", workflowRunId: id("mock-run"), traceId: id("mock-trace"), citations: [], createdAt: now(),
    };
    this.tasks.set(taskId, { task, reads: 0 });
    return structuredClone(task);
  }

  async getDocumentTask(taskId: string) {
    const entry = this.tasks.get(taskId);
    if (!entry) throw new Error("Mock 文档任务不存在");
    entry.reads += 1;
    if (entry.reads === 1) entry.task = { ...entry.task, status: "running" };
    if (entry.reads >= 2) {
      const assetId = `asset-${taskId}`;
      entry.task = { ...entry.task, status: "succeeded", draftAssetId: assetId, finishedAt: now() };
      this.assets.set(assetId, {
        id: assetId, type: "document", name: "Mock 企业文档.md", version: 1, status: "draft",
        mimeType: "text/markdown", storageUri: `memory://download/${assetId}`, creatorId: "mock-user",
        ownerDepartmentId: "dept-platform", dataScope: "department", securityLevel: "internal", lineage: [],
        workflowRunId: entry.task.workflowRunId, traceId: entry.task.traceId, createdAt: now(), updatedAt: now(),
      });
    }
    return structuredClone(entry.task);
  }

  async getAsset(assetId: string) {
    const asset = this.assets.get(assetId);
    if (!asset) throw new Error("Mock 资产不存在");
    return structuredClone(asset);
  }
}
