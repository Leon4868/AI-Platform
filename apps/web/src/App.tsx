import { useCallback, useMemo, useRef, useState } from "react";

import { AgentCanvas } from "./components/canvas/AgentCanvas";
import { MarqueeCommandBar } from "./components/ui/MarqueeCommandBar";
import { OverlayDrawer } from "./components/ui/OverlayDrawer";
import { TraceBar } from "./components/ui/TraceBar";
import { IconRail, type AppSection } from "./components/workspace/IconRail";
import { Inspector } from "./components/workspace/Inspector";
import { NodeLibrary } from "./components/workspace/NodeLibrary";
import { TopBar } from "./components/workspace/TopBar";
import { useWorkspaceState } from "./hooks/useWorkspaceState";
import { workflowNodes, type AgentNodeData } from "./lib/workflow";
import { HttpWorkflowRunTransport } from "./features/workflow-run/api";
import { MockWorkflowRunTransport } from "./features/workflow-run/mockTransport";
import { useWorkflowRun } from "./features/workflow-run/useWorkflowRun";
import { completedStepCount } from "./features/workflow-run/types";
import { createConfiguredEnterpriseApi } from "./features/enterprise-api/config";
import { KnowledgePage } from "./features/knowledge/KnowledgePage";
import { DocumentPage } from "./features/documents/DocumentPage";
import { AssetsPage } from "./features/assets/AssetsPage";
import { GovernancePage } from "./features/governance/GovernancePage";
import { AgentCenterPage, type AgentSummary, type AgentWorkflowDefinition, type AgentWorkflowDraft } from "./features/agent-center";

const nodeLabels = Object.fromEntries(workflowNodes.map((node) => [node.id, node.data.label]));

function createConfiguredTransport() {
  const mode = import.meta.env.VITE_WORKFLOW_TRANSPORT ?? "http";
  if (mode === "http") return new HttpWorkflowRunTransport(import.meta.env.VITE_API_BASE_URL ?? "/api");
  if (mode === "mock") return new MockWorkflowRunTransport();
  throw new Error(`VITE_WORKFLOW_TRANSPORT 必须是 http 或 mock，当前为 ${mode}`);
}

