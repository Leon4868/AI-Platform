import asyncio
from datetime import UTC, datetime, timedelta
from io import StringIO
from pathlib import Path
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy.dialects import postgresql

from app.core.tables import WorkflowRunEventRecord, WorkflowRunRecord
from app.runtime.schemas import PermissionSnapshot, RunEventType, RunStatus, WorkflowRun
from app.runtime.repository import (
    InMemoryWorkflowRunRepository,
    RunEventWrite,
    StaleWorkflowRunError,
)
from app.runtime.sql_repository import (
    SQLAlchemyWorkflowRunRepository,
    _run_from_record,
    _run_to_record,
    list_workflow_run_events,
    list_workflow_runs,
    select_workflow_run,
)

API_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = API_ROOT / "alembic.ini"
TENANT_ID = UUID("00000000-0000-4000-8000-000000000010")
ACTOR_ID = UUID("00000000-0000-4000-8000-000000000001")


def workflow_run() -> WorkflowRun:
    timestamp = datetime.now(UTC)
    return WorkflowRun(
        id=uuid4(),
        tenant_id=TENANT_ID,
        workflow_id=uuid4(),
        workflow_revision=3,
        triggered_by=ACTOR_ID,
        permission_snapshot=PermissionSnapshot(
            subject_id=ACTOR_ID,
            department_ids=["dept-product"],
            project_ids=["project-alpha"],
            roles=["employee"],
            allowed_scopes=["department"],
            security_clearance="internal",
            captured_at=timestamp,
            policy_version="rbac-v2",
        ),
        trace_id=uuid4(),
        status="waiting_human",
        input={"topic": "产品周报"},
        node_executions=[
            {
                "node_id": "review",
                "node_type": "approval",
                "status": "waiting_human",
                "started_at": timestamp,
            }
        ],
        pending_approval={
            "node_id": "review",
            "prompt": "请审核",
            "approvers": ["reviewer"],
            "requested_at": timestamp,
        },
        started_at=timestamp,
        created_at=timestamp,
        updated_at=timestamp,
    )


def _sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect())).lower()


def test_workflow_run_record_round_trip_preserves_nested_state() -> None:
    run = workflow_run()
    restored = _run_from_record(_run_to_record(run))
    assert restored == run
    assert restored is not run


def test_in_memory_atomic_create_transition_and_subscriber_notification() -> None:
    async def scenario() -> None:
        repository = InMemoryWorkflowRunRepository()
        run = workflow_run()
        created, first = await repository.create_with_event(
            run,
            event=RunEventWrite(type=RunEventType.RUN_QUEUED, data={"phase": "admitted"}),
        )
        assert created == run
        assert first.sequence == 1

        async with repository.subscribe(run.id) as stream:
            assert (await anext(stream)).sequence == 1
            updated = run.model_copy(
                update={
                    "status": RunStatus.RUNNING,
                    "updated_at": run.updated_at + timedelta(seconds=1),
                }
            )
            persisted, events = await repository.transition_with_events(
                updated,
                expected_updated_at=run.updated_at,
                events=[
                    RunEventWrite(type=RunEventType.RUN_STARTED),
                    RunEventWrite(
                        type=RunEventType.NODE_STARTED,
                        node_id="review",
                        data={"attempt": 1},
                    ),
                ],
            )
            assert persisted == updated
            assert [event.sequence for event in events] == [2, 3]
            assert [(await anext(stream)).sequence, (await anext(stream)).sequence] == [2, 3]

        stored_events = await repository.list_events(TENANT_ID, run.id, limit=100)
        assert [event.sequence for event in stored_events] == [1, 2, 3]

    asyncio.run(scenario())


def test_in_memory_atomic_transition_rejects_stale_writer_without_events() -> None:
    async def scenario() -> None:
        repository = InMemoryWorkflowRunRepository()
        run = workflow_run()
        await repository.create_with_event(
            run,
            event=RunEventWrite(type=RunEventType.RUN_QUEUED),
        )
        updated = run.model_copy(
            update={
                "status": RunStatus.RUNNING,
                "updated_at": run.updated_at + timedelta(seconds=1),
            }
        )
        try:
            await repository.transition_with_events(
                updated,
                expected_updated_at=run.updated_at - timedelta(seconds=1),
                events=[RunEventWrite(type=RunEventType.RUN_STARTED)],
            )
        except StaleWorkflowRunError as exc:
            assert exc.run_id == run.id
            assert exc.actual_updated_at == run.updated_at
        else:
            raise AssertionError("stale workflow transition must fail")

        assert await repository.get(TENANT_ID, run.id) == run
        assert len(await repository.list_events(TENANT_ID, run.id, limit=100)) == 1

    asyncio.run(scenario())


