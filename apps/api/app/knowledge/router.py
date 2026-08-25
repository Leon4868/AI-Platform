from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends, Path, Request, status

from app.core.http import IdempotencyKey
from app.core.idempotency import IdempotencyScope, request_fingerprint
from app.identity.dependencies import require
from app.identity.schemas import Permission, Principal
from app.knowledge.schemas import (
    KnowledgeBase,
    KnowledgeBaseCreate,
    KnowledgeBaseView,
    KnowledgeDocumentView,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)
from app.knowledge.service import KnowledgeDocumentService, parse_uploaded_file

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge"])


def _documents(request: Request) -> KnowledgeDocumentService:
    container = request.app.state.container
    return KnowledgeDocumentService(
        container.object_storage,
        container.asset_repository,
        container.audit_service,
        container.knowledge_index,
    )


@router.get("", response_model=list[KnowledgeBaseView])
async def list_knowledge_bases(
    principal: Annotated[Principal, Depends(require(Permission.KNOWLEDGE_READ))],
    request: Request,
) -> list[KnowledgeBaseView]:
    items, _ = await request.app.state.container.knowledge_service.list(
        principal.tenant_id, limit=100, offset=0
    )
    return [KnowledgeBaseView.of(item) for item in items]


@router.post("", response_model=KnowledgeBaseView, status_code=status.HTTP_201_CREATED)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    principal: Annotated[Principal, Depends(require(Permission.KNOWLEDGE_WRITE))],
    request: Request,
    idempotency_key: IdempotencyKey,
) -> KnowledgeBaseView:
    container = request.app.state.container
    scope = IdempotencyScope(
        principal.tenant_id,
        principal.user_id,
        "knowledge-base.create",
        idempotency_key,
    )

    async def create() -> KnowledgeBaseView:
        entity = KnowledgeBase.from_create(principal, payload)
        created = await container.knowledge_repository.add(entity)
        await container.audit_service.record(
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            action="knowledge_base.created",
            resource_type="knowledge_base",
            resource_id=created.id,
        )
        return KnowledgeBaseView.of(created)

    return await container.idempotency_store.execute(
        scope,
        request_fingerprint(payload),
        create,
    )


@router.post(
    "/{knowledgeBaseId}/documents",
    response_model=KnowledgeDocumentView,
    response_model_exclude_none=True,
    status_code=status.HTTP_202_ACCEPTED,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            "dataScope": {
                                "type": "string",
                                "enum": ["personal", "project", "department", "enterprise"],
                            },
                            "securityLevel": {
                                "type": "string",
                                "enum": ["internal", "department_sensitive", "confidential"],
                            },
                            "projectId": {"type": "string", "maxLength": 128},
                        },
                    }
                }
            },
        }
    },
)
async def upload_knowledge_document(
    knowledge_base_id: Annotated[UUID, Path(alias="knowledgeBaseId")],
    body: Annotated[bytes, Body(media_type="multipart/form-data")],
    principal: Annotated[Principal, Depends(require(Permission.KNOWLEDGE_WRITE))],
    request: Request,
    idempotency_key: IdempotencyKey,
) -> KnowledgeDocumentView:
    container = request.app.state.container
    knowledge_base = await container.knowledge_service.get(
        principal.tenant_id, knowledge_base_id
    )
    uploaded = parse_uploaded_file(
        request.headers.get("content-type", "application/octet-stream"),
        body,
        request.headers.get("X-Filename"),
    )
    scope = IdempotencyScope(
        principal.tenant_id,
        principal.user_id,
        "knowledge-document.upload",
        idempotency_key,
    )

    async def upload() -> KnowledgeDocumentView:
        return await _documents(request).upload(principal, knowledge_base, uploaded)

    return await container.idempotency_store.execute(
        scope,
        request_fingerprint(
            {
                "knowledgeBaseId": knowledge_base_id,
                "filename": uploaded.filename,
                "contentType": uploaded.content_type,
                "dataScope": uploaded.data_scope,
                "projectId": uploaded.project_id,
                "securityLevel": uploaded.security_level,
                "content": uploaded.content,
            }
        ),
        upload,
    )


@router.post(
    "/{knowledgeBaseId}/search",
    response_model=KnowledgeSearchResponse,
    response_model_exclude_none=True,
)
async def search_knowledge_base(
    knowledge_base_id: Annotated[UUID, Path(alias="knowledgeBaseId")],
    payload: KnowledgeSearchRequest,
    principal: Annotated[Principal, Depends(require(Permission.KNOWLEDGE_READ))],
    request: Request,
) -> KnowledgeSearchResponse:
    knowledge_base = await request.app.state.container.knowledge_service.get(
        principal.tenant_id, knowledge_base_id
    )
    return await _documents(request).search(principal, knowledge_base, payload)
