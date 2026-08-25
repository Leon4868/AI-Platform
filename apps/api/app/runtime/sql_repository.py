"""PostgreSQL workflow-run repository with an append-only event log."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Select

from app.core.tables import WorkflowRunEventRecord, WorkflowRunRecord
from app.runtime.repository import (
    MAX_EVENT_PAGE,
    RunEventWrite,
    StaleWorkflowRunError,
)
from app.runtime.schemas import RunEvent, WorkflowRun


def select_workflow_run(tenant_id: UUID, run_id: UUID, *, for_update: bool = False) -> Select:
    statement = select(WorkflowRunRecord).where(
        WorkflowRunRecord.tenant_id == tenant_id,
        WorkflowRunRecord.id == run_id,
    )
    return statement.with_for_update() if for_update else statement


def list_workflow_runs(
    tenant_id: UUID,
    *,
    workflow_id: UUID | None,
    limit: int,
    offset: int,
) -> Select:
    statement = select(WorkflowRunRecord).where(WorkflowRunRecord.tenant_id == tenant_id)
    if workflow_id is not None:
        statement = statement.where(WorkflowRunRecord.workflow_id == workflow_id)
    return statement.order_by(
        WorkflowRunRecord.created_at.desc(), WorkflowRunRecord.id.desc()
    ).limit(limit).offset(offset)


def list_workflow_run_events(
    tenant_id: UUID,
    run_id: UUID,
    *,
    after_sequence: int,
    limit: int,
) -> Select:
    return (
        select(WorkflowRunEventRecord)
        .where(
            WorkflowRunEventRecord.tenant_id == tenant_id,
            WorkflowRunEventRecord.run_id == run_id,
            WorkflowRunEventRecord.sequence > after_sequence,
        )
        .order_by(WorkflowRunEventRecord.sequence.asc())
        .limit(min(limit, MAX_EVENT_PAGE))
    )


def _run_to_record(run: WorkflowRun) -> WorkflowRunRecord:
    record = WorkflowRunRecord(id=run.id, tenant_id=run.tenant_id)
    _apply_run(record, run)
    return record


def _apply_run(record: WorkflowRunRecord, run: WorkflowRun) -> None:
    record.workflow_id = run.workflow_id
    record.workflow_revision = run.workflow_revision
    record.triggered_by = run.triggered_by
    record.permission_snapshot = run.permission_snapshot.model_dump(mode="json")
    record.trace_id = run.trace_id
    record.status = run.status.value
    record.input = run.input
    record.output = run.output
    record.node_executions = [item.model_dump(mode="json") for item in run.node_executions]
    record.pending_approval = (
        None if run.pending_approval is None else run.pending_approval.model_dump(mode="json")
    )
    record.error = None if run.error is None else run.error.model_dump(mode="json")
    record.started_at = run.started_at
    record.finished_at = run.finished_at
    record.created_at = run.created_at
    record.updated_at = run.updated_at


def _run_from_record(record: WorkflowRunRecord) -> WorkflowRun:
    return WorkflowRun(
        id=record.id,
        tenant_id=record.tenant_id,
        workflow_id=record.workflow_id,
        workflow_revision=record.workflow_revision,
        triggered_by=record.triggered_by,
        permission_snapshot=record.permission_snapshot,
        trace_id=record.trace_id,
        status=record.status,
        input=record.input,
        output=record.output,
        node_executions=record.node_executions,
        pending_approval=record.pending_approval,
        error=record.error,
        started_at=record.started_at,
        finished_at=record.finished_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _event_from_record(record: WorkflowRunEventRecord) -> RunEvent:
    return RunEvent(
        id=record.id,
        tenant_id=record.tenant_id,
        run_id=record.run_id,
        sequence=record.sequence,
        type=record.type,
        occurred_at=record.occurred_at,
        node_id=record.node_id,
        data=record.data,
    )


def _event_records(
    run: WorkflowRun,
    *,
    previous_sequence: int,
    writes: list[RunEventWrite],
) -> list[WorkflowRunEventRecord]:
    occurred_at = datetime.now(UTC)
    return [
        WorkflowRunEventRecord(
            id=uuid4(),
            tenant_id=run.tenant_id,
            run_id=run.id,
            sequence=previous_sequence + offset,
            type=write.type.value,
            occurred_at=occurred_at,
            node_id=write.node_id,
            data=deepcopy(write.data) if write.data else {},
        )
        for offset, write in enumerate(writes, start=1)
    ]


class SQLAlchemyWorkflowRunRepository:
    """Tenant-scoped run state and durable, strictly ordered events.

    Event append locks the parent run row before reading the latest sequence.
    Concurrent writers for the same run are therefore serialized, while
    different runs remain independent.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        poll_interval: float = 0.25,
    ) -> None:
        self._sessions = session_factory
        self._poll_interval = poll_interval

    async def create_with_event(
        self,
        run: WorkflowRun,
        *,
        event: RunEventWrite,
    ) -> tuple[WorkflowRun, RunEvent]:
        run_record = _run_to_record(run)
        event_record = _event_records(run, previous_sequence=0, writes=[event])[0]
        async with self._sessions() as session:
            async with session.begin():
                session.add_all([run_record, event_record])
                await session.flush()
        return _run_from_record(run_record), _event_from_record(event_record)

    async def transition_with_events(
        self,
        run: WorkflowRun,
        *,
        expected_updated_at: datetime,
        events: list[RunEventWrite],
    ) -> tuple[WorkflowRun, list[RunEvent]]:
        if not events:
            raise ValueError("a workflow run transition must append at least one event")
        event_records: list[WorkflowRunEventRecord]
        async with self._sessions() as session:
            async with session.begin():
                current = (
                    await session.execute(
                        select_workflow_run(run.tenant_id, run.id, for_update=True)
                    )
                ).scalar_one_or_none()
                if current is None:
                    raise KeyError(run.id)
                if current.updated_at != expected_updated_at:
                    raise StaleWorkflowRunError(
                        run.id,
                        expected_updated_at=expected_updated_at,
                        actual_updated_at=current.updated_at,
                    )
                latest = await session.scalar(
                    select(func.max(WorkflowRunEventRecord.sequence)).where(
                        WorkflowRunEventRecord.tenant_id == run.tenant_id,
                        WorkflowRunEventRecord.run_id == run.id,
                    )
                )
                event_records = _event_records(
                    run,
                    previous_sequence=int(latest or 0),
                    writes=events,
                )
                _apply_run(current, run)
                session.add_all(event_records)
                await session.flush()
        return _run_from_record(current), [
            _event_from_record(record) for record in event_records
        ]

    async def get(self, tenant_id: UUID, run_id: UUID) -> WorkflowRun | None:
        async with self._sessions() as session:
            record = (
                await session.execute(select_workflow_run(tenant_id, run_id))
            ).scalar_one_or_none()
            return None if record is None else _run_from_record(record)

    async def list_runs(
        self,
        tenant_id: UUID,
        *,
        workflow_id: UUID | None = None,
        limit: int,
        offset: int,
    ) -> tuple[list[WorkflowRun], int]:
        predicates = [WorkflowRunRecord.tenant_id == tenant_id]
        if workflow_id is not None:
            predicates.append(WorkflowRunRecord.workflow_id == workflow_id)
        async with self._sessions() as session:
            count = await session.scalar(
                select(func.count()).select_from(WorkflowRunRecord).where(*predicates)
            )
            rows = (
                await session.scalars(
                    list_workflow_runs(
                        tenant_id,
                        workflow_id=workflow_id,
                        limit=limit,
                        offset=offset,
                    )
                )
            ).all()
            return [_run_from_record(row) for row in rows], int(count or 0)

    async def list_events(
        self,
        tenant_id: UUID,
        run_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[RunEvent]:
        async with self._sessions() as session:
            exists = (
                await session.execute(select_workflow_run(tenant_id, run_id))
            ).scalar_one_or_none()
            if exists is None:
                return []
            rows = (
                await session.scalars(
                    list_workflow_run_events(
                        tenant_id,
                        run_id,
                        after_sequence=after_sequence,
                        limit=limit,
                    )
                )
            ).all()
            return [_event_from_record(row) for row in rows]

    @asynccontextmanager
    async def subscribe(
        self, run_id: UUID, *, after_sequence: int = 0
    ) -> AsyncIterator[AsyncIterator[RunEvent]]:
        yield self._poll_events(run_id, after_sequence=after_sequence)

    async def _poll_events(
        self, run_id: UUID, *, after_sequence: int
    ) -> AsyncIterator[RunEvent]:
        """Replay then follow durable events.

        `subscribe` is called only after the service performs a tenant-scoped
        `get`. Run ids are globally unique primary keys; polling keeps streams
        functional across multiple API processes without process-local queues.
        """
        cursor = after_sequence
        while True:
            async with self._sessions() as session:
                rows = (
                    await session.scalars(
                        select(WorkflowRunEventRecord)
                        .where(
                            WorkflowRunEventRecord.run_id == run_id,
                            WorkflowRunEventRecord.sequence > cursor,
                        )
                        .order_by(WorkflowRunEventRecord.sequence.asc())
                        .limit(MAX_EVENT_PAGE)
                    )
                ).all()
            if not rows:
                await asyncio.sleep(self._poll_interval)
                continue
            for row in rows:
                event = _event_from_record(row)
                cursor = event.sequence
                yield event
