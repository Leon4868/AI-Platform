from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.audit.schemas import AuditEvent


class AuditService:
    """Development audit sink. Production adapter must be append-only and retention-protected."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

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
        self._events.append(event)
        return event.model_copy(deep=True)

    async def list(self, tenant_id: UUID, *, limit: int, offset: int) -> tuple[list[AuditEvent], int]:
        events = [event for event in reversed(self._events) if event.tenant_id == tenant_id]
        return [event.model_copy(deep=True) for event in events[offset : offset + limit]], len(events)
