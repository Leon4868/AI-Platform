"""Append-only PostgreSQL audit sink used by the persistent runtime."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Select

from app.audit.schemas import AuditEvent
from app.audit.service import AuditService
from app.core.tables import AuditEventRecord


def list_audit_events(tenant_id: UUID, *, limit: int, offset: int) -> Select:
    return (
        select(AuditEventRecord)
        .where(AuditEventRecord.tenant_id == tenant_id)
        .order_by(AuditEventRecord.occurred_at.desc(), AuditEventRecord.id.desc())
        .limit(limit)
        .offset(offset)
    )


def audit_event_from_record(record: AuditEventRecord) -> AuditEvent:
    return AuditEvent(
        id=record.id,
        tenant_id=record.tenant_id,
        actor_id=record.actor_id,
        action=record.action,
        resource_type=record.resource_type,
        resource_id=record.resource_id,
        occurred_at=record.occurred_at,
        request_id=record.request_id,
        metadata=record.event_metadata,
    )


class SQLAlchemyAuditService(AuditService):
    """Persists audit events without exposing update or delete operations."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def record(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        action: str,
        resource_type: str,
        resource_id: UUID | None,
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> AuditEvent:
        event = AuditEvent(
            id=uuid4(),
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            occurred_at=datetime.now(UTC),
            request_id=request_id,
            metadata=metadata or {},
        )
        async with self._sessions() as session:
            record = AuditEventRecord(
                id=event.id,
                tenant_id=event.tenant_id,
                actor_id=event.actor_id,
                action=event.action,
                resource_type=event.resource_type,
                resource_id=event.resource_id,
                occurred_at=event.occurred_at,
                request_id=event.request_id,
                event_metadata=event.metadata,
            )
            session.add(record)
            await session.commit()
            return audit_event_from_record(record)

    async def list(
        self,
        tenant_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[AuditEvent], int]:
        async with self._sessions() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(AuditEventRecord)
                .where(AuditEventRecord.tenant_id == tenant_id)
            )
            rows = (await session.scalars(
                list_audit_events(tenant_id, limit=limit, offset=offset)
            )).all()
            return [audit_event_from_record(row) for row in rows], int(count or 0)
