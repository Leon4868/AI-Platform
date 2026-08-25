from dataclasses import dataclass

from app.assets.schemas import DataScope, SecurityLevel


@dataclass(frozen=True, slots=True)
class UploadedFile:
    filename: str
    content_type: str
    content: bytes
    data_scope: DataScope = DataScope.DEPARTMENT
    security_level: SecurityLevel = SecurityLevel.INTERNAL
    project_id: str | None = None
