"""Runtime package tests. Run with: pytest app/runtime/tests"""

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.audit.service import AuditService
from app.core.errors import ConflictError, NotFoundError
from app.core.repository import InMemoryRepository
from app.identity.schemas import Permission, Principal
from app.runtime.executor import ExecutionPlan, InProcessGraphExecutor, LangGraphExecutor
from app.runtime.repository import InMemoryWorkflowRunRepository
from app.runtime.schemas import (
    ApprovalDecision,
    NodeRunStatus,
    RunApprovalRequest,
    RunEventType,
    RunStartRequest,
    RunStatus,
)
from app.runtime.service import WorkflowRunService
from app.workflows.schemas import WorkflowDefinition, WorkflowGraph
from app.workflows.validator import WorkflowGraphValidator

TENANT_ID = UUID("11111111-1111-1111-1111-111111111111")
USER_ID = UUID("22222222-2222-2222-2222-222222222222")


def principal() -> Principal:
    return Principal(
        user_id=USER_ID,
        tenant_id=TENANT_ID,
        display_name="tester",
        permissions=frozenset({Permission.WORKFLOW_READ, Permission.WORKFLOW_WRITE}),
    )


def node(node_id: str, node_type: str, x: float = 0, **config) -> dict:
    return {
        "id": node_id,
        "type": node_type,
        "name": node_id,
        "position": {"x": x, "y": 0},
        "config": config,
    }


def linear_graph() -> dict:
    return {
        "nodes": [
            node("start", "start"),
            node("retrieve", "knowledge_retrieval", 200, top_k=2, query="政策"),
            node("draft", "model", 400, model="mock-model", prompt="写一段"),
            node("publish", "tool", 600, tool="publisher"),
            node("end", "end", 800),
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "retrieve"},
            {"id": "e2", "source": "retrieve", "target": "draft"},
            {"id": "e3", "source": "draft", "target": "publish"},
            {"id": "e4", "source": "publish", "target": "end"},
        ],
    }


def approval_graph() -> dict:
    return {
        "nodes": [
            node("start", "start"),
            node("review", "approval", 200, prompt="请审批", approvers=["ops"]),
            node("end", "end", 400),
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "review"},
            {"id": "e2", "source": "review", "target": "end"},
        ],
    }


def condition_graph() -> dict:
    return {
        "nodes": [
            node("start", "start"),
            node("route", "condition", 200, branch="vip"),
            node("vip_reply", "model", 400),
            node("basic_reply", "model", 400),
            node("end", "end", 600),
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "route"},
            {"id": "e2", "source": "route", "target": "basic_reply", "label": "basic"},
            {"id": "e3", "source": "route", "target": "vip_reply", "label": "vip"},
            {"id": "e4", "source": "vip_reply", "target": "end"},
            {"id": "e5", "source": "basic_reply", "target": "end"},
        ],
    }


class Harness:
    def __init__(self) -> None:
        self.workflows: InMemoryRepository[WorkflowDefinition] = InMemoryRepository()
        self.runs = InMemoryWorkflowRunRepository()
        self.service = WorkflowRunService(
            self.workflows, self.runs, InProcessGraphExecutor(), AuditService()
        )

    async def publish(self, graph: dict) -> WorkflowDefinition:
        now = datetime.now(UTC)
        workflow = WorkflowDefinition(
            id=uuid4(),
            tenant_id=TENANT_ID,
            owner_id=USER_ID,
            name="流程",
            description="",
            graph=WorkflowGraph.model_validate(graph),
            revision=1,
            created_at=now,
            updated_at=now,
        )
        return await self.workflows.add(workflow)

    async def events_until_end(self, run_id: UUID) -> list:
        return [event async for event in self.service.stream(TENANT_ID, run_id)]

    async def wait_for(self, run_id: UUID, status: RunStatus):
        for _ in range(500):
            run = await self.service.get(TENANT_ID, run_id)
            if run.status is status:
                return run
            await asyncio.sleep(0)
        raise AssertionError(f"run never reached {status}")


def outputs_by_node(run) -> dict:
    return {item.node_id: item.output for item in run.node_executions}


