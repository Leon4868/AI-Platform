from uuid import UUID

from app.assets.schemas import DataScope, SecurityLevel
from app.identity.schemas import Principal

_CLEARANCE = {
    SecurityLevel.INTERNAL: 0,
    SecurityLevel.DEPARTMENT_SENSITIVE: 1,
    SecurityLevel.CONFIDENTIAL: 2,
}


def security_rank(level: SecurityLevel | str) -> int:
    try:
        normalized = level if isinstance(level, SecurityLevel) else SecurityLevel(level)
    except ValueError:
        return -1
    return _CLEARANCE[normalized]


def can_read_resource(
    principal: Principal,
    *,
    creator_id: UUID,
    owner_department_id: str,
    project_id: str | None,
    data_scope: DataScope,
    security_level: SecurityLevel,
) -> bool:
    if security_rank(principal.security_clearance) < security_rank(security_level):
        return False
    if creator_id == principal.user_id:
        return True
    if data_scope is DataScope.PERSONAL:
        return False
    if data_scope is DataScope.PROJECT:
        return project_id is not None and project_id in principal.project_ids
    if data_scope is DataScope.DEPARTMENT:
        return owner_department_id in principal.department_ids
    return data_scope is DataScope.ENTERPRISE
