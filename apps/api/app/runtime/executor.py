"""Graph execution: a deterministic in-process walk plus the seam a LangGraph
runtime can be plugged into.

An executor turns an :class:`ExecutionPlan` into a stream of
:class:`ExecutionStep` values. It owns no persistence and emits no events of its
own; the service layer projects the steps onto run state and the event log.
"""

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from heapq import heappop, heappush
from typing import Any, Protocol
from uuid import UUID

from app.runtime.nodes import NodeContext, UnsupportedNodeTypeError, evaluate
from app.runtime.schemas import ApprovalDecision, RunError
from app.workflows.schemas import NodeType, WorkflowEdge, WorkflowGraph


class StepKind(StrEnum):
    NODE_STARTED = "node_started"
    NODE_AWAITING_APPROVAL = "node_awaiting_approval"
    NODE_SUCCEEDED = "node_succeeded"
    NODE_SKIPPED = "node_skipped"
    NODE_FAILED = "node_failed"
    RUN_SUCCEEDED = "run_succeeded"
    RUN_FAILED = "run_failed"
    RUN_CANCELLED = "run_cancelled"


@dataclass(slots=True, frozen=True)
class ExecutionStep:
    kind: StepKind
    node_id: str | None = None
    node_type: NodeType | None = None
    output: dict[str, Any] = field(default_factory=dict)
    error: RunError | None = None


@dataclass(slots=True, frozen=True)
class ApprovalSubmission:
    decision: ApprovalDecision
    comment: str = ""
    decided_by: UUID | None = None


class ApprovalInbox:
    """Hand-off point between a caller answering an approval and the paused run.

    A submission that arrives before the run reaches the node is held until the
    executor asks for it, so the two orderings behave identically.
    """

    def __init__(self) -> None:
        self._waiters: dict[str, asyncio.Future[ApprovalSubmission]] = {}
        self._delivered: dict[str, ApprovalSubmission] = {}

    async def wait(self, node_id: str) -> ApprovalSubmission:
        delivered = self._delivered.pop(node_id, None)
        if delivered is not None:
            return delivered
        future: asyncio.Future[ApprovalSubmission] = asyncio.get_running_loop().create_future()
        self._waiters[node_id] = future
        try:
            return await future
        finally:
            self._waiters.pop(node_id, None)

    def submit(self, node_id: str, submission: ApprovalSubmission) -> None:
        future = self._waiters.get(node_id)
        if future is not None and not future.done():
            future.set_result(submission)
            return
        self._delivered[node_id] = submission


@dataclass(slots=True)
class ExecutionPlan:
    run_id: UUID
    graph: WorkflowGraph
    input: dict[str, Any] = field(default_factory=dict)
    approvals: ApprovalInbox = field(default_factory=ApprovalInbox)
    cancellation: asyncio.Event = field(default_factory=asyncio.Event)


class GraphExecutor(Protocol):
    def run(self, plan: ExecutionPlan) -> AsyncIterator[ExecutionStep]: ...


class InProcessGraphExecutor:
    """Executes a validated workflow graph in the current process.

    Nodes run one at a time in a stable topological order, so a graph and an
    input reproduce the same step stream on every run. Branch nodes activate a
    single outgoing edge and parallel nodes activate all of them; a node whose
    branch was not taken is reported skipped rather than silently dropped.
    """

    async def run(self, plan: ExecutionPlan) -> AsyncIterator[ExecutionStep]:
        graph = plan.graph
        nodes = {node.id: node for node in graph.nodes}
        outgoing, incoming = _adjacency(graph)

        starts = [node for node in graph.nodes if node.type is NodeType.START]
        if len(starts) != 1:
            yield _run_failed("start_node_missing", "The graph must contain exactly one start node")
            return
        order = _topological_order(graph, outgoing)
        if order is None:
            yield _run_failed("graph_not_acyclic", "The graph contains a cycle and cannot be executed")
            return

        activated = {starts[0].id}
        outputs: dict[str, dict[str, Any]] = {}
        ends: dict[str, dict[str, Any]] = {}

        for node_id in order:
            if plan.cancellation.is_set():
                yield ExecutionStep(kind=StepKind.RUN_CANCELLED)
                return
            node = nodes[node_id]
            if node_id not in activated:
                yield ExecutionStep(kind=StepKind.NODE_SKIPPED, node_id=node_id, node_type=node.type)
                continue

            yield ExecutionStep(kind=StepKind.NODE_STARTED, node_id=node_id, node_type=node.type)
            context = NodeContext(
                node=node,
                run_input=plan.input,
                upstream={
                    edge.source: outputs[edge.source] for edge in incoming[node_id] if edge.source in outputs
                },
                outgoing_labels=tuple(edge.label for edge in outgoing[node_id]),
            )
            try:
                output = evaluate(context)
            except UnsupportedNodeTypeError as exc:
                for step in _node_failure(node_id, node.type, "unsupported_node_type", str(exc)):
                    yield step
                return
            except Exception as exc:  # defensive: a handler must never take the run down silently
                for step in _node_failure(node_id, node.type, "node_execution_failed", str(exc)):
                    yield step
                return

            if node.type is NodeType.APPROVAL:
                yield ExecutionStep(
                    kind=StepKind.NODE_AWAITING_APPROVAL,
                    node_id=node_id,
                    node_type=node.type,
                    output=output,
                )
                submission = await _await_decision(plan, node_id)
                if submission is None:
                    yield ExecutionStep(kind=StepKind.RUN_CANCELLED)
                    return
                output = {
                    **output,
                    "decision": submission.decision.value,
                    "comment": submission.comment,
                    "decided_by": str(submission.decided_by) if submission.decided_by else None,
                }
                if submission.decision is ApprovalDecision.REJECTED:
                    error = RunError(
                        code="approval_rejected",
                        message=submission.comment or "The approval was rejected",
                        node_id=node_id,
                    )
                    yield ExecutionStep(
                        kind=StepKind.NODE_FAILED,
                        node_id=node_id,
                        node_type=node.type,
                        output=output,
                        error=error,
                    )
                    yield ExecutionStep(kind=StepKind.RUN_FAILED, error=error)
                    return

            outputs[node_id] = output
            yield ExecutionStep(
                kind=StepKind.NODE_SUCCEEDED, node_id=node_id, node_type=node.type, output=output
            )
            if node.type is NodeType.END:
                ends[node_id] = output
            activated.update(_activated_targets(node.type, output, outgoing[node_id]))

        yield ExecutionStep(kind=StepKind.RUN_SUCCEEDED, output={"ends": ends})


