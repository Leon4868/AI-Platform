import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy.dialects import postgresql

from app.assets.schemas import DataScope, SecurityLevel
from app.core.tables import AssetRecord, KnowledgeChunkRecord
from app.knowledge.index import InMemoryKnowledgeIndex, KnowledgeIndex
from app.knowledge.schemas import KnowledgeDocumentStatus, KnowledgeSearchFilters
from app.knowledge.sql_index import (
    MAX_QUERY_PREFILTER_TOKENS,
    MAX_SEARCH_CANDIDATES,
    MIN_SEARCH_CANDIDATES,
    SQLAlchemyKnowledgeIndex,
    delete_document_chunks,
    search_candidate_limit,
    select_search_candidates,
)

TENANT_ID = UUID("00000000-0000-4000-8000-000000000010")
OTHER_TENANT_ID = UUID("00000000-0000-4000-8000-000000000099")
SUBJECT_ID = UUID("00000000-0000-4000-8000-000000000001")


def _sql(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect())).lower()


def _accepts_index(index: KnowledgeIndex) -> KnowledgeIndex:
    return index


def test_memory_and_sql_indexes_implement_the_same_protocol() -> None:
    assert _accepts_index(InMemoryKnowledgeIndex())
    assert _accepts_index(SQLAlchemyKnowledgeIndex(lambda: None))


def test_candidate_query_applies_tenant_kb_acl_clearance_and_filters_before_read() -> None:
    document_id = uuid4()
    asset_id = uuid4()
    statement = select_search_candidates(
        tenant_id=TENANT_ID,
        knowledge_base_id=uuid4(),
        query_tokens=("alpha_beta", "苹"),
        subject_id=SUBJECT_ID,
        department_ids=frozenset({"dept-product"}),
        project_ids=frozenset({"project-alpha"}),
        security_clearance=SecurityLevel.DEPARTMENT_SENSITIVE.value,
        candidate_limit=500,
        filters=KnowledgeSearchFilters(
            document_status=KnowledgeDocumentStatus.INDEXED,
            document_ids=[document_id],
            asset_ids=[asset_id],
            data_scopes=[DataScope.DEPARTMENT],
            security_levels=[SecurityLevel.DEPARTMENT_SENSITIVE],
            title_contains="  产品_周报  ",
        ),
    )
    assert statement is not None
    sql = _sql(statement)

    assert "knowledge_chunks join assets" in sql
    assert "assets.tenant_id = knowledge_chunks.tenant_id" in sql
    assert sql.count("tenant_id") >= 4
    assert "knowledge_base_id" in sql
    assert "assets.security_level in" in sql
    assert "assets.creator_id" in sql
    assert "assets.owner_department_id in" in sql
    assert "assets.project_id in" in sql
    assert "knowledge_chunks.document_id in" in sql
    assert "knowledge_chunks.asset_id in" in sql
    assert "assets.data_scope in" in sql
    assert "lower(knowledge_chunks.content) like" in sql
    assert "lower(knowledge_chunks.title) like" in sql


def test_candidate_query_refuses_unknown_clearance_and_nonindexed_status() -> None:
    base = dict(
        tenant_id=TENANT_ID,
        knowledge_base_id=uuid4(),
        query_tokens=("search",),
        subject_id=SUBJECT_ID,
        department_ids=frozenset(),
        project_ids=frozenset(),
        candidate_limit=500,
    )
    assert select_search_candidates(
        **base,
        security_clearance="unknown",
        filters=None,
    ) is None
    assert select_search_candidates(
        **base,
        security_clearance=SecurityLevel.CONFIDENTIAL.value,
        filters=KnowledgeSearchFilters(document_status=KnowledgeDocumentStatus.ARCHIVED),
    ) is None


def test_candidate_limit_is_bounded_and_scales_with_top_k() -> None:
    assert search_candidate_limit(1) == MIN_SEARCH_CANDIDATES
    assert search_candidate_limit(10) == MIN_SEARCH_CANDIDATES
    assert search_candidate_limit(50) == 2_500
    assert search_candidate_limit(10_000) == MAX_SEARCH_CANDIDATES


def test_delete_statement_is_scoped_to_tenant_kb_and_document() -> None:
    sql = _sql(delete_document_chunks(TENANT_ID, uuid4(), uuid4()))
    assert "delete from knowledge_chunks" in sql
    assert "tenant_id" in sql
    assert "knowledge_base_id" in sql
    assert "document_id" in sql


class _Transaction:
    def __init__(self, state: dict) -> None:
        self.state = state

    async def __aenter__(self):
        self.state["transaction_entered"] = True
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        self.state["transaction_exited"] = True
        self.state["transaction_error"] = exc_type
        del exc, traceback


class _ReplaceSession:
    def __init__(self, state: dict) -> None:
        self.state = state

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback

    def begin(self):
        return _Transaction(self.state)

    async def execute(self, statement):
        self.state["delete"] = statement

    def add_all(self, records) -> None:
        assert self.state["transaction_entered"]
        self.state["records"] = list(records)


class _ReplaceFactory:
    def __init__(self, state: dict) -> None:
        self.state = state

    def __call__(self):
        return _ReplaceSession(self.state)


