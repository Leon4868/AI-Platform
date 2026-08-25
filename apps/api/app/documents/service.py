from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol
from uuid import UUID, uuid4

from app.assets.schemas import (
    Asset,
    AssetLineageRef,
    AssetLineageRelation,
    AssetStatus,
    AssetType,
    DataScope,
)
from app.core.errors import ConflictError, NotFoundError
from app.core.repository import Repository
from app.core.storage import ObjectStorage
from app.documents.schemas import (
    DocumentGenerationJob,
    DocumentOutputFormat,
    DocumentSourceKind,
    DocumentTaskError,
    DocumentTaskStatus,
)
from app.identity.schemas import Principal
from app.model_gateway.gateway import ModelGateway
from app.model_gateway.providers import ModelGatewayError
from app.model_gateway.schemas import MessageRole, ModelMessage, ModelRequest
from app.runtime.schemas import RunEventType
from app.runtime.service import WorkflowRunService


@dataclass(frozen=True, slots=True)
class ComposedDraft:
    content: bytes
    output_format: DocumentOutputFormat
    content_type: str
    extension: str


class DraftComposer(Protocol):
    """Model/provider boundary. Implementations receive no provider credentials."""

    async def compose(self, job: DocumentGenerationJob) -> ComposedDraft: ...


class DraftCompositionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class DeterministicMarkdownComposer:
    """Local/test composer that is deterministic and never calls an external model."""

    async def compose(self, job: DocumentGenerationJob) -> ComposedDraft:
        if job.output_format is not DocumentOutputFormat.MARKDOWN:
            raise DraftCompositionError(
                "unsupported_output_format",
                f"Deterministic composer does not support {job.output_format.value}",
            )
        source_lines = [
            f"- [{source.kind.value}] {source.label}"
            + (f" (`{source.id}`)" if source.id else "")
            for source in job.sources
        ]
        sources = "\n".join(source_lines) if source_lines else "- 无外部来源"
        markdown = (
            f"# {job.title}\n\n"
            f"{job.instructions.strip()}\n\n"
            "## 来源\n\n"
            f"{sources}\n"
        )
        return ComposedDraft(
            content=markdown.encode("utf-8"),
            output_format=DocumentOutputFormat.MARKDOWN,
            content_type="text/markdown; charset=utf-8",
            extension="md",
        )


class ModelGatewayMarkdownComposer:
    """Explicit opt-in composer; routing and credentials stay inside the gateway."""

    def __init__(self, gateway: ModelGateway) -> None:
        self._gateway = gateway

    async def compose(self, job: DocumentGenerationJob) -> ComposedDraft:
        if job.output_format is not DocumentOutputFormat.MARKDOWN:
            raise DraftCompositionError(
                "unsupported_output_format",
                f"Model gateway composer does not support {job.output_format.value}",
            )
        source_context = "\n".join(
            f"- {citation.quote} (asset={citation.asset_id}, chunk={citation.chunk_id})"
            for citation in job.citations
        ) or "- 无知识库引用"
        user_prompt = (
            f"文档标题：{job.title}\n\n"
            f"写作要求：\n{job.instructions.strip()}\n\n"
            f"可引用资料：\n{source_context}\n\n"
            "请输出完整 Markdown 正文，并保留可核验的来源说明。"
        )
        try:
            response = await self._gateway.complete(ModelRequest(
                model=job.logical_model_code,
                messages=[
                    ModelMessage(
                        role=MessageRole.SYSTEM,
                        content="你是企业文档助手。只能依据用户要求和已授权资料生成草稿，不得虚构来源。",
                    ),
                    ModelMessage(role=MessageRole.USER, content=user_prompt),
                ],
                metadata={"traceId": str(job.trace_id), "workflowRunId": str(job.workflow_run_id)},
            ))
        except ModelGatewayError as exc:
            raise DraftCompositionError(exc.code.lower(), str(exc)) from exc
        return ComposedDraft(
            content=response.content.encode("utf-8"),
            output_format=DocumentOutputFormat.MARKDOWN,
            content_type="text/markdown; charset=utf-8",
            extension="md",
        )


_TERMINAL_STATUSES = frozenset(
    {
        DocumentTaskStatus.SUCCEEDED,
        DocumentTaskStatus.FAILED,
        DocumentTaskStatus.CANCELLED,
    }
)