export default function App() {
  const { state, dispatch, commandRef, nodeSearchRef } = useWorkspaceState();
  const [section, setSection] = useState<AppSection>("agent-center");
  const [activeAgent, setActiveAgent] = useState<AgentSummary>();
  const [workflowDraft, setWorkflowDraft] = useState<AgentWorkflowDraft>();
  const [workflowLoading, setWorkflowLoading] = useState(false);
  const [workflowError, setWorkflowError] = useState<string>();
  const [workflowDirty, setWorkflowDirty] = useState(false);
  const [workflowSaving, setWorkflowSaving] = useState(false);
  const workflowLoadRef = useRef<AbortController | undefined>(undefined);
  const activeAgentIdRef = useRef<string | undefined>(undefined);
  const [command, setCommand] = useState("");
  const [canvasNodeData, setCanvasNodeData] = useState<Record<string, AgentNodeData>>({});
  const enterpriseApi = useMemo(createConfiguredEnterpriseApi, []);
  const transport = useMemo(createConfiguredTransport, []);
  const run = useWorkflowRun({ transport, workflowDefinitionId: "", workflowDefinitionVersion: 1 });
  const selectedNodeData = useMemo(
    () => canvasNodeData[state.selectedNodeId],
    [canvasNodeData, state.selectedNodeId],
  );
  const runNodeStatuses = useMemo(
    () => Object.fromEntries(run.snapshot?.nodeRuns.map((node) => [node.nodeId, node.status]) ?? []),
    [run.snapshot],
  );
  const currentNodeLabel = run.currentNode ? (nodeLabels[run.currentNode.nodeId] ?? run.currentNode.nodeId) : "准备运行";
  const totalSteps = run.snapshot?.nodeRuns.length ?? 0;
  const finishedSteps = completedStepCount(run.snapshot);
  const stepLabel = totalSteps > 0 ? `步骤 ${Math.min(finishedSteps + 1, totalSteps)} / ${totalSteps} · ${run.status === "waiting_human" ? "等待人工审批" : "正在处理企业资产"}` : "正在创建运行实例";
  const commandMode = run.error ? "error" : run.isRunning ? "loading" : state.commandMode;
  const runCommand = () => undefined;
  const navigate = (next: AppSection) => {
    if (next !== "agent") {
      workflowLoadRef.current?.abort();
      activeAgentIdRef.current = undefined;
      setActiveAgent(undefined);
      setWorkflowDraft(undefined);
      setWorkflowLoading(false);
      setWorkflowSaving(false);
      setWorkflowDirty(false);
    }
    setSection(next === "agent" && !activeAgent ? "agent-center" : next);
  };
  const openAgent = (agent: AgentSummary) => {
    workflowLoadRef.current?.abort();
    const controller = new AbortController();
    workflowLoadRef.current = controller;
    activeAgentIdRef.current = agent.id;
    setActiveAgent(agent);
    setCanvasNodeData({});
    setWorkflowDraft(undefined);
    setWorkflowError(undefined);
    setWorkflowDirty(false);
    setWorkflowSaving(false);
    setWorkflowLoading(true);
    setSection("agent");
    enterpriseApi.getAgentWorkflowDraft(agent.id, controller.signal)
      .then((draft) => {
        if (activeAgentIdRef.current === agent.id && draft.agentId === agent.id) setWorkflowDraft(draft);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted && activeAgentIdRef.current === agent.id) {
          setWorkflowError(error instanceof Error ? error.message : "Agent 画布加载失败");
        }
      })
      .finally(() => {
        if (activeAgentIdRef.current === agent.id) setWorkflowLoading(false);
      });
  };
  const updateWorkflowDefinition = useCallback((definition: AgentWorkflowDefinition) => {
    setWorkflowDraft((current) => {
      if (!current || JSON.stringify(current.definition) === JSON.stringify(definition)) return current;
      setWorkflowDirty(true);
      return { ...current, definition };
    });
  }, []);
  const saveWorkflow = () => {
    if (!activeAgent || !workflowDraft || !workflowDirty || workflowSaving) return;
    const controller = new AbortController();
    const savingAgentId = activeAgent.id;
    setWorkflowSaving(true);
    setWorkflowError(undefined);
    enterpriseApi.saveAgentWorkflowDraft(
      activeAgent.id,
      workflowDraft.aggregateRevision,
      workflowDraft.definition,
      controller.signal,
    )
      .then((saved) => {
        if (activeAgentIdRef.current !== savingAgentId || saved.agentId !== savingAgentId) return;
        setWorkflowDraft(saved);
        setWorkflowDirty(false);
        setActiveAgent((current) => current ? { ...current, aggregateRevision: saved.aggregateRevision, hasUnpublishedChanges: true } : current);
      })
      .catch((error: unknown) => {
        if (activeAgentIdRef.current === savingAgentId) {
          setWorkflowError(error instanceof Error ? error.message : "Agent 画布保存失败");
        }
      })
      .finally(() => {
        if (activeAgentIdRef.current === savingAgentId) setWorkflowSaving(false);
      });
  };

  return (
    <div className="workspace-grid relative isolate h-full w-full">
      <IconRail active={section} onNavigate={navigate} />
      {section === "agent-center" ? <AgentCenterPage api={enterpriseApi} onOpenAgent={openAgent} /> : null}
      {section === "knowledge" ? <KnowledgePage api={enterpriseApi} /> : null}
      {section === "documents" ? <DocumentPage api={enterpriseApi} /> : null}
      {section === "assets" ? <AssetsPage api={enterpriseApi} /> : null}
      {section === "governance" ? <GovernancePage /> : null}
      {section === "agent" ? <>
      <TopBar
        focusMode={state.focusMode}
        runStatus={run.status}
        transportKind={run.transportKind}
        agentName={activeAgent?.name ?? "未选择 Agent"}
        workflowDefinitionId={activeAgent?.ownedWorkflowDraftId ?? ""}
        canRun={false}
        workflowDirty={workflowDirty}
        workflowSaving={workflowSaving}
        onSaveWorkflow={saveWorkflow}
        onToggleLeft={() => dispatch({ type: "toggle-left" })}
        onToggleRight={() => dispatch({ type: "toggle-right" })}
        onToggleFocus={() => dispatch({ type: "toggle-focus" })}
        onRun={runCommand}
      />
      {workflowDraft ? <AgentCanvas
        focusMode={state.focusMode}
        selectedNodeId={state.selectedNodeId}
        runNodeStatuses={runNodeStatuses}
        workflowKey={workflowDraft.workflowDraftId}
        definition={workflowDraft.definition}
        definitionRevision={workflowDraft.aggregateRevision}
        onDefinitionChange={updateWorkflowDefinition}
        onSelectNode={(nodeId, data) => {
          setCanvasNodeData((current) => ({ ...current, [nodeId]: data }));
          dispatch({ type: "select-node", nodeId });
        }}
      /> : null}
      {workflowLoading ? <div className="absolute inset-0 top-topbar left-rail z-20 grid place-items-center bg-canvas/72 text-sm text-muted">正在加载 Agent 私有画布…</div> : null}
      {workflowError ? <div className="absolute top-[calc(var(--spacing-topbar)+12px)] left-[calc(var(--spacing-rail)+50%)] z-45 -translate-x-1/2 rounded-xl border border-accent-red/25 bg-[#241018]/92 px-4 py-2 text-[10px] text-[#ffd7dc]">{workflowError}</div> : null}
      <OverlayDrawer
        side="left"
        open={state.leftDrawerOpen}
        title="节点与工具"
        eyebrow="BUILDING BLOCKS"
        onClose={() => dispatch({ type: "toggle-left" })}
      >
        <NodeLibrary searchInputRef={nodeSearchRef} />
      </OverlayDrawer>
      <OverlayDrawer
        side="right"
        open={state.rightDrawerOpen}
        title="节点属性"
        eyebrow="INSPECTOR"
        onClose={() => dispatch({ type: "toggle-right" })}
      >
        <Inspector data={selectedNodeData} />
      </OverlayDrawer>
      <TraceBar snapshot={run.snapshot} status={run.status} nodeLabels={nodeLabels} />
      <MarqueeCommandBar
        mode={commandMode}
        inputRef={commandRef}
        value={command}
        currentNodeLabel={currentNodeLabel}
        stepLabel={stepLabel}
        errorMessage={run.error?.message}
        transportKind={run.transportKind}
        runDisabled
        onValueChange={setCommand}
        onModeChange={(mode) => dispatch({ type: "command-mode", mode })}
        onRun={runCommand}
        onStop={() => void run.cancel()}
        onRetry={() => void run.retry()}
      />
      </> : null}
    </div>
  );
}
