import { useMemo, useState } from "react";

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

const nodeLabels = Object.fromEntries(workflowNodes.map((node) => [node.id, node.data.label]));

function createConfiguredTransport() {
  const mode = import.meta.env.VITE_WORKFLOW_TRANSPORT ?? "http";
  if (mode === "http") return new HttpWorkflowRunTransport(import.meta.env.VITE_API_BASE_URL ?? "/api");
  if (mode === "mock") return new MockWorkflowRunTransport();
  throw new Error(`VITE_WORKFLOW_TRANSPORT 必须是 http 或 mock，当前为 ${mode}`);
}

export default function App() {
  const { state, dispatch, commandRef, nodeSearchRef } = useWorkspaceState();
  const [section, setSection] = useState<AppSection>("agent");
  const [command, setCommand] = useState("");
  const [canvasNodeData, setCanvasNodeData] = useState<Record<string, AgentNodeData>>({});
  const [workflowDefinitionId, setWorkflowDefinitionId] = useState(import.meta.env.VITE_DEFAULT_WORKFLOW_ID ?? "");
  const enterpriseApi = useMemo(createConfiguredEnterpriseApi, []);
  const transport = useMemo(createConfiguredTransport, []);
  const run = useWorkflowRun({ transport, workflowDefinitionId, workflowDefinitionVersion: 1 });
  const selectedNodeData = useMemo(
    () => canvasNodeData[state.selectedNodeId] ?? workflowNodes.find((node) => node.id === state.selectedNodeId)?.data,
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
  const runCommand = () => void run.start(command);

  return (
    <div className="workspace-grid relative isolate h-full w-full">
      <IconRail active={section} onNavigate={setSection} />
      {section === "knowledge" ? <KnowledgePage api={enterpriseApi} /> : null}
      {section === "documents" ? <DocumentPage api={enterpriseApi} /> : null}
      {section === "assets" ? <AssetsPage api={enterpriseApi} /> : null}
      {section === "agent" ? <>
      <TopBar
        focusMode={state.focusMode}
        runStatus={run.status}
        transportKind={run.transportKind}
        workflowDefinitionId={workflowDefinitionId}
        onWorkflowDefinitionIdChange={setWorkflowDefinitionId}
        onToggleLeft={() => dispatch({ type: "toggle-left" })}
        onToggleRight={() => dispatch({ type: "toggle-right" })}
        onToggleFocus={() => dispatch({ type: "toggle-focus" })}
        onRun={runCommand}
      />
      <AgentCanvas
        focusMode={state.focusMode}
        selectedNodeId={state.selectedNodeId}
        runNodeStatuses={runNodeStatuses}
        onSelectNode={(nodeId, data) => {
          setCanvasNodeData((current) => ({ ...current, [nodeId]: data }));
          dispatch({ type: "select-node", nodeId });
        }}
      />
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
