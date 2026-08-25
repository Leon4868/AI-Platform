from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field, model_validator

from app.core.schemas import ApiModel, ContractModel, Entity


class NodeType(StrEnum):
    START = "start"
    END = "end"
    MODEL = "model"
    KNOWLEDGE_RETRIEVAL = "knowledge_retrieval"
    CONDITION = "condition"
    PARALLEL = "parallel"
    TOOL = "tool"
    APPROVAL = "approval"
    ASSET_COMMIT = "asset_commit"


class WorkflowStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class NodePosition(ApiModel):
    x: float = Field(ge=-100_000, le=100_000)
    y: float = Field(ge=-100_000, le=100_000)


class WorkflowNode(ApiModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    type: NodeType
    name: str = Field(min_length=1, max_length=100)
    position: NodePosition
    config: dict[str, Any] = Field(default_factory=dict)


class WorkflowEdge(ApiModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    source: str = Field(min_length=1, max_length=80)
    target: str = Field(min_length=1, max_length=80)
    label: str | None = Field(default=None, max_length=100)


class WorkflowGraph(ApiModel):
    schema_version: str = Field(default="1.0", pattern=r"^1\.[0-9]+$")
    nodes: list[WorkflowNode] = Field(min_length=2, max_length=500)
    edges: list[WorkflowEdge] = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def unique_ids(self) -> "WorkflowGraph":
        node_ids = [node.id for node in self.nodes]
        edge_ids = [edge.id for edge in self.edges]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("node ids must be unique")
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("edge ids must be unique")
        return self


class WorkflowDefinitionCreate(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    graph: WorkflowGraph


class WorkflowDefinitionUpdate(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    graph: WorkflowGraph
    revision: int = Field(ge=1)


class WorkflowDefinition(Entity):
    owner_id: UUID
    name: str
    description: str
    graph: WorkflowGraph
    status: WorkflowStatus = WorkflowStatus.DRAFT
    revision: int = Field(ge=1)


class WorkflowValidationRequest(ApiModel):
    graph: WorkflowGraph


class WorkflowValidationIssue(ApiModel):
    code: str
    message: str
    node_id: str | None = None
    edge_id: str | None = None


class WorkflowValidationResult(ApiModel):
    valid: bool
    errors: list[WorkflowValidationIssue]


class ContractNodeType(StrEnum):
    INPUT = "input"
    KNOWLEDGE_SEARCH = "knowledge_search"
    PROMPT = "prompt"
    LLM = "llm"
    DOCUMENT_COMPOSE = "document_compose"
    HUMAN_REVIEW = "human_review"
    ASSET_PUBLISH = "asset_publish"
    OUTPUT = "output"


class WorkflowRetry(ContractModel):
    max_attempts: int = Field(ge=1, le=5)
    backoff_seconds: int = Field(ge=0, le=300)


class ContractWorkflowNode(ContractModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    type: ContractNodeType
    name: str = Field(min_length=1, max_length=120)
    version: int = Field(ge=1)
    position: NodePosition
    config: dict[str, Any]
    timeout_seconds: int | None = Field(default=None, ge=1, le=3_600)
    retry: WorkflowRetry | None = None


class ContractEdgeConditionKind(StrEnum):
    ALWAYS = "always"
    ON_SUCCESS = "on_success"
    ON_FAILURE = "on_failure"
    JSON_LOGIC = "json_logic"


class ContractEdgeCondition(ContractModel):
    kind: ContractEdgeConditionKind
    expression: Any | None = None


class ContractWorkflowEdge(ContractModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    source_node_id: str = Field(min_length=1, max_length=80)
    target_node_id: str = Field(min_length=1, max_length=80)
    source_handle: str | None = None
    target_handle: str | None = None
    condition: ContractEdgeCondition


class WorkflowDefinitionContract(ContractModel):
    id: UUID
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1_000)
    definition_version: int = Field(ge=1)
    status: WorkflowStatus
    entry_node_id: str
    nodes: list[ContractWorkflowNode] = Field(min_length=2, max_length=500)
    edges: list[ContractWorkflowEdge] = Field(min_length=1, max_length=2_000)
    owner_department_id: str = Field(min_length=1, max_length=128)
    created_by: UUID
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_contract_graph(self) -> "WorkflowDefinitionContract":
        node_ids = {node.id for node in self.nodes}
        if len(node_ids) != len(self.nodes):
            raise ValueError("node ids must be unique")
        if self.entry_node_id not in node_ids:
            raise ValueError("entryNodeId must reference an existing node")
        if sum(node.type is ContractNodeType.INPUT for node in self.nodes) != 1:
            raise ValueError("workflow must contain exactly one input node")
        if not any(node.type is ContractNodeType.OUTPUT for node in self.nodes):
            raise ValueError("workflow must contain at least one output node")
        edge_ids = {edge.id for edge in self.edges}
        if len(edge_ids) != len(self.edges):
            raise ValueError("edge ids must be unique")
        return self
