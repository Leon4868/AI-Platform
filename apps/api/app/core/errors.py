from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class DomainError(Exception):
    title: str
    detail: str
    status_code: int
    error_code: str
    errors: list[dict[str, Any]] = field(default_factory=list)


class NotFoundError(DomainError):
    def __init__(self, resource: str, resource_id: str) -> None:
        super().__init__(
            title="Resource not found",
            detail=f"{resource} '{resource_id}' does not exist or is not accessible",
            status_code=404,
            error_code="resource_not_found",
        )


class ConflictError(DomainError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            title="Conflict",
            detail=detail,
            status_code=409,
            error_code="resource_conflict",
        )


class DefinitionValidationError(DomainError):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__(
            title="Invalid workflow definition",
            detail="The workflow graph failed semantic validation",
            status_code=422,
            error_code="workflow_definition_invalid",
            errors=errors,
        )


class AuthenticationError(DomainError):
    def __init__(self, detail: str = "Authentication is required") -> None:
        super().__init__(
            title="Unauthenticated",
            detail=detail,
            status_code=401,
            error_code="unauthenticated",
        )


class AuthorizationError(DomainError):
    def __init__(self, detail: str = "You do not have permission for this operation") -> None:
        super().__init__(
            title="Forbidden",
            detail=detail,
            status_code=403,
            error_code="forbidden",
        )