def test_replace_document_deletes_and_inserts_all_chunks_in_one_transaction() -> None:
    state = {}
    index = SQLAlchemyKnowledgeIndex(_ReplaceFactory(state))
    knowledge_base_id = uuid4()
    document_id = uuid4()
    asset_id = uuid4()
    indexed = asyncio.run(
        index.replace_document(
            tenant_id=TENANT_ID,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            asset_id=asset_id,
            creator_id=SUBJECT_ID,
            owner_department_id="dept-product",
            project_id=None,
            data_scope=DataScope.DEPARTMENT,
            security_level=SecurityLevel.INTERNAL,
            title="产品周报",
            chunks=["第一段", "第二段"],
        )
    )

    assert state["transaction_entered"] and state["transaction_exited"]
    assert state["transaction_error"] is None
    assert "tenant_id" in _sql(state["delete"])
    assert [record.ordinal for record in state["records"]] == [0, 1]
    assert [record.id for record in state["records"]] == [chunk.chunk_id for chunk in indexed]
    assert all(record.tenant_id == TENANT_ID for record in state["records"])
    assert all(record.document_id == document_id for record in state["records"])


class _RowsResult:
    def __init__(self, rows) -> None:
        self._rows = rows

    def all(self):
        return self._rows


class _SearchSession:
    def __init__(self, rows, statements) -> None:
        self.rows = rows
        self.statements = statements

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback

    async def execute(self, statement):
        self.statements.append(statement)
        return _RowsResult(self.rows)


class _SearchFactory:
    def __init__(self, rows) -> None:
        self.rows = rows
        self.statements = []

    def __call__(self):
        return _SearchSession(self.rows, self.statements)


def test_search_preserves_lexical_ranking_after_database_acl_prefilter() -> None:
    timestamp = datetime.now(UTC)
    asset_id = uuid4()
    base_record = dict(
        tenant_id=TENANT_ID,
        knowledge_base_id=uuid4(),
        document_id=uuid4(),
        asset_id=asset_id,
        title="产品周报",
        created_at=timestamp,
        updated_at=timestamp,
    )
    chunks = [
        KnowledgeChunkRecord(
            id=uuid4(), ordinal=0, content="苹果 苹果 发布计划", **base_record
        ),
        KnowledgeChunkRecord(id=uuid4(), ordinal=1, content="苹果 发布", **base_record),
    ]
    asset = AssetRecord(
        id=asset_id,
        tenant_id=TENANT_ID,
        type="document",
        name="产品周报.txt",
        version=1,
        status="draft",
        creator_id=SUBJECT_ID,
        owner_department_id="dept-product",
        data_scope="department",
        security_level="internal",
        lineage=[],
        created_at=timestamp,
        updated_at=timestamp,
    )
    factory = _SearchFactory([(chunks[1], asset), (chunks[0], asset)])
    index = SQLAlchemyKnowledgeIndex(factory)

    matches = asyncio.run(
        index.search(
            tenant_id=TENANT_ID,
            knowledge_base_id=base_record["knowledge_base_id"],
            query="苹果 苹果",
            top_k=1,
            subject_id=SUBJECT_ID,
            department_ids=frozenset(),
            project_ids=frozenset(),
            security_clearance="internal",
        )
    )

    assert len(matches) == 1
    assert matches[0].chunk.ordinal == 0
    assert matches[0].score > 0
    assert len(factory.statements) == 1
    sql = _sql(factory.statements[0])
    assert "tenant_id" in sql and "knowledge_base_id" in sql


class _ForbiddenFactory:
    def __call__(self):
        raise AssertionError("database must not be queried for a denied search")


def test_denied_search_returns_before_opening_database_session() -> None:
    index = SQLAlchemyKnowledgeIndex(_ForbiddenFactory())
    result = asyncio.run(
        index.search(
            tenant_id=OTHER_TENANT_ID,
            knowledge_base_id=uuid4(),
            query="secret",
            top_k=5,
            subject_id=SUBJECT_ID,
            department_ids=frozenset(),
            project_ids=frozenset(),
            security_clearance="unknown",
        )
    )
    assert result == []


def test_search_bounds_sql_prefilter_tokens_and_candidate_rows() -> None:
    factory = _SearchFactory([])
    index = SQLAlchemyKnowledgeIndex(factory)
    # 80 distinct CJK characters are all standalone tokens in the current tokenizer.
    query = "".join(chr(0x4E00 + offset) for offset in range(80))

    result = asyncio.run(
        index.search(
            tenant_id=TENANT_ID,
            knowledge_base_id=uuid4(),
            query=query,
            top_k=50,
            subject_id=SUBJECT_ID,
            department_ids=frozenset(),
            project_ids=frozenset(),
            security_clearance="internal",
        )
    )

    assert result == []
    assert len(factory.statements) == 1
    compiled = factory.statements[0].compile(dialect=postgresql.dialect())
    token_parameters = [name for name in compiled.params if name.startswith("lower_")]
    assert len(token_parameters) == MAX_QUERY_PREFILTER_TOKENS
    assert compiled.params["param_1"] == search_candidate_limit(50)
