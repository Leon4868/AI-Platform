"""HTTP contract of the workflow-run API.

These tests pin the two things the adapter exists for: the wire vocabulary of
`packages/contracts` (camelCase, contract event names only) and SSE resumption.
"""

import json
from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.workflow_runs.schemas import RunEventType

CONTRACT_EVENT_TYPES = {member.value for member in RunEventType}
DEFAULT_USER_ID = "00000000-0000-4000-8000-000000000001"


def linear_graph() -> dict:
    return {
        "schema_version": "1.0",
        "nodes": [
            {"id": "start", "type": "start", "name": "开始", "position": {"x": 0, "y": 0}},
            {"id": "model", "type": "model", "name": "模型", "position": {"x": 200, "y": 0}},
            {"id": "end", "type": "end", "name": "结束", "position": {"x": 400, "y": 0}},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "model"},
            {"id": "e2", "source": "model", "target": "end"},
        ],
    }


def approval_graph() -> dict:
    return {
        "schema_version": "1.0",
        "nodes": [
            {"id": "start", "type": "start", "name": "开始", "position": {"x": 0, "y": 0}},
            {
                "id": "review",
                "type": "approval",
                "name": "人工审核",
                "position": {"x": 200, "y": 0},
                "config": {"prompt": "请审核", "approvers": ["reviewer"]},
            },
            {"id": "end", "type": "end", "name": "结束", "position": {"x": 400, "y": 0}},
        ],
        "edges": [
            {"id": "e1", "source": "start", "target": "review"},
            {"id": "e2", "source": "review", "target": "end"},
        ],
    }


def client() -> TestClient:
    return TestClient(
        create_app(Settings(environment="test")),
        headers={"Idempotency-Key": "workflow-run-http-tests"},
    )