class LangGraphAdapter(Protocol):
    """Bridge to an external LangGraph runtime.

    An adapter translates a plan into the same step stream the in-process
    executor produces, which is what keeps the service layer unaware of which
    runtime is active. None ships with the platform.
    """

    def stream(self, plan: ExecutionPlan) -> AsyncIterator[ExecutionStep]: ...


class LangGraphExecutor:
    """Safe placeholder: graphs stay in-process until an adapter is injected."""

    def __init__(self, adapter: LangGraphAdapter | None = None) -> None:
        self._adapter = adapter

    async def run(self, plan: ExecutionPlan) -> AsyncIterator[ExecutionStep]:
        if self._adapter is None:
            raise RuntimeError("No LangGraph adapter is configured")
        async for step in self._adapter.stream(plan):
            yield step


async def _await_decision(plan: ExecutionPlan, node_id: str) -> ApprovalSubmission | None:
    """Blocks until the approval is answered or the run is cancelled."""
    decision = asyncio.ensure_future(plan.approvals.wait(node_id))
    cancellation = asyncio.ensure_future(plan.cancellation.wait())
    try:
        await asyncio.wait({decision, cancellation}, return_when=asyncio.FIRST_COMPLETED)
        if plan.cancellation.is_set():
            return None
        return decision.result()
    finally:
        for pending in (decision, cancellation):
            if not pending.done():
                pending.cancel()


def _adjacency(graph: WorkflowGraph) -> tuple[dict[str, list[WorkflowEdge]], dict[str, list[WorkflowEdge]]]:
    known = {node.id for node in graph.nodes}
    outgoing: dict[str, list[WorkflowEdge]] = defaultdict(list)
    incoming: dict[str, list[WorkflowEdge]] = defaultdict(list)
    for edge in graph.edges:
        if edge.source not in known or edge.target not in known or edge.source == edge.target:
            continue
        outgoing[edge.source].append(edge)
        incoming[edge.target].append(edge)
    return outgoing, incoming


def _topological_order(graph: WorkflowGraph, outgoing: dict[str, list[WorkflowEdge]]) -> list[str] | None:
    """Definition order breaks ties, so the walk is stable across runs.

    Returns None when the graph is cyclic and therefore not executable.
    """
    position = {node.id: index for index, node in enumerate(graph.nodes)}
    indegree = {node.id: 0 for node in graph.nodes}
    for edges in outgoing.values():
        for edge in edges:
            indegree[edge.target] += 1

    ready: list[int] = []
    for node_id, degree in indegree.items():
        if degree == 0:
            heappush(ready, position[node_id])

    order: list[str] = []
    while ready:
        node_id = graph.nodes[heappop(ready)].id
        order.append(node_id)
        for edge in outgoing[node_id]:
            indegree[edge.target] -= 1
            if indegree[edge.target] == 0:
                heappush(ready, position[edge.target])
    return order if len(order) == len(position) else None


def _activated_targets(
    node_type: NodeType, output: dict[str, Any], edges: list[WorkflowEdge]
) -> set[str]:
    if node_type is not NodeType.CONDITION or not edges:
        return {edge.target for edge in edges}
    branch = output.get("branch")
    taken = next((edge for edge in edges if edge.label == branch), edges[0])
    return {taken.target}


def _node_failure(node_id: str, node_type: NodeType, code: str, message: str) -> list[ExecutionStep]:
    error = RunError(code=code, message=message, node_id=node_id)
    return [
        ExecutionStep(kind=StepKind.NODE_FAILED, node_id=node_id, node_type=node_type, error=error),
        ExecutionStep(kind=StepKind.RUN_FAILED, error=error),
    ]


def _run_failed(code: str, message: str) -> ExecutionStep:
    return ExecutionStep(kind=StepKind.RUN_FAILED, error=RunError(code=code, message=message))