def test_graphs_are_accepted_by_the_workflow_validator() -> None:
    validator = WorkflowGraphValidator()
    for graph in (linear_graph(), approval_graph(), condition_graph()):
        result = validator.validate(WorkflowGraph.model_validate(graph))
        assert result.valid, result.errors


def test_linear_run_succeeds_with_monotonic_events() -> None:
    async def scenario() -> None:
        harness = Harness()
        workflow = await harness.publish(linear_graph())
        run = await harness.service.start(principal(), workflow.id, RunStartRequest(input={"q": "你好"}))
        assert run.status is RunStatus.QUEUED

        events = await harness.events_until_end(run.id)
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))
        assert events[0].type is RunEventType.RUN_QUEUED
        assert events[1].type is RunEventType.RUN_STARTED
        assert events[-1].type is RunEventType.RUN_SUCCEEDED

        finished = await harness.service.get(TENANT_ID, run.id)
        assert finished.status is RunStatus.SUCCEEDED
        assert [item.node_id for item in finished.node_executions] == [
            "start",
            "retrieve",
            "draft",
            "publish",
            "end",
        ]
        assert all(item.status is NodeRunStatus.SUCCEEDED for item in finished.node_executions)
        assert len(outputs_by_node(finished)["retrieve"]["chunks"]) == 2
        assert outputs_by_node(finished)["draft"]["provider"] == "mock"
        assert "end" in finished.output["ends"]

    asyncio.run(scenario())


def test_same_input_produces_identical_node_outputs() -> None:
    async def scenario() -> None:
        harness = Harness()
        workflow = await harness.publish(linear_graph())
        first = await harness.service.start(principal(), workflow.id, RunStartRequest(input={"q": "你好"}))
        await harness.events_until_end(first.id)
        second = await harness.service.start(principal(), workflow.id, RunStartRequest(input={"q": "你好"}))
        await harness.events_until_end(second.id)
        other = await harness.service.start(principal(), workflow.id, RunStartRequest(input={"q": "别的"}))
        await harness.events_until_end(other.id)

        first_outputs = outputs_by_node(await harness.service.get(TENANT_ID, first.id))
        second_outputs = outputs_by_node(await harness.service.get(TENANT_ID, second.id))
        other_outputs = outputs_by_node(await harness.service.get(TENANT_ID, other.id))
        assert first_outputs == second_outputs
        assert first_outputs["draft"]["content"] != other_outputs["draft"]["content"]

    asyncio.run(scenario())


def test_condition_takes_one_branch_and_skips_the_other() -> None:
    async def scenario() -> None:
        harness = Harness()
        workflow = await harness.publish(condition_graph())
        run = await harness.service.start(principal(), workflow.id, RunStartRequest())
        await harness.events_until_end(run.id)

        finished = await harness.service.get(TENANT_ID, run.id)
        statuses = {item.node_id: item.status for item in finished.node_executions}
        assert finished.status is RunStatus.SUCCEEDED
        assert statuses["vip_reply"] is NodeRunStatus.SUCCEEDED
        assert statuses["basic_reply"] is NodeRunStatus.SKIPPED
        assert outputs_by_node(finished)["route"]["branch"] == "vip"

    asyncio.run(scenario())


def test_approval_pauses_the_run_until_a_decision_arrives() -> None:
    async def scenario() -> None:
        harness = Harness()
        workflow = await harness.publish(approval_graph())
        run = await harness.service.start(principal(), workflow.id, RunStartRequest())
        waiting = await harness.wait_for(run.id, RunStatus.WAITING_HUMAN)
        assert waiting.pending_approval is not None
        assert waiting.pending_approval.node_id == "review"
        assert waiting.pending_approval.prompt == "请审批"
        assert waiting.pending_approval.approvers == ["ops"]

        resumed = await harness.service.approve(
            principal(),
            run.id,
            RunApprovalRequest(node_id="review", decision=ApprovalDecision.APPROVED, comment="ok"),
        )
        assert resumed.status is RunStatus.RUNNING
        assert resumed.pending_approval is None

        events = await harness.events_until_end(run.id)
        types = [event.type for event in events]
        assert RunEventType.NODE_AWAITING_APPROVAL in types
        assert RunEventType.NODE_RESUMED in types
        assert types[-1] is RunEventType.RUN_SUCCEEDED

        finished = await harness.service.get(TENANT_ID, run.id)
        assert outputs_by_node(finished)["review"]["decision"] == "approved"
        assert outputs_by_node(finished)["review"]["decided_by"] == str(USER_ID)

    asyncio.run(scenario())


