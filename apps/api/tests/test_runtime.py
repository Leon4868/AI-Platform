"""Workflow 运行时的跨模块 / 契约防线测试。

本文件由后端测试 Agent 独占（apps/api/tests/test_runtime.py），不修改其他文件。

分工：app/runtime/tests/test_runtime.py 由实现方维护、断言内部行为；本文件站在
packages/contracts 一侧，钉住"内部实现能不能满足对外契约"：
  * 运行状态枚举与契约一致；
  * 每个内部事件类型都有对外去向，且投影后的帧符合契约 runEvent 形状、不丢帧不断号；
  * 取消语义按 openapi：进行中 -> cancelled，已取消 -> 幂等，成功/失败 -> 409 冲突；
  * 未知 run 与跨租户在四个入口一律 NotFound。

无外部依赖：运行时节点处理器是确定性 mock（app/runtime/nodes.py），不触网、不调模型、
不读凭证；WorkflowRunService 的构造里根本没有模型网关这一环。
"""

from __future__ import annotations

import asyncio
import functools
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.audit.service import AuditService
from app.core.errors import ConflictError, NotFoundError
from app.core.repository import InMemoryRepository
from app.identity.schemas import Permission, Principal
from app.runtime import (
    TERMINAL_EVENT_TYPES,
    TERMINAL_RUN_STATUSES,
    ApprovalDecision,
    InMemoryWorkflowRunRepository,
    InProcessGraphExecutor,
    NodeExecution,
    NodeRunStatus,
    RunApprovalRequest,
    RunEvent,
    RunEventType,
    RunStartRequest,
    RunStatus,
    WorkflowRun,
    WorkflowRunService,
)
from app.workflows.schemas import (
    NodeType,
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowGraph,
    WorkflowNode,
)

CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "packages" / "contracts"

#: 内部事件类型 -> 对外帧名。内部按节点粒度、对外按运行粒度是既定决策，
#: 映射在 workflow_runs 适配层落地；这里只保证"每个内部事件都有去向"，不做静默过滤。
EVENT_TYPE_TO_WIRE = {
    RunEventType.RUN_QUEUED: "run.queued",
    RunEventType.RUN_STARTED: "run.started",
    RunEventType.NODE_STARTED: "node.started",
    RunEventType.NODE_AWAITING_APPROVAL: "run.waiting_human",
    RunEventType.NODE_RESUMED: "node.resumed",
    RunEventType.NODE_SUCCEEDED: "node.completed",
    RunEventType.NODE_SKIPPED: "node.skipped",
    RunEventType.NODE_FAILED: "node.failed",
    RunEventType.NODE_CANCELLED: "node.cancelled",
    RunEventType.RUN_SUCCEEDED: "run.completed",
    RunEventType.RUN_FAILED: "run.failed",
    RunEventType.RUN_CANCELLED: "run.cancelled",
}

#: 已决定补进 packages/contracts、但尚未落到 schema 里的帧名。补齐后本集合应清空。
PENDING_CONTRACT_ADDITIONS = frozenset({"node.resumed", "node.skipped"})

#: 契约里已有、运行时还没发的帧名。运行时已补齐 node.cancelled，应保持为空。
PENDING_RUNTIME_EVENTS: frozenset[str] = frozenset()

TENANT_ID = UUID("00000000-0000-4000-8000-000000000010")
OTHER_TENANT_ID = UUID("00000000-0000-4000-8000-000000000099")
RUN_INPUT = {"title": "产品周报", "instructions": "根据已授权的企业知识生成本周进展"}
SETTLE_TURNS = 500


def async_test(fn: Callable[..., Any]) -> Callable[..., Any]:
    """每个用例一个事件循环，避免为 pytest-asyncio 增加依赖。"""

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        return asyncio.run(fn(*args, **kwargs))

    return wrapper


def contract_defs() -> dict[str, Any]:
    schema_path = CONTRACTS_DIR / "schemas" / "workflow.schema.json"
    assert schema_path.exists(), f"契约文件缺失，对外防线无从谈起：{schema_path}"
    return json.loads(schema_path.read_text(encoding="utf-8"))["$defs"]


# --------------------------------------------------------------------------- #
# 事件不变量与对外投影
# --------------------------------------------------------------------------- #


def _sequence(event: Any) -> int:
    return int(event["sequence"] if isinstance(event, dict) else event.sequence)


