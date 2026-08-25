from datetime import UTC, datetime
from functools import partial
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

IDEMPOTENCY_HEADERS = {"Idempotency-Key": "contract-test-001"}
DEFAULT_USER_ID = "00000000-0000-4000-8000-000000000001"


def client() -> TestClient:
    return TestClient(create_app(Settings(environment="test", storage_backend="memory")))


def workflow_definition() -> dict:
    now = datetime.now(UTC).isoformat()
    return {
        "id": str(uuid4()),
        "name": "企业周报工作流",
        "description": "契约测试",
        "definitionVersion": 1,
        "status": "published",
        "entryNodeId": "input",
        "nodes": [
            {
                "id": "input",
                "type": "input",
                "name": "输入",
                "version": 1,
                "position": {"x": 0, "y": 0},
                "config": {},
            },
            {
                "id": "llm",
                "type": "llm",
                "name": "生成",
                "version": 1,
                "position": {"x": 200, "y": 0},
                "config": {"logicalModelCode": "enterprise-doc-main"},
            },
            {
                "id": "output",
                "type": "output",
                "name": "输出",
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


def create_knowledge_base(api: TestClient) -> dict:
    response = api.post(
        "/api/v1/knowledge-bases",
        headers=IDEMPOTENCY_HEADERS,
        json={
            "name": "企业制度库",
            "description": "制度与流程",
            "ownerDepartmentId": "dept-product",
            "securityLevel": "internal",
            "embeddingModelCode": "embed-main",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_health_endpoints() -> None:
    with client() as api:
        assert api.get("/health/live").json() == {"status": "ok"}
        ready = api.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["repository"] == "memory"
        assert ready.headers["X-Request-Id"]


def test_workflow_definition_contract_is_camel_case_and_persisted_for_runtime() -> None:
    payload = workflow_definition()
    with client() as api:
        response = api.post(
            "/api/v1/workflow-definitions",
            headers=IDEMPOTENCY_HEADERS,
            json=payload,
        )
        assert response.status_code == 201, response.text
        saved = response.json()
        assert saved["id"] == payload["id"]
        assert saved["definitionVersion"] == payload["definitionVersion"]
        assert saved["nodes"] == payload["nodes"]
        assert saved["edges"] == payload["edges"]

        stored = api.app.state.container.workflow_repository._items[UUID(payload["id"])]
        assert stored.revision == payload["definitionVersion"]
        assert [node.type.value for node in stored.graph.nodes] == ["start", "model", "end"]


def test_knowledge_upload_search_and_asset_contract() -> None:
    with client() as api:
        knowledge_base = create_knowledge_base(api)
        assert set(knowledge_base) == {
            "id",
            "name",
            "description",
            "ownerDepartmentId",
            "securityLevel",
            "embeddingModelCode",
            "createdAt",
        }
        assert api.get("/api/v1/knowledge-bases").json() == [knowledge_base]

        document = api.post(
            f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents",
            headers={
                **IDEMPOTENCY_HEADERS,
                "X-Dev-Security-Clearance": "department_sensitive",
                "X-Dev-Project-Ids": "project-apollo",
            },
            data={
                "dataScope": "project",
                "securityLevel": "department_sensitive",
                "projectId": "project-apollo",
            },
            files={
                "file": (
                    "policy.txt",
                    "休假制度：员工每年享有带薪年假。".encode(),
                    "text/plain",
                )
            },
        )
        assert document.status_code == 202, document.text
        uploaded = document.json()
        assert uploaded["knowledgeBaseId"] == knowledge_base["id"]
        assert uploaded["filename"] == "policy.txt"
        assert uploaded["status"] == "indexed"

        asset = api.get(
            f"/api/v1/assets/{uploaded['assetId']}",
            headers={"X-Dev-Security-Clearance": "department_sensitive"},
        )
        assert asset.status_code == 200, asset.text
        assert asset.json()["id"] == uploaded["assetId"]
        assert asset.json()["type"] == "document"
        assert asset.json()["dataScope"] == "project"
        assert asset.json()["projectId"] == "project-apollo"
        assert asset.json()["securityLevel"] == "department_sensitive"
        assert "tenantId" not in asset.json()

        search = api.post(
            f"/api/v1/knowledge-bases/{knowledge_base['id']}/search",
            headers={"X-Dev-Security-Clearance": "department_sensitive"},
            json={"query": "休假制度", "topK": 5, "filters": {}},
        )
        assert search.status_code == 200, search.text
        assert search.json()["citations"][0]["assetId"] == uploaded["assetId"]
        assert "休假制度" in search.json()["citations"][0]["quote"]
        assert search.json()["traceId"]


def test_document_task_create_and_get_use_contract_shape() -> None:
    definition = workflow_definition()
    with client() as api:
        saved = api.post(
            "/api/v1/workflow-definitions",
            headers={"Idempotency-Key": "document-workflow-001"},
            json=definition,
        )
        assert saved.status_code == 201, saved.text
        response = api.post(
            "/api/v1/document-tasks",
            headers=IDEMPOTENCY_HEADERS,
            json={
                "title": "产品周报",
                "workflowDefinitionId": definition["id"],
                "knowledgeBaseIds": [],
                "logicalModelCode": "enterprise-doc-main",
                "instructions": "总结本周进展",
                "sources": [{"kind": "user_input", "label": "补充说明"}],
                "outputFormat": "markdown",
            },
        )
        assert response.status_code == 202, response.text
        task = response.json()
        assert task["status"] in {"queued", "running", "succeeded"}
        assert {"taskId", "workflowRunId", "traceId", "citations", "createdAt"} <= task.keys()
        assert not {key for key in task if "_" in key}

        current = task
        for _ in range(50):
            fetched = api.get(f"/api/v1/document-tasks/{task['taskId']}")
            assert fetched.status_code == 200
            current = fetched.json()
            if current["status"] in {"succeeded", "failed", "cancelled"}:
                break
        assert current["status"] == "succeeded", current
        assert current["draftAssetId"]

        run = api.get(f"/api/v1/workflow-runs/{current['workflowRunId']}")
        assert run.status_code == 200
        assert run.json()["traceId"] == current["traceId"]

        asset = api.get(f"/api/v1/assets/{current['draftAssetId']}")
        assert asset.status_code == 200
        assert asset.json()["status"] == "draft"
        assert asset.json()["workflowRunId"] == current["workflowRunId"]
        assert asset.json()["traceId"] == current["traceId"]
        assert asset.json()["contentHash"]


def test_generated_draft_inherits_security_and_remains_private_to_creator() -> None:
    definition = workflow_definition()
    actor_headers = {
        "X-Dev-Department-Ids": "dept-product",
        "X-Dev-Security-Clearance": "department_sensitive",
    }
    with client() as api:
        knowledge_base_response = api.post(
            "/api/v1/knowledge-bases",
            headers={**actor_headers, "Idempotency-Key": "sensitive-kb-001"},
            json={
                "name": "敏感制度库",
                "ownerDepartmentId": "dept-product",
                "securityLevel": "department_sensitive",
                "embeddingModelCode": "offline-lexical-v1",
            },
        )
        assert knowledge_base_response.status_code == 201
        knowledge_base = knowledge_base_response.json()
        upload_response = api.post(
            f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents",
            headers={**actor_headers, "Idempotency-Key": "sensitive-upload-001"},
            data={"dataScope": "department", "securityLevel": "department_sensitive"},
            files={"file": ("budget.txt", "凤凰预算为一百万元".encode(), "text/plain")},
        )
        assert upload_response.status_code == 202, upload_response.text

        saved = api.post(
            "/api/v1/workflow-definitions",
            headers={**actor_headers, "Idempotency-Key": "sensitive-workflow-001"},
            json=definition,
        )
        assert saved.status_code == 201, saved.text
        task_response = api.post(
            "/api/v1/document-tasks",
            headers={**actor_headers, "Idempotency-Key": "sensitive-task-001"},
            json={
                "title": "预算周报",
                "workflowDefinitionId": definition["id"],
                "knowledgeBaseIds": [knowledge_base["id"]],
                "logicalModelCode": "enterprise-doc-main",
                "instructions": "总结凤凰预算",
                "sources": [],
                "outputFormat": "markdown",
            },
        )
        assert task_response.status_code == 202, task_response.text
        task = task_response.json()
        for _ in range(50):
            fetched = api.get(f"/api/v1/document-tasks/{task['taskId']}", headers=actor_headers)
            assert fetched.status_code == 200
            task = fetched.json()
            if task["status"] in {"succeeded", "failed", "cancelled"}:
                break
        assert task["status"] == "succeeded", task

        asset = api.get(f"/api/v1/assets/{task['draftAssetId']}", headers=actor_headers)
        assert asset.status_code == 200, asset.text
        assert asset.json()["dataScope"] == "personal"
        assert asset.json()["securityLevel"] == "department_sensitive"

        colleague_headers = {
            **actor_headers,
            "X-Dev-User-Id": str(uuid4()),
        }
        assert api.get(
            f"/api/v1/document-tasks/{task['taskId']}",
            headers=colleague_headers,
        ).status_code == 404
        assert api.get(
            f"/api/v1/assets/{task['draftAssetId']}",
            headers=colleague_headers,
        ).status_code == 404


def test_memory_download_url_is_opaque_downloadable_and_expires(monkeypatch) -> None:
    with client() as api:
        storage = api.app.state.container.object_storage
        object_key = "tenants/private/assets/draft.md"
        api.portal.call(partial(storage.put, object_key, b"private draft", content_type="text/markdown"))
        download_url = api.portal.call(partial(storage.create_download_url, object_key, expires_in=1))

        assert download_url.startswith("/api/v1/assets/downloads/")
        assert object_key not in download_url
        available = api.get(download_url)
        assert available.status_code == 200
        assert available.content == b"private draft"
        assert available.headers["cache-control"] == "private, no-store"

        monkeypatch.setattr("app.core.storage.monotonic", lambda: float("inf"))
        expired = api.get(download_url)
        assert expired.status_code == 404
        assert object_key not in expired.text


def test_idempotency_header_and_permissions_are_enforced() -> None:
    with client() as api:
        missing_key = api.post(
            "/api/v1/knowledge-bases",
            json={
                "name": "无幂等键",
                "ownerDepartmentId": "dept-product",
                "securityLevel": "internal",
                "embeddingModelCode": "embed-main",
            },
        )
        assert missing_key.status_code == 422

        forbidden = api.get(
            "/api/v1/knowledge-bases",
            headers={"X-Dev-Permissions": "asset.read"},
        )
        assert forbidden.status_code == 403


def test_undocumented_legacy_v1_routes_are_not_exposed() -> None:
    with client() as api:
        for method, path in [
            ("get", "/api/v1/audit-events"),
            ("get", "/api/v1/document-jobs"),
            ("post", "/api/v1/assets"),
            ("get", "/api/v1/workflows"),
            ("post", "/api/v1/workflows/validate"),
        ]:
            assert api.request(method, path).status_code in {404, 405}


def test_production_refuses_development_identity() -> None:
    try:
        create_app(Settings(environment="production"))
    except RuntimeError as exc:
        assert "production identity provider" in str(exc)
    else:
        raise AssertionError("production application accepted development identity")
