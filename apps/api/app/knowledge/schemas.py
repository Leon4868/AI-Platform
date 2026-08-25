from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field

from app.assets.schemas import DataScope, SecurityLevel
from app.core.schemas import ContractModel, Entity
from app.identity.schemas import Principal


class KnowledgeBaseCreate(ContractModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1_000)
    owner_department_id: str = Field(min_length=1, max_length=128)
    security_level: SecurityLevel
    embedding_model_code: str = Field(min_length=1, max_length=128)


class KnowledgeBase(Entity):
    owner_id: UUID
    name: str
    description: str
    owner_department_id: str
    security_level: SecurityLevel
    embedding_model_code: str

    @classmethod
    def from_create(cls, principal: Principal, payload: KnowledgeBaseCreate) -> "KnowledgeBase":
        now = datetime.now(UTC)
        return cls(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            owner_id=principal.user_id,
            created_at=now,
            updated_at=now,
            **payload.model_dump(),
        )


class KnowledgeBaseView(ContractModel):
    id: UUID
    name: str
    description: str | None = None
    owner_department_id: str
    security_level: SecurityLevel
    embedding_model_code: str
    created_at: datetime

    @classmethod
    def of(cls, knowledge_base: KnowledgeBase) -> "KnowledgeBaseView":
        return cls.model_validate(knowledge_base)


class KnowledgeDocumentStatus(StrEnum):
    UPLOADED = "uploaded"
    PARSING = "parsing"
    INDEXED = "indexed"
    FAILED = "failed"
    ARCHIVED = "archived"


class KnowledgeDocumentView(ContractModel):
    id: UUID
    knowledge_base_id: UUID
    asset_id: UUID
    filename: str
    mime_type: str
    status: KnowledgeDocumentStatus
    version: int = Field(ge=1)
    source_uri: str | None = None
    indexed_at: datetime | None = None


class KnowledgeSearchFilters(ContractModel):
    document_status: KnowledgeDocumentStatus | None = None
    document_ids: list[UUID] = Field(default_factory=list, max_length=200)
    asset_ids: list[UUID] = Field(default_factory=list, max_length=200)
    data_scopes: list[DataScope] = Field(default_factory=list)
    security_levels: list[SecurityLevel] = Field(default_factory=list)
    title_contains: str | None = Field(default=None, min_length=1, max_length=240)


class KnowledgeSearchRequest(ContractModel):
    query: str = Field(min_length=1, max_length=4_000)
    top_k: int = Field(ge=1, le=50)
    filters: KnowledgeSearchFilters = Field(default_factory=KnowledgeSearchFilters)


class CitationView(ContractModel):
    knowledge_document_id: str
    chunk_id: str
    asset_id: str
    quote: str
    page: int | None = Field(default=None, ge=1)
    score: float = Field(ge=0, le=1)


class KnowledgeSearchResponse(ContractModel):
    citations: list[CitationView] = Field(default_factory=list)
    trace_id: UUID
