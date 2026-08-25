from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field

from app.core.schemas import ContractModel, Entity
from app.identity.schemas import Principal


class AssetType(StrEnum):
    DOCUMENT = "document"
    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    PROMPT = "prompt"
    AGENT = "agent"
    WORKFLOW = "workflow"
    DATASET = "dataset"
    REPORT = "report"
    CODE = "code"
    OTHER = "other"


class AssetStatus(StrEnum):
    DRAFT = "draft"
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class DataScope(StrEnum):
    PERSONAL = "personal"
    PROJECT = "project"
    DEPARTMENT = "department"
    ENTERPRISE = "enterprise"


class SecurityLevel(StrEnum):
    INTERNAL = "internal"
    DEPARTMENT_SENSITIVE = "department_sensitive"
    CONFIDENTIAL = "confidential"


class AssetLineageRelation(StrEnum):
    SOURCE = "source"
    DERIVED_FROM = "derived_from"
    GENERATED_BY = "generated_by"
    SUPERSEDES = "supersedes"


class AssetLineageRef(ContractModel):
    asset_id: str
    version: int = Field(ge=1)
    relation: AssetLineageRelation


class Asset(Entity):
    type: AssetType
    name: str
    description: str | None = None
    version: int = Field(default=1, ge=1)
    status: AssetStatus = AssetStatus.DRAFT
    mime_type: str | None = None
    storage_uri: str | None = None
    content_hash: str | None = None
    creator_id: UUID
    owner_department_id: str
    project_id: str | None = None
    data_scope: DataScope = DataScope.DEPARTMENT
    security_level: SecurityLevel = SecurityLevel.INTERNAL
    lineage: list[AssetLineageRef] = Field(default_factory=list)
    workflow_run_id: UUID | None = None
    trace_id: UUID | None = None

    @classmethod
    def from_upload(
        cls,
        principal: Principal,
        *,
        name: str,
        mime_type: str,
        storage_uri: str,
        owner_department_id: str,
        data_scope: DataScope = DataScope.DEPARTMENT,
        security_level: SecurityLevel = SecurityLevel.INTERNAL,
        project_id: str | None = None,
    ) -> "Asset":
        now = datetime.now(UTC)
        return cls(
            id=uuid4(),
            tenant_id=principal.tenant_id,
            type=AssetType.DOCUMENT,
            name=name,
            mime_type=mime_type,
            storage_uri=storage_uri,
            creator_id=principal.user_id,
            owner_department_id=owner_department_id,
            project_id=project_id,
            data_scope=data_scope,
            security_level=security_level,
            created_at=now,
            updated_at=now,
        )


class AssetView(ContractModel):
    id: UUID
    type: AssetType
    name: str
    description: str | None = None
    version: int
    status: AssetStatus
    mime_type: str | None = None
    storage_uri: str | None = None
    content_hash: str | None = None
    creator_id: UUID
    owner_department_id: str
    project_id: str | None = None
    data_scope: DataScope
    security_level: SecurityLevel
    lineage: list[AssetLineageRef]
    workflow_run_id: UUID | None = None
    trace_id: UUID | None = None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, asset: Asset) -> "AssetView":
        return cls.model_validate(asset)