class DocumentTaskService:
    def __init__(
        self,
        job_repository: Repository[DocumentGenerationJob],
        asset_repository: Repository[Asset],
        storage: ObjectStorage,
        composer: DraftComposer,
    ) -> None:
        self._jobs = job_repository
        self._assets = asset_repository
        self._storage = storage
        self._composer = composer
        self._locks: dict[UUID, asyncio.Lock] = {}

    async def get(self, tenant_id: UUID, task_id: UUID) -> DocumentGenerationJob:
        job = await self._jobs.get(tenant_id, task_id)
        if job is None:
            raise NotFoundError("document_generation_job", str(task_id))
        return job

    async def settle(self, saved_job: DocumentGenerationJob) -> DocumentGenerationJob:
        """Generate and persist one draft. Repeated calls after a terminal state are no-ops."""
        lock = self._locks.setdefault(saved_job.id, asyncio.Lock())
        async with lock:
            job = await self.get(saved_job.tenant_id, saved_job.id)
            if job.status in _TERMINAL_STATUSES:
                return job
            if job.status not in {DocumentTaskStatus.QUEUED, DocumentTaskStatus.RUNNING}:
                raise ConflictError(f"Document task is {job.status.value} and cannot be settled")

            if job.status is DocumentTaskStatus.QUEUED:
                now = datetime.now(UTC)
                running = job.model_copy(
                    update={
                        "status": DocumentTaskStatus.RUNNING,
                        "updated_at": now,
                        "error": None,
                    }
                )
                running = await self._jobs.update(running)
            else:
                running = job
            object_key: str | None = None
            created_asset: Asset | None = None

            try:
                draft = await self._composer.compose(running)
                self._validate_draft(running, draft)
                object_key = self._object_key(running, draft.extension)
                await self._storage.put(object_key, draft.content, content_type=draft.content_type)
                created_asset = await self._assets.add(
                    self._build_asset(running, draft, object_key)
                )
                completed_at = datetime.now(UTC)
                succeeded = running.model_copy(
                    update={
                        "status": DocumentTaskStatus.SUCCEEDED,
                        "draft_asset_id": created_asset.id,
                        "updated_at": completed_at,
                        "finished_at": completed_at,
                        "error": None,
                    }
                )
                return await self._jobs.update(succeeded)
            except Exception as exc:
                await self._discard_partial_draft(created_asset, object_key)
                failed_at = datetime.now(UTC)
                error = self._task_error(exc)
                failed = running.model_copy(
                    update={
                        "status": DocumentTaskStatus.FAILED,
                        "draft_asset_id": None,
                        "updated_at": failed_at,
                        "finished_at": failed_at,
                        "error": error,
                    }
                )
                return await self._jobs.update(failed)

    async def transition(
        self,
        tenant_id: UUID,
        task_id: UUID,
        status: DocumentTaskStatus,
        *,
        error: DocumentTaskError | None = None,
    ) -> DocumentGenerationJob:
        """Mirror workflow progress without changing an existing terminal task."""

        lock = self._locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            job = await self.get(tenant_id, task_id)
            if job.status in _TERMINAL_STATUSES:
                return job
            now = datetime.now(UTC)
            terminal = status in _TERMINAL_STATUSES
            updated = job.model_copy(
                update={
                    "status": status,
                    "error": error,
                    "updated_at": now,
                    "finished_at": now if terminal else None,
                }
            )
            return await self._jobs.update(updated)

    async def _discard_partial_draft(
        self,
        asset: Asset | None,
        object_key: str | None,
    ) -> None:
        """Best-effort compensation; cleanup failure must not leave the task running."""
        if asset is not None:
            try:
                await self._assets.delete(asset.tenant_id, asset.id)
            except Exception:
                # The asset was created as DRAFT, so even a failed cleanup can never publish it.
                pass
        if object_key is not None:
            try:
                await self._storage.delete(object_key)
            except Exception:
                pass

    @staticmethod
    def _validate_draft(job: DocumentGenerationJob, draft: ComposedDraft) -> None:
        if not draft.content:
            raise DraftCompositionError("empty_draft", "Draft composer returned empty content")
        if draft.output_format is not job.output_format:
            raise DraftCompositionError(
                "output_format_mismatch",
                f"Composer returned {draft.output_format.value} for {job.output_format.value} task",
            )
        if not draft.content_type or not draft.extension.strip("."):
            raise DraftCompositionError("invalid_draft_metadata", "Draft metadata is incomplete")

    @staticmethod
    def _object_key(job: DocumentGenerationJob, extension: str) -> str:
        safe_extension = extension.strip(".").lower()
        return (
            f"tenants/{job.tenant_id}/document-tasks/{job.id}/"
            f"drafts/v1/draft.{safe_extension}"
        )

    @staticmethod
    def _lineage(job: DocumentGenerationJob) -> list[AssetLineageRef]:
        relations: dict[str, AssetLineageRelation] = {}
        if job.template_asset_id:
            relations[job.template_asset_id] = AssetLineageRelation.SOURCE
        for source in job.sources:
            if source.kind is DocumentSourceKind.ASSET and source.id:
                relations.setdefault(source.id, AssetLineageRelation.DERIVED_FROM)
        for citation in job.citations:
            relations.setdefault(citation.asset_id, AssetLineageRelation.DERIVED_FROM)
        return [
            AssetLineageRef(
                asset_id=source_id,
                version=1,
                relation=relation,
            )
            for source_id, relation in relations.items()
        ]

    def _build_asset(
        self,
        job: DocumentGenerationJob,
        draft: ComposedDraft,
        object_key: str,
    ) -> Asset:
        now = datetime.now(UTC)
        return Asset(
            id=uuid4(),
            tenant_id=job.tenant_id,
            type=AssetType.DOCUMENT,
            name=f"{job.title}.{draft.extension.strip('.')}",
            description=f"AI generated draft for document task {job.id}",
            version=1,
            status=AssetStatus.DRAFT,
            mime_type=draft.content_type,
            storage_uri=object_key,
            content_hash=sha256(draft.content).hexdigest(),
            creator_id=job.requested_by,
            owner_department_id=job.owner_department_id,
            data_scope=job.data_scope,
            security_level=job.security_level,
            lineage=self._lineage(job),
            workflow_run_id=job.workflow_run_id,
            trace_id=job.trace_id,
            created_at=now,
            updated_at=now,
        )

    @staticmethod
    def _task_error(exc: Exception) -> DocumentTaskError:
        if isinstance(exc, DraftCompositionError):
            return DocumentTaskError(code=exc.code, message=str(exc)[:2_000])
        return DocumentTaskError(
            code="document_generation_failed",
            message=(str(exc) or exc.__class__.__name__)[:2_000],
        )


