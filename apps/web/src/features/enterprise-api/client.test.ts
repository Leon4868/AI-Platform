import { afterEach, describe, expect, it, vi } from "vitest";

import { EnterpriseApiError, HttpEnterpriseApi } from "./client";

const ok = (body: unknown) => ({ ok: true, status: 200, json: async () => body }) as Response;
const problem = (status: number, body: unknown) => ({ ok: false, status, json: async () => body }) as Response;
afterEach(() => vi.unstubAllGlobals());

describe("HttpEnterpriseApi", () => {
  it("adapts Agent Center pagination and uses the Agent create contract", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(ok({ items: [], total: 0, limit: 12, offset: 12 }))
      .mockResolvedValueOnce(ok([{ id: "dept-platform", name: "平台部" }]))
      .mockResolvedValueOnce(ok({ id: "agent-1" }))
      .mockResolvedValueOnce(ok({ agentId: "agent/1", aggregateRevision: 1, definition: { nodes: [], edges: [] } }))
      .mockResolvedValueOnce(ok({ agentId: "agent/1", aggregateRevision: 2, definition: { nodes: [], edges: [] } }));
    vi.stubGlobal("fetch", fetchMock);
    const api = new HttpEnterpriseApi();
    const signal = new AbortController().signal;

    await expect(api.listAgents({ page: 2, pageSize: 12 }, signal)).resolves.toEqual({
      items: [], total: 0, page: 2, pageSize: 12,
    });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/agents?limit=12&offset=12");

    await api.listManageableDepartments(signal);
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/agents/manageable-departments");

    await api.createAgent({ name: "助手", description: "", ownerDepartmentId: "dept-platform" }, signal);
    expect(fetchMock.mock.calls[2][0]).toBe("/api/v1/agents");
    expect(JSON.parse(fetchMock.mock.calls[2][1].body)).toEqual({
      name: "助手", description: "", ownerDepartmentId: "dept-platform",
    });
    expect(fetchMock.mock.calls[2][1].headers["Idempotency-Key"]).toBeTruthy();

    await api.getAgentWorkflowDraft("agent/1", signal);
    expect(fetchMock.mock.calls[3][0]).toBe("/api/v1/agents/agent%2F1/workflow-draft");
    await api.saveAgentWorkflowDraft("agent/1", 1, { nodes: [], edges: [] }, signal);
    expect(fetchMock.mock.calls[4][1].headers["If-Match"]).toBe('"1"');
  });

  it("uses exact knowledge paths and camelCase JSON", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ id: "kb", name: "制度库" }));
    vi.stubGlobal("fetch", fetchMock);
    const api = new HttpEnterpriseApi("/api/");
    await api.createKnowledgeBase({ name: "制度库", description: "", ownerDepartmentId: "dept", securityLevel: "internal", embeddingModelCode: "embed" });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/knowledge-bases");
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ name: "制度库", description: "", ownerDepartmentId: "dept", securityLevel: "internal", embeddingModelCode: "embed" });
    expect(fetchMock.mock.calls[0][1].credentials).toBe("same-origin");
  });

  it("reuses the idempotency key for one automatic transport retry", async () => {
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError("network lost")).mockResolvedValueOnce(ok({ id: "kb" }));
    vi.stubGlobal("fetch", fetchMock);
    const api = new HttpEnterpriseApi();
    await api.createKnowledgeBase({ name: "A", description: "", ownerDepartmentId: "dept", securityLevel: "internal", embeddingModelCode: "embed" });
    const first = fetchMock.mock.calls[0][1].headers["Idempotency-Key"];
    const second = fetchMock.mock.calls[1][1].headers["Idempotency-Key"];
    expect(first).toBe(second);
    expect(first.length).toBeGreaterThanOrEqual(8);
  });

  it("uploads FormData without overriding its content type", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ id: "doc", mimeType: "text/markdown" }));
    vi.stubGlobal("fetch", fetchMock);
    const api = new HttpEnterpriseApi();
    const file = new File(["# 制度"], "员工制度.md", { type: "text/markdown" });
    await api.uploadKnowledgeDocument("kb/1", file, "project", "internal", undefined, "project-apollo");
    const init = fetchMock.mock.calls[0][1];
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/knowledge-bases/kb%2F1/documents");
    expect(init.headers["Content-Type"]).toBeUndefined();
    expect(init.body).toBeInstanceOf(FormData);
    expect((init.body as FormData).get("dataScope")).toBe("project");
    expect((init.body as FormData).get("projectId")).toBe("project-apollo");
  });

  it("parses RFC Problem Details and never falls back to Mock", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(problem(403, { type: "urn:enterprise-ai:error:forbidden", title: "Forbidden", detail: "无权访问" })));
    const api = new HttpEnterpriseApi();
    await expect(api.listKnowledgeBases()).rejects.toEqual(expect.objectContaining<Partial<EnterpriseApiError>>({ code: "forbidden", message: "无权访问", status: 403 }));
    expect(api.kind).toBe("http");
  });

  it("posts document tasks and encodes task and asset ids", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({ taskId: "task" }));
    vi.stubGlobal("fetch", fetchMock);
    const api = new HttpEnterpriseApi();
    await api.createDocumentTask({ title: "周报", workflowDefinitionId: "wf", knowledgeBaseIds: [], logicalModelCode: "model", instructions: "总结", sources: [], outputFormat: "markdown" });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/document-tasks");
    await api.getDocumentTask("task/1");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/document-tasks/task%2F1");
    await api.getAsset("asset/1");
    expect(fetchMock.mock.calls[2][0]).toBe("/api/v1/assets/asset%2F1");
  });
});
