from __future__ import annotations

import hashlib
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "apps" / "api"))

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


DEFAULT_USER_ID = "00000000-0000-4000-8000-000000000001"
TENANT_A = "00000000-0000-4000-8000-000000000010"
TENANT_B = "00000000-0000-4000-8000-000000000099"


def tenant_headers(tenant_id: str, *, idempotency_key: str | None = None) -> dict[str, str]:
    headers = {"X-Dev-Tenant-Id": tenant_id}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def knowledge_base_payload(name: str = "企业制度库") -> dict[str, Any]:
    return {
        "name": name,
        "description": "制度、流程与项目资料",
        "ownerDepartmentId": "dept-product",
        "securityLevel": "internal",
        "embeddingModelCode": "offline-lexical-v1",
    }


def create_knowledge_base(
    api: TestClient,
    *,
    tenant_id: str = TENANT_A,
    key: str = "integration-kb-create-001",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = api.post(
        "/api/v1/knowledge-bases",
        headers=tenant_headers(tenant_id, idempotency_key=key),
        json=payload or knowledge_base_payload(),
    )
    assert response.status_code == 201, response.text
    return response.json()


def upload_document(
    api: TestClient,
    knowledge_base_id: str,
    *,
    filename: str,
    content: bytes,
    mime_type: str,
    tenant_id: str = TENANT_A,
    key: str = "integration-upload-001",
) -> dict[str, Any]:
    response = api.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        headers=tenant_headers(tenant_id, idempotency_key=key),
        data={"dataScope": "department", "securityLevel": "internal"},
        files={"file": (filename, content, mime_type)},
    )
    assert response.status_code == 202, response.text
    return response.json()


def workflow_definition(name: str = "企业文档生产") -> dict[str, Any]:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {
        "id": str(uuid4()),
        "name": name,
        "description": "离线确定性文档生产集成流程",
        "definitionVersion": 1,
        "status": "published",
        "entryNodeId": "input",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "name": "接收请求",
                "version": 1,
                "position": {"x": 0, "y": 0},
                "config": {},
            },
            {
                "id": "llm",
                "type": "llm",
                "name": "确定性生成",
                "version": 1,
                "position": {"x": 200, "y": 0},
                "config": {"logicalModelCode": "local-deterministic"},
            },
            {
                "id": "output",
                "type": "output",
                "name": "输出草稿",
                "version": 1,
                "position": {"x": 400, "y": 0},
                "config": {},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "sourceNodeId": "input",
                "targetNodeId": "llm",
                "condition": {"kind": "always"},
            },
            {
                "id": "e2",
                "sourceNodeId": "llm",
                "targetNodeId": "output",
                "condition": {"kind": "on_success"},
            },
        ],
        "ownerDepartmentId": "dept-product",
        "createdBy": DEFAULT_USER_ID,
        "createdAt": now,
        "updatedAt": now,
    }


