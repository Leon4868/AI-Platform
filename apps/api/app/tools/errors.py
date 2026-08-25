from app.core.errors import DomainError


class ToolNotFoundError(DomainError):
    def __init__(self, tool_id: str) -> None:
        super().__init__(
            title="Tool not found",
            detail=f"Tool '{tool_id}' does not exist or is not accessible",
            status_code=404,
            error_code="tool_not_found",
        )


class ToolDraftNotFoundError(DomainError):
    def __init__(self, draft_id: str) -> None:
        super().__init__(
            title="Tool draft not found",
            detail=f"Tool draft '{draft_id}' does not exist or is not accessible",
            status_code=404,
            error_code="tool_draft_not_found",
        )


class ToolVersionNotFoundError(DomainError):
    def __init__(self, tool_id: str, version: int) -> None:
        super().__init__(
            title="Tool version not found",
            detail=f"Tool '{tool_id}' has no version {version}",
            status_code=404,
            error_code="tool_version_not_found",
        )


class DraftTransitionError(DomainError):
    def __init__(self, *, current: str, requested: str) -> None:
        super().__init__(
            title="Invalid draft transition",
            detail=f"A '{current}' draft cannot become '{requested}'",
            status_code=409,
            error_code="tool_draft_transition_invalid",
        )


class DraftImmutableError(DomainError):
    """A draft that has been published is history, not a working copy."""

    def __init__(self, *, draft_id: str, status: str) -> None:
        super().__init__(
            title="Draft is no longer editable",
            detail=(
                f"Draft '{draft_id}' is {status}; start a new draft instead of editing it"
            ),
            status_code=409,
            error_code="tool_draft_immutable",
        )


class ConcurrentPublishError(DomainError):
    """The draft moved between the check and the write, so this publish lost.

    Detected inside the same lock that allocates the version number: without
    that, two requests both read a verified draft and both mint a version from
    it, and the tool ends up with two "first" releases of the same contract.
    """

    def __init__(self, *, draft_id: str) -> None:
        super().__init__(
            title="Draft was published concurrently",
            detail=f"Draft '{draft_id}' is no longer a verified, unpublished draft",
            status_code=409,
            error_code="tool_draft_publish_conflict",
        )


class AvailabilityTransitionError(DomainError):
    def __init__(self, *, current: str, requested: str) -> None:
        super().__init__(
            title="Invalid availability transition",
            detail=f"A '{current}' version cannot become '{requested}'",
            status_code=409,
            error_code="tool_availability_transition_invalid",
        )


class ToolNotBindableError(DomainError):
    def __init__(self, *, tool_id: str, version: int, state: str) -> None:
        super().__init__(
            title="Tool version is not available",
            detail=f"Version {version} of tool '{tool_id}' is {state} and cannot be bound",
            status_code=409,
            error_code="tool_version_not_bindable",
        )


class ToolCallBlockedError(DomainError):
    """Refused at the point of the side effect, not merely at binding time."""

    def __init__(self, *, tool_id: str, version: int, state: str) -> None:
        super().__init__(
            title="Tool call blocked",
            detail=(
                f"Version {version} of tool '{tool_id}' is {state}; "
                "no further calls are permitted"
            ),
            status_code=409,
            error_code="tool_call_blocked",
        )


class McpBindingInvalidError(DomainError):
    """The MCP binding no longer describes something callable.

    Covers a disabled server, a re-pointed configuration and a changed or
    withdrawn capability. Refusing is the safe direction: the arguments were
    built from the reviewed schema, and a server that now expects something else
    may act on a misinterpreted call rather than reject it.
    """

    def __init__(self, *, detail: str) -> None:
        super().__init__(
            title="MCP binding is not usable",
            detail=detail,
            status_code=409,
            error_code="mcp_binding_invalid",
        )