def _run_id(event: Any) -> str:
    return str(event["runId"] if isinstance(event, dict) else event.run_id)


def assert_sequence_strictly_increasing(events: list[Any]) -> None:
    """同一 run 内 sequence 从 1 开始、严格递增、无重复无空洞。"""
    per_run: dict[str, list[int]] = {}
    for event in events:
        per_run.setdefault(_run_id(event), []).append(_sequence(event))
    for run_id, sequences in per_run.items():
        assert sequences == sorted(sequences), f"{run_id} 事件未按 sequence 排序: {sequences}"
        assert sequences == list(range(1, len(sequences) + 1)), (
            f"{run_id} sequence 必须从 1 开始严格递增且无空洞/重复: {sequences}"
        )


def assert_events_conform(events: list[RunEvent], run_id: UUID) -> None:
    assert events, "运行至少要产生一条事件"
    assert_sequence_strictly_increasing(events)
    assert {event.type for event in events} <= set(RunEventType)
    assert {_run_id(event) for event in events} == {str(run_id)}, "事件不得跨 run 泄漏"


def assert_terminal_last(events: list[RunEvent], expected: RunEventType) -> None:
    terminal = [event for event in events if event.type in TERMINAL_EVENT_TYPES]
    assert len(terminal) == 1, f"终态事件必须恰好一条，实际 {[event.type.value for event in terminal]}"
    assert events[-1].type is expected, f"末事件应为 {expected.value}，实际 {events[-1].type.value}"


def wire_frame(event: RunEvent) -> dict[str, Any]:
    """参考投影，仅用于证明内部事件带齐了对外帧所需的信息。

    生产 mapper 由 workflow_runs 适配层实现，这里既不代替它、也不假设它已存在。
    """
    frame: dict[str, Any] = {
        "sequence": event.sequence,
        "runId": str(event.run_id),
        "type": EVENT_TYPE_TO_WIRE[event.type],
        "occurredAt": event.occurred_at.isoformat().replace("+00:00", "Z"),
        "payload": event.data,
    }
    if event.node_id is not None:
        frame["nodeId"] = event.node_id
    return frame


def assert_frame_matches_contract(frame: dict[str, Any]) -> None:
    definition = contract_defs()["runEvent"]
    allowed = set(definition["properties"])
    assert set(frame) >= set(definition["required"]), f"帧缺少契约必填字段: {frame}"
    assert set(frame) <= allowed, f"帧含契约未定义字段 {set(frame) - allowed}"
    assert isinstance(frame["sequence"], int) and frame["sequence"] >= 1
    assert frame["type"] in set(definition["properties"]["type"]["enum"]) | PENDING_CONTRACT_ADDITIONS


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #


def make_principal(tenant_id: UUID = TENANT_ID) -> Principal:
    return Principal(
        user_id=uuid4(),
        tenant_id=tenant_id,
        display_name="运行时测试用户",
        permissions=frozenset(Permission),
    )


def _definition(nodes: list[WorkflowNode], edges: list[WorkflowEdge], tenant_id: UUID) -> WorkflowDefinition:
    now = datetime.now(UTC)
    return WorkflowDefinition(
        id=uuid4(),
        tenant_id=tenant_id,
        owner_id=uuid4(),
        name="运行时测试流程",
        description="",
        graph=WorkflowGraph(nodes=nodes, edges=edges),
        revision=1,
        created_at=now,
        updated_at=now,
    )


def linear_definition(tenant_id: UUID = TENANT_ID) -> WorkflowDefinition:
    """start -> model -> end。"""
    return _definition(
        [
            WorkflowNode(id="start", type=NodeType.START, name="开始", position={"x": 0, "y": 0}),
            WorkflowNode(
                id="model",
                type=NodeType.MODEL,
                name="模型",
                position={"x": 200, "y": 0},
                config={"model": "logical-default", "prompt": "总结输入"},
            ),
            WorkflowNode(id="end", type=NodeType.END, name="结束", position={"x": 400, "y": 0}),
        ],
        [
            WorkflowEdge(id="e1", source="start", target="model"),
            WorkflowEdge(id="e2", source="model", target="end"),
        ],
        tenant_id,
    )


