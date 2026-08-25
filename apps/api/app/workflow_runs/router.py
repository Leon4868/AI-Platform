from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, status
from fastapi.responses import StreamingResponse

from app.core.errors import ConflictError
from app.core.http import IdempotencyKey
from app.core.idempotency import IdempotencyScope, request_fingerprint
from app.identity.dependencies import require
from app.identity.schemas import Permission, Principal
from app.runtime.schemas import RunStartRequest
from app.runtime.service import WorkflowRunService
from app.workflow_runs.schemas import (
    WorkflowRunCancelRequest,
    WorkflowRunEventView,
    WorkflowRunStartRequest,
    WorkflowRunView,
)

router = APIRouter(tags=["workflow-runs"])

_STREAM_HEADERS = {
    "Cache-Control": "no-cache",
    # nginx buffers proxied responses by default, which would hold every frame
    # back until the run finished.
    "X-Accel-Buffering": "no",
}


def _runs(request: Request) -> WorkflowRunService:
    return request.app.state.container.workflow_run_service


def _resume_after(last_event_id: str | None) -> int:
    """Sequence the client already has; 0 replays the run from its first event.

    A malformed Last-Event-ID replays from the start rather than failing the
    request: losing the cursor costs duplicates, refusing costs the stream.
    """
    if last_event_id is None:
        return 0
    try:
        return max(int(last_event_id), 0)
    except ValueError:
        return 0


def _frame(event: WorkflowRunEventView) -> str:
    return (
        f"id: {event.sequence}\n"
        f"event: {event.type.value}\n"
        f"data: {event.model_dump_json(by_alias=True)}\n\n"
    )


@router.post(
    "/workflows/{workflow_id}/runs",
    response_model=WorkflowRunView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_workflow_run(
    workflow_id: UUID,
    payload: WorkflowRunStartRequest,
    principal: Annotated[Principal, Depends(require(Permission.WORKFLOW_WRITE))],
    request: Request,
    idempotency_key: IdempotencyKey,
) -> WorkflowRunView:
    container = request.app.state.container
    scope = IdempotencyScope(
        principal.tenant_id,
        principal.user_id,
        "workflow-run.start",
        idempotency_key,
    )

    async def start() -> WorkflowRunView:
        requested = payload.workflow_definition_version
        if requested is not None:
            # Pin the run to the revision the caller saw, so an edit landing between
            # read and start cannot silently execute a different graph.
            workflow = await container.workflow_service.get(principal.tenant_id, workflow_id)
            if requested != workflow.revision:
                raise ConflictError(
                    f"Workflow revision changed: requested {requested}, current {workflow.revision}"
                )
        run = await _runs(request).start(
            principal,
            workflow_id,
            RunStartRequest(input=payload.input),
        )
        return WorkflowRunView.of(run)

    return await container.idempotency_store.execute(
        scope,
        request_fingerprint({"workflowId": workflow_id, "request": payload}),
        start,
    )


@router.get("/workflow-runs/{run_id}", response_model=WorkflowRunView)
async def get_workflow_run(
    run_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.WORKFLOW_READ))],
    request: Request,
) -> WorkflowRunView:
    return WorkflowRunView.of(await _runs(request).get(principal.tenant_id, run_id))


@router.post("/workflow-runs/{run_id}/cancel", response_model=WorkflowRunView)
async def cancel_workflow_run(
    run_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.WORKFLOW_WRITE))],
    request: Request,
    idempotency_key: IdempotencyKey,
    payload: WorkflowRunCancelRequest | None = None,
) -> WorkflowRunView:
    container = request.app.state.container
    scope = IdempotencyScope(
        principal.tenant_id,
        principal.user_id,
        "workflow-run.cancel",
        idempotency_key,
    )

    async def cancel() -> WorkflowRunView:
        reason = None if payload is None else payload.reason
        return WorkflowRunView.of(await _runs(request).cancel(principal, run_id, reason))

    return await container.idempotency_store.execute(
        scope,
        request_fingerprint({"runId": run_id, "request": payload or {}}),
        cancel,
    )


@router.get("/workflow-runs/{run_id}/events")
async def stream_workflow_run_events(
    run_id: UUID,
    principal: Annotated[Principal, Depends(require(Permission.WORKFLOW_READ))],
    request: Request,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    service = _runs(request)
    # Resolved before streaming starts: once the first frame is written the
    # status line is committed, and a missing run could no longer read as 404.
    await service.get(principal.tenant_id, run_id)
    events = service.stream(
        principal.tenant_id, run_id, after_sequence=_resume_after(last_event_id)
    )

    async def frames() -> AsyncIterator[str]:
        async for event in events:
            view = WorkflowRunEventView.of(event)
            if view is not None:
                yield _frame(view)

    return StreamingResponse(frames(), media_type="text/event-stream", headers=_STREAM_HEADERS)
