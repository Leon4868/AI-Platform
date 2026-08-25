import type { Edge, Node, XYPosition } from "@xyflow/react";

export type NodeTone = "cyan" | "blue" | "violet" | "green" | "amber";

export type AgentNodeData = Record<string, unknown> & {
  label: string;
  subtitle: string;
  category: string;
  tone: NodeTone;
  icon: string;
  status: "ready" | "active" | "idle" | "succeeded" | "failed" | "cancelled";
  model?: string;
  latency?: string;
};

export type AgentFlowNode = Node<AgentNodeData, "agent">;

export const workflowNodes: AgentFlowNode[] = [
  { id: "trigger", type: "agent", position: { x: 40, y: 300 }, data: { label: "员工请求", subtitle: "文档生成入口", category: "触发器", tone: "cyan", icon: "spark", status: "ready", latency: "12ms" } },
  { id: "identity", type: "agent", position: { x: 300, y: 150 }, data: { label: "身份与权限", subtitle: "RBAC / 数据范围", category: "控制", tone: "blue", icon: "shield", status: "ready", latency: "18ms" } },
  { id: "intent", type: "agent", position: { x: 300, y: 450 }, data: { label: "意图分类", subtitle: "识别任务与模板", category: "Agent", tone: "violet", icon: "route", status: "active", model: "enterprise-chat-fast", latency: "380ms" } },
  { id: "knowledge", type: "agent", position: { x: 590, y: 80 }, data: { label: "知识检索", subtitle: "ACL 过滤后召回", category: "知识库", tone: "cyan", icon: "search", status: "ready", model: "embedding-main", latency: "96ms" } },
  { id: "parser", type: "agent", position: { x: 590, y: 300 }, data: { label: "文档解析", subtitle: "结构与元数据提取", category: "工具", tone: "blue", icon: "file", status: "ready", latency: "140ms" } },
  { id: "fusion", type: "agent", position: { x: 590, y: 520 }, data: { label: "上下文融合", subtitle: "去重、排序与引用", category: "处理器", tone: "violet", icon: "layers", status: "idle", model: "rerank-main" } },
  { id: "prompt", type: "agent", position: { x: 890, y: 190 }, data: { label: "Prompt 编排", subtitle: "模板 v1.8 · 变量检查", category: "编排", tone: "violet", icon: "braces", status: "ready", latency: "22ms" } },
  { id: "model", type: "agent", position: { x: 890, y: 430 }, data: { label: "模型网关", subtitle: "路由、限流与降级", category: "模型", tone: "cyan", icon: "cpu", status: "active", model: "enterprise-chat-main", latency: "1.8s" } },
  { id: "safety", type: "agent", position: { x: 1190, y: 90 }, data: { label: "内容安全", subtitle: "敏感信息与合规", category: "控制", tone: "amber", icon: "scan", status: "ready", latency: "84ms" } },
  { id: "approval", type: "agent", position: { x: 1190, y: 300 }, data: { label: "人工审核", subtitle: "发布前确认", category: "人工节点", tone: "amber", icon: "usercheck", status: "idle" } },
  { id: "document", type: "agent", position: { x: 1190, y: 520 }, data: { label: "文档生成", subtitle: "DOCX / Markdown", category: "输出", tone: "green", icon: "pen", status: "idle", model: "document-writer" } },
  { id: "asset", type: "agent", position: { x: 1490, y: 190 }, data: { label: "企业资产归档", subtitle: "版本、血缘与责任人", category: "资产", tone: "green", icon: "archive", status: "idle" } },
  { id: "notify", type: "agent", position: { x: 1490, y: 430 }, data: { label: "结果通知", subtitle: "工作台 / 企业微信", category: "结束", tone: "blue", icon: "send", status: "idle" } },
];

const edge = (source: string, target: string, label?: string): Edge => ({
  id: `${source}-${target}`,
  source,
  target,
  label,
  type: "smoothstep",
  animated: source === "intent" || source === "model",
  style: { stroke: "rgba(95, 211, 255, .58)", strokeWidth: 1.6 },
  labelStyle: { fill: "#8fa9bd", fontSize: 10 },
});

export const workflowEdges: Edge[] = [
  edge("trigger", "identity"), edge("trigger", "intent"),
  edge("identity", "knowledge", "允许"), edge("identity", "parser"),
  edge("intent", "parser"), edge("intent", "fusion"),
  edge("knowledge", "prompt"), edge("parser", "prompt"), edge("fusion", "model"),
  edge("prompt", "model"), edge("model", "safety"), edge("model", "document"),
  edge("safety", "approval", "通过"), edge("approval", "document"),
  edge("document", "asset"), edge("asset", "notify"),
];

export const paletteItems = [
  { label: "触发器", icon: "spark", tone: "cyan" },
  { label: "知识检索", icon: "search", tone: "cyan" },
  { label: "大模型", icon: "cpu", tone: "violet" },
  { label: "条件判断", icon: "route", tone: "amber" },
  { label: "人工审核", icon: "usercheck", tone: "amber" },
  { label: "资产归档", icon: "archive", tone: "green" },
] as const;

export type PaletteItem = (typeof paletteItems)[number];

export const PALETTE_DRAG_MIME = "application/x-ai-platform-node";

export function serializePaletteItem(item: PaletteItem): string {
  return JSON.stringify({ label: item.label });
}

export function parsePaletteItem(value: string): PaletteItem | null {
  if (!value) return null;
  try {
    const payload = JSON.parse(value) as { label?: unknown };
    return paletteItems.find((item) => item.label === payload.label) ?? null;
  } catch {
    return null;
  }
}

export function createPaletteNode(item: PaletteItem, position: XYPosition, id: string): AgentFlowNode {
  return {
    id,
    type: "agent",
    position,
    data: {
      label: item.label,
      subtitle: "待配置",
      category: item.label,
      tone: item.tone,
      icon: item.icon,
      status: "idle",
    },
  };
}

export function isWorkflowConnected(nodes: AgentFlowNode[], edges: Edge[]): boolean {
  if (nodes.length === 0) return true;
  const adjacency = new Map<string, string[]>();
  edges.forEach(({ source, target }) => adjacency.set(source, [...(adjacency.get(source) ?? []), target]));
  const visited = new Set<string>();
  const queue = [nodes[0].id];
  while (queue.length) {
    const current = queue.shift();
    if (!current || visited.has(current)) continue;
    visited.add(current);
    queue.push(...(adjacency.get(current) ?? []));
  }
  return nodes.every(({ id }) => visited.has(id));
}
