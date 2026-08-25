import { useCallback, useEffect, useMemo } from "react";
import {
  addEdge,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Connection,
} from "@xyflow/react";

import { workflowEdges, workflowNodes } from "../../lib/workflow";
import { cn } from "../../lib/cn";
import type { NodeRunStatus } from "../../features/workflow-run/types";
import { AgentNode } from "./AgentNode";

type Props = {
  focusMode: boolean;
  selectedNodeId: string;
  runNodeStatuses?: Record<string, NodeRunStatus>;
  onSelectNode: (nodeId: string) => void;
};

export function AgentCanvas({ focusMode, selectedNodeId, runNodeStatuses, onSelectNode }: Props) {
  const initialNodes = useMemo(
    () => workflowNodes.map((node) => ({ ...node, selected: node.id === selectedNodeId })),
    // Initial state is intentionally seeded once; React Flow owns subsequent selection.
    [],
  );
  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(workflowEdges);
  const nodeTypes = useMemo(() => ({ agent: AgentNode }), []);

  useEffect(() => {
    if (!runNodeStatuses) return;
    setNodes((current) => current.map((node) => {
      const runStatus = runNodeStatuses[node.id];
      if (!runStatus) return node;
      const status = runStatus === "running" || runStatus === "waiting_human" ? "active" :
        runStatus === "succeeded" || runStatus === "skipped" ? "succeeded" :
          runStatus === "failed" ? "failed" : runStatus === "cancelled" ? "cancelled" : "idle";
      return { ...node, data: { ...node.data, status } };
    }));
  }, [runNodeStatuses, setNodes]);

  const onConnect = useCallback(
    (connection: Connection) => setEdges((current) => addEdge({ ...connection, animated: true }, current)),
    [setEdges],
  );

  return (
    <main className={cn("absolute inset-0 top-topbar left-rail z-1 transition-[filter] duration-200", focusMode && "focus-canvas")} aria-label="Agent 工作流画布">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onNodeClick={(_, node) => {
          setNodes((current) => current.map((item) => ({ ...item, selected: item.id === node.id })));
          onSelectNode(node.id);
        }}
        fitView
        fitViewOptions={{ padding: 0.16, maxZoom: 0.82 }}
        minZoom={0.32}
        maxZoom={1.4}
        colorMode="dark"
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} color="rgba(113, 179, 224, .16)" />
        <Controls showInteractive={false} position="top-right" />
        <MiniMap
          position="bottom-left"
          pannable
          zoomable
          nodeColor={(node) => node.id === selectedNodeId ? "#56d9ff" : "#31516a"}
          maskColor="rgba(5, 10, 18, .78)"
        />
      </ReactFlow>
    </main>
  );
}
