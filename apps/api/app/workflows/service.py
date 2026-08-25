from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.audit.service import AuditService
from app.core.errors import AuthorizationError, ConflictError, DefinitionValidationError, NotFoundError
from app.core.repository import Repository
from app.identity.schemas import Principal
from app.workflows.schemas import (
    ContractNodeType,
    ContractWorkflowNode,
    NodePosition,
    NodeType,
    WorkflowDefinition,
    WorkflowDefinitionContract,
    WorkflowDefinitionCreate,
    WorkflowDefinitionUpdate,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
    WorkflowValidationResult,
)
from app.workflows.validator import WorkflowGraphValidator


class WorkflowService:
    def __init__(
        self,
        repository: Repository[WorkflowDefinition],
        validator: WorkflowGraphValidator,
        audit_service: AuditService,
    ) -> None:
        self._repository = repository
        self._validator = validator
        self._audit = audit_service

    def validate(self, graph: WorkflowGraph) -> WorkflowValidationResult:
        return self._validator.validate(graph)

    async def save_contract_definition(
        self,
        principal: Principal,
        payload: WorkflowDefinitionContract,
    ) -> WorkflowDefinitionContract:
        if payload.created_by != principal.user_id:
            raise AuthorizationError("createdBy must match the authenticated principal")
        if await self._repository.get(principal.tenant_id, payload.id) is not None:
            raise ConflictError(f"Workflow definition '{payload.id}' already exists")

        graph = _contract_graph(payload)
        result = self.validate(graph)
        if not result.valid:
            raise DefinitionValidationError([issue.model_dump(exclude_none=True) for issue in result.errors])
        entity = WorkflowDefinition(
            id=payload.id,
            tenant_id=principal.tenant_id,
            owner_id=principal.user_id,
            name=payload.name,
            description=payload.description or "",
            graph=graph,
            status=payload.status,
            revision=payload.definition_version,
            created_at=payload.created_at,
            updated_at=payload.updated_at,
        )
        await self._repository.add(entity)
        await self._audit.record(
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            action="workflow_definition.saved",
            resource_type="workflow_definition",
            resource_id=entity.id,
            metadata={"definition_version": entity.revision},
        )
        return payload

    async def create(self, principal: Principal, payload: WorkflowDefinitionCreate) -> WorkflowDefinition:
        result = self.validate(payload.graph)
        if not result.valid:
            raise DefinitionValidationError([issue.model_dump(exclude_none=True) for issue in result.errors])
        now = datetime.now(UTC)
        workflow = WorkflowDefinition(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            owner_id=principal.user_id,
            name=payload.name,
            description=payload.description,
            graph=payload.graph,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        created = await self._repository.add(workflow)
        await self._audit.record(
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            action="workflow.created",
            resource_type="workflow_definition",
            resource_id=created.id,
            metadata={"revision": created.revision},
        )
        return created

    async def get(self, tenant_id: UUID, workflow_id: UUID) -> WorkflowDefinition:
        workflow = await self._repository.get(tenant_id, workflow_id)
        if workflow is None:
            raise NotFoundError("workflow_definition", str(workflow_id))
        return workflow

    async def list(self, tenant_id: UUID, *, limit: int, offset: int) -> tuple[list[WorkflowDefinition], int]:
        return await self._repository.list(tenant_id, limit=limit, offset=offset)

    async def update(
        self,
        principal: Principal,
        workflow_id: UUID,
        payload: WorkflowDefinitionUpdate,
    ) -> WorkflowDefinition:
        current = await self.get(principal.tenant_id, workflow_id)
        if current.revision != payload.revision:
            raise ConflictError(
                f"Workflow revision changed: expected {payload.revision}, current {current.revision}"
            )
        result = self.validate(payload.graph)
        if not result.valid:
            raise DefinitionValidationError([issue.model_dump(exclude_none=True) for issue in result.errors])
        updated = current.model_copy(
            update={
                "name": payload.name,
                "description": payload.description,
                "graph": payload.graph,
                "revision": current.revision + 1,
                "updated_at": datetime.now(UTC),
            }
        )
        await self._repository.update(updated)
        await self._audit.record(
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            action="workflow.updated",
            resource_type="workflow_definition",
            resource_id=updated.id,
            metadata={"revision": updated.revision},
        )
        return updated

    async def delete(self, principal: Principal, workflow_id: UUID) -> None:
        if not await self._repository.delete(principal.tenant_id, workflow_id):
            raise NotFoundError("workflow_definition", str(workflow_id))
        await self._audit.record(
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            action="workflow.deleted",
            resource_type="workflow_definition",
            resource_id=workflow_id,
        )


_NODE_TYPE_MAP = {
    ContractNodeType.INPUT: NodeType.START,
    ContractNodeType.KNOWLEDGE_SEARCH: NodeType.KNOWLEDGE_RETRIEVAL,
    ContractNodeType.PROMPT: NodeType.TOOL,
    ContractNodeType.LLM: NodeType.MODEL,
    ContractNodeType.DOCUMENT_COMPOSE: NodeType.TOOL,
    ContractNodeType.HUMAN_REVIEW: NodeType.APPROVAL,
    ContractNodeType.ASSET_PUBLISH: NodeType.ASSET_COMMIT,
    ContractNodeType.OUTPUT: NodeType.END,
}


def _contract_graph(payload: WorkflowDefinitionContract) -> WorkflowGraph:
    return WorkflowGraph(
        schema_version="1.0",
        nodes=[_contract_node(node) for node in payload.nodes],
        edges=[
            WorkflowEdge(
                id=edge.id,
                source=edge.source_node_id,
                target=edge.target_node_id,
            )
            for edge in payload.edges
        ],
    )


def _contract_node(node: ContractWorkflowNode) -> WorkflowNode:
    config = dict(node.config)
    if node.type is ContractNodeType.KNOWLEDGE_SEARCH:
        config = {
            "knowledge_base_ids": config.get("knowledgeBaseIds", []),
            "top_k": config.get("topK", 5),
        }
    elif node.type is ContractNodeType.LLM:
        config = {
            "model": config.get("logicalModelCode", "unconfigured"),
            "temperature": config.get("temperature"),
            "max_output_tokens": config.get("maxOutputTokens"),
        }
    elif node.type is ContractNodeType.HUMAN_REVIEW:
        config = {
            "prompt": node.name,
            "approvers": [config.get("reviewerRole", "reviewer")],
        }
    elif node.type in {ContractNodeType.PROMPT, ContractNodeType.DOCUMENT_COMPOSE}:
        config = {"tool": node.type.value, "arguments": config}

    return WorkflowNode(
        id=node.id,
        type=_NODE_TYPE_MAP[node.type],
        name=node.name,
        position=NodePosition(x=node.position.x, y=node.position.y),
        config=config,
    )