def test_rejected_approval_fails_the_run() -> None:
    async def scenario() -> None:
        harness = Harness()
        workflow = await harness.publish(approval_graph())
        run = await harness.service.start(principal(), workflow.id, RunStartRequest())
        await harness.wait_for(run.id, RunStatus.WAITING_HUMAN)
        await harness.service.approve(
            principal(),
            run.id,
            RunApprovalRequest(node_id="review", decision=ApprovalDecision.REJECTED, comment="不通过"),
        )
        events = await harness.events_until_end(run.id)
        assert events[-1].type is RunEventType.RUN_FAILED

        finished = await harness.service.get(TENANT_ID, run.id)
        assert finished.status is RunStatus.FAILED
        assert finished.error is not None
        assert finished.error.code == "approval_rejected"
        assert finished.error.node_id == "review"

    asyncio.run(scenario())


def test_approval_on_a_finished_run_conflicts() -> None:
    async def scenario() -> None:
        harness = Harness()
        workflow = await harness.publish(linear_graph())
        run = await harness.service.start(principal(), workflow.id, RunStartRequest())
        await harness.events_until_end(run.id)
        with pytest.raises(ConflictError):
            await harness.service.approve(
                principal(),
                run.id,
                RunApprovalRequest(node_id="draft", decision=ApprovalDecision.APPROVED),
            )

    asyncio.run(scenario())


def test_cancel_is_idempotent() -> None:
    async def scenario() -> None:
        harness = Harness()
        workflow = await harness.publish(approval_graph())
        run = await harness.service.start(principal(), workflow.id, RunStartRequest())
        await harness.wait_for(run.id, RunStatus.WAITING_HUMAN)

        cancelled = await harness.service.cancel(principal(), run.id, reason="运营撤回")
        assert cancelled.status is RunStatus.CANCELLED
        assert cancelled.pending_approval is None
        assert all(
            item.status is not NodeRunStatus.WAITING_HUMAN for item in cancelled.node_executions
        )
        events = await harness.runs.list_events(TENANT_ID, run.id, limit=500)

        assert events[-1].type is RunEventType.RUN_CANCELLED
        assert events[-1].data["reason"] == "运营撤回"
        assert [event.type for event in events[-2:]] == [
            RunEventType.NODE_CANCELLED,
            RunEventType.RUN_CANCELLED,
        ], "挂起节点应先收口再发 run.cancelled"
        assert events[-2].node_id == "review"
        assert events[-2].data["reason"] == "运营撤回"
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))

        again = await harness.service.cancel(principal(), run.id)
        assert again.status is RunStatus.CANCELLED
        assert again.finished_at == cancelled.finished_at
        assert await harness.runs.list_events(TENANT_ID, run.id, limit=500) == events

    asyncio.run(scenario())


def test_cancelling_a_finished_run_conflicts() -> None:
    async def scenario() -> None:
        harness = Harness()
        workflow = await harness.publish(linear_graph())
        run = await harness.service.start(principal(), workflow.id, RunStartRequest())
        await harness.events_until_end(run.id)
        with pytest.raises(ConflictError):
            await harness.service.cancel(principal(), run.id)

    asyncio.run(scenario())


def test_cancel_before_the_first_node_runs() -> None:
    async def scenario() -> None:
        harness = Harness()
        workflow = await harness.publish(linear_graph())
        run = await harness.service.start(principal(), workflow.id, RunStartRequest())
        cancelled = await harness.service.cancel(principal(), run.id)
        assert cancelled.status is RunStatus.CANCELLED
        events = await harness.runs.list_events(TENANT_ID, run.id, limit=500)
        assert events[-1].type is RunEventType.RUN_CANCELLED
        assert RunEventType.NODE_CANCELLED not in {event.type for event in events}, (
            "没有节点开跑时不应发 node.cancelled"
        )
        assert [event.sequence for event in events] == list(range(1, len(events) + 1))

    asyncio.run(scenario())


