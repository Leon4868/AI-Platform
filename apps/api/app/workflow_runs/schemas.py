"""Wire models for the workflow-run API.

`app.runtime` keeps a fine-grained internal vocabulary; `packages/contracts` is
the coarser vocabulary clients code against. Translation happens here and only
here, so the runtime is free to add internal event kinds without any of them
leaking onto the wire.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from app.core.schemas import ContractModel
from app.runtime.schemas import (
    NodeExecution,
    PermissionSnapshot,
    RunEvent,
    RunEventType as RuntimeEventType,
    WorkflowRun,
)


class WireModel(ContractModel):
    """Workflow-run wire model using the shared public contract policy."""


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NodeRunStatus(StrEnum):
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class RunEventType(StrEnum):
    """The event vocabulary of `packages/contracts` — nothing else goes out."""

    RUN_QUEUED = "run.queued"
    RUN_STARTED = "run.started"
    NODE_STARTED = "node.started"
    NODE_COMPLETED = "node.completed"
    NODE_FAILED = "node.failed"
    NODE_CANCELLED = "node.cancelled"
    RUN_WAITING_HUMAN = "run.waiting_human"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"


# Internal kinds absent from this table never reach a client. `node.resumed` and
# `node.skipped` are deliberately absent: the contract has no counterpart, and
# reporting a skipped node as completed would misstate what happened. Their
# outcome still reaches clients through the node's status on the run record.
_CONTRACT_EVENT_TYPE = {
    RuntimeEventType.RUN_QUEUED: RunEventType.RUN_QUEUED,
    RuntimeEventType.RUN_STARTED: RunEventType.RUN_STARTED,
    RuntimeEventType.NODE_STARTED: RunEventType.NODE_STARTED,
    RuntimeEventType.NODE_AWAITING_APPROVAL: RunEventType.RUN_WAITING_HUMAN,
    RuntimeEventType.NODE_SUCCEEDED: RunEventType.NODE_COMPLETED,
    RuntimeEventType.NODE_FAILED: RunEventType.NODE_FAILED,
    RuntimeEventType.NODE_CANCELLED: RunEventType.NODE_CANCELLED,
    RuntimeEventType.RUN_SUCCEEDED: RunEventType.RUN_COMPLETED,
    RuntimeEventType.RUN_FAILED: RunEventType.RUN_FAILED,
    RuntimeEventType.RUN_CANCELLED: RunEventType.RUN_CANCELLED,
}


class WorkflowRunStartRequest(WireModel):
    input: dict[str, Any] = Field(default_factory=dict)
    workflow_definition_version: int | None = Field(default=None, ge=1)


class WorkflowRunCancelRequest(WireModel):
    reason: str | None = Field(default=None, min_length=1, max_length=500)


class RunErrorView(WireModel):
    code: str
    message: str
    node_id: str | None = None


class NodeRunView(WireModel):
    node_id: str
    attempt: int = Field(ge=1)
    status: NodeRunStatus
    started_at: datetime | None = None
    finished_at: datetime | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    error: RunErrorView | None = None

    @classmethod
    def of(cls, execution: NodeExecution) -> "NodeRunView":
        return cls(
            node_id=execution.node_id,
            # The in-process executor runs each node once; retries would have to
            # supply a real counter before this can vary.
            attempt=1,
            status=NodeRunStatus(execution.status.value),
            started_at=execution.started_at,
            finished_at=execution.finished_at,
            output=execution.output,
            error=None if execution.error is None else RunErrorView.model_validate(
                execution.error.model_dump()
            ),
        )


class PermissionSnapshotView(WireModel):
    subject_id: UUID
    department_ids: list[str]
    project_ids: list[str]
    roles: list[str]
    allowed_scopes: list[str]
    security_clearance: str
    captured_at: datetime
    policy_version: str

    @classmethod
    def of(cls, snapshot: PermissionSnapshot) -> "PermissionSnapshotView":
        return cls.model_validate(snapshot.model_dump())


class WorkflowRunView(WireModel):
    id: UUID
    workflow_definition_id: UUID
    workflow_definition_version: int = Field(ge=1)
    status: RunStatus
    initiated_by: UUID
    permission_snapshot: PermissionSnapshotView
    input: dict[str, Any]
    output: dict[str, Any] = Field(default_factory=dict)
    node_runs: list[NodeRunView] = Field(default_factory=list)
    error: RunErrorView | None = None
    trace_id: UUID
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @classmethod
    def of(cls, run: WorkflowRun) -> "WorkflowRunView":
        return cls(
            id=run.id,
            workflow_definition_id=run.workflow_id,
            workflow_definition_version=run.workflow_revision,
            status=RunStatus(run.status.value),
            initiated_by=run.triggered_by,
            permission_snapshot=PermissionSnapshotView.of(run.permission_snapshot),
            input=run.input,
            output=run.output,
            node_runs=[NodeRunView.of(item) for item in run.node_executions],
            error=None if run.error is None else RunErrorView.model_validate(run.error.model_dump()),
            trace_id=run.trace_id,
            created_at=run.created_at,
            started_at=run.started_at,
            finished_at=run.finished_at,
        )


class WorkflowRunEventView(WireModel):
    sequence: int = Field(ge=1)
    run_id: UUID
    type: RunEventType
    occurred_at: datetime
    node_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def of(cls, event: RunEvent) -> "WorkflowRunEventView | None":
        """Returns ``None`` for internal events that have no contract counterpart."""
        contract_type = _CONTRACT_EVENT_TYPE.get(event.type)
        if contract_type is None:
            return None
        return cls(
            sequence=event.sequence,
            run_id=event.run_id,
            type=contract_type,
            occurred_at=event.occurred_at,
            node_id=event.node_id,
            payload=event.data,
        )
