import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AgentCenterPage } from "./AgentCenterPage";
import type { AgentCenterApi, AgentListPage, AgentSummary, CreateAgentInput } from "./types";

const draftAgent: AgentSummary = {
  id: "agent-docs",
  name: "企业文档助手",
  description: "检索制度并生成可审计的企业文档。",
  ownerDepartmentId: "dept-platform",
  createdBy: "user-leon",
  lifecycleStatus: "active",
  aggregateRevision: 3,
  hasUnpublishedChanges: true,
  publishedVersion: null,
  ownedWorkflowDraftId: "workflow-draft-docs",
  createdAt: "2026-08-24T08:00:00Z",
  updatedAt: "2026-08-25T09:30:00Z",
};

const publishedAgent: AgentSummary = {
  ...draftAgent,
  id: "agent-review",
  name: "合规审核助手",
  lifecycleStatus: "archived",
  aggregateRevision: 5,
  publishedVersion: 4,
};

function page(items: AgentSummary[]): AgentListPage {
  return { items, page: 1, pageSize: 12, total: items.length };
}

function apiWith(overrides: Partial<AgentCenterApi> = {}): AgentCenterApi {
  return {
    listAgents: vi.fn().mockResolvedValue(page([])),
    listManageableDepartments: vi.fn().mockResolvedValue([{ id: "dept-platform", name: "平台研发部" }]),
    createAgent: vi.fn().mockResolvedValue(draftAgent),
    getAgentWorkflowDraft: vi.fn().mockResolvedValue({
      agentId: draftAgent.id,
      workflowDraftId: draftAgent.ownedWorkflowDraftId,
      aggregateRevision: draftAgent.aggregateRevision,
      definition: { nodes: [], edges: [] },
    }),
    saveAgentWorkflowDraft: vi.fn(),
    ...overrides,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe("AgentCenterPage", () => {
  it("renders API-backed agents with status, owner and update metadata", async () => {
    const onOpenAgent = vi.fn();
    const api = apiWith({ listAgents: vi.fn().mockResolvedValue(page([draftAgent, publishedAgent])) });

    render(<AgentCenterPage api={api} onOpenAgent={onOpenAgent} />);

    expect(await screen.findByText("企业文档助手")).toBeInTheDocument();
    expect(screen.getByText("启用中")).toBeInTheDocument();
    expect(screen.getByText("已归档")).toBeInTheDocument();
    expect(screen.getAllByText("负责人 · user-leon")).toHaveLength(2);
    expect(screen.getByText("尚未发布")).toBeInTheDocument();
    expect(screen.getByText("生产 v4")).toBeInTheDocument();
    expect(screen.getByText("修订 r3")).toBeInTheDocument();
    expect(screen.getByText("修订 r5")).toBeInTheDocument();
    expect(screen.getAllByText("有未发布的草稿修改")).toHaveLength(2);
    expect(screen.getAllByText(/更新于 ·/)).toHaveLength(2);

    const publishedCard = screen.getByRole("button", { name: "打开 Agent：合规审核助手" });
    expect(within(publishedCard).getByText("生产 v4")).toBeInTheDocument();
    expect(within(publishedCard).getByText("有未发布的草稿修改")).toBeInTheDocument();

    fireEvent.click(publishedCard);
    expect(onOpenAgent).toHaveBeenCalledWith(publishedAgent);
    expect(api.listAgents).toHaveBeenCalledWith({ page: 1, pageSize: 12 }, expect.any(AbortSignal));
  });

  it("shows a real empty state without manufacturing agent rows", async () => {
    render(<AgentCenterPage api={apiWith()} onOpenAgent={vi.fn()} />);

    expect(await screen.findByRole("heading", { name: "还没有 Agent" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /打开 Agent/ })).not.toBeInTheDocument();
  });

  it("submits a trimmed draft, exposes loading and opens the created agent", async () => {
    const creation = deferred<AgentSummary>();
    const createAgent = vi.fn((_input: CreateAgentInput, _signal: AbortSignal) => creation.promise);
    const onOpenAgent = vi.fn();
    const api = apiWith({ createAgent });
    render(<AgentCenterPage api={api} onOpenAgent={onOpenAgent} />);
    await screen.findByRole("heading", { name: "还没有 Agent" });

    fireEvent.click(screen.getAllByRole("button", { name: "新建 Agent" })[0]);
    expect(screen.getByRole("dialog", { name: "新建 Agent" })).toBeInTheDocument();
    expect(await screen.findByRole("option", { name: "平台研发部" })).toBeInTheDocument();
    expect(screen.getByLabelText("责任部门").tagName).toBe("SELECT");
    expect(api.listManageableDepartments).toHaveBeenCalledWith(expect.any(AbortSignal));

    fireEvent.change(screen.getByLabelText("Agent 名称"), { target: { value: "  企业文档助手  " } });
    fireEvent.change(screen.getByLabelText("用途说明"), { target: { value: "  生成制度文档  " } });
    fireEvent.change(screen.getByLabelText("责任部门"), { target: { value: "dept-platform" } });
    fireEvent.click(screen.getByRole("button", { name: "创建并进入编排" }));

    expect(createAgent).toHaveBeenCalledWith(
      { name: "企业文档助手", description: "生成制度文档", ownerDepartmentId: "dept-platform" },
      expect.any(AbortSignal),
    );
    expect(screen.getByRole("button", { name: "正在创建…" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "关闭新建 Agent" })).toBeDisabled();

    creation.resolve(draftAgent);
    await waitFor(() => expect(onOpenAgent).toHaveBeenCalledWith(draftAgent));
    expect(screen.queryByRole("dialog", { name: "新建 Agent" })).not.toBeInTheDocument();
  });

  it("keeps the drawer open and reports the API creation error", async () => {
    const api = apiWith({ createAgent: vi.fn().mockRejectedValue(new Error("Agent 名称已存在")) });
    render(<AgentCenterPage api={api} onOpenAgent={vi.fn()} />);
    await screen.findByRole("heading", { name: "还没有 Agent" });

    fireEvent.click(screen.getAllByRole("button", { name: "新建 Agent" })[0]);
    await screen.findByRole("option", { name: "平台研发部" });
    fireEvent.change(screen.getByLabelText("Agent 名称"), { target: { value: "企业文档助手" } });
    fireEvent.change(screen.getByLabelText("责任部门"), { target: { value: "dept-platform" } });
    fireEvent.click(screen.getByRole("button", { name: "创建并进入编排" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Agent 名称已存在");
    expect(screen.getByRole("dialog", { name: "新建 Agent" })).toBeInTheDocument();
  });

  it("blocks creation when manageable departments cannot be loaded and supports retry", async () => {
    const listManageableDepartments = vi.fn()
      .mockRejectedValueOnce(new Error("没有读取部门的权限"))
      .mockResolvedValueOnce([{ id: "dept-platform", name: "平台研发部" }]);
    const api = apiWith({ listManageableDepartments });
    render(<AgentCenterPage api={api} onOpenAgent={vi.fn()} />);
    await screen.findByRole("heading", { name: "还没有 Agent" });

    fireEvent.click(screen.getAllByRole("button", { name: "新建 Agent" })[0]);
    expect(await screen.findByRole("alert")).toHaveTextContent("没有读取部门的权限");
    expect(screen.getByLabelText("责任部门")).toBeDisabled();
    expect(screen.getByRole("button", { name: "创建并进入编排" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "重新加载部门" }));
    expect(await screen.findByRole("option", { name: "平台研发部" })).toBeInTheDocument();
    expect(screen.getByLabelText("责任部门")).toBeEnabled();
    expect(listManageableDepartments).toHaveBeenCalledTimes(2);
  });

  it("reports list failures and retries through the injected API", async () => {
    const listAgents = vi.fn()
      .mockRejectedValueOnce(new Error("Agent 服务暂不可用"))
      .mockResolvedValueOnce(page([draftAgent]));
    render(<AgentCenterPage api={apiWith({ listAgents })} onOpenAgent={vi.fn()} />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Agent 服务暂不可用");
    fireEvent.click(screen.getByRole("button", { name: "重新加载" }));

    expect(await screen.findByText("企业文档助手")).toBeInTheDocument();
    expect(listAgents).toHaveBeenCalledTimes(2);
  });
});