def approval_definition(tenant_id: UUID = TENANT_ID) -> WorkflowDefinition:
    """start -> approval -> end：审核节点处停在 waiting_human。"""
    return _definition(
        [
            WorkflowNode(id="start", type=NodeType.START, name="开始", position={"x": 0, "y": 0}),
            WorkflowNode(
                id="review",
                type=NodeType.APPROVAL,
                name="人工审核",
                position={"x": 200, "y": 0},
                config={"prompt": "请确认周报内容", "approvers": ["editor"]},
            ),
            WorkflowNode(id="end", type=NodeType.END, name="结束", position={"x": 400, "y": 0}),
        ],
        [
            WorkflowEdge(id="e1", source="start", target="review"),
            WorkflowEdge(id="e2", source="review", target="end"),
        ],
        tenant_id,
    )


class RuntimeDriver:
    """WorkflowRunService 加一套内存依赖；退出时收掉仍在飞的运行。"""

    def __init__(self) -> None:
        self.workflows: InMemoryRepository[WorkflowDefinition] = InMemoryRepository()
        self.runs = InMemoryWorkflowRunRepository()
        self.audit = AuditService()
        self.service = WorkflowRunService(
            self.workflows, self.runs, InProcessGraphExecutor(), self.audit
        )

    async def __aenter__(self) -> RuntimeDriver:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.service.aclose()

    async def start(self, principal: Principal, definition: WorkflowDefinition) -> WorkflowRun:
        await self.workflows.add(definition)
        return await self.service.start(principal, definition.id, RunStartRequest(input=RUN_INPUT))

    async def settle(self, tenant_id: UUID, run: WorkflowRun) -> WorkflowRun:
        """等待后台任务把运行推到 succeeded / failed / cancelled / waiting_human。"""
        for _ in range(SETTLE_TURNS):
            if run.status not in {RunStatus.QUEUED, RunStatus.RUNNING}:
                return run
            await asyncio.sleep(0)
            run = await self.service.get(tenant_id, run.id)
        raise AssertionError(f"运行未在 {SETTLE_TURNS} 轮内脱离运行中状态：{run.status}")

    async def start_and_settle(self, principal: Principal, definition: WorkflowDefinition) -> WorkflowRun:
        return await self.settle(principal.tenant_id, await self.start(principal, definition))

    async def approve(
        self,
        principal: Principal,
        run_id: UUID,
        node_id: str,
        decision: ApprovalDecision = ApprovalDecision.APPROVED,
    ) -> WorkflowRun:
        return await self.service.approve(
            principal,
            run_id,
            RunApprovalRequest(node_id=node_id, decision=decision, comment="测试审批"),
        )

    async def events(self, tenant_id: UUID, run_id: UUID, after_sequence: int = 0) -> list[RunEvent]:
        return await self.service.events(tenant_id, run_id, after_sequence=after_sequence)


def node_execution(run: WorkflowRun, node_id: str) -> NodeExecution:
    for execution in run.node_executions:
        if execution.node_id == node_id:
            return execution
    raise AssertionError(
        f"节点 {node_id} 没有对应的 node_execution：{[item.node_id for item in run.node_executions]}"
    )


# --------------------------------------------------------------------------- #
# 静态契约防线
# --------------------------------------------------------------------------- #


def test_run_statuses_match_wire_contract() -> None:
    contract = set(contract_defs()["run"]["properties"]["status"]["enum"])
    assert {status.value for status in RunStatus} == contract
    assert {status.value for status in TERMINAL_RUN_STATUSES} == {"succeeded", "failed", "cancelled"}


def test_every_internal_event_type_has_a_wire_destination() -> None:
    """内部事件不允许被静默丢弃：丢帧会让对外流出现 sequence 空洞。"""
    assert set(RunEventType) == set(EVENT_TYPE_TO_WIRE), (
        f"新增内部事件类型未登记对外去向：{set(RunEventType) ^ set(EVENT_TYPE_TO_WIRE)}"
    )
    assert len(set(EVENT_TYPE_TO_WIRE.values())) == len(EVENT_TYPE_TO_WIRE), (
        "映射必须一对一：两个内部事件合并成同一个对外帧名会丢失可区分性"
    )


def test_contract_gaps_are_exactly_the_agreed_ones() -> None:
    """两侧还没对齐的帧名必须与约定一致，任一侧补齐后本用例会红，提醒同步收敛。"""
    contract = set(contract_defs()["runEvent"]["properties"]["type"]["enum"])
    mapped = set(EVENT_TYPE_TO_WIRE.values())
    assert mapped - contract == PENDING_CONTRACT_ADDITIONS, (
        f"待补进 packages/contracts 的帧名变了：{mapped - contract}；"
        "契约补齐后请清空 PENDING_CONTRACT_ADDITIONS"
    )
    assert contract - mapped == PENDING_RUNTIME_EVENTS, (
        f"契约有、运行时还没发的帧名变了：{contract - mapped}；"
        "运行时补齐后请同步清空 PENDING_RUNTIME_EVENTS"
    )


