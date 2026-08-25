from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable, Coroutine
from typing import Any
from uuid import UUID, uuid4

from app.assets.schemas import Asset, AssetLineageRelation, AssetStatus
from app.core.repository import InMemoryRepository
from app.documents.schemas import (
    DocumentGenerationCreate,
    DocumentGenerationJob,
    DocumentOutputFormat,
    DocumentSource,
    DocumentSourceKind,
    DocumentTaskStatus,
)
from app.documents.service import (
    DeterministicMarkdownComposer,
    DocumentTaskService,
    DraftComposer,
    ModelGatewayMarkdownComposer,
)
from app.identity.schemas import Permission, Principal
from app.model_gateway.schemas import ModelProvider, ModelRequest, ModelResponse, ModelUsage


def async_test(fn: Callable[..., Coroutine[Any, Any, None]]) -> Callable[..., None]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        asyncio.run(fn(*args, **kwargs))

    return wrapper


class RecordingJobRepository(InMemoryRepository[DocumentGenerationJob]):
    def __init__(self) -> None:
        super().__init__()
        self.transitions: list[DocumentTaskStatus] = []

    async def update(self, entity: DocumentGenerationJob) -> DocumentGenerationJob:
        self.transitions.append(entity.status)
        return await super().update(entity)


class RecordingStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.deleted: list[str] = []

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        self.objects[key] = (bytes(data), content_type)

    async def get(self, key: str) -> bytes:
        return self.objects[key][0]

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.objects.pop(key, None)

    async def create_upload_url(self, key: str, *, content_type: str, expires_in: int = 900) -> str:
        del content_type, expires_in
        return f"memory://{key}"


class CountingComposer(DeterministicMarkdownComposer):
    def __init__(self) -> None:
        self.calls = 0

    async def compose(self, job: DocumentGenerationJob):
        self.calls += 1
        return await super().compose(job)


class FailingComposer:
    def __init__(self) -> None:
        self.calls = 0

    async def compose(self, job: DocumentGenerationJob):
        del job
        self.calls += 1
        raise RuntimeError("composer unavailable")


class RejectingAssetRepository(InMemoryRepository[Asset]):
    async def add(self, entity: Asset) -> Asset:
        del entity
        raise RuntimeError("asset repository unavailable")


class RecordingModelGateway:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(
            provider=ModelProvider.OPENAI,
            model="provider-model",
            content="# 模型生成正文\n\n内容。",
            usage=ModelUsage(input_tokens=10, output_tokens=6),
        )

    async def stream(self, request: ModelRequest):
        del request
        if False:
            yield ""


def principal() -> Principal:
    return Principal(
        user_id=uuid4(),
        tenant_id=uuid4(),
        display_name="Document Test User",
        permissions=frozenset(Permission),
    )


def job_for(principal_value: Principal, *, source_asset_id: UUID | None = None) -> DocumentGenerationJob:
    sources = [DocumentSource(kind=DocumentSourceKind.USER_INPUT, label="补充说明")]
    if source_asset_id is not None:
        sources.append(
            DocumentSource(
                kind=DocumentSourceKind.ASSET,
                id=str(source_asset_id),
                label="季度计划",
            )
        )
    payload = DocumentGenerationCreate(
        title="产品周报",
        workflow_definition_id=str(uuid4()),
        knowledge_base_ids=[],
        logical_model_code="enterprise-doc-main",
        instructions="总结本周完成事项，并列出下周计划。",
        sources=sources,
        output_format=DocumentOutputFormat.MARKDOWN,
    )
    return DocumentGenerationJob.from_create(principal_value, payload)