def create_workflow(api: TestClient, graph: dict, **kwargs) -> str:
    type_map = {
        "start": "input",
        "model": "llm",
        "knowledge_retrieval": "knowledge_search",
        "approval": "human_review",
        "end": "output",
    }
    now = datetime.now(UTC).isoformat()
    workflow_id = str(uuid4())
    headers = {
        "Idempotency-Key": f"runtime-test-{workflow_id}",
        **kwargs.pop("headers", {}),
    }
    response = api.post(
        "/api/v1/workflow-definitions",
        headers=headers,
        json={
            "id": workflow_id,
            "name": "运行时用例",
            "description": "运行态契约测试",
            "definitionVersion": 1,
            "status": "published",
            "entryNodeId": graph["nodes"][0]["id"],
            "nodes": [
                {
                    "id": node["id"],
                    "type": type_map[node["type"]],
                    "name": node["name"],
                    "version": 1,
                    "position": node["position"],
                    "config": node.get("config", {}),
                }
                for node in graph["nodes"]
            ],
            "edges": [
                {
                    "id": edge["id"],
                    "sourceNodeId": edge["source"],
                    "targetNodeId": edge["target"],
                    "condition": {"kind": "always"},
                }
                for edge in graph["edges"]
            ],
            "ownerDepartmentId": "dept-runtime-test",
            "createdBy": DEFAULT_USER_ID,
            "createdAt": now,
            "updatedAt": now,
        },
        **kwargs,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def parse_sse(body: str) -> list[dict]:
    """Splits an SSE body into ``{"id", "event", "data"}`` records."""
    frames = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        frame: dict = {}
        for line in block.splitlines():
            field, _, value = line.partition(": ")
            frame[field] = value
        frames.append(frame)
    return frames


def stream_events(api: TestClient, run_id: str, **kwargs) -> list[dict]:
    response = api.get(f"/api/v1/workflow-runs/{run_id}/events", **kwargs)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_sse(response.text)


def await_status(api: TestClient, run_id: str, wanted: str, attempts: int = 50) -> dict:
    for _ in range(attempts):
        run = api.get(f"/api/v1/workflow-runs/{run_id}").json()
        if run["status"] == wanted:
            return run
    raise AssertionError(f"run never reached {wanted}; last status {run['status']}")


def test_start_run_answers_in_camel_case() -> None:
    with client() as api:
        workflow_id = create_workflow(api, linear_graph())
        response = api.post(f"/api/v1/workflows/{workflow_id}/runs", json={"input": {"topic": "周报"}})

        assert response.status_code == 202, response.text
        run = response.json()
        assert {
            "workflowDefinitionId",
            "workflowDefinitionVersion",
            "initiatedBy",
            "permissionSnapshot",
            "nodeRuns",
            "traceId",
            "createdAt",
        } <= run.keys()
        assert not {key for key in run if "_" in key}
        assert run["workflowDefinitionId"] == workflow_id
        assert run["workflowDefinitionVersion"] == 1
        assert run["input"] == {"topic": "周报"}
        assert run["permissionSnapshot"] == {
            "subjectId": run["initiatedBy"],
            "departmentIds": [],
            "projectIds": [],
            "roles": ["employee"],
            "allowedScopes": ["personal"],
            "securityClearance": "internal",
            "capturedAt": run["createdAt"],
            "policyVersion": "temporary-identity-v1",
        }
        assert run["traceId"]


def test_stream_uses_contract_event_names_and_monotonic_ids() -> None:
    with client() as api:
        workflow_id = create_workflow(api, linear_graph())
        run_id = api.post(f"/api/v1/workflows/{workflow_id}/runs", json={"input": {}}).json()["id"]

        frames = stream_events(api, run_id)

        assert frames, "stream closed without a single event"
        assert all(frame.keys() >= {"id", "event", "data"} for frame in frames)

        types = [frame["event"] for frame in frames]
        assert set(types) <= CONTRACT_EVENT_TYPES, set(types) - CONTRACT_EVENT_TYPES
        # The internal vocabulary must not reach a client.
        assert "node.succeeded" not in types
        assert "run.succeeded" not in types
        assert "node.resumed" not in types
        assert "node.skipped" not in types

        assert types[0] == "run.queued"
        assert types[-1] == "run.completed"
        assert "node.completed" in types

        sequences = [int(frame["id"]) for frame in frames]
        assert sequences == sorted(set(sequences)), sequences

        payload = json.loads(frames[-1]["data"])
        assert payload["runId"] == run_id
        assert payload["sequence"] == sequences[-1]
        assert await_status(api, run_id, "succeeded")["nodeRuns"][0]["attempt"] == 1


def test_last_event_id_resumes_after_the_cursor() -> None:
    with client() as api:
        workflow_id = create_workflow(api, linear_graph())
        run_id = api.post(f"/api/v1/workflows/{workflow_id}/runs", json={"input": {}}).json()["id"]

        everything = stream_events(api, run_id)
        cursor = int(everything[1]["id"])
        resumed = stream_events(api, run_id, headers={"Last-Event-ID": str(cursor)})

        assert [int(frame["id"]) for frame in resumed] == [
            int(frame["id"]) for frame in everything if int(frame["id"]) > cursor
        ]


def test_malformed_last_event_id_replays_instead_of_failing() -> None:
    with client() as api:
        workflow_id = create_workflow(api, linear_graph())
        run_id = api.post(f"/api/v1/workflows/{workflow_id}/runs", json={"input": {}}).json()["id"]

        everything = stream_events(api, run_id)
        replayed = stream_events(api, run_id, headers={"Last-Event-ID": "not-a-number"})

        assert [frame["id"] for frame in replayed] == [frame["id"] for frame in everything]


def test_approval_run_waits_then_cancels() -> None:
    with client() as api:
        workflow_id = create_workflow(api, approval_graph())
        run_id = api.post(f"/api/v1/workflows/{workflow_id}/runs", json={"input": {}}).json()["id"]

        waiting = await_status(api, run_id, "waiting_human")
        assert [node["status"] for node in waiting["nodeRuns"]][-1] == "waiting_human"
        admitted_context = (waiting["permissionSnapshot"], waiting["traceId"])

        cancelled = api.post(f"/api/v1/workflow-runs/{run_id}/cancel", json={"reason": "演示结束"})
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"
        assert (
            cancelled.json()["permissionSnapshot"],
            cancelled.json()["traceId"],
        ) == admitted_context

        types = [frame["event"] for frame in stream_events(api, run_id)]
        assert "run.waiting_human" in types
        assert "node.awaiting_approval" not in types
        assert types[-1] == "run.cancelled"


def test_cancel_closes_open_nodes_before_the_run() -> None:
    with client() as api:
        workflow_id = create_workflow(api, approval_graph())
        run_id = api.post(f"/api/v1/workflows/{workflow_id}/runs", json={"input": {}}).json()["id"]
        await_status(api, run_id, "waiting_human")
        api.post(f"/api/v1/workflow-runs/{run_id}/cancel", json={"reason": "演示结束"})

        frames = stream_events(api, run_id)
        types = [frame["event"] for frame in frames]

        # The node that was still open must reach a terminal state of its own
        # before the run does, or a replay stops mid-flight.
        assert types.index("node.cancelled") < types.index("run.cancelled")
        cancelled = frames[types.index("node.cancelled")]
        assert json.loads(cancelled["data"])["nodeId"] == "review"


def test_cancellation_events_replay_from_a_cursor() -> None:
    with client() as api:
        workflow_id = create_workflow(api, approval_graph())
        run_id = api.post(f"/api/v1/workflows/{workflow_id}/runs", json={"input": {}}).json()["id"]
        await_status(api, run_id, "waiting_human")
        api.post(f"/api/v1/workflow-runs/{run_id}/cancel", json={"reason": "演示结束"})

        everything = stream_events(api, run_id)
        cursor = int(everything[0]["id"])
        resumed = stream_events(api, run_id, headers={"Last-Event-ID": str(cursor)})

        assert [frame["event"] for frame in resumed] == [
            frame["event"] for frame in everything if int(frame["id"]) > cursor
        ]
        assert "node.cancelled" in {frame["event"] for frame in resumed}


def test_cancel_is_idempotent() -> None:
    with client() as api:
        workflow_id = create_workflow(api, approval_graph())
        run_id = api.post(f"/api/v1/workflows/{workflow_id}/runs", json={"input": {}}).json()["id"]
        await_status(api, run_id, "waiting_human")

        first = api.post(f"/api/v1/workflow-runs/{run_id}/cancel")
        second = api.post(f"/api/v1/workflow-runs/{run_id}/cancel")

        assert first.status_code == second.status_code == 200
        assert second.json()["status"] == "cancelled"
        assert second.json()["finishedAt"] == first.json()["finishedAt"]


def test_start_rejects_a_stale_definition_version() -> None:
    with client() as api:
        workflow_id = create_workflow(api, linear_graph())
        conflict = api.post(
            f"/api/v1/workflows/{workflow_id}/runs",
            json={"input": {}, "workflowDefinitionVersion": 2},
        )
        assert conflict.status_code == 409, conflict.text
        assert conflict.headers["content-type"].startswith("application/problem+json")

        accepted = api.post(
            f"/api/v1/workflows/{workflow_id}/runs",
            json={"input": {}, "workflowDefinitionVersion": 1},
        )
        assert accepted.status_code == 202


def test_start_rejects_unknown_fields() -> None:
    with client() as api:
        workflow_id = create_workflow(api, linear_graph())
        response = api.post(
            f"/api/v1/workflows/{workflow_id}/runs",
            json={"input": {}, "unexpected": True},
        )
        assert response.status_code == 422


def test_runs_are_scoped_to_a_tenant() -> None:
    tenant_a = str(uuid4())
    tenant_b = str(uuid4())
    with client() as api:
        workflow_id = create_workflow(api, linear_graph(), headers={"X-Dev-Tenant-Id": tenant_a})
        run_id = api.post(
            f"/api/v1/workflows/{workflow_id}/runs",
            headers={"X-Dev-Tenant-Id": tenant_a},
            json={"input": {}},
        ).json()["id"]

        hidden = api.get(
            f"/api/v1/workflow-runs/{run_id}", headers={"X-Dev-Tenant-Id": tenant_b}
        )
        assert hidden.status_code == 404


def test_starting_a_run_requires_write_permission() -> None:
    with client() as api:
        workflow_id = create_workflow(api, linear_graph())
        forbidden = api.post(
            f"/api/v1/workflows/{workflow_id}/runs",
            headers={"X-Dev-Permissions": "workflow.read"},
            json={"input": {}},
        )
        assert forbidden.status_code == 403


def test_unknown_run_is_rejected_before_the_stream_opens() -> None:
    with client() as api:
        response = api.get(f"/api/v1/workflow-runs/{uuid4()}/events")
        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/problem+json")
