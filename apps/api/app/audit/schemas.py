from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.core.schemas import ApiModel


class AuditEvent(ApiModel):
    id: UUID
    tenant_id: UUID
    actor_id: UUID
    action: str = Field(pattern=r"^[a-z][a-z0-9_.-]{2,99}$")
    resource_type: str = Field(min_length=1, max_length=80)
    resource_id: UUID | None = None
    occurred_at: datetime
    request_id: str | None = Field(default=None, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)