@async_test
async def test_markdown_pipeline_persists_draft_asset_with_lineage() -> None:
    actor = principal()
    source_asset_id = uuid4()
    job = job_for(actor, source_asset_id=source_asset_id)
    workflow_run_id, trace_id = job.workflow_run_id, job.trace_id
    jobs = RecordingJobRepository()
    assets: InMemoryRepository[Asset] = InMemoryRepository()
    storage = RecordingStorage()
    composer = CountingComposer()
    await jobs.add(job)
    service = DocumentTaskService(jobs, assets, storage, composer)

    settled = await service.settle(job)

    assert settled.status is DocumentTaskStatus.SUCCEEDED
    assert jobs.transitions == [DocumentTaskStatus.RUNNING, DocumentTaskStatus.SUCCEEDED]
    assert settled.workflow_run_id == workflow_run_id
    assert settled.trace_id == trace_id
    assert settled.draft_asset_id is not None
    asset = await assets.get(actor.tenant_id, settled.draft_asset_id)
    assert asset is not None
    assert asset.status is AssetStatus.DRAFT, "生成完成只能产生草稿资产，不得自动发布"
    assert asset.workflow_run_id == workflow_run_id
    assert asset.trace_id == trace_id
    assert asset.storage_uri is not None
    assert asset.content_hash
    assert [(item.asset_id, item.relation) for item in asset.lineage] == [
        (str(source_asset_id), AssetLineageRelation.DERIVED_FROM)
    ]
    content = await storage.get(asset.storage_uri)
    assert content.startswith("# 产品周报".encode())
    assert "下周计划".encode() in content
    assert composer.calls == 1


@async_test
async def test_repeated_settle_of_succeeded_job_is_idempotent() -> None:
    actor = principal()
    job = job_for(actor)
    jobs = RecordingJobRepository()
    assets: InMemoryRepository[Asset] = InMemoryRepository()
    storage = RecordingStorage()
    composer = CountingComposer()
    await jobs.add(job)
    service = DocumentTaskService(jobs, assets, storage, composer)

    first = await service.settle(job)
    second = await service.settle(job)

    assert second == first
    assert composer.calls == 1
    _, total = await assets.list(actor.tenant_id, limit=100, offset=0)
    assert total == 1
    assert jobs.transitions == [DocumentTaskStatus.RUNNING, DocumentTaskStatus.SUCCEEDED]


@async_test
async def test_composer_failure_settles_failed_once_without_asset_or_object() -> None:
    actor = principal()
    job = job_for(actor)
    workflow_run_id, trace_id = job.workflow_run_id, job.trace_id
    jobs = RecordingJobRepository()
    assets: InMemoryRepository[Asset] = InMemoryRepository()
    storage = RecordingStorage()
    composer = FailingComposer()
    await jobs.add(job)
    service = DocumentTaskService(jobs, assets, storage, composer)

    first = await service.settle(job)
    second = await service.settle(job)

    assert first.status is DocumentTaskStatus.FAILED
    assert second == first
    assert first.workflow_run_id == workflow_run_id
    assert first.trace_id == trace_id
    assert first.draft_asset_id is None
    assert first.error is not None and first.error.code == "document_generation_failed"
    assert composer.calls == 1
    assert storage.objects == {}
    _, total = await assets.list(actor.tenant_id, limit=100, offset=0)
    assert total == 0
    assert jobs.transitions == [DocumentTaskStatus.RUNNING, DocumentTaskStatus.FAILED]


@async_test
async def test_asset_failure_compensates_written_object_and_does_not_publish() -> None:
    actor = principal()
    job = job_for(actor)
    jobs = RecordingJobRepository()
    assets = RejectingAssetRepository()
    storage = RecordingStorage()
    await jobs.add(job)
    composer: DraftComposer = DeterministicMarkdownComposer()
    service = DocumentTaskService(jobs, assets, storage, composer)

    settled = await service.settle(job)

    assert settled.status is DocumentTaskStatus.FAILED
    assert settled.draft_asset_id is None
    assert storage.objects == {}
    assert len(storage.deleted) == 1
    _, total = await assets.list(actor.tenant_id, limit=100, offset=0)
    assert total == 0


@async_test
async def test_model_gateway_composer_uses_logical_model_and_returns_markdown() -> None:
    actor = principal()
    job = job_for(actor)
    gateway = RecordingModelGateway()
    composer = ModelGatewayMarkdownComposer(gateway)

    draft = await composer.compose(job)

    assert draft.content == "# 模型生成正文\n\n内容。".encode()
    assert draft.output_format is DocumentOutputFormat.MARKDOWN
    assert len(gateway.requests) == 1
    assert gateway.requests[0].model == "enterprise-doc-main"
    assert gateway.requests[0].metadata == {
        "traceId": str(job.trace_id),
        "workflowRunId": str(job.workflow_run_id),
    }
