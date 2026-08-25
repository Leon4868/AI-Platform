"""Run lifecycle: start, observe, approve, cancel.

The service is the only writer of run state. It drives an executor in a
background task, projects each execution step onto the run record and the event
log, and exposes the reads a router needs (get, list, events, live stream).
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from app.audit.service import AuditService
from app.core.errors import ConflictError, NotFoundError
from app.core.repository import Repository
from app.identity.schemas import Principal
from app.runtime.executor import (
    ApprovalInbox,
    ApprovalSubmission,
    ExecutionPlan,
    ExecutionStep,
    GraphExecutor,
    StepKind,
)
from app.runtime.repository import (
    RunEventWrite,
    StaleWorkflowRunError,
    WorkflowRunRepository,
)
from app.runtime.schemas import (
    TERMINAL_EVENT_TYPES,
    TERMINAL_RUN_STATUSES,
    ApprovalDecision,
    NodeExecution,
    NodeRunStatus,
    PendingApproval,
    PermissionSnapshot,
    RunApprovalRequest,
    RunError,
    RunEvent,
    RunEventType,
    RunStartRequest,
    RunStatus,
    WorkflowRun,
)
from app.workflows.schemas import WorkflowDefinition

_EVENT_BY_STEP = {
    StepKind.NODE_STARTED: RunEventType.NODE_STARTED,
    StepKind.NODE_AWAITING_APPROVAL: RunEventType.NODE_AWAITING_APPROVAL,
    StepKind.NODE_SUCCEEDED: RunEventType.NODE_SUCCEEDED,
    StepKind.NODE_SKIPPED: RunEventType.NODE_SKIPPED,
    StepKind.NODE_FAILED: RunEventType.NODE_FAILED,
    StepKind.RUN_SUCCEEDED: RunEventType.RUN_SUCCEEDED,
    StepKind.RUN_FAILED: RunEventType.RUN_FAILED,
    StepKind.RUN_CANCELLED: RunEventType.RUN_CANCELLED,
}

_UNFINISHED_NODE_STATUSES = frozenset({NodeRunStatus.RUNNING, NodeRunStatus.WAITING_HUMAN})


@dataclass(slots=True)
class _Execution:
    """Everything a live run needs while its task is in flight."""

    plan: ExecutionPlan
    run: WorkflowRun
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    task: asyncio.Task[None] | None = None
    cancel_reason: str | None = None


class WorkflowRunService:
    def __init__(
        self,
        workflow_repository: Repository[WorkflowDefinition],
        run_repository: WorkflowRunRepository,
        executor: GraphExecutor,
        audit_service: AuditService,
    ) -> None:
        self._workflows = workflow_repository
        self._runs = run_repository
        self._executor = executor
        self._audit = audit_service
        self._executions: dict[UUID, _Execution] = {}

    async def start(
        self, principal: Principal, workflow_id: UUID, payload: RunStartRequest
    ) -> WorkflowRun:
        workflow = await self._workflows.get(principal.tenant_id, workflow_id)
        if workflow is None:
            raise NotFoundError("workflow_definition", str(workflow_id))

        now = datetime.now(UTC)
        run, _ = await self._runs.create_with_event(
            WorkflowRun(
                id=uuid4(),
                tenant_id=principal.tenant_id,
                workflow_id=workflow.id,
                workflow_revision=workflow.revision,
                triggered_by=principal.user_id,
                permission_snapshot=_capture_permission_snapshot(principal, now),
                trace_id=uuid4(),
                status=RunStatus.QUEUED,
                input=payload.input,
                created_at=now,
                updated_at=now,
            ),
            event=RunEventWrite(
                type=RunEventType.RUN_QUEUED,
                data={"workflow_id": str(workflow.id), "workflow_revision": workflow.revision},
            ),
        )
        await self._audit.record(
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            action="workflow_run.started",
            resource_type="workflow_run",
            resource_id=run.id,
            metadata={"workflow_id": str(workflow.id), "workflow_revision": workflow.revision},
        )

        execution = _Execution(
            plan=ExecutionPlan(
                run_id=run.id,
                graph=workflow.graph,
                input=payload.input,
                approvals=ApprovalInbox(),
                cancellation=asyncio.Event(),
            ),
            run=run,
        )
        self._executions[run.id] = execution
        execution.task = asyncio.create_task(self._drive(execution), name=f"workflow-run:{run.id}")
        return run

    async def get(self, tenant_id: UUID, run_id: UUID) -> WorkflowRun:
        run = await self._runs.get(tenant_id, run_id)
        if run is None:
            raise NotFoundError("workflow_run", str(run_id))
        return run

    async def list_runs(
        self,
        tenant_id: UUID,
        *,
        workflow_id: UUID | None = None,
        limit: int,
        offset: int,
    ) -> tuple[list[WorkflowRun], int]:
        return await self._runs.list_runs(
            tenant_id, workflow_id=workflow_id, limit=limit, offset=offset
        )

    async def events(
        self,
        tenant_id: UUID,
        run_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[RunEvent]:
        await self.get(tenant_id, run_id)
        return await self._runs.list_events(
            tenant_id, run_id, after_sequence=after_sequence, limit=limit
        )

    async def stream(
        self, tenant_id: UUID, run_id: UUID, *, after_sequence: int = 0
    ) -> AsyncIterator[RunEvent]:
        """Yields the run's events from a cursor and closes once the run ends.

        A finished run replays its whole log and closes immediately, so a
        reconnecting client behaves the same whether or not it missed the end.
        """
        await self.get(tenant_id, run_id)
        async with self._runs.subscribe(run_id, after_sequence=after_sequence) as events:
            async for event in events:
                yield event
                if event.type in TERMINAL_EVENT_TYPES:
                    return

    async def approve(
        self, principal: Principal, run_id: UUID, payload: RunApprovalRequest
    ) -> WorkflowRun:
        run = await self.get(principal.tenant_id, run_id)
        if run.status is not RunStatus.WAITING_HUMAN or run.pending_approval is None:
            raise ConflictError(f"Run is {run.status}, no approval is pending")
        if run.pending_approval.node_id != payload.node_id:
            raise ConflictError(
                f"Run is waiting on node '{run.pending_approval.node_id}', not '{payload.node_id}'"
            )
        execution = self._executions.get(run_id)
        if execution is None:
            raise ConflictError("Run is no longer resumable in this process")

        # The decision is recorded before it is handed to the executor, so the
        # caller never sees a run that is still waiting on an answered approval.
        updated = await self._record(
            execution,
            event_type=RunEventType.NODE_RESUMED,
            node_id=payload.node_id,
            data={"decision": payload.decision.value, "comment": payload.comment},
            updates={"status": RunStatus.RUNNING, "pending_approval": None},
        )
        execution.plan.approvals.submit(
            payload.node_id,
            ApprovalSubmission(
                decision=payload.decision,
                comment=payload.comment,
                decided_by=principal.user_id,
            ),
        )
        await self._audit.record(
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            action="workflow_run.approval_submitted",
            resource_type="workflow_run",
            resource_id=run_id,
            metadata={"node_id": payload.node_id, "decision": payload.decision.value},
        )
        return updated

    async def cancel(
        self, principal: Principal, run_id: UUID, reason: str | None = None
    ) -> WorkflowRun:
        """Idempotent: cancelling an already cancelled run returns it untouched.

        A run that finished on its own is a different matter — there is nothing
        left to stop, so succeeded and failed runs conflict instead.
        """
        run = await self.get(principal.tenant_id, run_id)
        if run.status is RunStatus.CANCELLED:
            return run
        if run.status in TERMINAL_RUN_STATUSES:
            raise ConflictError(f"Run already finished as {run.status} and cannot be cancelled")

        execution = self._executions.get(run_id)
        if execution is None:
            cancelled = await self._mark_cancelled_without_task(run, reason)
        else:
            execution.cancel_reason = reason
            execution.plan.cancellation.set()
            if execution.task is not None:
                with suppress(asyncio.CancelledError):
                    await asyncio.shield(execution.task)
            cancelled = await self.get(principal.tenant_id, run_id)

        await self._audit.record(
            tenant_id=principal.tenant_id,
            actor_id=principal.user_id,
            action="workflow_run.cancelled",
            resource_type="workflow_run",
            resource_id=run_id,
            metadata={"reason": reason} if reason else None,
        )
        return cancelled

    async def aclose(self) -> None:
        """Stops every in-flight run; used on application shutdown.

        Runs are asked to stop the same way a caller cancels one, so each closes
        out with a cancelled event instead of vanishing mid-flight.
        """
        executions = list(self._executions.values())
        for execution in executions:
            execution.plan.cancellation.set()
        for execution in executions:
            if execution.task is not None:
                with suppress(asyncio.CancelledError, Exception):
                    await execution.task
        self._executions.clear()

    async def _drive(self, execution: _Execution) -> None:
        run_id = execution.plan.run_id
        try:
            await self._record(
                execution,
                event_type=RunEventType.RUN_STARTED,
                updates={"status": RunStatus.RUNNING, "started_at": datetime.now(UTC)},
            )
            async for step in self._executor.run(execution.plan):
                await self._apply_step(execution, step)
        except Exception as exc:  # an executor fault must still close the run out
            with suppress(Exception):
                await self._apply_step(
                    execution,
                    ExecutionStep(
                        kind=StepKind.RUN_FAILED,
                        error=RunError(code="runtime_error", message=str(exc)),
                    ),
                )
        finally:
            self._executions.pop(run_id, None)

    async def _apply_step(self, execution: _Execution, step: ExecutionStep) -> None:
        now = datetime.now(UTC)
        async with execution.lock:
            run = execution.run
            if run.status in TERMINAL_RUN_STATUSES:
                return
            executions = [item.model_copy(deep=True) for item in run.node_executions]
            updates: dict[str, Any] = {}
            cancelled_nodes: list[str] = []

            match step.kind:
                case StepKind.NODE_STARTED:
                    executions.append(
                        NodeExecution(
                            node_id=str(step.node_id),
                            node_type=step.node_type,
                            status=NodeRunStatus.RUNNING,
                            started_at=now,
                        )
                    )
                case StepKind.NODE_SKIPPED:
                    executions.append(
                        NodeExecution(
                            node_id=str(step.node_id),
                            node_type=step.node_type,
                            status=NodeRunStatus.SKIPPED,
                            started_at=now,
                            finished_at=now,
                        )
                    )
                case StepKind.NODE_AWAITING_APPROVAL:
                    _mark_node(executions, step.node_id, status=NodeRunStatus.WAITING_HUMAN, output=step.output)
                    updates["status"] = RunStatus.WAITING_HUMAN
                    updates["pending_approval"] = PendingApproval(
                        node_id=str(step.node_id),
                        prompt=str(step.output.get("prompt", "")),
                        approvers=list(step.output.get("approvers", [])),
                        requested_at=now,
                    )
                case StepKind.NODE_SUCCEEDED:
                    _mark_node(
                        executions,
                        step.node_id,
                        status=NodeRunStatus.SUCCEEDED,
                        output=step.output,
                        finished_at=now,
                    )
                case StepKind.NODE_FAILED:
                    _mark_node(
                        executions,
                        step.node_id,
                        status=NodeRunStatus.FAILED,
                        output=step.output,
                        error=step.error,
                        finished_at=now,
                    )
                case StepKind.RUN_SUCCEEDED:
                    updates["status"] = RunStatus.SUCCEEDED
                    updates["output"] = step.output
                    updates["finished_at"] = now
                case StepKind.RUN_FAILED:
                    updates["status"] = RunStatus.FAILED
                    updates["error"] = step.error
                    updates["finished_at"] = now
                    updates["pending_approval"] = None
                case StepKind.RUN_CANCELLED:
                    cancelled_nodes = _cancel_unfinished_nodes(executions, now)
                    updates["status"] = RunStatus.CANCELLED
                    updates["finished_at"] = now
                    updates["pending_approval"] = None

            updates["node_executions"] = executions
            updates["updated_at"] = _monotonic_updated_at(run.updated_at, now)
            # A node that was still open is closed out in its own event before
            # the run's, so a replayed stream shows every node reaching a
            # terminal state rather than stopping mid-flight.
            event_writes = [
                RunEventWrite(
                    type=RunEventType.NODE_CANCELLED,
                    node_id=node_id,
                    data={"reason": execution.cancel_reason} if execution.cancel_reason else None,
                )
                for node_id in cancelled_nodes
            ]
            data = _event_data(step)
            if step.kind is StepKind.RUN_CANCELLED and execution.cancel_reason:
                data["reason"] = execution.cancel_reason
            event_writes.append(
                RunEventWrite(
                    type=_EVENT_BY_STEP[step.kind],
                    node_id=step.node_id,
                    data=data,
                )
            )
            candidate = run.model_copy(update=updates)
            try:
                stored, _ = await self._runs.transition_with_events(
                    candidate,
                    expected_updated_at=run.updated_at,
                    events=event_writes,
                )
            except StaleWorkflowRunError:
                latest = await self.get(run.tenant_id, run.id)
                execution.run = latest
                if latest.status in TERMINAL_RUN_STATUSES:
                    return
                raise
            execution.run = stored

    async def _record(
        self,
        execution: _Execution,
        *,
        event_type: RunEventType,
        updates: dict[str, Any],
        node_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> WorkflowRun:
        """Applies a transition the service itself owns (start, resume)."""
        async with execution.lock:
            run = execution.run
            if run.status in TERMINAL_RUN_STATUSES:
                return run
            candidate = run.model_copy(
                update={
                    **updates,
                    "updated_at": _monotonic_updated_at(run.updated_at),
                }
            )
            try:
                stored, _ = await self._runs.transition_with_events(
                    candidate,
                    expected_updated_at=run.updated_at,
                    events=[RunEventWrite(type=event_type, node_id=node_id, data=data)],
                )
            except StaleWorkflowRunError:
                latest = await self.get(run.tenant_id, run.id)
                execution.run = latest
                if latest.status in TERMINAL_RUN_STATUSES:
                    return latest
                raise
            execution.run = stored
            return stored

    async def _mark_cancelled_without_task(self, run: WorkflowRun, reason: str | None) -> WorkflowRun:
        """Closes out a run whose task is gone, e.g. one orphaned by a restart."""
        current = run
        while True:
            now = datetime.now(UTC)
            executions = [item.model_copy(deep=True) for item in current.node_executions]
            cancelled_nodes = _cancel_unfinished_nodes(executions, now)
            cancelled = current.model_copy(
                update={
                    "status": RunStatus.CANCELLED,
                    "pending_approval": None,
                    "node_executions": executions,
                    "finished_at": now,
                    "updated_at": _monotonic_updated_at(current.updated_at, now),
                }
            )
            events = [
                RunEventWrite(
                    type=RunEventType.NODE_CANCELLED,
                    node_id=node_id,
                    data={"reason": reason} if reason else None,
                )
                for node_id in cancelled_nodes
            ]
            events.append(
                RunEventWrite(
                    type=RunEventType.RUN_CANCELLED,
                    data={"reason": reason} if reason else None,
                )
            )
            try:
                stored, _ = await self._runs.transition_with_events(
                    cancelled,
                    expected_updated_at=current.updated_at,
                    events=events,
                )
                return stored
            except StaleWorkflowRunError:
                current = await self.get(run.tenant_id, run.id)
                if current.status is RunStatus.CANCELLED:
                    return current
                if current.status in TERMINAL_RUN_STATUSES:
                    raise ConflictError(
                        f"Run already finished as {current.status} and cannot be cancelled"
                    )


def _monotonic_updated_at(previous: datetime, current: datetime | None = None) -> datetime:
    """Return a timestamp that can safely act as an optimistic-lock token.

    PostgreSQL stores microsecond precision. Two transitions may otherwise be
    assigned the same wall-clock value and make a stale writer look current.
    """
    candidate = current or datetime.now(UTC)
    return candidate if candidate > previous else previous + timedelta(microseconds=1)


def _cancel_unfinished_nodes(executions: list[NodeExecution], now: datetime) -> list[str]:
    """Marks every node still open as cancelled and reports which ones were."""
    cancelled: list[str] = []
    for item in executions:
        if item.status in _UNFINISHED_NODE_STATUSES:
            item.status = NodeRunStatus.CANCELLED
            item.finished_at = now
            cancelled.append(item.node_id)
    return cancelled


def _capture_permission_snapshot(
    principal: Principal, captured_at: datetime
) -> PermissionSnapshot:
    """Build the conservative phase-one snapshot from the active identity.

    Development identities intentionally do not claim enterprise departments,
    projects, or elevated data scopes.  The snapshot is still persisted so a
    later identity-provider migration cannot rewrite what a historical run was
    authorized to do.
    """

    allowed_scopes = ["personal"]
    if principal.project_ids:
        allowed_scopes.append("project")
    if principal.department_ids:
        allowed_scopes.append("department")
    if "enterprise_reader" in principal.roles:
        allowed_scopes.append("enterprise")
    return PermissionSnapshot(
        subject_id=principal.user_id,
        department_ids=sorted(principal.department_ids),
        project_ids=sorted(principal.project_ids),
        roles=sorted(principal.roles),
        allowed_scopes=allowed_scopes,
        security_clearance=principal.security_clearance,
        captured_at=captured_at,
        policy_version="temporary-identity-v1",
    )


def _mark_node(
    executions: list[NodeExecution],
    node_id: str | None,
    *,
    status: NodeRunStatus,
    output: dict[str, Any] | None = None,
    error: RunError | None = None,
    finished_at: datetime | None = None,
) -> None:
    for item in reversed(executions):
        if item.node_id == node_id:
            item.status = status
            if output is not None:
                item.output = output
            if error is not None:
                item.error = error
            item.finished_at = finished_at
            return


def _event_data(step: ExecutionStep) -> dict[str, Any]:
    data: dict[str, Any] = {}
    if step.output:
        data["output"] = step.output
    if step.error is not None:
        data["error"] = step.error.model_dump(exclude_none=True)
    return data
