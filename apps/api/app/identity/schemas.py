from enum import StrEnum
from uuid import UUID

from pydantic import Field

from app.core.schemas import ApiModel


class Permission(StrEnum):
    KNOWLEDGE_READ = "knowledge.read"
    KNOWLEDGE_WRITE = "knowledge.write"
    DOCUMENT_READ = "document.read"
    DOCUMENT_WRITE = "document.write"
    ASSET_READ = "asset.read"
    ASSET_WRITE = "asset.write"
    WORKFLOW_READ = "workflow.read"
    WORKFLOW_WRITE = "workflow.write"
    AUDIT_READ = "audit.read"


class Principal(ApiModel):
    user_id: UUID
    tenant_id: UUID
    display_name: str = Field(min_length=1, max_length=100)
    permissions: frozenset[Permission] = frozenset()
    department_ids: frozenset[str] = frozenset()
    project_ids: frozenset[str] = frozenset()
    roles: frozenset[str] = frozenset({"employee"})
    security_clearance: str = Field(
        default="internal",
        pattern=r"^(internal|department_sensitive|confidential)$",
    )

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions
