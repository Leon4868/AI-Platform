import asyncio
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from uuid import UUID, uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy.dialects import postgresql

from app.assets.schemas import Asset, AssetLineageRef
from app.core.tables import (
    AssetRecord,
    DocumentGenerationJobRecord,
    KnowledgeBaseRecord,
    KnowledgeChunkRecord,
    WorkflowDefinitionRecord,
)
from app.documents.schemas import DocumentGenerationJob, DocumentSource
from app.knowledge.schemas import CitationView, KnowledgeBase
from app.persistence.asset_repository import (
    SQLAlchemyAssetRepository,
    delete_asset,
    list_assets,
    select_asset,
)
from app.persistence.document_repository import (
    SQLAlchemyDocumentRepository,
    delete_document,
    list_documents,
    select_document,
)
from app.persistence.knowledge_repository import (
    SQLAlchemyKnowledgeBaseRepository,
    delete_knowledge_base,
    list_knowledge_bases,
    select_knowledge_base,
)
from app.persistence.mappers import (
    asset_from_record,
    asset_to_record,
    document_from_record,
    document_to_record,
    knowledge_base_from_record,
    knowledge_base_to_record,
    workflow_from_record,
    workflow_to_record,
)
from app.persistence.workflow_repository import (
    SQLAlchemyWorkflowRepository,
    delete_workflow,
    list_workflows,
    select_workflow,
)
from app.workflows.schemas import WorkflowDefinition, WorkflowGraph

API_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_INI = API_ROOT / "alembic.ini"
TENANT_ID = UUID("00000000-0000-4000-8000-000000000010")
ACTOR_ID = UUID("00000000-0000-4000-8000-000000000001")


def now() -> datetime:
    return datetime.now(UTC)


def workflow() -> WorkflowDefinition:
    timestamp = now()
    return WorkflowDefinition(
        id=uuid4(),
        tenant_id=TENANT_ID,
        owner_id=ACTOR_ID,
        name="周报工作流",
        description="持久化测试",
        graph=WorkflowGraph(
            nodes=[
                {"id": "start", "type": "start", "name": "开始", "position": {"x": 0, "y": 0}},
                {"id": "end", "type": "end", "name": "结束", "position": {"x": 100, "y": 0}},
            ],
            edges=[{"id": "e1", "source": "start", "target": "end"}],
        ),
        status="published",
        revision=2,
        created_at=timestamp,
        updated_at=timestamp,
    )


def knowledge_base() -> KnowledgeBase:
    timestamp = now()
    return KnowledgeBase(
        id=uuid4(),
        tenant_id=TENANT_ID,
        owner_id=ACTOR_ID,
        name="制度库",
        description="企业制度",
        owner_department_id="dept-product",
        security_level="department_sensitive",
        embedding_model_code="embed-main",
        created_at=timestamp,
        updated_at=timestamp,
    )


def asset() -> Asset:
    timestamp = now()
    return Asset(
        id=uuid4(),
        tenant_id=TENANT_ID,
        type="document",
        name="周报.md",
        description="草稿",
        version=2,
        status="draft",
        mime_type="text/markdown",
        storage_uri="tenants/demo/assets/report/v2",
        content_hash="sha256:redacted-example",
        creator_id=ACTOR_ID,
        owner_department_id="dept-product",
        project_id="project-alpha",
        data_scope="department",
        security_level="internal",
        lineage=[AssetLineageRef(asset_id="source-1", version=1, relation="derived_from")],
        workflow_run_id=uuid4(),
        trace_id=uuid4(),
        created_at=timestamp,
        updated_at=timestamp,
    )


def document() -> DocumentGenerationJob:
    timestamp = now()
    return DocumentGenerationJob(
        id=uuid4(),
        tenant_id=TENANT_ID,
        requested_by=ACTOR_ID,
        title="产品周报",
        template_asset_id="template-1",
        workflow_definition_id="workflow-1",
        knowledge_base_ids=["kb-1"],
        logical_model_code="doc-main",
        instructions="总结进展",
        sources=[DocumentSource(kind="user_input", label="补充说明")],
        output_format="markdown",
        owner_department_id="dept-product",
        status="failed",
        workflow_run_id=uuid4(),
        trace_id=uuid4(),
        citations=[
            CitationView(
                knowledge_document_id="document-1",
                chunk_id="chunk-1",
                asset_id="asset-1",
                quote="真实引用",
                score=0.9,
            )
        ],
        error={"code": "MODEL_TIMEOUT", "message": "模型超时"},
        finished_at=timestamp,
        created_at=timestamp,
        updated_at=timestamp,
    )


def test_domain_mappers_round_trip_without_json_shape_drift() -> None:
    pairs = [
        (workflow(), workflow_to_record, workflow_from_record),
        (knowledge_base(), knowledge_base_to_record, knowledge_base_from_record),
        (document(), document_to_record, document_from_record),
        (asset(), asset_to_record, asset_from_record),
    ]
    for entity, to_record, from_record in pairs:
        restored = from_record(to_record(entity))
        assert restored == entity
        assert restored is not entity


