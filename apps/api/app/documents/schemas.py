from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field

from app.assets.schemas import DataScope, SecurityLevel
from app.core.schemas import ContractModel, Entity
from app.identity.schemas import Principal
from app.knowledge.schemas import CitationView


class DocumentTaskStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DocumentOutputFormat(StrEnum):
    MARKDOWN = "markdown"
    DOCX = "docx"
    PDF = "pdf"


class DocumentSourceKind(StrEnum):
    ASSET = "asset"
    CITATION = "citation"
    USER_INPUT = "user_input"


class DocumentSource(ContractModel):
    kind: DocumentSourceKind
    id: str | None = None
    label: str


class DocumentGenerationCreate(ContractModel):
    title: str = Field(min_length=1, max_length=240)
    template_asset_id: str | None = None
    workflow_definition_id: str = Field(min_length=1, max_length=128)
    knowledge_base_ids: list[str]
    logical_model_code: str = Field(min_length=1, max_length=128)
    instructions: str = Field(min_length=1, max_length=20_000)
    sources: list[DocumentSource]
    output_format: DocumentOutputFormat


class DocumentTaskError(ContractModel):
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=2_000)


class DocumentGenerationJob(Entity):
    requested_by: UUID
    title: str
    template_asset_id: str | None
    workflow_definition_id: str
    knowledge_base_ids: list[str]
    logical_model_code: str
    instructions: str
    sources: list[DocumentSource]
    output_format: DocumentOutputFormat
    owner_department_id: str = Field(min_length=1, max_length=128)
    data_scope: DataScope = DataScope.PERSONAL
    security_level: SecurityLevel = SecurityLevel.INTERNAL
    status: DocumentTaskStatus
    draft_asset_id: UUID | None = None
    workflow_run_id: UUID
    trace_id: UUID
    citations: list[CitationView] = Field(default_factory=list)
    error: DocumentTaskError | None = None
    finished_at: datetime | None = None

    @classmethod
    def from_create(
        cls,
        principal: Principal,
        payload: DocumentGenerationCreate,
    ) -> "DocumentGenerationJob":
        now = datetime.now(UTC)
        return cls(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            requested_by=principal.user_id,
            status=DocumentTaskStatus.QUEUED,
            owner_department_id=f"tenant:{principal.tenant_id}",
            data_scope=DataScope.PERSONAL,
            security_level=SecurityLevel.INTERNAL,
            workflow_run_id=uuid4(),
            trace_id=uuid4(),
            created_at=now,
            updated_at=now,
            **payload.model_dump(),
        )


class GeneratedDocumentView(ContractModel):
    task_id: UUID
    status: DocumentTaskStatus
    draft_asset_id: UUID | None = None
    workflow_run_id: UUID
    trace_id: UUID
    citations: list[CitationView]
    error: DocumentTaskError | None = None
    created_at: datetime
    finished_at: datetime | None = None

    @classmethod
    def of(cls, job: DocumentGenerationJob) -> "GeneratedDocumentView":
        return cls(
            task_id=job.id,
            status=job.status,
            draft_asset_id=job.draft_asset_id,
            workflow_run_id=job.workflow_run_id,
            trace_id=job.trace_id,
            citations=job.citations,
            error=job.error,
            created_at=job.created_at,
            finished_at=job.finished_at,
        )