def test_cancelled_nodes_are_closed_out_in_the_replayed_stream() -> None:
    """A reconnecting client must see every node reach a terminal state."""

    async def scenario() -> None:
        harness = Harness()
        workflow = await harness.publish(approval_graph())
        run = await harness.service.start(principal(), workflow.id, RunStartRequest())
        await harness.wait_for(run.id, RunStatus.WAITING_HUMAN)
        await harness.service.cancel(principal(), run.id)

        replayed = [event async for event in harness.service.stream(TENANT_ID, run.id)]
        assert [event.sequence for event in replayed] == list(range(1, len(replayed) + 1))
        assert replayed[-1].type is RunEventType.RUN_CANCELLED

        opened = {event.node_id for event in replayed if event.type is RunEventType.NODE_STARTED}
        closed = {
            event.node_id
            for event in replayed
            if event.type
            in {
                RunEventType.NODE_SUCCEEDED,
                RunEventType.NODE_FAILED,
                RunEventType.NODE_CANCELLED,
            }
        }
        assert opened == closed, f"未收口的节点：{opened - closed}"

    asyncio.run(scenario())


def test_stream_replays_a_finished_run_from_a_cursor() -> None:
    async def scenario() -> None:
        harness = Harness()
        workflow = await harness.publish(linear_graph())
        run = await harness.service.start(principal(), workflow.id, RunStartRequest())
        events = await harness.events_until_end(run.id)

        replay = [event async for event in harness.service.stream(TENANT_ID, run.id, after_sequence=3)]
        assert [event.sequence for event in replay] == [event.sequence for event in events[3:]]

    asyncio.run(scenario())


def test_runs_are_tenant_isolated() -> None:
    async def scenario() -> None:
        harness = Harness()
        workflow = await harness.publish(linear_graph())
        run = await harness.service.start(principal(), workflow.id, RunStartRequest())
        await harness.events_until_end(run.id)
        with pytest.raises(NotFoundError):
            await harness.service.get(uuid4(), run.id)
        items, total = await harness.service.list_runs(TENANT_ID, workflow_id=workflow.id, limit=10, offset=0)
        assert total == 1 and items[0].id == run.id

    asyncio.run(scenario())


def test_starting_an_unknown_workflow_is_not_found() -> None:
    async def scenario() -> None:
        harness = Harness()
        with pytest.raises(NotFoundError):
            await harness.service.start(principal(), uuid4(), RunStartRequest())

    asyncio.run(scenario())


def test_cyclic_graph_fails_the_run_instead_of_hanging() -> None:
    async def scenario() -> None:
        graph = WorkflowGraph.model_validate(
            {
                "nodes": [
                    node("start", "start"),
                    node("a", "model", 200),
                    node("b", "model", 400),
                    node("end", "end", 600),
                ],
                "edges": [
                    {"id": "e1", "source": "start", "target": "a"},
                    {"id": "e2", "source": "a", "target": "b"},
                    {"id": "e3", "source": "b", "target": "a"},
                    {"id": "e4", "source": "b", "target": "end"},
                ],
            }
        )
        steps = [step async for step in InProcessGraphExecutor().run(ExecutionPlan(run_id=uuid4(), graph=graph))]
        assert len(steps) == 1
        assert steps[0].error is not None
        assert steps[0].error.code == "graph_not_acyclic"

    asyncio.run(scenario())


def test_langgraph_executor_requires_an_adapter() -> None:
    async def scenario() -> None:
        graph = WorkflowGraph.model_validate(linear_graph())
        with pytest.raises(RuntimeError):
            async for _ in LangGraphExecutor().run(ExecutionPlan(run_id=uuid4(), graph=graph)):
                pass

    asyncio.run(scenario())


def test_shutdown_closes_running_runs() -> None:
    async def scenario() -> None:
        harness = Harness()
        workflow = await harness.publish(approval_graph())
        run = await harness.service.start(principal(), workflow.id, RunStartRequest())
        await harness.wait_for(run.id, RunStatus.WAITING_HUMAN)
        await harness.service.aclose()
        closed = await harness.service.get(TENANT_ID, run.id)
        assert closed.status is RunStatus.CANCELLED
        assert all(
            item.status is NodeRunStatus.CANCELLED
            for item in closed.node_executions
            if item.node_id == "review"
        )

    asyncio.run(scenario())
