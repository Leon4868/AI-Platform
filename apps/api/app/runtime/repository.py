"""Run storage plus the append-only event log the streaming layer reads from.

The event log is the ordering authority: sequences are handed out under a lock,
start at 1 and never repeat for a run, so a client can resume from a cursor and
be sure it missed nothing.
"""

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.runtime.schemas import RunEvent, RunEventType, WorkflowRun

MAX_EVENT_PAGE = 500


class WorkflowRunRepository(Protocol):
    async def add(self, run: WorkflowRun) -> WorkflowRun: ...

    async def get(self, tenant_id: UUID, run_id: UUID) -> WorkflowRun | None: ...

    async def list_runs(
        self,
        tenant_id: UUID,
        *,
        workflow_id: UUID | None = None,
        limit: int,
        offset: int,
    ) -> tuple[list[WorkflowRun], int]: ...

    async def update(self, run: WorkflowRun) -> WorkflowRun: ...

    async def append_event(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        event_type: RunEventType,
        node_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> RunEvent: ...

    async def list_events(
        self,
        tenant_id: UUID,
        run_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[RunEvent]: ...

    def subscribe(
        self, run_id: UUID, *, after_sequence: int = 0
    ) -> AbstractAsyncContextManager[AsyncIterator[RunEvent]]: ...


class InMemoryWorkflowRunRepository:
    """Development store with tenant isolation and copy-on-read semantics.

    The read methods are `list_runs`/`list_events` rather than a bare `list`:
    binding that name in a class body shadows the builtin for every deferred
    annotation in the same body, and `list[RunEvent]` below would resolve to the
    method (TypeError on Python 3.14).
    """

    def __init__(self) -> None:
        self._runs: dict[UUID, WorkflowRun] = {}
        self._events: dict[UUID, list[RunEvent]] = defaultdict(list)
        self._subscribers: dict[UUID, list[asyncio.Queue[RunEvent]]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def add(self, run: WorkflowRun) -> WorkflowRun:
        async with self._lock:
            if run.id in self._runs:
                raise ValueError(f"duplicate run id: {run.id}")
            self._runs[run.id] = run.model_copy(deep=True)
        return run.model_copy(deep=True)

    async def get(self, tenant_id: UUID, run_id: UUID) -> WorkflowRun | None:
        run = self._runs.get(run_id)
        if run is None or run.tenant_id != tenant_id:
            return None
        return run.model_copy(deep=True)

    async def list_runs(
        self,
        tenant_id: UUID,
        *,
        workflow_id: UUID | None = None,
        limit: int,
        offset: int,
    ) -> tuple[list[WorkflowRun], int]:
        runs = [
            run
            for run in self._runs.values()
            if run.tenant_id == tenant_id and (workflow_id is None or run.workflow_id == workflow_id)
        ]
        runs.sort(key=lambda run: (run.created_at, str(run.id)), reverse=True)
        page = [run.model_copy(deep=True) for run in runs[offset : offset + limit]]
        return page, len(runs)

    async def update(self, run: WorkflowRun) -> WorkflowRun:
        async with self._lock:
            current = self._runs.get(run.id)
            if current is None or current.tenant_id != run.tenant_id:
                raise KeyError(run.id)
            self._runs[run.id] = run.model_copy(deep=True)
        return run.model_copy(deep=True)

    async def delete(self, tenant_id: UUID, run_id: UUID) -> bool:
        async with self._lock:
            current = self._runs.get(run_id)
            if current is None or current.tenant_id != tenant_id:
                return False
            del self._runs[run_id]
            self._events.pop(run_id, None)
            return True

    async def append_event(
        self,
        *,
        tenant_id: UUID,
        run_id: UUID,
        event_type: RunEventType,
        node_id: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> RunEvent:
        async with self._lock:
            log = self._events[run_id]
            event = RunEvent(
                id=uuid4(),
                tenant_id=tenant_id,
                run_id=run_id,
                sequence=len(log) + 1,
                type=event_type,
                occurred_at=datetime.now(UTC),
                node_id=node_id,
                data=deepcopy(data) if data else {},
            )
            log.append(event)
            for queue in self._subscribers[run_id]:
                queue.put_nowait(event)
        return event.model_copy(deep=True)

    async def list_events(
        self,
        tenant_id: UUID,
        run_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int = 100,
    ) -> list[RunEvent]:
        run = self._runs.get(run_id)
        if run is None or run.tenant_id != tenant_id:
            return []
        window = [event for event in self._events[run_id] if event.sequence > after_sequence]
        return [event.model_copy(deep=True) for event in window[: min(limit, MAX_EVENT_PAGE)]]

    @asynccontextmanager
    async def subscribe(
        self, run_id: UUID, *, after_sequence: int = 0
    ) -> AsyncIterator[AsyncIterator[RunEvent]]:
        """Replays the log from a cursor, then follows live appends.

        Registering the queue before reading the backlog is what closes the gap:
        an event appended mid-handover arrives twice and the duplicate is
        dropped by sequence.
        """
        queue: asyncio.Queue[RunEvent] = asyncio.Queue()
        async with self._lock:
            self._subscribers[run_id].append(queue)
            backlog = [event for event in self._events[run_id] if event.sequence > after_sequence]
        try:
            yield _replay_then_follow(backlog, queue)
        finally:
            async with self._lock:
                subscribers = self._subscribers[run_id]
                if queue in subscribers:
                    subscribers.remove(queue)
                if not subscribers:
                    self._subscribers.pop(run_id, None)


async def _replay_then_follow(
    backlog: list[RunEvent], queue: asyncio.Queue[RunEvent]
) -> AsyncIterator[RunEvent]:
    last_sequence = 0
    for event in backlog:
        last_sequence = event.sequence
        yield event.model_copy(deep=True)
    while True:
        event = await queue.get()
        if event.sequence <= last_sequence:
            continue
        last_sequence = event.sequence
        yield event.model_copy(deep=True)
