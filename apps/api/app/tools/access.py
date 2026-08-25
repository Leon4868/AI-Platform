"""Resource-level authorization for registry resources.

Being in the same tenant is not a permission. Every resource carries its own
owning department, reach and sensitivity, and a caller's authority is computed
against *that resource* — otherwise one `edit` grant lets anyone in the tenant
rewrite every tool in it.

Three questions are answered in order, and all three must pass:

1. **Clearance** — is the caller cleared for material this sensitive?
2. **Reach** — does the resource's scope extend to the caller at all?
3. **Action** — within that reach, is this particular action permitted?

Reach and action are separate on purpose: a tool published tenant-wide is
visible and usable by everyone, and still editable only by its owners.
"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from pydantic import Field

from app.core.errors import AuthorizationError
from app.core.schemas import ApiModel


class ResourceAction(StrEnum):
    VIEW = "view"
    USE = "use"
    EDIT = "edit"
    PUBLISH = "publish"
    APPROVE = "approve"
    ADMIN = "admin"


class DataScope(StrEnum):
    PERSONAL = "personal"
    PROJECT = "project"
    DEPARTMENT = "department"
    TENANT = "tenant"


class SecurityLevel(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


_CLEARANCE_RANK = {
    SecurityLevel.PUBLIC: 0,
    SecurityLevel.INTERNAL: 1,
    SecurityLevel.CONFIDENTIAL: 2,
    SecurityLevel.RESTRICTED: 3,
}

_OWNER_ACTIONS = frozenset({ResourceAction.VIEW, ResourceAction.USE, ResourceAction.EDIT})
"""What owning a resource gets you.

Deliberately not publish, approve or admin: releasing something to the whole
organisation, or signing off on it, is authority the owning team has to be given
rather than authority it holds over its own work by default.
"""

_READER_ACTIONS = frozenset({ResourceAction.VIEW, ResourceAction.USE})


class SubjectType(StrEnum):
    USER = "user"
    DEPARTMENT = "department"
    ROLE = "role"


class AclGrant(ApiModel):
    """One explicit delegation of authority over a resource."""

    subject_type: SubjectType
    subject_id: str = Field(min_length=1, max_length=100)
    actions: frozenset[ResourceAction] = Field(min_length=1)


class ResourceAcl(ApiModel):
    """Who a resource belongs to, how far it reaches, and how sensitive it is."""

    owner_department_id: str = Field(min_length=1, max_length=100)
    data_scope: DataScope = DataScope.DEPARTMENT
    security_level: SecurityLevel = SecurityLevel.INTERNAL
    project_ids: frozenset[str] = frozenset()
    grants: list[AclGrant] = Field(default_factory=list, max_length=100)


@dataclass(frozen=True, slots=True)
class Principal:
    """The caller, as the platform resolved them for this request.

    Passed in rather than looked up: the registry must never widen its own
    authority, and every entry point has to name the action it needs.
    """

    tenant_id: UUID
    actor_id: UUID
    department_ids: frozenset[str] = frozenset()
    project_ids: frozenset[str] = frozenset()
    roles: frozenset[str] = frozenset()
    security_clearance: SecurityLevel = SecurityLevel.INTERNAL
    platform_admin: bool = False


def permitted_actions(
    principal: Principal, acl: ResourceAcl, *, owner_id: UUID
) -> frozenset[ResourceAction]:
    """Everything this caller may do to this resource, and nothing more."""
    if principal.platform_admin:
        return frozenset(ResourceAction)
    if _CLEARANCE_RANK[principal.security_clearance] < _CLEARANCE_RANK[acl.security_level]:
        return frozenset()

    actions: set[ResourceAction] = set()
    if acl.owner_department_id in principal.department_ids or principal.actor_id == owner_id:
        actions |= _OWNER_ACTIONS
    actions |= _reach(principal, acl)
    for grant in acl.grants:
        if _matches(principal, grant):
            actions |= grant.actions
    return frozenset(actions)


def authorize(
    principal: Principal,
    acl: ResourceAcl,
    action: ResourceAction,
    *,
    owner_id: UUID,
) -> None:
    allowed = permitted_actions(principal, acl, owner_id=owner_id)
    if action in allowed or ResourceAction.ADMIN in allowed:
        return
    raise AuthorizationError(f"This operation requires the '{action.value}' action")


def _reach(principal: Principal, acl: ResourceAcl) -> frozenset[ResourceAction]:
    """What the resource's own scope hands to a caller who is not an owner."""
    if acl.data_scope is DataScope.TENANT:
        return _READER_ACTIONS
    if acl.data_scope is DataScope.PROJECT and (acl.project_ids & principal.project_ids):
        return _READER_ACTIONS
    # `department` reaches only its own department, which the owner branch
    # already covered; `personal` reaches nobody else at all.
    return frozenset()


def _matches(principal: Principal, grant: AclGrant) -> bool:
    if grant.subject_type is SubjectType.USER:
        return grant.subject_id == str(principal.actor_id)
    if grant.subject_type is SubjectType.DEPARTMENT:
        return grant.subject_id in principal.department_ids
    return grant.subject_id in principal.roles
