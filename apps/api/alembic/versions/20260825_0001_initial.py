"""Create the initial tenant-safe enterprise AI persistence schema."""

from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260825_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tenant_columns() -> list[sa.Column]:
    return [
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    ]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "workflow_definitions",
        *_tenant_columns(),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("graph", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("revision >= 1", name="ck_workflow_definitions_revision_positive"),
        sa.CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_workflow_definitions_status",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_workflow_definitions_tenant_id"),
    )
    op.create_index(
        "ix_workflow_definitions_tenant_updated",
        "workflow_definitions",
        ["tenant_id", "updated_at"],
    )

    op.create_table(
        "knowledge_bases",
        *_tenant_columns(),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("owner_department_id", sa.String(length=128), nullable=False),
        sa.Column("security_level", sa.String(length=32), nullable=False),
        sa.Column("embedding_model_code", sa.String(length=128), nullable=False),
        sa.CheckConstraint(
            "security_level IN ('internal', 'department_sensitive', 'confidential')",
            name="ck_knowledge_bases_security_level",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_knowledge_bases_tenant_id"),
    )
    op.create_index(
        "ix_knowledge_bases_tenant_created",
        "knowledge_bases",
        ["tenant_id", "created_at"],
    )

    op.create_table(
        "assets",
        *_tenant_columns(),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=240), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("mime_type", sa.String(length=255), nullable=True),
        sa.Column("storage_uri", sa.String(length=2048), nullable=True),
        sa.Column("content_hash", sa.String(length=255), nullable=True),
        sa.Column("creator_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_department_id", sa.String(length=128), nullable=False),
        sa.Column("project_id", sa.String(length=128), nullable=True),
        sa.Column("data_scope", sa.String(length=24), nullable=False),
        sa.Column("security_level", sa.String(length=32), nullable=False),
        sa.Column("lineage", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("workflow_run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("version >= 1", name="ck_assets_version_positive"),
        sa.CheckConstraint(
            "type IN ('document', 'image', 'video', 'audio', 'prompt', 'agent', "
            "'workflow', 'dataset', 'report', 'code', 'other')",
            name="ck_assets_type",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'pending_review', 'approved', 'published', 'archived')",
            name="ck_assets_status",
        ),
        sa.CheckConstraint(
            "data_scope IN ('personal', 'project', 'department', 'enterprise')",
            name="ck_assets_data_scope",
        ),
        sa.CheckConstraint(
            "security_level IN ('internal', 'department_sensitive', 'confidential')",
            name="ck_assets_security_level",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_assets_tenant_id"),
        sa.UniqueConstraint("tenant_id", "storage_uri", name="uq_assets_tenant_storage_uri"),
    )
    op.create_index("ix_assets_tenant_type_updated", "assets", ["tenant_id", "type", "updated_at"])

    op.create_table(
        "document_generation_jobs",
        *_tenant_columns(),
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("template_asset_id", sa.String(length=128), nullable=True),
        sa.Column("workflow_definition_id", sa.String(length=128), nullable=False),
        sa.Column("knowledge_base_ids", postgresql.ARRAY(sa.String(length=128)), nullable=False),
        sa.Column("logical_model_code", sa.String(length=128), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("sources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output_format", sa.String(length=16), nullable=False),
        sa.Column("owner_department_id", sa.String(length=128), nullable=False),
        sa.Column("data_scope", sa.String(length=24), nullable=False),
        sa.Column("security_level", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("draft_asset_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("workflow_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("citations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'waiting_human', 'succeeded', 'failed', 'cancelled')",
            name="ck_document_jobs_status",
        ),
        sa.CheckConstraint(
            "output_format IN ('markdown', 'docx', 'pdf')",
            name="ck_document_jobs_output_format",
        ),
        sa.CheckConstraint(
            "data_scope IN ('personal', 'project', 'department', 'enterprise')",
            name="ck_document_jobs_data_scope",
        ),
        sa.CheckConstraint(
            "security_level IN ('internal', 'department_sensitive', 'confidential')",
            name="ck_document_jobs_security_level",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "draft_asset_id"],
            ["assets.tenant_id", "assets.id"],
            name="fk_document_jobs_tenant_draft_asset",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_document_jobs_tenant_id"),
    )
    op.create_index(
        "ix_document_jobs_tenant_status_created",
        "document_generation_jobs",
        ["tenant_id", "status", "created_at"],
    )

    op.create_table(
        "knowledge_chunks",
        *_tenant_columns(),
        sa.Column("knowledge_base_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("document_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding_model_code", sa.String(length=128), nullable=True),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(dim=1536), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id", "knowledge_base_id"],
            ["knowledge_bases.tenant_id", "knowledge_bases.id"],
            name="fk_knowledge_chunks_tenant_base",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "asset_id"],
            ["assets.tenant_id", "assets.id"],
            name="fk_knowledge_chunks_tenant_asset",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "knowledge_base_id",
            "document_id",
            "ordinal",
            name="uq_knowledge_chunks_document_ordinal",
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_tenant_base",
        "knowledge_chunks",
        ["tenant_id", "knowledge_base_id"],
    )

    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=80), nullable=False),
        sa.Column("resource_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("request_id", sa.String(length=100), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_events_tenant_time", "audit_events", ["tenant_id", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_audit_events_tenant_time", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_index("ix_knowledge_chunks_tenant_base", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_index("ix_document_jobs_tenant_status_created", table_name="document_generation_jobs")
    op.drop_table("document_generation_jobs")
    op.drop_index("ix_assets_tenant_type_updated", table_name="assets")
    op.drop_table("assets")
    op.drop_index("ix_knowledge_bases_tenant_created", table_name="knowledge_bases")
    op.drop_table("knowledge_bases")
    op.drop_index("ix_workflow_definitions_tenant_updated", table_name="workflow_definitions")
    op.drop_table("workflow_definitions")