def save_workflow(
    api: TestClient,
    *,
    tenant_id: str = TENANT_A,
    key: str = "integration-workflow-save-001",
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = api.post(
        "/api/v1/workflow-definitions",
        headers=tenant_headers(tenant_id, idempotency_key=key),
        json=payload or workflow_definition(),
    )
    assert response.status_code == 201, response.text
    return response.json()


def document_task_payload(workflow_id: str, knowledge_base_id: str) -> dict[str, Any]:
    return {
        "title": "产品周报",
        "workflowDefinitionId": workflow_id,
        "knowledgeBaseIds": [knowledge_base_id],
        "logicalModelCode": "local-deterministic",
        "instructions": "总结本周进展、风险和下周计划",
        "sources": [{"kind": "user_input", "label": "集成测试补充说明"}],
        "outputFormat": "markdown",
    }


def create_document_task(
    api: TestClient,
    payload: dict[str, Any],
    *,
    tenant_id: str = TENANT_A,
    key: str = "integration-document-task-001",
) -> dict[str, Any]:
    response = api.post(
        "/api/v1/document-tasks",
        headers=tenant_headers(tenant_id, idempotency_key=key),
        json=payload,
    )
    assert response.status_code == 202, response.text
    return response.json()


def wait_for_task(
    api: TestClient,
    task_id: str,
    expected_status: str,
    *,
    tenant_id: str = TENANT_A,
    timeout_seconds: float = 2,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = api.get(
            f"/api/v1/document-tasks/{task_id}",
            headers=tenant_headers(tenant_id),
        )
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] == expected_status:
            return last
        if last["status"] in {"failed", "cancelled"}:
            raise AssertionError(f"task reached {last['status']} instead of {expected_status}: {last}")
        time.sleep(0.01)
    raise AssertionError(f"task never reached {expected_status}; last snapshot={last}")


def test_idempotency_replays_all_phase_one_writes_and_rejects_payload_drift() -> None:
    app = create_app(Settings(environment="test", storage_backend="memory"))
    with TestClient(app) as api:
        shared_key = "integration-shared-idempotency-001"

        kb_payload = knowledge_base_payload()
        first_kb = create_knowledge_base(api, key=shared_key, payload=kb_payload)
        replayed_kb = create_knowledge_base(api, key=shared_key, payload=kb_payload)
        assert replayed_kb == first_kb
        assert len(api.app.state.container.knowledge_repository._items) == 1
        conflict = api.post(
            "/api/v1/knowledge-bases",
            headers=tenant_headers(TENANT_A, idempotency_key=shared_key),
            json=knowledge_base_payload("不同名称"),
        )
        assert conflict.status_code == 409

        source = "产品周报 总结本周进展、风险和下周计划。".encode()
        first_upload = upload_document(
            api,
            first_kb["id"],
            filename="weekly.txt",
            content=source,
            mime_type="text/plain",
            key=shared_key,
        )
        replayed_upload = upload_document(
            api,
            first_kb["id"],
            filename="weekly.txt",
            content=source,
            mime_type="text/plain",
            key=shared_key,
        )
        assert replayed_upload == first_upload
        assert len(api.app.state.container.asset_repository._items) == 1
        conflict = api.post(
            f"/api/v1/knowledge-bases/{first_kb['id']}/documents",
            headers=tenant_headers(TENANT_A, idempotency_key=shared_key),
            files={"file": ("weekly.txt", b"different content", "text/plain")},
        )
        assert conflict.status_code == 409

        definition_payload = workflow_definition()
        workflow = save_workflow(api, key=shared_key, payload=definition_payload)
        assert save_workflow(api, key=shared_key, payload=definition_payload) == workflow
        task_payload = document_task_payload(workflow["id"], first_kb["id"])
        first_task = create_document_task(api, task_payload, key=shared_key)
        replayed_task = create_document_task(api, task_payload, key=shared_key)
        assert replayed_task == first_task
        assert len(api.app.state.container.document_repository._items) == 1
        conflict_payload = {**task_payload, "instructions": "不同的生成要求"}
        conflict = api.post(
            "/api/v1/document-tasks",
            headers=tenant_headers(TENANT_A, idempotency_key=shared_key),
            json=conflict_payload,
        )
        assert conflict.status_code == 409

        run_request = {"workflowDefinitionVersion": 1, "input": {"topic": "幂等运行"}}
        first_run_response = api.post(
            f"/api/v1/workflows/{workflow['id']}/runs",
            headers=tenant_headers(TENANT_A, idempotency_key=shared_key),
            json=run_request,
        )
        replayed_run_response = api.post(
            f"/api/v1/workflows/{workflow['id']}/runs",
            headers=tenant_headers(TENANT_A, idempotency_key=shared_key),
            json=run_request,
        )
        assert first_run_response.status_code == replayed_run_response.status_code == 202
        assert replayed_run_response.json() == first_run_response.json()
        conflict = api.post(
            f"/api/v1/workflows/{workflow['id']}/runs",
            headers=tenant_headers(TENANT_A, idempotency_key=shared_key),
            json={**run_request, "input": {"topic": "不同输入"}},
        )
        assert conflict.status_code == 409

        # One run belongs to the document task and one to the explicit start.
        assert len(api.app.state.container.workflow_run_service._runs._runs) == 2


def test_text_upload_is_indexed_and_returns_traceable_citation() -> None:
    app = create_app(Settings(environment="test", storage_backend="memory"))
    with TestClient(app) as api:
        knowledge_base = create_knowledge_base(api)
        uploaded = upload_document(
            api,
            knowledge_base["id"],
            filename="leave-policy.txt",
            content="休假制度：正式员工每年享有十天带薪年假。".encode(),
            mime_type="text/plain",
        )
        assert uploaded["status"] == "indexed"
        assert uploaded["indexedAt"]
        assert "sourceUri" not in uploaded

        response = api.post(
            f"/api/v1/knowledge-bases/{knowledge_base['id']}/search",
            headers=tenant_headers(TENANT_A),
            json={"query": "十天带薪年假", "topK": 5, "filters": {}},
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["traceId"]
        assert result["citations"]
        citation = result["citations"][0]
        assert citation["knowledgeDocumentId"] == uploaded["id"]
        assert citation["assetId"] == uploaded["assetId"]
        assert citation["chunkId"]
        assert "十天带薪年假" in citation["quote"]
        assert 0 < citation["score"] <= 1


def test_document_task_completes_with_shared_run_trace_hash_and_lineage() -> None:
    app = create_app(Settings(environment="test", storage_backend="memory"))
    with TestClient(app) as api:
        knowledge_base = create_knowledge_base(api)
        uploaded = upload_document(
            api,
            knowledge_base["id"],
            filename="weekly.md",
            content=(
                "# 产品周报\n\n本周进展完成离线知识检索，风险是索引仍为内存，"
                "下周计划是接入 PostgreSQL。"
            ).encode(),
            mime_type="text/markdown",
        )
        workflow = save_workflow(api)
        task = create_document_task(
            api,
            document_task_payload(workflow["id"], knowledge_base["id"]),
        )
        completed = wait_for_task(api, task["taskId"], "succeeded")
        assert completed["draftAssetId"]
        assert completed["citations"]
        assert completed["citations"][0]["knowledgeDocumentId"] == uploaded["id"]

        run_response = api.get(f"/api/v1/workflow-runs/{completed['workflowRunId']}")
        assert run_response.status_code == 200, run_response.text
        run = run_response.json()
        assert run["status"] == "succeeded"
        assert run["id"] == completed["workflowRunId"]
        assert run["traceId"] == completed["traceId"]

        asset_response = api.get(f"/api/v1/assets/{completed['draftAssetId']}")
        assert asset_response.status_code == 200, asset_response.text
        asset = asset_response.json()
        assert asset["status"] == "draft"
        assert asset["type"] == "document"
        assert asset["mimeType"].startswith("text/markdown")
        assert asset["workflowRunId"] == completed["workflowRunId"]
        assert asset["traceId"] == completed["traceId"]
        assert asset["contentHash"]
        assert {
            (item["assetId"], item["relation"])
            for item in asset["lineage"]
        } >= {(uploaded["assetId"], "derived_from")}

        internal_asset = api.portal.call(
            api.app.state.container.asset_repository.get,
            UUID(TENANT_A),
            UUID(asset["id"]),
        )
        assert internal_asset is not None
        assert internal_asset.storage_uri
        assert asset["storageUri"].startswith("/api/v1/assets/downloads/")
        assert asset["storageUri"] != internal_asset.storage_uri
        assert internal_asset.storage_uri not in asset["storageUri"]

        download = api.get(asset["storageUri"])
        assert download.status_code == 200
        assert download.headers["cache-control"] == "private, no-store"

        # ObjectStorage is async; TestClient exposes its portal for safe calls on
        # the same application event loop.
        draft_bytes = api.portal.call(
            api.app.state.container.object_storage.get,
            internal_asset.storage_uri,
        )
        assert hashlib.sha256(draft_bytes).hexdigest() == asset["contentHash"]
        text = draft_bytes.decode("utf-8")
        assert "# 产品周报" in text
        assert "总结本周进展、风险和下周计划" in text


def test_pdf_index_failure_never_returns_a_citation() -> None:
    app = create_app(Settings(environment="test", storage_backend="memory"))
    with TestClient(app) as api:
        knowledge_base = create_knowledge_base(api)
        failed = upload_document(
            api,
            knowledge_base["id"],
            filename="unsupported.pdf",
            content=b"%PDF-1.7\nunique-pdf-token\n%%EOF",
            mime_type="application/pdf",
        )
        assert failed["status"] == "failed"
        assert "indexedAt" not in failed
        assert "sourceUri" not in failed

        response = api.post(
            f"/api/v1/knowledge-bases/{knowledge_base['id']}/search",
            headers=tenant_headers(TENANT_A),
            json={"query": "unique-pdf-token", "topK": 10, "filters": {}},
        )
        assert response.status_code == 200
        assert response.json()["citations"] == []


def test_knowledge_task_run_and_assets_are_isolated_between_tenants() -> None:
    app = create_app(Settings(environment="test", storage_backend="memory"))
    with TestClient(app) as api:
        knowledge_base = create_knowledge_base(api)
        uploaded = upload_document(
            api,
            knowledge_base["id"],
            filename="tenant-a.txt",
            content="租户 A 的产品周报和下周计划。".encode(),
            mime_type="text/plain",
        )
        workflow = save_workflow(api)
        task = create_document_task(
            api,
            document_task_payload(workflow["id"], knowledge_base["id"]),
        )
        completed = wait_for_task(api, task["taskId"], "succeeded")

        tenant_b_headers = tenant_headers(TENANT_B)
        listing = api.get("/api/v1/knowledge-bases", headers=tenant_b_headers)
        assert listing.status_code == 200
        assert listing.json() == []

        for method, path, body in [
            (
                "post",
                f"/api/v1/knowledge-bases/{knowledge_base['id']}/search",
                {"query": "产品周报", "topK": 5, "filters": {}},
            ),
            ("get", f"/api/v1/assets/{uploaded['assetId']}", None),
            ("get", f"/api/v1/assets/{completed['draftAssetId']}", None),
            ("get", f"/api/v1/document-tasks/{completed['taskId']}", None),
            ("get", f"/api/v1/workflow-runs/{completed['workflowRunId']}", None),
        ]:
            response = api.request(method, path, headers=tenant_b_headers, json=body)
            assert response.status_code == 404, (method, path, response.text)