def test_sequence_invariant_holds_for_contract_example() -> None:
    """用契约样例反向验证 sequence 断言本身正确。"""
    example = CONTRACTS_DIR / "examples" / "workflow-run-events.example.json"
    assert example.exists(), f"契约样例缺失：{example}"
    events = json.loads(example.read_text(encoding="utf-8"))
    assert_sequence_strictly_increasing(events)
    assert len({_run_id(event) for event in events}) >= 3, "样例应覆盖成功/失败/取消三条运行"


def test_sequence_invariant_rejects_duplicates_and_gaps() -> None:
    def event(sequence: int) -> dict[str, Any]:
        return {"sequence": sequence, "runId": "run_x", "type": "node.started"}

    for broken in ([event(1), event(1)], [event(1), event(3)], [event(2), event(3)]):
        with pytest.raises(AssertionError):
            assert_sequence_strictly_increasing(broken)


# --------------------------------------------------------------------------- #
# 运行时行为
# --------------------------------------------------------------------------- #


@async_test
async def test_successful_run_reaches_succeeded_with_mock_nodes_only() -> None:
    async with RuntimeDriver() as driver:
        principal = make_principal()
        run = await driver.start_and_settle(principal, linear_definition(principal.tenant_id))

        assert run.status is RunStatus.SUCCEEDED, f"期望 succeeded，实际 {run.status}"
        assert run.error is None
        assert run.finished_at is not None and run.started_at is not None
        assert {execution.status for execution in run.node_executions} <= {
            NodeRunStatus.SUCCEEDED,
            NodeRunStatus.SKIPPED,
        }, "成功运行不得留下失败或挂起的节点"

        model_output = node_execution(run, "model").output
        assert model_output["provider"] == "mock", "模型节点必须走确定性 mock，不得触达真实供应商"
        assert model_output["content"], "模型节点应产出内容"
        assert run.output.get("ends"), "成功运行应带上 end 节点的产物"

        events = await driver.events(principal.tenant_id, run.id)
        assert_events_conform(events, run.id)
        assert_terminal_last(events, RunEventType.RUN_SUCCEEDED)
        assert [event.type for event in events][:2] == [
            RunEventType.RUN_QUEUED,
            RunEventType.RUN_STARTED,
        ], "运行开头必须是 queued -> started，SSE 客户端据此建立状态"


@async_test
async def test_event_sequence_is_strictly_increasing_and_per_run_isolated() -> None:
    async with RuntimeDriver() as driver:
        principal = make_principal()
        first = await driver.start_and_settle(principal, linear_definition(principal.tenant_id))
        second = await driver.start_and_settle(principal, linear_definition(principal.tenant_id))
        assert first.id != second.id

        first_events = await driver.events(principal.tenant_id, first.id)
        second_events = await driver.events(principal.tenant_id, second.id)
        assert_events_conform(first_events, first.id)
        assert_events_conform(second_events, second.id)

        # 断线续传（Last-Event-ID）：游标之后的事件原样返回、编号不重排、不重放
        tail = await driver.events(principal.tenant_id, first.id, after_sequence=1)
        assert [event.sequence for event in tail] == [event.sequence for event in first_events[1:]]
        assert all(event.sequence > 1 for event in tail)
        assert await driver.events(principal.tenant_id, first.id, after_sequence=len(first_events)) == []

        # 两条运行的编号各自独立，客户端按 (runId, sequence) 去重才成立
        assert [event.sequence for event in first_events] == [
            event.sequence for event in second_events
        ]


