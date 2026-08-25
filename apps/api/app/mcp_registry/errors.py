from app.core.errors import DomainError


class McpServerNotFoundError(DomainError):
    def __init__(self, server_id: str) -> None:
        super().__init__(
            title="MCP server not found",
            detail=f"MCP server '{server_id}' does not exist or is not accessible",
            status_code=404,
            error_code="mcp_server_not_found",
        )


class McpConfigRevisionNotFoundError(DomainError):
    def __init__(self, server_id: str, revision: int) -> None:
        super().__init__(
            title="MCP configuration revision not found",
            detail=f"MCP server '{server_id}' has no configuration revision {revision}",
            status_code=404,
            error_code="mcp_config_revision_not_found",
        )


class CapabilitySnapshotNotFoundError(DomainError):
    def __init__(self, server_id: str, revision: int) -> None:
        super().__init__(
            title="Capability snapshot not found",
            detail=f"MCP server '{server_id}' has no capability revision {revision}",
            status_code=404,
            error_code="mcp_capability_snapshot_not_found",
        )


class McpNotConfiguredError(DomainError):
    def __init__(self, server_id: str) -> None:
        super().__init__(
            title="MCP server has no configuration",
            detail=f"MCP server '{server_id}' has no configuration revision yet",
            status_code=409,
            error_code="mcp_not_configured",
        )


class McpNotVerifiedError(DomainError):
    """Enabling a server whose current configuration was never tested.

    Verification is tied to a configuration revision, not to the server: a new
    endpoint has not been tested just because the previous one was.
    """

    def __init__(self, server_id: str) -> None:
        super().__init__(
            title="MCP server is not verified",
            detail=(
                f"MCP server '{server_id}' has not passed a connection test on its "
                "current configuration"
            ),
            status_code=409,
            error_code="mcp_not_verified",
        )


class CapabilitiesNotSnapshottedError(DomainError):
    """Enabling a server whose capabilities were never captured and reviewed.

    The registration flow is register → configure → test → sync → review.
    Enabling before a snapshot exists would let a workflow bind to whatever the
    server happens to answer at call time, which is the opposite of a reviewed
    contract.
    """

    def __init__(self, server_id: str) -> None:
        super().__init__(
            title="Capabilities not snapshotted",
            detail=(
                f"MCP server '{server_id}' has no capability snapshot for its current "
                "configuration; sync and review its capabilities before enabling it"
            ),
            status_code=409,
            error_code="mcp_capabilities_not_snapshotted",
        )


class IsolatedWorkerRequiredError(DomainError):
    """A `stdio` server spawns a local process, which this phase cannot contain.

    Registering one is allowed so the intent is recorded, but it stays unusable
    until an isolated worker exists — the flag is a plan, not a sandbox.
    """

    def __init__(self, server_id: str) -> None:
        super().__init__(
            title="Isolated worker required",
            detail=(
                f"MCP server '{server_id}' uses the stdio transport and can only run "
                "inside an isolated worker, which is not available in this phase"
            ),
            status_code=409,
            error_code="mcp_isolated_worker_required",
        )
