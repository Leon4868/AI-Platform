from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "apps" / "api"))

from app.core.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402


def approval_definition() -> dict[str, Any]:
    now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    return {
        "id": str(uuid4()),
        "name": "企业资产人工审批",
        "description": "无外部模型的运行态验收流程",
        "definitionVersion": 1,
        "status": "published",
        "entryNodeId": "start",
        "nodes": [
            {
                "id": "start",
                "type": "input",
                "name": "开始",
                "version": 1,
                "position": {"x": 0, "y": 0},
                "config": {},
            },
            {
                "id": "review",
                "type": "human_review",
                "name": "人工审批",
                "version": 1,
                "position": {"x": 200, "y": 0},
                "config": {"reviewerRole": "asset_reviewer"},
            },
            {
                "id": "end",
                "type": "output",
                "name": "结束",
                "version": 1,
                "position": {"x": 400, "y": 0},
                "config": {},
            },
        ],
        "edges": [
            {
                "id": "e1",
                "sourceNodeId": "start",
                "targetNodeId": "review",
                "condition": {"kind": "always"},
            },
            {
                "id": "e2",
                "sourceNodeId": "review",
                "targetNodeId": "end",
                "condition": {"kind": "always"},
            },
        ],
        "ownerDepartmentId": "dept_integration",
        "createdBy": "00000000-0000-4000-8000-000000000001",
        "createdAt": now,
        "updatedAt": now,
    }


def wait_for_status(
    api: TestClient,
    run_id: str,
    expected: str,
    *,
    timeout_seconds: float = 2,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        response = api.get(f"/api/v1/workflow-runs/{run_id}")
        assert response.status_code == 200, response.text
        last = response.json()
        if last["status"] == expected:
            return last
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} never reached {expected}; last snapshot={last}")


def parse_sse(text: str) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    for block in text.strip().split("\n\n"):
        if not block.strip():
            continue
        fields: dict[str, str] = {}
        for line in block.splitlines():
            key, separator, value = line.partition(":")
            if separator:
                fields[key] = value.lstrip()
        assert {"id", "event", "data"} <= fields.keys(), block
        payload = json.loads(fields["data"])
        assert int(fields["id"]) == payload["sequence"]
        assert fields["event"] == payload["type"]
        frames.append(payload)
    return frames


def create_approval_workflow(api: TestClient) -> dict[str, Any]:
    response = api.post(
        "/api/v1/workflow-definitions",
        headers={"Idempotency-Key": "integration-definition-001"},
        json=approval_definition(),
    )
    assert response.status_code == 201, response.text
    return response.json()


def start_approval_workflow(api: TestClient, workflow: dict[str, Any]) -> dict[str, Any]:
    response = api.post(
        f"/api/v1/workflows/{workflow['id']}/runs",
        headers={"Idempotency-Key": "integration-start-001"},
        json={
            "workflowDefinitionVersion": workflow["definitionVersion"],
            "input": {"assetId": "asset_test_001"},
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def test_start_query_cancel_waiting_human_without_external_providers() -> None:
    app = create_app(Settings(environment="test", storage_backend="memory"))
    with TestClient(app) as api:
        workflow = create_approval_workflow(api)
        started = start_approval_workflow(api, workflow)
        assert started["workflowDefinitionId"] == workflow["id"]
        assert started["workflowDefinitionVersion"] == workflow["definitionVersion"]
        assert started["status"] in {"queued", "running", "waiting_human"}
        run_id = started["id"]

        waiting = wait_for_status(api, run_id, "waiting_human")
        assert any(
            node["nodeId"] == "review" and node["status"] == "waiting_human"
            for node in waiting["nodeRuns"]
        )

        cancelled_response = api.post(
            f"/api/v1/workflow-runs/{run_id}/cancel",
            headers={"Idempotency-Key": "integration-cancel-001"},
            json={"reason": "集成测试结束等待态"},
        )
        assert cancelled_response.status_code == 200, cancelled_response.text
        cancelled = cancelled_response.json()
        assert cancelled["status"] == "cancelled"
        assert all(node["status"] != "waiting_human" for node in cancelled["nodeRuns"])

        queried = api.get(f"/api/v1/workflow-runs/{run_id}")
        assert queried.status_code == 200
        assert queried.json() == cancelled

        event_response = api.get(f"/api/v1/workflow-runs/{run_id}/events")
        assert event_response.status_code == 200, event_response.text
        assert event_response.headers["content-type"].startswith("text/event-stream")
        events = parse_sse(event_response.text)
        sequences = [event["sequence"] for event in events]
        assert sequences == sorted(set(sequences))
        assert events[0]["type"] == "run.queued"
        assert "run.waiting_human" in {event["type"] for event in events}
        assert "node.cancelled" in {event["type"] for event in events}
        assert events[-1]["type"] == "run.cancelled"

        replay = api.get(
            f"/api/v1/workflow-runs/{run_id}/events",
            headers={"Last-Event-ID": str(events[-2]["sequence"])},
        )
        replayed_events = parse_sse(replay.text)
        assert [event["sequence"] for event in replayed_events] == [events[-1]["sequence"]]


def test_run_response_freezes_contract_security_context_and_trace_id() -> None:
    app = create_app(Settings(environment="test", storage_backend="memory"))
    with TestClient(app) as api:
        workflow = create_approval_workflow(api)
        started = start_approval_workflow(api, workflow)

        assert isinstance(started.get("traceId"), str) and started["traceId"]
        snapshot = started.get("permissionSnapshot")
        assert isinstance(snapshot, dict)
        assert {
            "subjectId",
            "departmentIds",
            "projectIds",
            "roles",
            "allowedScopes",
            "securityClearance",
            "capturedAt",
            "policyVersion",
        } <= snapshot.keys()
