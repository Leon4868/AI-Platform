"""SQLAlchemy 2 records aligned with the current domain Pydantic models."""

import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

EMBEDDING_DIMENSIONS = 1536


class TenantRecordMixin:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WorkflowDefinitionRecord(TenantRecordMixin, Base):
    __tablename__ = "workflow_definitions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_workflow_definitions_tenant_id"),
        CheckConstraint("revision >= 1", name="ck_workflow_definitions_revision_positive"),
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_workflow_definitions_status",
        ),
        Index("ix_workflow_definitions_tenant_updated", "tenant_id", "updated_at"),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    graph: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)


class KnowledgeBaseRecord(TenantRecordMixin, Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_knowledge_bases_tenant_id"),
        CheckConstraint(
            "security_level IN ('internal', 'department_sensitive', 'confidential')",
            name="ck_knowledge_bases_security_level",
        ),
        Index("ix_knowledge_bases_tenant_created", "tenant_id", "created_at"),
    )

    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    owner_department_id: Mapped[str] = mapped_column(String(128), nullable=False)
    security_level: Mapped[str] = mapped_column(String(32), nullable=False)
    embedding_model_code: Mapped[str] = mapped_column(String(128), nullable=False)


class AssetRecord(TenantRecordMixin, Base):
    __tablename__ = "assets"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_assets_tenant_id"),
        UniqueConstraint("tenant_id", "storage_uri", name="uq_assets_tenant_storage_uri"),
        CheckConstraint("version >= 1", name="ck_assets_version_positive"),
        CheckConstraint(
            "type IN ('document', 'image', 'video', 'audio', 'prompt', 'agent', "
            "'workflow', 'dataset', 'report', 'code', 'other')",
            name="ck_assets_type",
        ),
        CheckConstraint(
            "status IN ('draft', 'pending_review', 'approved', 'published', 'archived')",
            name="ck_assets_status",
        ),
        CheckConstraint(
            "data_scope IN ('personal', 'project', 'department', 'enterprise')",
            name="ck_assets_data_scope",
        ),
        CheckConstraint(
            "security_level IN ('internal', 'department_sensitive', 'confidential')",
            name="ck_assets_security_level",
        ),
        Index("ix_assets_tenant_type_updated", "tenant_id", "type", "updated_at"),
    )

    type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="draft", nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(255))
    storage_uri: Mapped[str | None] = mapped_column(String(2048))
    content_hash: Mapped[str | None] = mapped_column(String(255))
    creator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    owner_department_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(128))
    data_scope: Mapped[str] = mapped_column(String(24), nullable=False)
    security_level: Mapped[str] = mapped_column(String(32), nullable=False)
    lineage: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    workflow_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    trace_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))


class DocumentGenerationJobRecord(TenantRecordMixin, Base):
    __tablename__ = "document_generation_jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_document_jobs_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "draft_asset_id"],
            ["assets.tenant_id", "assets.id"],
            name="fk_document_jobs_tenant_draft_asset",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('queued', 'running', 'waiting_human', 'succeeded', 'failed', 'cancelled')",
            name="ck_document_jobs_status",
        ),
        CheckConstraint(
            "output_format IN ('markdown', 'docx', 'pdf')",
            name="ck_document_jobs_output_format",
        ),
        CheckConstraint(
            "data_scope IN ('personal', 'project', 'department', 'enterprise')",
            name="ck_document_jobs_data_scope",
        ),
        CheckConstraint(
            "security_level IN ('internal', 'department_sensitive', 'confidential')",
            name="ck_document_jobs_security_level",
        ),
        Index("ix_document_jobs_tenant_status_created", "tenant_id", "status", "created_at"),
    )

    requested_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    template_asset_id: Mapped[str | None] = mapped_column(String(128))
    workflow_definition_id: Mapped[str] = mapped_column(String(128), nullable=False)
    knowledge_base_ids: Mapped[list[str]] = mapped_column(ARRAY(String(128)), default=list, nullable=False)
    logical_model_code: Mapped[str] = mapped_column(String(128), nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    output_format: Mapped[str] = mapped_column(String(16), nullable=False)
    owner_department_id: Mapped[str] = mapped_column(String(128), nullable=False)
    data_scope: Mapped[str] = mapped_column(String(24), nullable=False)
    security_level: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    draft_asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    trace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class KnowledgeChunkRecord(TenantRecordMixin, Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "knowledge_base_id",
            "document_id",
            "ordinal",
            name="uq_knowledge_chunks_document_ordinal",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_knowledge_chunks_tenant_base",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            ["assets.tenant_id", "assets.id"],
            name="fk_knowledge_chunks_tenant_asset",
            ondelete="RESTRICT",
        ),
        Index("ix_knowledge_chunks_tenant_base", "tenant_id", "knowledge_base_id"),
    )

    knowledge_base_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_model_code: Mapped[str | None] = mapped_column(String(128))
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))


class AuditEventRecord(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_tenant_time", "tenant_id", "occurred_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    actor_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    request_id: Mapped[str | None] = mapped_column(String(100))
    event_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, default=dict, nullable=False)


class WorkflowRunRecord(TenantRecordMixin, Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_workflow_runs_tenant_id"),
        ForeignKeyConstraint(
            ["tenant_id", "workflow_id"],
            ["workflow_definitions.tenant_id", "workflow_definitions.id"],
            name="fk_workflow_runs_tenant_workflow",
            ondelete="RESTRICT",
        ),
        CheckConstraint("workflow_revision >= 1", name="ck_workflow_runs_revision_positive"),
        CheckConstraint(
            "status IN ('queued', 'running', 'waiting_human', 'succeeded', 'failed', "
            "'cancelled')",
            name="ck_workflow_runs_status",
        ),
        Index(
            "ix_workflow_runs_tenant_workflow_created",
            "tenant_id",
            "workflow_id",
            "created_at",
        ),
        Index("ix_workflow_runs_tenant_status_created", "tenant_id", "status", "created_at"),
    )

    workflow_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workflow_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    triggered_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    permission_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    trace_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    node_executions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list, nullable=False)
    pending_approval: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowRunEventRecord(Base):
    __tablename__ = "workflow_run_events"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["workflow_runs.tenant_id", "workflow_runs.id"],
            name="fk_workflow_run_events_tenant_run",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "run_id",
            "sequence",
            name="uq_workflow_run_events_tenant_run_sequence",
        ),
        CheckConstraint("sequence >= 1", name="ck_workflow_run_events_sequence_positive"),
        Index(
            "ix_workflow_run_events_tenant_run_sequence",
            "tenant_id",
            "run_id",
            "sequence",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    node_id: Mapped[str | None] = mapped_column(String(80))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
