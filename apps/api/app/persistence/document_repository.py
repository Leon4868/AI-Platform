from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Delete, Select

from app.core.tables import DocumentGenerationJobRecord
from app.documents.schemas import DocumentGenerationJob
from app.persistence.mappers import apply_document, document_from_record, document_to_record


def select_document(tenant_id: UUID, entity_id: UUID) -> Select:
    return select(DocumentGenerationJobRecord).where(
        DocumentGenerationJobRecord.tenant_id == tenant_id,
        DocumentGenerationJobRecord.id == entity_id,
    )


def list_documents(tenant_id: UUID, *, limit: int, offset: int) -> Select:
    return (
        select(DocumentGenerationJobRecord)
        .where(DocumentGenerationJobRecord.tenant_id == tenant_id)
        .order_by(
            DocumentGenerationJobRecord.created_at.desc(),
            DocumentGenerationJobRecord.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )


def delete_document(tenant_id: UUID, entity_id: UUID) -> Delete:
    return delete(DocumentGenerationJobRecord).where(
        DocumentGenerationJobRecord.tenant_id == tenant_id,
        DocumentGenerationJobRecord.id == entity_id,
    )


class SQLAlchemyDocumentRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def add(self, entity: DocumentGenerationJob) -> DocumentGenerationJob:
        async with self._sessions() as session:
            record = document_to_record(entity)
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return document_from_record(record)

    async def get(self, tenant_id: UUID, entity_id: UUID) -> DocumentGenerationJob | None:
        async with self._sessions() as session:
            record = (
                await session.execute(select_document(tenant_id, entity_id))
            ).scalar_one_or_none()
            return None if record is None else document_from_record(record)

    async def list(
        self,
        tenant_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[DocumentGenerationJob], int]:
        async with self._sessions() as session:
            count = await session.scalar(
                select(func.count()).select_from(DocumentGenerationJobRecord).where(
                    DocumentGenerationJobRecord.tenant_id == tenant_id
                )
            )
            rows = (await session.scalars(list_documents(tenant_id, limit=limit, offset=offset))).all()
            return [document_from_record(row) for row in rows], int(count or 0)

    async def update(self, entity: DocumentGenerationJob) -> DocumentGenerationJob:
        async with self._sessions() as session:
            record = (
                await session.execute(select_document(entity.tenant_id, entity.id))
            ).scalar_one_or_none()
            if record is None:
                raise KeyError(entity.id)
            apply_document(record, entity)
            await session.commit()
            await session.refresh(record)
            return document_from_record(record)

    async def delete(self, tenant_id: UUID, entity_id: UUID) -> bool:
        async with self._sessions() as session:
            result = await session.execute(delete_document(tenant_id, entity_id))
            await session.commit()
            return bool(result.rowcount)