class DocumentTaskCoordinator:
    """Projects a real Workflow Run onto a document task, then composes a draft."""

    def __init__(
        self,
        tasks: DocumentTaskService,
        runtime: WorkflowRunService,
    ) -> None:
        self._tasks = tasks
        self._runtime = runtime
        self._followers: dict[UUID, asyncio.Task[None]] = {}

    def follow(self, principal: Principal, job: DocumentGenerationJob) -> None:
        if job.id in self._followers:
            return
        task = asyncio.create_task(
            self._follow(principal, job),
            name=f"document-task:{job.id}",
        )
        self._followers[job.id] = task
        task.add_done_callback(lambda _: self._followers.pop(job.id, None))

    async def aclose(self) -> None:
        followers = list(self._followers.values())
        if followers:
            await asyncio.gather(*followers, return_exceptions=True)

    async def _follow(self, principal: Principal, job: DocumentGenerationJob) -> None:
        try:
            async for event in self._runtime.stream(
                principal.tenant_id,
                job.workflow_run_id,
            ):
                if event.type is RunEventType.RUN_STARTED:
                    await self._tasks.transition(
                        principal.tenant_id,
                        job.id,
                        DocumentTaskStatus.RUNNING,
                    )
                elif event.type is RunEventType.NODE_AWAITING_APPROVAL:
                    await self._tasks.transition(
                        principal.tenant_id,
                        job.id,
                        DocumentTaskStatus.WAITING_HUMAN,
                    )
                elif event.type is RunEventType.NODE_RESUMED:
                    await self._tasks.transition(
                        principal.tenant_id,
                        job.id,
                        DocumentTaskStatus.RUNNING,
                    )
                elif event.type is RunEventType.RUN_SUCCEEDED:
                    current = await self._tasks.get(principal.tenant_id, job.id)
                    if current.status is DocumentTaskStatus.WAITING_HUMAN:
                        current = await self._tasks.transition(
                            principal.tenant_id,
                            job.id,
                            DocumentTaskStatus.RUNNING,
                        )
                    await self._tasks.settle(current)
                    return
                elif event.type is RunEventType.RUN_FAILED:
                    await self._tasks.transition(
                        principal.tenant_id,
                        job.id,
                        DocumentTaskStatus.FAILED,
                        error=DocumentTaskError(
                            code="workflow_run_failed",
                            message="The document workflow failed before a draft was produced",
                        ),
                    )
                    return
                elif event.type is RunEventType.RUN_CANCELLED:
                    await self._tasks.transition(
                        principal.tenant_id,
                        job.id,
                        DocumentTaskStatus.CANCELLED,
                    )
                    return
        except Exception as exc:
            await self._tasks.transition(
                principal.tenant_id,
                job.id,
                DocumentTaskStatus.FAILED,
                error=DocumentTaskService._task_error(exc),
            )
