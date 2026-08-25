from uuid import UUID

from fastapi import Request
from pydantic import TypeAdapter, ValidationError

from app.core.errors import AuthenticationError
from app.identity.schemas import Permission, Principal

_uuid_adapter = TypeAdapter(UUID)
_DEFAULT_PERMISSIONS = frozenset(Permission)


class DevelopmentIdentityProvider:
    """Header identity for local/test use only; production startup must replace it."""

    async def authenticate(self, request: Request) -> Principal:
        raw_user_id = request.headers.get("X-Dev-User-Id", "00000000-0000-4000-8000-000000000001")
        raw_tenant_id = request.headers.get("X-Dev-Tenant-Id", "00000000-0000-4000-8000-000000000010")
        try:
            user_id = _uuid_adapter.validate_python(raw_user_id)
            tenant_id = _uuid_adapter.validate_python(raw_tenant_id)
        except ValidationError as exc:
            raise AuthenticationError("Development identity headers must contain valid UUIDs") from exc

        raw_permissions = request.headers.get("X-Dev-Permissions")
        if raw_permissions is None:
            permissions = _DEFAULT_PERMISSIONS
        else:
            requested = [value.strip() for value in raw_permissions.split(",") if value.strip()]
            try:
                permissions = frozenset(Permission(value) for value in requested)
            except ValueError as exc:
                raise AuthenticationError("X-Dev-Permissions contains an unknown permission") from exc

        return Principal(
            user_id=user_id,
            tenant_id=tenant_id,
            display_name=request.headers.get("X-Dev-Display-Name", "Development User")[:100],
            permissions=permissions,
            department_ids=_csv(request.headers.get("X-Dev-Department-Ids")),
            project_ids=_csv(request.headers.get("X-Dev-Project-Ids")),
            roles=_csv(request.headers.get("X-Dev-Roles")) or frozenset({"employee"}),
            security_clearance=_clearance(request.headers.get("X-Dev-Security-Clearance")),
        )


def _csv(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset(item.strip()[:128] for item in value.split(",") if item.strip())


def _clearance(value: str | None) -> str:
    clearance = (value or "internal").strip()
    if clearance not in {"internal", "department_sensitive", "confidential"}:
        raise AuthenticationError("X-Dev-Security-Clearance contains an unknown level")
    return clearance
