from collections.abc import Awaitable, Callable

import pytest
from fastapi.testclient import TestClient

from app.container import build_container
from app.core.config import Settings
from app.core.database import Database
from app.core.repository import InMemoryRepository
from app.core.storage import InMemoryObjectStorage
from app.main import create_app
from app.knowledge.index import InMemoryKnowledgeIndex
from app.knowledge.sql_index import SQLAlchemyKnowledgeIndex
from app.persistence.asset_repository import SQLAlchemyAssetRepository
from app.persistence.document_repository import SQLAlchemyDocumentRepository
from app.persistence.knowledge_repository import SQLAlchemyKnowledgeBaseRepository
from app.persistence.workflow_repository import SQLAlchemyWorkflowRepository
from app.runtime.repository import InMemoryWorkflowRunRepository
from app.runtime.sql_repository import SQLAlchemyWorkflowRunRepository


POSTGRES_URL = "postgresql+asyncpg://platform@127.0.0.1:5432/platform"


def postgres_settings() -> Settings:
    return Settings(
        environment="test",
        repository_backend="postgresql",
        database_url=POSTGRES_URL,
        storage_backend="memory",
    )


def test_memory_repository_backend_remains_the_safe_default() -> None:
    container = build_container(Settings(environment="test", storage_backend="memory"))

    assert container.repository_backend == "memory"
    assert container.database is None
    assert isinstance(container.workflow_repository, InMemoryRepository)
    assert isinstance(container.knowledge_repository, InMemoryRepository)
    assert isinstance(container.document_repository, InMemoryRepository)
    assert isinstance(container.asset_repository, InMemoryRepository)
    assert isinstance(container.workflow_run_repository, InMemoryWorkflowRunRepository)
    assert isinstance(container.knowledge_index, InMemoryKnowledgeIndex)


def test_postgresql_backend_selects_all_sqlalchemy_repositories() -> None:
    container = build_container(postgres_settings())

    assert container.repository_backend == "postgresql"
    assert isinstance(container.database, Database)
    assert isinstance(container.workflow_repository, SQLAlchemyWorkflowRepository)
    assert isinstance(container.knowledge_repository, SQLAlchemyKnowledgeBaseRepository)
    assert isinstance(container.document_repository, SQLAlchemyDocumentRepository)
    assert isinstance(container.asset_repository, SQLAlchemyAssetRepository)
    assert isinstance(container.workflow_run_repository, SQLAlchemyWorkflowRunRepository)
    assert isinstance(container.knowledge_index, SQLAlchemyKnowledgeIndex)


def test_postgresql_backend_requires_database_url() -> None:
    with pytest.raises(
        RuntimeError,
        match="APP_DATABASE_URL is required when APP_REPOSITORY_BACKEND=postgresql",
    ):
        build_container(
            Settings(
                environment="test",
                repository_backend="postgresql",
                database_url=None,
            )
        )


def _patch_ping(
    monkeypatch: pytest.MonkeyPatch,
    implementation: Callable[[Database], Awaitable[None]],
) -> None:
    monkeypatch.setattr(Database, "ping", implementation)


def test_readiness_reports_postgresql_after_successful_ping(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    async def successful_ping(database: Database) -> None:
        nonlocal calls
        assert database.engine is not None
        calls += 1

    _patch_ping(monkeypatch, successful_ping)
    with TestClient(create_app(postgres_settings())) as api:
        response = api.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "repository": "postgresql",
        "storage": "memory",
    }
    assert response.headers["X-Request-Id"]
    assert calls == 1


def test_readiness_failure_is_503_and_does_not_leak_database_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sensitive_marker = "private-database.internal:5432"

    async def failed_ping(database: Database) -> None:
        assert database.engine is not None
        raise RuntimeError(f"connection refused: {sensitive_marker}")

    _patch_ping(monkeypatch, failed_ping)
    with TestClient(create_app(postgres_settings())) as api:
        response = api.get("/health/ready", headers={"X-Request-Id": "ready-gate-001"})

    assert response.status_code == 503
    assert response.json() == {
        "status": "unready",
        "repository": "postgresql",
        "storage": "memory",
    }
    assert response.headers["X-Request-Id"] == "ready-gate-001"
    assert sensitive_marker not in response.text
    assert POSTGRES_URL not in response.text


def test_readiness_fails_closed_when_object_storage_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sensitive_marker = "private-object-store.internal"

    async def failed_ping(storage: InMemoryObjectStorage) -> None:
        assert storage is not None
        raise RuntimeError(f"connection refused: {sensitive_marker}")

    monkeypatch.setattr(InMemoryObjectStorage, "ping", failed_ping)
    with TestClient(create_app(Settings(environment="test", storage_backend="memory"))) as api:
        response = api.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unready",
        "repository": "memory",
        "storage": "memory",
    }
    assert sensitive_marker not in response.text