def test_table_columns_match_current_domain_models() -> None:
    audit_columns = {"id", "tenant_id", "created_at", "updated_at"}
    expected = {
        WorkflowDefinitionRecord: audit_columns
        | {"owner_id", "name", "description", "graph", "status", "revision"},
        KnowledgeBaseRecord: audit_columns
        | {
            "owner_id",
            "name",
            "description",
            "owner_department_id",
            "security_level",
            "embedding_model_code",
        },
        DocumentGenerationJobRecord: audit_columns
        | {
            "requested_by",
            "title",
            "template_asset_id",
            "workflow_definition_id",
            "knowledge_base_ids",
            "logical_model_code",
            "instructions",
            "sources",
            "output_format",
            "owner_department_id",
            "data_scope",
            "security_level",
            "status",
            "draft_asset_id",
            "workflow_run_id",
            "trace_id",
            "citations",
            "error",
            "finished_at",
        },
        AssetRecord: audit_columns
        | {
            "type",
            "name",
            "description",
            "version",
            "status",
            "mime_type",
            "storage_uri",
            "content_hash",
            "creator_id",
            "owner_department_id",
            "project_id",
            "data_scope",
            "security_level",
            "lineage",
            "workflow_run_id",
            "trace_id",
        },
    }
    for record, columns in expected.items():
        assert set(record.__table__.columns.keys()) == columns


def _postgresql_sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect())).lower()


def test_every_repository_statement_is_tenant_scoped() -> None:
    entity_id = uuid4()
    statement_groups = [
        (
            select_workflow(TENANT_ID, entity_id),
            list_workflows(TENANT_ID, limit=10, offset=0),
            delete_workflow(TENANT_ID, entity_id),
        ),
        (
            select_knowledge_base(TENANT_ID, entity_id),
            list_knowledge_bases(TENANT_ID, limit=10, offset=0),
            delete_knowledge_base(TENANT_ID, entity_id),
        ),
        (
            select_document(TENANT_ID, entity_id),
            list_documents(TENANT_ID, limit=10, offset=0),
            delete_document(TENANT_ID, entity_id),
        ),
        (
            select_asset(TENANT_ID, entity_id),
            list_assets(TENANT_ID, limit=10, offset=0),
            delete_asset(TENANT_ID, entity_id),
        ),
    ]
    for get_statement, list_statement, delete_statement in statement_groups:
        assert "tenant_id" in _postgresql_sql(get_statement)
        assert "tenant_id" in _postgresql_sql(list_statement)
        assert "tenant_id" in _postgresql_sql(delete_statement)
        assert " limit " in _postgresql_sql(list_statement)
        assert " offset " in _postgresql_sql(list_statement)


class _ExecuteResult:
    def __init__(self, record) -> None:
        self._record = record

    def scalar_one_or_none(self):
        return self._record


class _FakeSession:
    def __init__(self, record) -> None:
        self.record = record
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback

    async def execute(self, statement):
        self.statements.append(statement)
        return _ExecuteResult(self.record)


class _FakeSessionFactory:
    def __init__(self, record) -> None:
        self.session = _FakeSession(record)

    def __call__(self) -> _FakeSession:
        return self.session


def test_repository_get_maps_fresh_models_for_every_domain() -> None:
    cases = [
        (workflow(), workflow_to_record, SQLAlchemyWorkflowRepository),
        (knowledge_base(), knowledge_base_to_record, SQLAlchemyKnowledgeBaseRepository),
        (document(), document_to_record, SQLAlchemyDocumentRepository),
        (asset(), asset_to_record, SQLAlchemyAssetRepository),
    ]
    for entity, mapper, repository_type in cases:
        factory = _FakeSessionFactory(mapper(entity))
        repository = repository_type(factory)
        restored = asyncio.run(repository.get(entity.tenant_id, entity.id))
        assert restored == entity
        assert restored is not entity
        assert "tenant_id" in _postgresql_sql(factory.session.statements[0])


def _offline_sql(revision: str) -> str:
    output = StringIO()
    config = Config(str(ALEMBIC_INI), output_buffer=output)
    if revision == "head":
        command.upgrade(config, "head", sql=True)
    else:
        command.downgrade(config, "20260825_0001:base", sql=True)
    return output.getvalue().lower()


def test_initial_migration_upgrade_and_downgrade_compile_offline() -> None:
    upgrade_sql = _offline_sql("head")
    for table in [
        "workflow_definitions",
        "knowledge_bases",
        "assets",
        "document_generation_jobs",
        "knowledge_chunks",
        "audit_events",
    ]:
        assert f"create table {table}" in upgrade_sql
    assert "vector(1536)" in upgrade_sql
    assert "on delete restrict" in upgrade_sql
    assert "on delete cascade" not in upgrade_sql
    assert "tenant_id" in upgrade_sql

    downgrade_sql = _offline_sql("base")
    assert "drop table knowledge_chunks" in downgrade_sql
    assert "drop table workflow_definitions" in downgrade_sql


def test_composite_foreign_keys_prevent_cross_tenant_references() -> None:
    constraints = [
        constraint
        for table in (DocumentGenerationJobRecord.__table__, KnowledgeChunkRecord.__table__)
        for constraint in table.foreign_key_constraints
    ]
    assert constraints
    assert all(constraint.ondelete == "RESTRICT" for constraint in constraints)
    assert all("tenant_id" in {column.name for column in constraint.columns} for constraint in constraints)
