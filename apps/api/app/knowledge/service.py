from dataclasses import replace
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from uuid import uuid4

from app.assets.policy import security_rank
from app.assets.schemas import Asset, DataScope, SecurityLevel
from app.audit.service import AuditService
from app.core.errors import AuthorizationError, DomainError
from app.core.repository import Repository
from app.core.storage import ObjectStorage
from app.identity.schemas import Principal
from app.knowledge.index import (
    DocumentExtractionError,
    InMemoryKnowledgeIndex,
    chunk_text,
    extract_document,
)
from app.knowledge.schemas import (
    CitationView,
    KnowledgeBase,
    KnowledgeDocumentStatus,
    KnowledgeDocumentView,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)
from app.knowledge.service_types import UploadedFile

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def parse_uploaded_file(content_type: str, body: bytes, fallback_filename: str | None = None) -> UploadedFile:
    if len(body) > MAX_UPLOAD_BYTES:
        raise _invalid_upload("Uploaded file exceeds the 50 MiB phase-one limit")

    if content_type.lower().startswith("multipart/form-data"):
        message = BytesParser(policy=policy.default).parsebytes(
            b"MIME-Version: 1.0\r\nContent-Type: "
            + content_type.encode("utf-8")
            + b"\r\n\r\n"
            + body
        )
        file_part = next(
            (
                part
                for part in message.iter_parts()
                if part.get_param("name", header="content-disposition") == "file"
            ),
            None,
        )
        if file_part is None:
            raise _invalid_upload("multipart/form-data body must contain a file field")
        content = file_part.get_payload(decode=True) or b""
        filename = file_part.get_filename() or fallback_filename or "upload.bin"
        mime_type = file_part.get_content_type()
        fields = {
            name: (part.get_payload(decode=True) or b"").decode("utf-8").strip()
            for part in message.iter_parts()
            if (name := part.get_param("name", header="content-disposition")) and name != "file"
        }
        try:
            data_scope = DataScope(fields.get("dataScope", DataScope.DEPARTMENT))
            security_level = SecurityLevel(fields.get("securityLevel", SecurityLevel.INTERNAL))
            project_id = fields.get("projectId") or None
        except ValueError as exc:
            raise _invalid_upload("dataScope or securityLevel is invalid") from exc
    else:
        content = body
        filename = fallback_filename or "upload.bin"
        mime_type = content_type.split(";", 1)[0] or "application/octet-stream"
        data_scope = DataScope.DEPARTMENT
        security_level = SecurityLevel.INTERNAL
        project_id = None

    if not content:
        raise _invalid_upload("Uploaded file must not be empty")
    safe_filename = filename.replace("\\", "/").rsplit("/", 1)[-1].strip()
    if not safe_filename:
        raise _invalid_upload("Uploaded file must have a valid filename")
    return UploadedFile(
        filename=safe_filename[:255],
        content_type=mime_type,
        content=content,
        data_scope=data_scope,
        security_level=security_level,
        project_id=project_id,
    )


