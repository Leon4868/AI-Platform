export type AgentLifecycleStatus = "active" | "archived";

export type AgentSummary = {
  id: string;
  name: string;
  description: string;
  ownerDepartmentId: string;
  createdBy: string;
  lifecycleStatus: AgentLifecycleStatus;
  aggregateRevision: number;
  hasUnpublishedChanges: boolean;
  publishedVersion: number | null;
  ownedWorkflowDraftId: string;
  createdAt: string;
  updatedAt: string;
};

export type ManageableDepartment = {
  id: string;
  name: string;
};

export type CreateAgentInput = {
  name: string;
  description: string;
  ownerDepartmentId: string;
};

export type AgentListQuery = {
  page: number;
  pageSize: number;
};

export type AgentListPage = {
  items: AgentSummary[];
  page: number;
  pageSize: number;
  total: number;
};

export type AgentWorkflowDefinition = {
  nodes: AgentFlowNode[];
  edges: Edge[];
};

export type AgentWorkflowDraft = {
  agentId: string;
  workflowDraftId: string;
  aggregateRevision: number;
  definition: AgentWorkflowDefinition;
};

/**
 * UI-facing Agent API boundary. The host application owns authentication,
 * transport and Problem Details conversion; this feature never falls back to
 * local sample data.
 */
export type AgentCenterApi = {
  listAgents(query: AgentListQuery, signal: AbortSignal): Promise<AgentListPage>;
  listManageableDepartments(signal: AbortSignal): Promise<ManageableDepartment[]>;
  createAgent(input: CreateAgentInput, signal: AbortSignal): Promise<AgentSummary>;
  getAgentWorkflowDraft(agentId: string, signal: AbortSignal): Promise<AgentWorkflowDraft>;
  saveAgentWorkflowDraft(
    agentId: string,
    aggregateRevision: number,
    definition: AgentWorkflowDefinition,
    signal: AbortSignal,
  ): Promise<AgentWorkflowDraft>;
};
import type { Edge } from "@xyflow/react";
import type { AgentFlowNode } from "../../lib/workflow";
