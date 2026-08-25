from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_agent_create_is_idempotent_and_returns_owned_workflow_contract() -> None:
    with TestClient(create_app(Settings(environment="test", storage_backend="memory"))) as api:
        departments = api.get("/api/v1/agents/manageable-departments")
        assert departments.status_code == 200
        assert departments.json() == [{"id": "dept-platform", "name": "dept-platform"}]

        payload = {
            "name": "企业文档助手",
            "description": "生成并留存企业文档",
            "ownerDepartmentId": "dept-platform",
        }
        headers = {"Idempotency-Key": "agent-create-contract-001"}
        first = api.post("/api/v1/agents", json=payload, headers=headers)
        replay = api.post("/api/v1/agents", json=payload, headers=headers)

        assert first.status_code == 201, first.text
        assert replay.status_code == 201, replay.text
        assert replay.json() == first.json()
        assert first.headers["etag"] == '"1"'
        created = first.json()
        assert created["name"] == payload["name"]
        assert created["lifecycleStatus"] == "active"
        assert created["aggregateRevision"] == 1
        assert created["hasUnpublishedChanges"] is True
        assert created["publishedVersion"] is None
        assert created["ownedWorkflowDraftId"]
        assert not {key for key in created if "_" in key}

        page = api.get("/api/v1/agents?limit=12&offset=0")
        assert page.status_code == 200
        assert page.json()["items"] == [created]
        assert page.json()["total"] == 1

        draft = api.get(f"/api/v1/agents/{created['id']}/workflow-draft")
        assert draft.status_code == 200, draft.text
        assert draft.json()["workflowDraftId"] == created["ownedWorkflowDraftId"]
        assert draft.json()["definition"] == {"nodes": [], "edges": []}
        assert draft.headers["etag"] == '"1"'

        saved = api.put(
            f"/api/v1/agents/{created['id']}/workflow-draft",
            headers={"If-Match": '"1"'},
            json={"definition": {"nodes": [{"id": "trigger"}], "edges": []}},
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["aggregateRevision"] == 2
        assert saved.json()["definition"]["nodes"] == [{"id": "trigger"}]
        assert saved.headers["etag"] == '"2"'

        invalid = api.put(
            f"/api/v1/agents/{created['id']}/workflow-draft",
            headers={"If-Match": '"2"'},
            json={"definition": {"unexpected": True}},
        )
        assert invalid.status_code == 422
        assert invalid.json()["code"] == "request_validation"


def test_agent_create_rejects_unmanaged_department_and_is_tenant_isolated() -> None:
    app = create_app(Settings(environment="test", storage_backend="memory"))
    with TestClient(app) as api:
        denied = api.post(
            "/api/v1/agents",
            headers={"Idempotency-Key": "agent-create-contract-002"},
            json={"name": "越权 Agent", "ownerDepartmentId": "dept-finance"},
        )
        assert denied.status_code == 403
        assert denied.json()["code"] == "forbidden"
        assert denied.json()["requestId"]

        created = api.post(
            "/api/v1/agents",
            headers={"Idempotency-Key": "agent-create-contract-003"},
            json={"name": "租户 A Agent", "ownerDepartmentId": "dept-platform"},
        )
        assert created.status_code == 201

        other_tenant = api.get(
            "/api/v1/agents",
            headers={"X-Dev-Tenant-Id": "00000000-0000-4000-8000-000000000099"},
        )
        assert other_tenant.status_code == 200
        assert other_tenant.json()["items"] == []


def test_use_only_department_member_cannot_read_private_workflow_draft() -> None:
    with TestClient(create_app(Settings(environment="test", storage_backend="memory"))) as api:
        owner_headers = {
            "X-Dev-Department-Ids": "dept-shared",
            "Idempotency-Key": "agent-private-workflow-001",
        }
        created = api.post(
            "/api/v1/agents",
            headers=owner_headers,
            json={"name": "私有草稿", "ownerDepartmentId": "dept-shared"},
        )
        assert created.status_code == 201

        colleague = api.get(
            f"/api/v1/agents/{created.json()['id']}/workflow-draft",
            headers={
                "X-Dev-Department-Ids": "dept-shared",
                "X-Dev-User-Id": "00000000-0000-4000-8000-000000000002",
                "X-Dev-Permissions": "agent.read",
            },
        )
        assert colleague.status_code == 403
        assert colleague.json()["code"] == "forbidden"
