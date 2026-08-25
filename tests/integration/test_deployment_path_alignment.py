from pathlib import Path

from app.core.config import Settings


ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_browser_dev_proxy_nginx_and_api_share_the_same_api_prefix() -> None:
    assert Settings(environment="test").api_prefix == "/api/v1"

    enterprise_config = _read("apps/web/src/features/enterprise-api/config.ts")
    workflow_app = _read("apps/web/src/App.tsx")
    vite_config = _read("apps/web/vite.config.ts")
    nginx_config = _read("apps/web/nginx.conf")
    compose = _read("compose.yaml")

    assert 'VITE_API_BASE_URL ?? "/api"' in enterprise_config
    assert 'VITE_API_BASE_URL ?? "/api"' in workflow_app
    assert '"/api"' in vite_config
    assert 'target: "http://127.0.0.1:8000"' in vite_config
    assert "location /api/" in nginx_config
    assert "proxy_pass http://api:8000/api/;" in nginx_config
    assert "  api:" in compose
    assert '"8000:8000"' in compose


def test_contract_operations_are_all_under_the_versioned_api_prefix() -> None:
    contract = _read("packages/contracts/openapi.yaml")
    assert "  - url: /api" in contract
    operation_paths = [
        line.strip().removesuffix(":")
        for line in contract.splitlines()
        if line.startswith("  /v") and line.rstrip().endswith(":")
    ]
    assert operation_paths
    assert all(path.startswith("/v1/") for path in operation_paths)


def test_contract_declares_problem_json_and_project_scoped_upload() -> None:
    contract = _read("packages/contracts/openapi.yaml")
    upload_operation = contract.split(
        "  /v1/knowledge-bases/{knowledgeBaseId}/documents:", 1
    )[1].split("  /v1/knowledge-bases/{knowledgeBaseId}/search:", 1)[0]
    error_response = contract.split("  responses:\n    Error:", 1)[1]

    assert "projectId:" in upload_operation
    assert "application/problem+json:" in error_response


def test_compose_runs_migrations_and_explicitly_selects_persistent_backends() -> None:
    compose = _read("compose.yaml")
    api_dockerfile = _read("apps/api/Dockerfile")

    assert "  migrate:" in compose
    assert 'command: ["alembic", "upgrade", "head"]' in compose
    assert "APP_REPOSITORY_BACKEND: postgresql" in compose
    assert "APP_STORAGE_BACKEND: s3" in compose
    assert "  minio-init:" in compose
    assert "mc mb --ignore-existing local/enterprise-ai-platform" in compose
    assert "condition: service_completed_successfully" in compose
    assert "COPY apps/api/alembic.ini ./alembic.ini" in api_dockerfile
    assert "COPY apps/api/alembic ./alembic" in api_dockerfile
