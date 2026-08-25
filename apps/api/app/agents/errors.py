"""Errors raised by the Agent aggregate."""

from typing import Any

from app.core.errors import DomainError


class AgentNotFoundError(DomainError):
    def __init__(self, agent_id: str) -> None:
        super().__init__(
            title="Agent not found",
            detail=f"agent '{agent_id}' does not exist or is not accessible",
            status_code=404,
            error_code="agent_not_found",
        )


class AgentConflictError(DomainError):
    def __init__(self, detail: str) -> None:
        super().__init__(
            title="Agent conflict",
            detail=detail,
            status_code=409,
            error_code="agent_conflict",
        )


class AgentVersionImmutableError(DomainError):
    def __init__(self, agent_id: str, version: int) -> None:
        super().__init__(
            title="Agent version is immutable",
            detail=f"agent '{agent_id}' version {version} has already been published",
            status_code=409,
            error_code="agent_version_immutable",
        )


class AgentResourceValidationError(DomainError):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__(
            title="Agent cannot be published",
            detail="one or more Agent resource or policy checks failed",
            status_code=422,
            error_code="agent_publish_invalid",
            errors=errors,
        )
