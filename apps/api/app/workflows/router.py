from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from app.core.http import IdempotencyKey
from app.core.idempotency import IdempotencyScope, request_fingerprint
from app.identity.dependencies import require
from app.identity.schemas import Permission, Principal
from app.workflows.schemas import WorkflowDefinitionContract

router = APIRouter(prefix="/workflow-definitions", tags=["workflows"])


@router.post(
    "",
    response_model=WorkflowDefinitionContract,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
)
async def save_workflow_definition(
    payload: WorkflowDefinitionContract,
    principal: Annotated[Principal, Depends(require(Permission.WORKFLOW_WRITE))],
    request: Request,
    idempotency_key: IdempotencyKey,
) -> WorkflowDefinitionContract:
    container = request.app.state.container
    scope = IdempotencyScope(
        principal.tenant_id,
        principal.user_id,
        "workflow-definition.save",
        idempotency_key,
    )

    async def save() -> WorkflowDefinitionContract:
        return await container.workflow_service.save_contract_definition(principal, payload)

    return await container.idempotency_store.execute(
        scope,
        request_fingerprint(payload),
        save,
    )
