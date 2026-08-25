"""PostgreSQL implementation of the tenant- and ACL-scoped knowledge index."""

from collections import Counter
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Delete, Select

from app.assets.schemas import DataScope, SecurityLevel
from app.core.tables import AssetRecord, KnowledgeChunkRecord
from app.knowledge.index import (
    IndexedChunk,
    ScoredChunk,
    build_indexed_chunks,
    lexical_score,
    normalize_text,
    tokenize,
)
from app.knowledge.schemas import KnowledgeDocumentStatus, KnowledgeSearchFilters

_VISIBLE_SECURITY_LEVELS = {
    SecurityLevel.INTERNAL.value: (SecurityLevel.INTERNAL.value,),
    SecurityLevel.DEPARTMENT_SENSITIVE.value: (
        SecurityLevel.INTERNAL.value,
        SecurityLevel.DEPARTMENT_SENSITIVE.value,
    ),
    SecurityLevel.CONFIDENTIAL.value: tuple(level.value for level in SecurityLevel),
}
MAX_QUERY_PREFILTER_TOKENS = 64
MIN_SEARCH_CANDIDATES = 500
SEARCH_CANDIDATE_MULTIPLIER = 50
MAX_SEARCH_CANDIDATES = 5_000


def delete_document_chunks(
    tenant_id: UUID,
    knowledge_base_id: UUID,
    document_id: UUID,
) -> Delete:
    return delete(KnowledgeChunkRecord).where(
        KnowledgeChunkRecord.tenant_id == tenant_id,
        KnowledgeChunkRecord.knowledge_base_id == knowledge_base_id,
        KnowledgeChunkRecord.document_id == document_id,
    )


def select_search_candidates(
    *,
    tenant_id: UUID,
    knowledge_base_id: UUID,
    query_tokens: tuple[str, ...],
    subject_id: UUID,
    department_ids: frozenset[str],
    project_ids: frozenset[str],
    security_clearance: str,
    filters: KnowledgeSearchFilters | None,
    candidate_limit: int,
) -> Select | None:
    """Build the mandatory pre-retrieval tenant, ACL and business filters."""
    visible_levels = _VISIBLE_SECURITY_LEVELS.get(security_clearance)
    if not visible_levels or not query_tokens:
        return None
    if filters is not None and filters.document_status not in {
        None,
        KnowledgeDocumentStatus.INDEXED,
    }:
        return None

    join_condition = and_(
        AssetRecord.tenant_id == KnowledgeChunkRecord.tenant_id,
        AssetRecord.id == KnowledgeChunkRecord.asset_id,
    )
    access_predicates = [
        AssetRecord.creator_id == subject_id,
        AssetRecord.data_scope == DataScope.ENTERPRISE.value,
    ]
    if department_ids:
        access_predicates.append(
            and_(
                AssetRecord.data_scope == DataScope.DEPARTMENT.value,
                AssetRecord.owner_department_id.in_(sorted(department_ids)),
            )
        )
    if project_ids:
        access_predicates.append(
            and_(
                AssetRecord.data_scope == DataScope.PROJECT.value,
                AssetRecord.project_id.in_(sorted(project_ids)),
            )
        )

    statement = (
        select(KnowledgeChunkRecord, AssetRecord)
        .join(AssetRecord, join_condition)
        .where(
            KnowledgeChunkRecord.tenant_id == tenant_id,
            KnowledgeChunkRecord.knowledge_base_id == knowledge_base_id,
            AssetRecord.tenant_id == tenant_id,
            AssetRecord.security_level.in_(visible_levels),
            or_(*access_predicates),
            or_(
                *(
                    func.lower(KnowledgeChunkRecord.content).contains(
                        token.casefold(), autoescape=True
                    )
                    for token in query_tokens
                )
            ),
        )
        .order_by(KnowledgeChunkRecord.ordinal.asc(), KnowledgeChunkRecord.id.asc())
        .limit(candidate_limit)
    )

    if filters is None:
        return statement
    if filters.document_ids:
        statement = statement.where(
            KnowledgeChunkRecord.document_id.in_(filters.document_ids)
        )
    if filters.asset_ids:
        statement = statement.where(KnowledgeChunkRecord.asset_id.in_(filters.asset_ids))
    if filters.data_scopes:
        statement = statement.where(
            AssetRecord.data_scope.in_(tuple(scope.value for scope in filters.data_scopes))
        )
    if filters.security_levels:
        statement = statement.where(
            AssetRecord.security_level.in_(
                tuple(level.value for level in filters.security_levels)
            )
        )
    if filters.title_contains:
        title = normalize_text(filters.title_contains).casefold()
        statement = statement.where(
            func.lower(KnowledgeChunkRecord.title).contains(title, autoescape=True)
        )
    return statement


