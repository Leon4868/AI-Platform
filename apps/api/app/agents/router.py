"""HTTP contracts for creating and selecting Agent-owned orchestration drafts."""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from pydantic import Field

from app.agents.schemas import AgentCreate, AgentDefinition
from app.core.errors import AuthorizationError
from app.core.http import IdempotencyKey
from app.core.idempotency import IdempotencyScope, request_fingerprint
from app.core.schemas import ContractModel
from app.identity.dependencies import require
from app.identity.schemas import Permission, Principal

router = APIRouter(prefix="/agents", tags=["agents"])


class AgentCreateContract(ContractModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    owner_department_id: str = Field(min_length=1, max_length=128)


class AgentSummaryContract(ContractModel):
    id: UUID
    name: str
    description: str
    owner_department_id: str
    created_by: UUID
    lifecycle_status: str
    aggregate_revision: int
    has_unpublished_changes: bool
    published_version: int | None
    owned_workflow_draft_id: UUID
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, agent: AgentDefinition) -> "AgentSummaryContract":
        return cls(
            id=agent.id,
            name=agent.name,
            description=agent.description,
            owner_department_id=agent.owner_department_id,
            created_by=agent.created_by,
            lifecycle_status=agent.lifecycle.value,
            aggregate_revision=agent.aggregate_revision,
            has_unpublished_changes=agent.has_unpublished_changes,
            published_version=agent.published_version,
            owned_workflow_draft_id=agent.owned_workflow_draft_id,
            created_at=agent.created_at,
            updated_at=agent.updated_at,
        )


class AgentPageContract(ContractModel):
    items: list[AgentSummaryContract]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class ManageableDepartmentContract(ContractModel):
    id: str
    name: str


class AgentWorkflowDefinitionContract(ContractModel):
    nodes: list[dict]
    edges: list[dict]


class AgentWorkflowDraftContract(ContractModel):
    agent_id: UUID
    workflow_draft_id: UUID
    aggregate_revision: int
    definition: AgentWorkflowDefinitionContract


class AgentWorkflowDraftUpdateContract(ContractModel):
    definition: AgentWorkflowDefinitionContract


@router.get("/manageable-departments", response_model=list[ManageableDepartmentContract])
async def list_manageable_departments(
    principal: Annotated[Principal, Depends(require(Permission.AGENT_WRITE))],
    request: Request,
) -> list[ManageableDepartmentContract]:
    return [
        ManageableDepartmentContract(id=department_id, name=department_id)
        for department_id in sorted(_manageable_department_ids(principal, request))
    ]


@router.post(
    "",
    response_model=AgentSummaryContract,
    response_model_by_alias=True,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent(
    payload: AgentCreateContract,
    principal: Annotated[Principal, Depends(require(Permission.AGENT_WRITE))],
    request: Request,
    response: Response,
    idempotency_key: IdempotencyKey,
) -> AgentSummaryContract:
    if payload.owner_department_id not in _manageable_department_ids(principal, request):
        raise AuthorizationError("The selected owner department is not manageable by this identity")
    scope = IdempotencyScope(
        principal.tenant_id,
        principal.user_id,
        "agent.create",
        idempotency_key,
    )

    async def create() -> AgentDefinition:
        return await request.app.state.container.agent_service.create_draft(
            principal,
            AgentCreate(
                name=payload.name,
                description=payload.description,
                owner_department_id=payload.owner_department_id,
            ),
        )

    created = await request.app.state.container.idempotency_store.execute(
        scope,
        request_fingerprint(payload),
        create,
    )
    response.headers["ETag"] = f'"{created.aggregate_revision}"'
    return AgentSummaryContract.from_domain(created)


@router.get("", response_model=AgentPageContract, response_model_by_alias=True)
async def list_agents(
    principal: Annotated[Principal, Depends(require(Permission.AGENT_READ))],
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AgentPageContract:
    items, total = await request.app.state.container.agent_service.list(
        principal,
        limit=limit,
        offset=offset,
    )
    return AgentPageContract(
        items=[AgentSummaryContract.from_domain(agent) for agent in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{agent_id}/workflow-draft",
    response_model=AgentWorkflowDraftContract,
    response_model_by_alias=True,
)
async def get_agent_workflow_draft(
    agent_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.AGENT_READ))],
    request: Request,
    response: Response,
) -> AgentWorkflowDraftContract:
    agent, workflow = await request.app.state.container.agent_service.get_owned_workflow(
        principal, agent_id
    )
    response.headers["ETag"] = f'"{agent.aggregate_revision}"'
    return AgentWorkflowDraftContract(
        agent_id=agent.id,
        workflow_draft_id=workflow.id,
        aggregate_revision=agent.aggregate_revision,
        definition=workflow.definition,
    )


@router.put(
    "/{agent_id}/workflow-draft",
    response_model=AgentWorkflowDraftContract,
    response_model_by_alias=True,
)
async def save_agent_workflow_draft(
    agent_id: UUID,
    payload: AgentWorkflowDraftUpdateContract,
    principal: Annotated[Principal, Depends(require(Permission.AGENT_WRITE))],
    request: Request,
    response: Response,
    if_match: Annotated[str, Header(alias="If-Match")],
) -> AgentWorkflowDraftContract:
    from app.agents.schemas import WorkflowDraftUpdate

    revision = _parse_etag(if_match)
    updated = await request.app.state.container.agent_service.update_owned_workflow(
        principal,
        agent_id,
        WorkflowDraftUpdate(
            aggregate_revision=revision,
            definition=payload.definition.model_dump(mode="python", by_alias=True),
        ),
    )
    _, workflow = await request.app.state.container.agent_service.get_owned_workflow(
        principal, agent_id
    )
    response.headers["ETag"] = f'"{updated.aggregate_revision}"'
    return AgentWorkflowDraftContract(
        agent_id=updated.id,
        workflow_draft_id=workflow.id,
        aggregate_revision=updated.aggregate_revision,
        definition=workflow.definition,
    )


@router.get("/{agent_id}", response_model=AgentSummaryContract, response_model_by_alias=True)
async def get_agent(
    agent_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.AGENT_READ))],
    request: Request,
    response: Response,
) -> AgentSummaryContract:
    agent = await request.app.state.container.agent_service.get(principal, agent_id)
    response.headers["ETag"] = f'"{agent.aggregate_revision}"'
    return AgentSummaryContract.from_domain(agent)


def _manageable_department_ids(principal: Principal, request: Request) -> frozenset[str]:
    if principal.department_ids:
        return principal.department_ids
    if request.app.state.settings.environment != "production":
        # Local development has no enterprise directory yet; keep this explicit
        # and outside the Principal so permission snapshots stay truthful.
        return frozenset({"dept-platform"})
    return frozenset()


def _parse_etag(value: str) -> int:
    normalized = value.strip()
    if normalized.startswith('W/'):
        normalized = normalized[2:]
    normalized = normalized.strip('"')
    if not normalized.isdigit() or int(normalized) < 1:
        from app.core.errors import ConflictError

        raise ConflictError("If-Match must contain the current positive aggregate revision")
    return int(normalized)