@async_test
async def test_waiting_human_pauses_and_approval_completes_run() -> None:
    async with RuntimeDriver() as driver:
        principal = make_principal()
        run = await driver.start_and_settle(principal, approval_definition(principal.tenant_id))

        assert run.status is RunStatus.WAITING_HUMAN, f"审核节点应暂停，实际 {run.status}"
        assert run.status.value in set(contract_defs()["run"]["properties"]["status"]["enum"])
        assert node_execution(run, "review").status is NodeRunStatus.WAITING_HUMAN
        assert run.pending_approval is not None
        assert run.pending_approval.node_id == "review"
        assert run.pending_approval.prompt == "请确认周报内容"
        assert run.finished_at is None

        paused_events = await driver.events(principal.tenant_id, run.id)
        assert_events_conform(paused_events, run.id)
        assert RunEventType.NODE_AWAITING_APPROVAL in {event.type for event in paused_events}
        assert not [event for event in paused_events if event.type in TERMINAL_EVENT_TYPES], (
            "等待人工时不得出现终态事件"
        )

        # 暂停是稳定态：多转几圈事件循环仍不推进、不追加事件
        for _ in range(10):
            await asyncio.sleep(0)
        assert (await driver.service.get(principal.tenant_id, run.id)).status is RunStatus.WAITING_HUMAN
        assert len(await driver.events(principal.tenant_id, run.id)) == len(paused_events)

        resumed = await driver.settle(
            principal.tenant_id, await driver.approve(principal, run.id, "review")
        )
        assert resumed.status is RunStatus.SUCCEEDED, f"批准后应完成，实际 {resumed.status}"
        assert resumed.pending_approval is None
        review = node_execution(resumed, "review")
        assert review.status is NodeRunStatus.SUCCEEDED
        assert review.output["decision"] == ApprovalDecision.APPROVED.value

        events = await driver.events(principal.tenant_id, run.id)
        assert_events_conform(events, run.id)
        assert_terminal_last(events, RunEventType.RUN_SUCCEEDED)
        assert len(events) > len(paused_events), "恢复后必须在同一条日志上续写"


@async_test
async def test_cancel_is_idempotent() -> None:
    async with RuntimeDriver() as driver:
        principal = make_principal()
        run = await driver.start_and_settle(principal, approval_definition(principal.tenant_id))
        assert run.status is RunStatus.WAITING_HUMAN

        first = await driver.service.cancel(principal, run.id, "用户主动停止本次演示运行")
        assert first.status is RunStatus.CANCELLED
        assert first.pending_approval is None
        assert node_execution(first, "review").status is NodeRunStatus.CANCELLED, (
            "挂起的节点必须随运行一起收口"
        )
        events_after_first = await driver.events(principal.tenant_id, run.id)
        assert_events_conform(events_after_first, run.id)
        assert_terminal_last(events_after_first, RunEventType.RUN_CANCELLED)
        assert events_after_first[-1].data.get("reason") == "用户主动停止本次演示运行"

        # 挂起节点先各自收口，再收口整条运行：重放流里不会有停在半途的节点
        node_cancelled = [
            event for event in events_after_first if event.type is RunEventType.NODE_CANCELLED
        ]
        assert [event.node_id for event in node_cancelled] == ["review"]
        assert node_cancelled[0].data.get("reason") == "用户主动停止本次演示运行"
        assert node_cancelled[0].sequence == events_after_first[-1].sequence - 1, (
            "node.cancelled 必须紧邻在 run.cancelled 之前，中间不得插入其它帧"
        )

        second = await driver.service.cancel(principal, run.id, "重复取消")
        assert second.status is RunStatus.CANCELLED, "重复取消必须幂等返回已取消的运行"
        assert second.id == run.id
        assert second.finished_at == first.finished_at, "重复取消不得改写终态时间"

        events_after_second = await driver.events(principal.tenant_id, run.id)
        assert [event.sequence for event in events_after_second] == [
            event.sequence for event in events_after_first
        ], "重复取消不得追加事件或改变 sequence"
        assert_terminal_last(events_after_second, RunEventType.RUN_CANCELLED)


@async_test
async def test_cancelling_a_finished_run_conflicts() -> None:
    """openapi：成功/失败终态取消返回稳定冲突错误，且运行一个字节都不改。"""
    async with RuntimeDriver() as driver:
        principal = make_principal()
        run = await driver.start_and_settle(principal, linear_definition(principal.tenant_id))
        assert run.status is RunStatus.SUCCEEDED
        assert run.status in TERMINAL_RUN_STATUSES

        before = await driver.events(principal.tenant_id, run.id)
        with pytest.raises(ConflictError):
            await driver.service.cancel(principal, run.id, "终态不可取消")

        after_run = await driver.service.get(principal.tenant_id, run.id)
        assert after_run.status is RunStatus.SUCCEEDED, "被拒的取消不得改写终态"
        assert after_run.finished_at == run.finished_at
        assert after_run.node_executions == run.node_executions
        after = await driver.events(principal.tenant_id, run.id)
        assert [(event.sequence, event.type) for event in after] == [
            (event.sequence, event.type) for event in before
        ]