class SQLAlchemyKnowledgeIndex:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def replace_document(
        self,
        *,
        tenant_id: UUID,
        knowledge_base_id: UUID,
        document_id: UUID,
        asset_id: UUID,
        creator_id: UUID,
        owner_department_id: str,
        project_id: str | None,
        data_scope: DataScope,
        security_level: SecurityLevel,
        title: str,
        chunks: list[str],
    ) -> list[IndexedChunk]:
        indexed = build_indexed_chunks(
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            document_id=document_id,
            asset_id=asset_id,
            creator_id=creator_id,
            owner_department_id=owner_department_id,
            project_id=project_id,
            data_scope=data_scope,
            security_level=security_level,
            title=title,
            chunks=chunks,
        )
        records = [
            KnowledgeChunkRecord(
                id=chunk.chunk_id,
                tenant_id=chunk.tenant_id,
                knowledge_base_id=chunk.knowledge_base_id,
                document_id=chunk.document_id,
                asset_id=chunk.asset_id,
                ordinal=chunk.ordinal,
                title=chunk.title,
                content=chunk.text,
            )
            for chunk in indexed
        ]
        async with self._sessions() as session:
            async with session.begin():
                await session.execute(
                    delete_document_chunks(tenant_id, knowledge_base_id, document_id)
                )
                session.add_all(records)
        return indexed

    async def search(
        self,
        *,
        tenant_id: UUID,
        knowledge_base_id: UUID,
        query: str,
        top_k: int,
        subject_id: UUID,
        department_ids: frozenset[str],
        project_ids: frozenset[str],
        security_clearance: str,
        filters: KnowledgeSearchFilters | None = None,
    ) -> list[ScoredChunk]:
        query_counter = Counter(tokenize(query))
        # Keep the full counter for final scoring, but bound the SQL expression.
        # dict preserves first appearance, which makes the prefilter deterministic.
        query_tokens = tuple(dict.fromkeys(query_counter))[:MAX_QUERY_PREFILTER_TOKENS]
        statement = select_search_candidates(
            tenant_id=tenant_id,
            knowledge_base_id=knowledge_base_id,
            query_tokens=query_tokens,
            subject_id=subject_id,
            department_ids=department_ids,
            project_ids=project_ids,
            security_clearance=security_clearance,
            filters=filters,
            candidate_limit=search_candidate_limit(top_k),
        )
        if statement is None:
            return []

        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        scored = []
        for chunk_record, asset_record in rows:
            chunk = IndexedChunk(
                tenant_id=chunk_record.tenant_id,
                knowledge_base_id=chunk_record.knowledge_base_id,
                document_id=chunk_record.document_id,
                asset_id=chunk_record.asset_id,
                creator_id=asset_record.creator_id,
                owner_department_id=asset_record.owner_department_id,
                project_id=asset_record.project_id,
                data_scope=DataScope(asset_record.data_scope),
                security_level=SecurityLevel(asset_record.security_level),
                chunk_id=chunk_record.id,
                title=chunk_record.title,
                ordinal=chunk_record.ordinal,
                text=chunk_record.content,
            )
            score = lexical_score(query_counter, chunk.text)
            if score > 0:
                scored.append(ScoredChunk(chunk=chunk, score=score))
        scored.sort(key=lambda item: (-item.score, item.chunk.ordinal, str(item.chunk.chunk_id)))
        return scored[:top_k]


def search_candidate_limit(top_k: int) -> int:
    return min(
        MAX_SEARCH_CANDIDATES,
        max(MIN_SEARCH_CANDIDATES, top_k * SEARCH_CANDIDATE_MULTIPLIER),
    )
