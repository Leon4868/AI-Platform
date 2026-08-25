from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request, status

from app.assets.policy import can_read_resource, security_rank
from app.assets.schemas import Asset, DataScope, SecurityLevel
from app.core.errors import NotFoundError
from app.core.http import IdempotencyKey
from app.core.idempotency import IdempotencyScope, request_fingerprint
from app.documents.schemas import (
    DocumentGenerationCreate,
    DocumentGenerationJob,
    DocumentSourceKind,
    GeneratedDocumentView,
)
from app.identity.dependencies import require
from app.identity.schemas import Permission, Principal
from app.knowledge.schemas import KnowledgeSearchRequest
from app.knowledge.service import KnowledgeDocumentService
from app.runtime.schemas import RunStartRequest

router = APIRouter(prefix="/document-tasks", tags=["documents"])


@router.post(
    "",
    response_model=GeneratedDocumentView,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_document_task(
    payload: DocumentGenerationCreate,
    principal: Annotated[Principal, Depends(require(Permission.DOCUMENT_WRITE))],
    request: Request,
    idempotency_key: IdempotencyKey,
) -> GeneratedDocumentView:
    container = request.app.state.container
    scope = IdempotencyScope(
        principal.tenant_id,
        principal.user_id,
        "document-task.create",
        idempotency_key,
    )

    async def create() -> GeneratedDocumentView:
        workflow_id = _uuid(payload.workflow_definition_id, "workflow_definition")
        await container.workflow_service.get(principal.tenant_id, workflow_id)

        knowledge_bases = []
        citations = []
        referenced_assets: dict[UUID, Asset] = {}
        knowledge_documents = KnowledgeDocumentService(
            container.object_storage,
            container.asset_repository,
            container.audit_service,
            container.knowledge_index,
        )
        query = f"{payload.title}\n{payload.instructions}"
        for raw_id in payload.knowledge_base_ids:
            knowledge_base = await container.knowledge_service.get(
                principal.tenant_id,
                _uuid(raw_id, "knowledge_base"),
            )
            knowledge_bases.append(knowledge_base)
            result = await knowledge_documents.search(
                principal,
                knowledge_base,
                KnowledgeSearchRequest(query=query, top_k=5),
            )
            citations.extend(result.citations)

        if payload.template_asset_id:
            template_asset = await _authorized_asset(
                principal,
                _uuid(payload.template_asset_id, "asset"),
                request,
            )
            referenced_assets[template_asset.id] = template_asset
        for source in payload.sources:
            if source.kind is DocumentSourceKind.ASSET and source.id:
                source_asset = await _authorized_asset(
                    principal,
                    _uuid(source.id, "asset"),
                    request,
                )
                referenced_assets[source_asset.id] = source_asset
            if source.kind is DocumentSourceKind.CITATION and source.id:
                known_chunk_ids = {citation.chunk_id for citation in citations}
                if source.id not in known_chunk_ids:
                    raise NotFoundError("citation", source.id)

        for citation in citations:
            citation_asset = await _authorized_asset(
                principal,
                _uuid(citation.asset_id, "asset"),
                request,
            )
            referenced_assets[citation_asset.id] = citation_asset

        security_levels = [base.security_level for base in knowledge_bases]
        security_levels.extend(asset.security_level for asset in referenced_assets.values())
        output_security = max(
            security_levels or [SecurityLevel.INTERNAL],
            key=security_rank,
        )

        run = await container.workflow_run_service.start(
            principal,
            workflow_id,
            RunStartRequest(
                input={
                    "documentTask": payload.model_dump(mode="json", by_alias=True),
                    "citations": [item.model_dump(mode="json", by_alias=True) for item in citations],
                }
            ),
        )
        entity = DocumentGenerationJob.from_create(principal, payload).model_copy(
            update={
                "owner_department_id": (
                    knowledge_bases[0].owner_department_id
                    if knowledge_bases
                    else f"tenant:{principal.tenant_id}"
                ),
                "workflow_run_id": run.id,
                "trace_id": run.trace_id,
                "citations": citations,
                # Generated output is a private draft until a separate review/publish flow
                # deliberately widens its scope.
                "data_scope": DataScope.PERSONAL,
                "security_level": output_security,
            }
        )
        created = await container.document_repository.add(entity)
        await container.audit_service.record(
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            action="document_generation.queued",
            resource_type="document_generation_job",
            resource_id=created.id,
            metadata={
                "output_format": created.output_format.value,
                "workflow_run_id": str(run.id),
                "trace_id": str(run.trace_id),
                "citation_count": len(citations),
            },
        )
        container.document_task_coordinator.follow(principal, created)
        return GeneratedDocumentView.of(created)

    return await container.idempotency_store.execute(
        scope,
        request_fingerprint(payload),
        create,
    )


@router.get(
    "/{taskId}",
    response_model=GeneratedDocumentView,
    response_model_exclude_none=True,
)
async def get_document_task(
    task_id: Annotated[UUID, Path(alias="taskId")],
    principal: Annotated[Principal, Depends(require(Permission.DOCUMENT_READ))],
    request: Request,
) -> GeneratedDocumentView:
    job = await request.app.state.container.document_task_service.get(
        principal.tenant_id,
        task_id,
    )
    if not can_read_resource(
        principal,
        creator_id=job.requested_by,
        owner_department_id=job.owner_department_id,
        project_id=None,
        data_scope=job.data_scope,
        security_level=job.security_level,
    ):
        raise NotFoundError("document_generation_job", str(task_id))
    return GeneratedDocumentView.of(job)


def _uuid(value: str, resource: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise NotFoundError(resource, value) from exc


async def _authorized_asset(principal: Principal, asset_id: UUID, request: Request) -> Asset:
    asset = await request.app.state.container.asset_service.get(principal.tenant_id, asset_id)
    if not can_read_resource(
        principal,
        creator_id=asset.creator_id,
        owner_department_id=asset.owner_department_id,
        project_id=asset.project_id,
        data_scope=asset.data_scope,
        security_level=asset.security_level,
    ):
        raise NotFoundError("asset", str(asset_id))
    return asset
