"""State model of a workflow run: the run record, its per-node executions and
the append-only event log that drives streaming."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from app.core.schemas import ApiModel, Entity
from app.workflows.schemas import NodeType


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_RUN_STATUSES = frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED})


class NodeRunStatus(StrEnum):
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class RunEventType(StrEnum):
    RUN_QUEUED = "run.queued"
    RUN_STARTED = "run.started"
    NODE_STARTED = "node.started"
    NODE_AWAITING_APPROVAL = "node.awaiting_approval"
    NODE_RESUMED = "node.resumed"
    NODE_SUCCEEDED = "node.succeeded"
    NODE_SKIPPED = "node.skipped"
    NODE_FAILED = "node.failed"
    NODE_CANCELLED = "node.cancelled"
    RUN_SUCCEEDED = "run.succeeded"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"


TERMINAL_EVENT_TYPES = frozenset(
    {RunEventType.RUN_SUCCEEDED, RunEventType.RUN_FAILED, RunEventType.RUN_CANCELLED}
)


class ApprovalDecision(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class RunError(ApiModel):
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(max_length=2_000)
    node_id: str | None = Field(default=None, max_length=80)


class NodeExecution(ApiModel):
    node_id: str
    node_type: NodeType
    status: NodeRunStatus
    started_at: datetime
    finished_at: datetime | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    error: RunError | None = None


class PendingApproval(ApiModel):
    node_id: str
    prompt: str = Field(default="", max_length=2_000)
    approvers: list[str] = Field(default_factory=list)
    requested_at: datetime


class PermissionSnapshot(ApiModel):
    """Authorization context captured when a run is admitted.

    The current development identity has no organization directory attached,
    so department and project membership remain empty instead of being
    invented.  A production identity/policy adapter can populate the same
    immutable shape without changing the runtime contract.
    """

    subject_id: UUID
    department_ids: list[str] = Field(default_factory=list)
    project_ids: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    allowed_scopes: list[str] = Field(default_factory=list)
    security_clearance: str
    captured_at: datetime
    policy_version: str


class WorkflowRun(Entity):
    workflow_id: UUID
    workflow_revision: int = Field(ge=1)
    triggered_by: UUID
    permission_snapshot: PermissionSnapshot
    trace_id: UUID
    status: RunStatus = RunStatus.QUEUED
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    node_executions: list[NodeExecution] = Field(default_factory=list)
    pending_approval: PendingApproval | None = None
    error: RunError | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class RunEvent(ApiModel):
    """One entry of a run's append-only log. `sequence` starts at 1 and is
    strictly monotonic per run, so a reconnecting stream resumes from a cursor."""

    id: UUID
    tenant_id: UUID
    run_id: UUID
    sequence: int = Field(ge=1)
    type: RunEventType
    occurred_at: datetime
    node_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class RunStartRequest(ApiModel):
    input: dict[str, Any] = Field(default_factory=dict)


class RunApprovalRequest(ApiModel):
    node_id: str = Field(min_length=1, max_length=80)
    decision: ApprovalDecision
    comment: str = Field(default="", max_length=2_000)
