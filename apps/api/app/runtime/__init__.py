"""Workflow runtime: deterministic in-process execution of a workflow graph.

The package is transport agnostic. `WorkflowRunService` owns the run lifecycle
and the event log; an HTTP layer wires it to routes and turns `RunEvent` values
into SSE frames.
"""

from app.runtime.executor import (
    ApprovalInbox,
    ApprovalSubmission,
    ExecutionPlan,
    ExecutionStep,
    GraphExecutor,
    InProcessGraphExecutor,
    LangGraphAdapter,
    LangGraphExecutor,
    StepKind,
)
from app.runtime.nodes import NodeContext, UnsupportedNodeTypeError, evaluate
from app.runtime.repository import InMemoryWorkflowRunRepository, WorkflowRunRepository
from app.runtime.schemas import (
    TERMINAL_EVENT_TYPES,
    TERMINAL_RUN_STATUSES,
    ApprovalDecision,
    NodeExecution,
    NodeRunStatus,
    PendingApproval,
    RunApprovalRequest,
    RunError,
    RunEvent,
    RunEventType,
    RunStartRequest,
    RunStatus,
    WorkflowRun,
)
from app.runtime.service import WorkflowRunService

__all__ = [
    "TERMINAL_EVENT_TYPES",
    "TERMINAL_RUN_STATUSES",
    "ApprovalDecision",
    "ApprovalInbox",
    "ApprovalSubmission",
    "ExecutionPlan",
    "ExecutionStep",
    "GraphExecutor",
    "InMemoryWorkflowRunRepository",
    "InProcessGraphExecutor",
    "LangGraphAdapter",
    "LangGraphExecutor",
    "NodeContext",
    "NodeExecution",
    "NodeRunStatus",
    "PendingApproval",
    "RunApprovalRequest",
    "RunError",
    "RunEvent",
    "RunEventType",
    "RunStartRequest",
    "RunStatus",
    "StepKind",
    "UnsupportedNodeTypeError",
    "WorkflowRun",
    "WorkflowRunRepository",
    "WorkflowRunService",
    "evaluate",
]
