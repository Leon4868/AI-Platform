from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Delete, Select

from app.core.tables import KnowledgeBaseRecord
from app.knowledge.schemas import KnowledgeBase
from app.persistence.mappers import (
    apply_knowledge_base,
    knowledge_base_from_record,
    knowledge_base_to_record,
)


def select_knowledge_base(tenant_id: UUID, entity_id: UUID) -> Select:
    return select(KnowledgeBaseRecord).where(
        KnowledgeBaseRecord.tenant_id == tenant_id,
        KnowledgeBaseRecord.id == entity_id,
    )


def list_knowledge_bases(tenant_id: UUID, *, limit: int, offset: int) -> Select:
    return (
        select(KnowledgeBaseRecord)
        .where(KnowledgeBaseRecord.tenant_id == tenant_id)
        .order_by(KnowledgeBaseRecord.created_at.desc(), KnowledgeBaseRecord.id.desc())
        .limit(limit)
        .offset(offset)
    )


def delete_knowledge_base(tenant_id: UUID, entity_id: UUID) -> Delete:
    return delete(KnowledgeBaseRecord).where(
        KnowledgeBaseRecord.tenant_id == tenant_id,
        KnowledgeBaseRecord.id == entity_id,
    )


class SQLAlchemyKnowledgeBaseRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def add(self, entity: KnowledgeBase) -> KnowledgeBase:
        async with self._sessions() as session:
            record = knowledge_base_to_record(entity)
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return knowledge_base_from_record(record)

    async def get(self, tenant_id: UUID, entity_id: UUID) -> KnowledgeBase | None:
        async with self._sessions() as session:
            record = (
                await session.execute(select_knowledge_base(tenant_id, entity_id))
            ).scalar_one_or_none()
            return None if record is None else knowledge_base_from_record(record)

    async def list(
        self,
        tenant_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[KnowledgeBase], int]:
        async with self._sessions() as session:
            count = await session.scalar(
                select(func.count()).select_from(KnowledgeBaseRecord).where(
                    KnowledgeBaseRecord.tenant_id == tenant_id
                )
            )
            rows = (await session.scalars(list_knowledge_bases(tenant_id, limit=limit, offset=offset))).all()
            return [knowledge_base_from_record(row) for row in rows], int(count or 0)

    async def update(self, entity: KnowledgeBase) -> KnowledgeBase:
        async with self._sessions() as session:
            record = (
                await session.execute(select_knowledge_base(entity.tenant_id, entity.id))
            ).scalar_one_or_none()
            if record is None:
                raise KeyError(entity.id)
            apply_knowledge_base(record, entity)
            await session.commit()
            await session.refresh(record)
            return knowledge_base_from_record(record)

    async def delete(self, tenant_id: UUID, entity_id: UUID) -> bool:
        async with self._sessions() as session:
            result = await session.execute(delete_knowledge_base(tenant_id, entity_id))
            await session.commit()
            return bool(result.rowcount)