class KnowledgeDocumentService:
    def __init__(
        self,
        storage: ObjectStorage,
        asset_repository: Repository[Asset],
        audit_service: AuditService,
        index: InMemoryKnowledgeIndex,
    ) -> None:
        self._storage = storage
        self._assets = asset_repository
        self._audit = audit_service
        self._index = index

    async def upload(
        self,
        principal: Principal,
        knowledge_base: KnowledgeBase,
        uploaded: UploadedFile,
    ) -> KnowledgeDocumentView:
        required_level = max(
            security_rank(knowledge_base.security_level),
            security_rank(uploaded.security_level),
        )
        if security_rank(principal.security_clearance) < required_level:
            raise AuthorizationError(
                "The current identity clearance is below the document security level"
            )
        if uploaded.data_scope is DataScope.PROJECT:
            if uploaded.project_id is None:
                raise _invalid_upload("projectId is required when dataScope is project")
            if uploaded.project_id not in principal.project_ids:
                raise AuthorizationError("The current identity is not a member of the selected project")
        elif uploaded.project_id is not None:
            raise _invalid_upload("projectId is only allowed when dataScope is project")
        if security_rank(uploaded.security_level) < security_rank(knowledge_base.security_level):
            uploaded = replace(uploaded, security_level=knowledge_base.security_level)

        document_id = uuid4()
        object_key = (
            f"tenants/{principal.tenant_id}/knowledge-bases/{knowledge_base.id}/"
            f"documents/{document_id}/v1/source"
        )
        await self._storage.put(object_key, uploaded.content, content_type=uploaded.content_type)
        asset = Asset.from_upload(
            principal,
            name=uploaded.filename,
            mime_type=uploaded.content_type,
            storage_uri=object_key,
            owner_department_id=knowledge_base.owner_department_id,
            data_scope=uploaded.data_scope,
            security_level=uploaded.security_level,
            project_id=uploaded.project_id,
        )
        asset = await self._assets.add(asset)
        await self._audit.record(
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            action="knowledge_document.uploaded",
            resource_type="knowledge_document",
            resource_id=document_id,
            metadata={"knowledge_base_id": str(knowledge_base.id), "asset_id": str(asset.id)},
        )
        try:
            extracted = extract_document(uploaded)
            chunks = chunk_text(extracted.text)
        except DocumentExtractionError as exc:
            await self._audit.record(
                tenant_id=principal.tenant_id,
                actor_id=principal.user_id,
                action="knowledge_document.index_failed",
                resource_type="knowledge_document",
                resource_id=document_id,
                metadata={"code": exc.code, "mime_type": uploaded.content_type},
            )
            return KnowledgeDocumentView(
                id=document_id,
                knowledge_base_id=knowledge_base.id,
                asset_id=asset.id,
                filename=uploaded.filename,
                mime_type=uploaded.content_type,
                status=KnowledgeDocumentStatus.FAILED,
                version=1,
            )

        await self._index.replace_document(
            tenant_id=principal.tenant_id,
            knowledge_base_id=knowledge_base.id,
            document_id=document_id,
            asset_id=asset.id,
            creator_id=asset.creator_id,
            owner_department_id=asset.owner_department_id,
            project_id=asset.project_id,
            data_scope=asset.data_scope,
            security_level=asset.security_level,
            title=extracted.title,
            chunks=chunks,
        )
        indexed_at = datetime.now(UTC)
        await self._audit.record(
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            action="knowledge_document.indexed",
            resource_type="knowledge_document",
            resource_id=document_id,
            metadata={"chunk_count": len(chunks)},
        )
        return KnowledgeDocumentView(
            id=document_id,
            knowledge_base_id=knowledge_base.id,
            asset_id=asset.id,
            filename=uploaded.filename,
            mime_type=uploaded.content_type,
            status=KnowledgeDocumentStatus.INDEXED,
            version=1,
            indexed_at=indexed_at,
        )

    async def search(
        self,
        principal: Principal,
        knowledge_base: KnowledgeBase,
        payload: KnowledgeSearchRequest,
    ) -> KnowledgeSearchResponse:
        matches = await self._index.search(
            tenant_id=principal.tenant_id,
            knowledge_base_id=knowledge_base.id,
            query=payload.query,
            top_k=payload.top_k,
            subject_id=principal.user_id,
            department_ids=principal.department_ids,
            project_ids=principal.project_ids,
            security_clearance=principal.security_clearance,
            filters=payload.filters,
        )
        return KnowledgeSearchResponse(
            citations=[
                CitationView(
                    knowledge_document_id=str(item.chunk.document_id),
                    chunk_id=str(item.chunk.chunk_id),
                    asset_id=str(item.chunk.asset_id),
                    quote=item.chunk.text,
                    score=item.score,
                )
                for item in matches
            ],
            trace_id=uuid4(),
        )


def _invalid_upload(detail: str) -> DomainError:
    return DomainError(
        title="Invalid knowledge document",
        detail=detail,
        status_code=422,
        error_code="knowledge_document_invalid",
    )
