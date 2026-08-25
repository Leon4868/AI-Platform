"""Persist workflow runs and their append-only event streams."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260825_0002"
down_revision: str | None = "20260825_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("workflow_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("workflow_revision", sa.Integer(), nullable=False),
        sa.Column("triggered_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("permission_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("trace_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("input", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("output", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("node_executions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("pending_approval", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("workflow_revision >= 1", name="ck_workflow_runs_revision_positive"),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'waiting_human', 'succeeded', 'failed', 'cancelled')",
            name="ck_workflow_runs_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workflow_id"],
            ["workflow_definitions.tenant_id", "workflow_definitions.id"],
            name="fk_workflow_runs_tenant_workflow",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_workflow_runs_tenant_id"),
    )
    op.create_index(
        "ix_workflow_runs_tenant_workflow_created",
        "workflow_runs",
        ["tenant_id", "workflow_id", "created_at"],
    )
    op.create_index(
        "ix_workflow_runs_tenant_status_created",
        "workflow_runs",
        ["tenant_id", "status", "created_at"],
    )

    op.create_table(
        "workflow_run_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("node_id", sa.String(length=80), nullable=True),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_workflow_run_events_sequence_positive"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["workflow_runs.tenant_id", "workflow_runs.id"],
            name="fk_workflow_run_events_tenant_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "sequence",
            name="uq_workflow_run_events_tenant_run_sequence",
        ),
    )
    op.create_index(
        "ix_workflow_run_events_tenant_run_sequence",
        "workflow_run_events",
        ["tenant_id", "run_id", "sequence"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_run_events_tenant_run_sequence", table_name="workflow_run_events"
    )
    op.drop_table("workflow_run_events")
    op.drop_index("ix_workflow_runs_tenant_status_created", table_name="workflow_runs")
    op.drop_index("ix_workflow_runs_tenant_workflow_created", table_name="workflow_runs")
    op.drop_table("workflow_runs")
