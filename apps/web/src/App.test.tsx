import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

const harness = vi.hoisted(() => ({
  getWorkflow: vi.fn(),
  saveWorkflow: vi.fn(),
}));

vi.mock("./features/enterprise-api/config", () => ({
  createConfiguredEnterpriseApi: () => ({
    kind: "http",
    listAgents: vi.fn(),
    listManageableDepartments: vi.fn(),
    createAgent: vi.fn(),
    getAgentWorkflowDraft: harness.getWorkflow,
    saveAgentWorkflowDraft: harness.saveWorkflow,
  }),
}));

vi.mock("./features/agent-center", () => ({
  AgentCenterPage: ({ onOpenAgent }: { onOpenAgent: (agent: Record<string, unknown>) => void }) => (
    <div>
      <button type="button" onClick={() => onOpenAgent(agent("a"))}>打开 A</button>
      <button type="button" onClick={() => onOpenAgent(agent("b"))}>打开 B</button>
    </div>
  ),
}));

vi.mock("./components/canvas/AgentCanvas", () => ({
  AgentCanvas: ({ workflowKey, onDefinitionChange }: { workflowKey: string; onDefinitionChange: (value: unknown) => void }) => (
    <div>
      <div data-testid="canvas-key">{workflowKey}</div>
      <button type="button" onClick={() => onDefinitionChange({ nodes: [{ id: "changed" }], edges: [] })}>修改画布</button>
    </div>
  ),
}));

vi.mock("./features/workflow-run/useWorkflowRun", () => ({
  useWorkflowRun: () => ({
    status: "idle",
    transportKind: "http",
    snapshot: undefined,
    currentNode: undefined,
    error: undefined,
    isRunning: false,
    start: vi.fn(),
    cancel: vi.fn(),
    retry: vi.fn(),
  }),
}));

import App from "./App";

function agent(id: string) {
  return {
    id,
    name: `Agent ${id}`,
    description: "",
    ownerDepartmentId: "dept-platform",
    createdBy: "user",
    lifecycleStatus: "active",
    aggregateRevision: 1,
    hasUnpublishedChanges: true,
    publishedVersion: null,
    ownedWorkflowDraftId: `workflow-${id}`,
    createdAt: "2026-08-25T00:00:00Z",
    updatedAt: "2026-08-25T00:00:00Z",
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

describe("App Agent workflow binding", () => {
  it("ignores a late workflow response from the previously opened Agent", async () => {
    const a = deferred<unknown>();
    const b = deferred<unknown>();
    harness.getWorkflow.mockImplementation((id: string) => id === "a" ? a.promise : b.promise);

    render(<App />);
    fireEvent.click(screen.getByRole("button", { name: "打开 A" }));
    fireEvent.click(screen.getByRole("button", { name: "Agent 中心" }));
    fireEvent.click(screen.getByRole("button", { name: "Agent 编排" }));
    expect(screen.getByRole("button", { name: "打开 B" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "打开 B" }));

    await act(async () => b.resolve({
      agentId: "b",
      workflowDraftId: "workflow-b",
      aggregateRevision: 1,
      definition: { nodes: [], edges: [] },
    }));
    expect(await screen.findByTestId("canvas-key")).toHaveTextContent("workflow-b");

    await act(async () => a.resolve({
      agentId: "a",
      workflowDraftId: "workflow-a",
      aggregateRevision: 1,
      definition: { nodes: [], edges: [] },
    }));
    expect(screen.getByTestId("canvas-key")).toHaveTextContent("workflow-b");
  });
});