@async_test
async def test_unknown_run_and_cross_tenant_access_are_not_found() -> None:
    async with RuntimeDriver() as driver:
        principal = make_principal()
        missing_id = uuid4()

        with pytest.raises(NotFoundError):
            await driver.service.get(principal.tenant_id, missing_id)
        with pytest.raises(NotFoundError):
            await driver.service.cancel(principal, missing_id)
        with pytest.raises(NotFoundError):
            await driver.events(principal.tenant_id, missing_id)
        with pytest.raises(NotFoundError):
            await driver.approve(principal, missing_id, "review")
        with pytest.raises(NotFoundError):
            await driver.service.start(principal, uuid4(), RunStartRequest(input=RUN_INPUT))

        run = await driver.start_and_settle(principal, approval_definition(principal.tenant_id))
        intruder = make_principal(OTHER_TENANT_ID)

        # 跨租户必须与"不存在"不可区分，四个入口一个都不能漏
        with pytest.raises(NotFoundError):
            await driver.service.get(intruder.tenant_id, run.id)
        with pytest.raises(NotFoundError):
            await driver.service.cancel(intruder, run.id)
        with pytest.raises(NotFoundError):
            await driver.events(intruder.tenant_id, run.id)
        with pytest.raises(NotFoundError):
            await driver.approve(intruder, run.id, "review")

        assert (await driver.service.get(principal.tenant_id, run.id)).status is RunStatus.WAITING_HUMAN


@async_test
async def test_illegal_state_transitions_conflict() -> None:
    async with RuntimeDriver() as driver:
        principal = make_principal()

        # 未处于 waiting_human 的运行不可审批
        finished = await driver.start_and_settle(principal, linear_definition(principal.tenant_id))
        with pytest.raises(ConflictError):
            await driver.approve(principal, finished.id, "model")

        # 已取消的运行不可审批
        paused = await driver.start_and_settle(principal, approval_definition(principal.tenant_id))
        await driver.service.cancel(principal, paused.id)
        with pytest.raises(ConflictError):
            await driver.approve(principal, paused.id, "review")

        # 审批的节点必须是当前挂起的那个
        waiting = await driver.start_and_settle(principal, approval_definition(principal.tenant_id))
        with pytest.raises(ConflictError):
            await driver.approve(principal, waiting.id, "不存在的节点")

        still_waiting = await driver.service.get(principal.tenant_id, waiting.id)
        assert still_waiting.status is RunStatus.WAITING_HUMAN, "被拒的审批不得推进运行"
        assert still_waiting.pending_approval is not None
        assert_events_conform(await driver.events(principal.tenant_id, waiting.id), waiting.id)

        # 同一节点重复审批：第二次已无挂起审批
        approved = await driver.approve(principal, waiting.id, "review")
        assert approved.pending_approval is None
        with pytest.raises(ConflictError):
            await driver.approve(principal, waiting.id, "review")


@async_test
async def test_emitted_events_project_onto_contract_frames() -> None:
    """成功与失败两条运行的每一条事件都能投影成契约帧，且投影后不丢帧、不断号。"""
    async with RuntimeDriver() as driver:
        principal = make_principal()

        success = await driver.start_and_settle(principal, linear_definition(principal.tenant_id))
        rejected_run = await driver.start_and_settle(principal, approval_definition(principal.tenant_id))
        rejected = await driver.settle(
            principal.tenant_id,
            await driver.approve(principal, rejected_run.id, "review", ApprovalDecision.REJECTED),
        )
        assert rejected.status is RunStatus.FAILED
        assert rejected.error is not None and rejected.error.code == "approval_rejected"

        for run, terminal in ((success, RunEventType.RUN_SUCCEEDED), (rejected, RunEventType.RUN_FAILED)):
            events = await driver.events(principal.tenant_id, run.id)
            assert_events_conform(events, run.id)
            assert_terminal_last(events, terminal)

            frames = [wire_frame(event) for event in events]
            for frame in frames:
                assert_frame_matches_contract(frame)
            assert [frame["sequence"] for frame in frames] == [
                event.sequence for event in events
            ], "投影不得丢帧或重排，否则对外流会出现 sequence 空洞"
            assert frames[-1]["type"] == EVENT_TYPE_TO_WIRE[terminal]
