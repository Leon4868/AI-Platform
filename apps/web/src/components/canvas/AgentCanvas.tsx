import { useCallback, useEffect, useMemo, useRef, type DragEvent } from "react";
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
  type ReactFlowInstance,
} from "@xyflow/react";

import {
  createPaletteNode,
  PALETTE_DRAG_MIME,
  parsePaletteItem,
  type AgentFlowNode,
  type AgentNodeData,
} from "../../lib/workflow";
import type { AgentWorkflowDefinition } from "../../features/agent-center";
import { cn } from "../../lib/cn";
import type { NodeRunStatus } from "../../features/workflow-run/types";
import { AgentNode } from "./AgentNode";

type Props = {
  focusMode: boolean;
  selectedNodeId: string;
  runNodeStatuses?: Record<string, NodeRunStatus>;
  workflowKey: string;
  definition: AgentWorkflowDefinition;
  definitionRevision: number;
  onDefinitionChange: (definition: AgentWorkflowDefinition) => void;
  onSelectNode: (nodeId: string, data: AgentNodeData) => void;
};

export function AgentCanvas({ focusMode, selectedNodeId, runNodeStatuses, workflowKey, definition, definitionRevision, onDefinitionChange, onSelectNode }: Props) {
  const initialNodes = useMemo(
    () => definition.nodes.map((node) => ({ ...node, selected: node.id === selectedNodeId })),
    // Initial state is intentionally seeded once; React Flow owns subsequent selection.
    [],
  );
  const [nodes, setNodes, onNodesChange] = useNodesState<AgentFlowNode>(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(definition.edges);
  const nodeTypes = useMemo(() => ({ agent: AgentNode }), []);
  const flowInstanceRef = useRef<ReactFlowInstance<AgentFlowNode> | null>(null);
  const droppedNodeSequence = useRef(0);
  const resetRef = useRef(`${workflowKey}:${definitionRevision}`);

  useEffect(() => {
    const identity = `${workflowKey}:${definitionRevision}`;
    if (resetRef.current === identity) return;
    resetRef.current = identity;
    setNodes(definition.nodes.map((node) => ({ ...node, selected: node.id === selectedNodeId })));
    setEdges(definition.edges);
  }, [definition.edges, definition.nodes, definitionRevision, selectedNodeId, setEdges, setNodes, workflowKey]);

  useEffect(() => {
    onDefinitionChange({
      nodes: nodes.map((node) => ({
        id: node.id,
        type: "agent",
        position: node.position,
        data: node.data,
      })),
      edges: edges.map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        sourceHandle: edge.sourceHandle,
        targetHandle: edge.targetHandle,
        type: edge.type,
        label: edge.label,
        animated: edge.animated,
        data: edge.data,
        style: edge.style,
        labelStyle: edge.labelStyle,
      })),
    });
  }, [edges, nodes, onDefinitionChange]);

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

  const onDragOver = useCallback((event: DragEvent<HTMLElement>) => {
    if (!Array.from(event.dataTransfer.types).includes(PALETTE_DRAG_MIME)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }, []);

  const onDrop = useCallback((event: DragEvent<HTMLElement>) => {
    const flowInstance = flowInstanceRef.current;
    const item = parsePaletteItem(event.dataTransfer.getData(PALETTE_DRAG_MIME));
    if (!flowInstance || !item) return;

    event.preventDefault();
    droppedNodeSequence.current += 1;
    const position = flowInstance.screenToFlowPosition({ x: event.clientX, y: event.clientY });
    const node = createPaletteNode(item, position, `palette-${Date.now()}-${droppedNodeSequence.current}`);
    setNodes((current) => [
      ...current.map((existing) => ({ ...existing, selected: false })),
      { ...node, selected: true },
    ]);
    onSelectNode(node.id, node.data);
  }, [onSelectNode, setNodes]);

  return (
    <main
      className={cn("absolute inset-0 top-topbar left-rail z-1 transition-[filter] duration-200", focusMode && "focus-canvas")}
      aria-label="Agent 工作流画布"
      onDragOver={onDragOver}
      onDrop={onDrop}
    >
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onInit={(instance) => { flowInstanceRef.current = instance; }}
        onNodeClick={(_, node) => {
          setNodes((current) => current.map((item) => ({ ...item, selected: item.id === node.id })));
          onSelectNode(node.id, node.data);
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