def test_run_repositories_do_not_expose_split_state_and_event_writes() -> None:
    """All production writers must use the atomic transition API."""
    for repository_type in (
        InMemoryWorkflowRunRepository,
        SQLAlchemyWorkflowRunRepository,
    ):
        assert not hasattr(repository_type, "add")
        assert not hasattr(repository_type, "update")
        assert not hasattr(repository_type, "append_event")


def test_run_and_event_queries_are_tenant_scoped_and_cursor_ordered() -> None:
    run_id = uuid4()
    workflow_id = uuid4()
    get_sql = _sql(select_workflow_run(TENANT_ID, run_id, for_update=True))
    runs_sql = _sql(
        list_workflow_runs(
            TENANT_ID,
            workflow_id=workflow_id,
            limit=20,
            offset=10,
        )
    )
    events_sql = _sql(
        list_workflow_run_events(
            TENANT_ID,
            run_id,
            after_sequence=7,
            limit=100,
        )
    )

    assert "tenant_id" in get_sql and "for update" in get_sql
    assert "tenant_id" in runs_sql and "workflow_id" in runs_sql
    assert " limit " in runs_sql and " offset " in runs_sql
    assert "tenant_id" in events_sql and "sequence >" in events_sql
    assert "order by workflow_run_events.sequence asc" in events_sql


class _ExecuteResult:
    def __init__(self, value) -> None:
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _AtomicTransaction:
    def __init__(self, state: dict) -> None:
        self._state = state
        self._run_before = None
        self._events_before = []

    async def __aenter__(self):
        self._state["transactions"] += 1
        current = self._state["run"]
        self._run_before = None if current is None else _run_from_record(current)
        self._events_before = list(self._state["events"])
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        del exc, traceback
        if exc_type is not None:
            self._state["rollbacks"] += 1
            self._state["run"] = (
                None if self._run_before is None else _run_to_record(self._run_before)
            )
            self._state["events"] = self._events_before


class _AtomicSession:
    def __init__(self, state: dict) -> None:
        self._state = state
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback

    def begin(self):
        return _AtomicTransaction(self._state)

    async def execute(self, statement):
        self.statements.append(statement)
        return _ExecuteResult(self._state["run"])

    async def scalar(self, statement):
        self.statements.append(statement)
        events = self._state["events"]
        return events[-1].sequence if events else None

    def add_all(self, records) -> None:
        for record in records:
            if isinstance(record, WorkflowRunRecord):
                self._state["run"] = record
            else:
                self._state["events"].append(record)

    async def flush(self) -> None:
        self._state["flushes"] += 1
        if self._state["fail_flush"]:
            raise RuntimeError("simulated event insert failure")


class _AtomicSessionFactory:
    def __init__(
        self,
        *,
        run: WorkflowRun | None = None,
        events: list[WorkflowRunEventRecord] | None = None,
        fail_flush: bool = False,
    ) -> None:
        self.state = {
            "run": None if run is None else _run_to_record(run),
            "events": list(events or []),
            "fail_flush": fail_flush,
            "transactions": 0,
            "rollbacks": 0,
            "flushes": 0,
        }
        self.sessions = []

    def __call__(self):
        session = _AtomicSession(self.state)
        self.sessions.append(session)
        return session


def _event_record(run: WorkflowRun, sequence: int) -> WorkflowRunEventRecord:
    return WorkflowRunEventRecord(
        id=uuid4(),
        tenant_id=run.tenant_id,
        run_id=run.id,
        sequence=sequence,
        type=RunEventType.RUN_QUEUED.value,
        occurred_at=datetime.now(UTC),
        data={},
    )


def test_sql_atomic_create_rolls_back_run_when_first_event_fails() -> None:
    run = workflow_run()
    factory = _AtomicSessionFactory(fail_flush=True)
    repository = SQLAlchemyWorkflowRunRepository(factory)

    try:
        asyncio.run(
            repository.create_with_event(
                run,
                event=RunEventWrite(type=RunEventType.RUN_QUEUED),
            )
        )
    except RuntimeError as exc:
        assert "event insert failure" in str(exc)
    else:
        raise AssertionError("failed first event must abort run creation")

    assert factory.state["transactions"] == 1
    assert factory.state["rollbacks"] == 1
    assert factory.state["run"] is None
    assert factory.state["events"] == []


