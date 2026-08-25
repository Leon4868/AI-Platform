from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Delete, Select

from app.core.tables import WorkflowDefinitionRecord
from app.persistence.mappers import apply_workflow, workflow_from_record, workflow_to_record
from app.workflows.schemas import WorkflowDefinition


def select_workflow(tenant_id: UUID, entity_id: UUID) -> Select:
    return select(WorkflowDefinitionRecord).where(
        WorkflowDefinitionRecord.tenant_id == tenant_id,
        WorkflowDefinitionRecord.id == entity_id,
    )


def list_workflows(tenant_id: UUID, *, limit: int, offset: int) -> Select:
    return (
        select(WorkflowDefinitionRecord)
        .where(WorkflowDefinitionRecord.tenant_id == tenant_id)
        .order_by(WorkflowDefinitionRecord.created_at.desc(), WorkflowDefinitionRecord.id.desc())
        .limit(limit)
        .offset(offset)
    )


def delete_workflow(tenant_id: UUID, entity_id: UUID) -> Delete:
    return delete(WorkflowDefinitionRecord).where(
        WorkflowDefinitionRecord.tenant_id == tenant_id,
        WorkflowDefinitionRecord.id == entity_id,
    )


class SQLAlchemyWorkflowRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def add(self, entity: WorkflowDefinition) -> WorkflowDefinition:
        async with self._sessions() as session:
            record = workflow_to_record(entity)
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return workflow_from_record(record)

    async def get(self, tenant_id: UUID, entity_id: UUID) -> WorkflowDefinition | None:
        async with self._sessions() as session:
            record = (await session.execute(select_workflow(tenant_id, entity_id))).scalar_one_or_none()
            return None if record is None else workflow_from_record(record)

    async def list(
        self,
        tenant_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[WorkflowDefinition], int]:
        async with self._sessions() as session:
            count = await session.scalar(
                select(func.count()).select_from(WorkflowDefinitionRecord).where(
                    WorkflowDefinitionRecord.tenant_id == tenant_id
                )
            )
            rows = (await session.scalars(list_workflows(tenant_id, limit=limit, offset=offset))).all()
            return [workflow_from_record(row) for row in rows], int(count or 0)

    async def update(self, entity: WorkflowDefinition) -> WorkflowDefinition:
        async with self._sessions() as session:
            record = (await session.execute(select_workflow(entity.tenant_id, entity.id))).scalar_one_or_none()
            if record is None:
                raise KeyError(entity.id)
            apply_workflow(record, entity)
            await session.commit()
            await session.refresh(record)
            return workflow_from_record(record)

    async def delete(self, tenant_id: UUID, entity_id: UUID) -> bool:
        async with self._sessions() as session:
            result = await session.execute(delete_workflow(tenant_id, entity_id))
            await session.commit()
            return bool(result.rowcount)