def test_sql_atomic_transition_appends_multiple_contiguous_events() -> None:
    run = workflow_run()
    factory = _AtomicSessionFactory(run=run, events=[_event_record(run, 1)])
    repository = SQLAlchemyWorkflowRunRepository(factory)
    updated = run.model_copy(
        update={
            "status": RunStatus.RUNNING,
            "updated_at": run.updated_at + timedelta(seconds=1),
        }
    )

    persisted, events = asyncio.run(
        repository.transition_with_events(
            updated,
            expected_updated_at=run.updated_at,
            events=[
                RunEventWrite(type=RunEventType.RUN_STARTED),
                RunEventWrite(type=RunEventType.NODE_STARTED, node_id="review"),
                RunEventWrite(type=RunEventType.NODE_SUCCEEDED, node_id="review"),
            ],
        )
    )

    assert persisted == updated
    assert [event.sequence for event in events] == [2, 3, 4]
    assert [record.sequence for record in factory.state["events"]] == [1, 2, 3, 4]
    assert factory.state["transactions"] == 1
    assert factory.state["flushes"] == 1
    lock_sql = _sql(factory.sessions[0].statements[0])
    assert "tenant_id" in lock_sql and "for update" in lock_sql


def test_sql_atomic_transition_rejects_stale_writer_before_mutation() -> None:
    run = workflow_run()
    factory = _AtomicSessionFactory(run=run, events=[_event_record(run, 1)])
    repository = SQLAlchemyWorkflowRunRepository(factory)
    updated = run.model_copy(
        update={
            "status": RunStatus.RUNNING,
            "updated_at": run.updated_at + timedelta(seconds=1),
        }
    )

    try:
        asyncio.run(
            repository.transition_with_events(
                updated,
                expected_updated_at=run.updated_at - timedelta(seconds=1),
                events=[RunEventWrite(type=RunEventType.RUN_STARTED)],
            )
        )
    except StaleWorkflowRunError as exc:
        assert exc.actual_updated_at == run.updated_at
    else:
        raise AssertionError("stale workflow transition must fail")

    assert _run_from_record(factory.state["run"]) == run
    assert len(factory.state["events"]) == 1
    assert factory.state["flushes"] == 0


def test_sql_atomic_transition_rolls_back_state_when_any_event_fails() -> None:
    run = workflow_run()
    factory = _AtomicSessionFactory(
        run=run,
        events=[_event_record(run, 1)],
        fail_flush=True,
    )
    repository = SQLAlchemyWorkflowRunRepository(factory)
    updated = run.model_copy(
        update={
            "status": RunStatus.RUNNING,
            "updated_at": run.updated_at + timedelta(seconds=1),
        }
    )

    try:
        asyncio.run(
            repository.transition_with_events(
                updated,
                expected_updated_at=run.updated_at,
                events=[
                    RunEventWrite(type=RunEventType.RUN_STARTED),
                    RunEventWrite(type=RunEventType.NODE_STARTED, node_id="review"),
                ],
            )
        )
    except RuntimeError as exc:
        assert "event insert failure" in str(exc)
    else:
        raise AssertionError("failed event batch must abort the state transition")

    assert factory.state["rollbacks"] == 1
    assert _run_from_record(factory.state["run"]) == run
    assert [record.sequence for record in factory.state["events"]] == [1]


def test_workflow_runtime_table_constraints_encode_order_and_tenant_boundary() -> None:
    event_constraints = WorkflowRunEventRecord.__table__.constraints
    assert any(
        constraint.name == "uq_workflow_run_events_tenant_run_sequence"
        for constraint in event_constraints
    )
    foreign_keys = WorkflowRunEventRecord.__table__.foreign_key_constraints
    assert len(foreign_keys) == 1
    assert next(iter(foreign_keys)).ondelete == "RESTRICT"
    assert any(
        constraint.name == "uq_workflow_runs_tenant_id"
        for constraint in WorkflowRunRecord.__table__.constraints
    )
    run_foreign_keys = WorkflowRunRecord.__table__.foreign_key_constraints
    assert len(run_foreign_keys) == 1
    assert next(iter(run_foreign_keys)).ondelete == "RESTRICT"


def test_workflow_runtime_migration_compiles_upgrade_and_downgrade_offline() -> None:
    upgrade_output = StringIO()
    upgrade_config = Config(str(ALEMBIC_INI), output_buffer=upgrade_output)
    command.upgrade(upgrade_config, "20260825_0002", sql=True)
    upgrade_sql = upgrade_output.getvalue().lower()
    assert "create table workflow_runs" in upgrade_sql
    assert "create table workflow_run_events" in upgrade_sql
    assert "uq_workflow_run_events_tenant_run_sequence" in upgrade_sql
    assert upgrade_sql.count("on delete restrict") >= 2

    downgrade_output = StringIO()
    downgrade_config = Config(str(ALEMBIC_INI), output_buffer=downgrade_output)
    command.downgrade(downgrade_config, "20260825_0002:20260825_0001", sql=True)
    downgrade_sql = downgrade_output.getvalue().lower()
    assert "drop table workflow_run_events" in downgrade_sql
    assert "drop table workflow_runs" in downgrade_sql
