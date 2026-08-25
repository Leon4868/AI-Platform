"""MCP registry policy.

Verification, enablement and health are three separate facts, so they move
independently:

* **verification** is tied to a *configuration revision*. Re-pointing the
  endpoint clears it — a new address has not been tested because the old one was.
* **enablement** is the administrative decision. A health blip must not erase it.
* **health** is the last observation, and nothing more.

A server is usable only when all three agree, which `McpServerState.usable`
expresses in one place instead of scattering the conjunction.

Nothing here performs I/O. Connection tests and capability discovery happen at
the edge; the registry records what they observed. That keeps the domain testable
and keeps outbound access on the one boundary that enforces egress.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.core.errors import AuthorizationError
from app.mcp_registry.errors import (
    CapabilitiesNotSnapshottedError,
    CapabilitySnapshotNotFoundError,
    IsolatedWorkerRequiredError,
    McpConfigRevisionNotFoundError,
    McpNotConfiguredError,
    McpNotVerifiedError,
    McpServerNotFoundError,
)
from app.mcp_registry.repository import McpRegistryRepository
from app.mcp_registry.schemas import (
    CapabilitySnapshot,
    EnablementState,
    HealthReport,
    HealthState,
    McpCapability,
    McpConfigRequest,
    McpConfigRevision,
    McpServerDefinition,
    McpServerRequest,
    McpServerState,
    McpTransport,
    VerificationState,
)
from app.tools.access import Principal, ResourceAction, authorize
from app.tools.credentials import CredentialPurpose, CredentialResolver
from app.tools.schemas import McpBindingState


class McpRegistryService:
    def __init__(
        self, repository: McpRegistryRepository, *, credentials: CredentialResolver
    ) -> None:
        self._repository = repository
        self._credentials = credentials

    async def register(
        self, principal: Principal, payload: McpServerRequest
    ) -> McpServerDefinition:
        if not principal.platform_admin and (
            payload.acl.owner_department_id not in principal.department_ids
        ):
            authorize(principal, payload.acl, ResourceAction.ADMIN, owner_id=principal.actor_id)
        now = datetime.now(UTC)
        server = await self._repository.add_server(
            McpServerDefinition(
                id=uuid4(),
                tenant_id=principal.tenant_id,
                name=payload.name,
                description=payload.description,
                acl=payload.acl,
                created_by=principal.actor_id,
                created_at=now,
                updated_at=now,
            )
        )
        await self._repository.put_state(
            McpServerState(
                id=uuid4(),
                tenant_id=principal.tenant_id,
                server_id=server.id,
                created_at=now,
                updated_at=now,
            )
        )
        return server

    async def get(self, principal: Principal, server_id: UUID) -> McpServerDefinition:
        return await self._authorized(principal, server_id, ResourceAction.VIEW)

    async def list(
        self, principal: Principal, *, limit: int = 20, offset: int = 0
    ) -> tuple[list[McpServerDefinition], int]:
        servers, _ = await self._repository.list_servers(
            principal.tenant_id, limit=limit, offset=offset
        )
        visible = [item for item in servers if self._may(principal, item)]
        return visible, len(visible)

    async def set_config(
        self, principal: Principal, server_id: UUID, payload: McpConfigRequest
    ) -> McpConfigRevision:
        """Writes a new immutable configuration revision and clears verification."""
        server = await self._authorized(principal, server_id, ResourceAction.EDIT)
        if payload.credential_binding_id is not None:
            await self._credentials.validate(
                tenant_id=principal.tenant_id,
                binding_id=payload.credential_binding_id,
                purpose=CredentialPurpose.MCP_SERVER,
            )
        now = datetime.now(UTC)

        def build(revision: int) -> McpConfigRevision:
            return McpConfigRevision(
                id=uuid4(),
                tenant_id=server.tenant_id,
                server_id=server_id,
                revision=revision,
                created_at=now,
                updated_at=now,
                **payload.model_dump(),
            )

        config = await self._repository.create_config(principal.tenant_id, server_id, build)
        state = await self._state(principal, server_id)
        await self._repository.put_state(
            state.model_copy(
                update={
                    # A new address has not been tested, and the capabilities
                    # captured on the old one describe a different server.
                    "verification": VerificationState.UNVERIFIED,
                    "verified_config_revision": None,
                    "enablement": EnablementState.DISABLED,
                    "health": HealthState.UNKNOWN,
                    "capability_revision": None,
                    "updated_at": now,
                }
            )
        )
        return config

    async def current_config(
        self, principal: Principal, server_id: UUID
    ) -> McpConfigRevision:
        await self._authorized(principal, server_id, ResourceAction.VIEW)
        config = await self._repository.current_config(principal.tenant_id, server_id)
        if config is None:
            raise McpNotConfiguredError(str(server_id))
        return config

    async def get_config(
        self, principal: Principal, server_id: UUID, revision: int
    ) -> McpConfigRevision:
        await self._authorized(principal, server_id, ResourceAction.VIEW)
        config = await self._repository.get_config(principal.tenant_id, server_id, revision)
        if config is None:
            raise McpConfigRevisionNotFoundError(str(server_id), revision)
        return config

    async def state(self, principal: Principal, server_id: UUID) -> McpServerState:
        await self._authorized(principal, server_id, ResourceAction.VIEW)
        return await self._state(principal, server_id)

    async def record_health(
        self, principal: Principal, server_id: UUID, report: HealthReport
    ) -> McpServerState:
        """Files a connection test against the configuration it was run on.

        A pass verifies that configuration; a failure records ill health without
        touching the administrative decision to have the server enabled.
        """
        await self._authorized(principal, server_id, ResourceAction.EDIT)
        config = await self.current_config(principal, server_id)
        state = await self._state(principal, server_id)
        updates: dict[str, object] = {
            "health": HealthState.HEALTHY if report.healthy else HealthState.UNHEALTHY,
            "last_health_check_at": report.checked_at,
            "updated_at": datetime.now(UTC),
        }
        if report.healthy:
            updates["verification"] = VerificationState.VERIFIED
            updates["verified_config_revision"] = config.revision
        return await self._repository.put_state(state.model_copy(update=updates))

    async def save_snapshot(
        self,
        principal: Principal,
        server_id: UUID,
        capabilities: list[McpCapability],
    ) -> CapabilitySnapshot:
        """Freezes what the server reported as a new, immutable revision."""
        await self._authorized(principal, server_id, ResourceAction.EDIT)
        config = await self.current_config(principal, server_id)
        now = datetime.now(UTC)

        def build(revision: int) -> CapabilitySnapshot:
            return CapabilitySnapshot(
                id=uuid4(),
                tenant_id=principal.tenant_id,
                server_id=server_id,
                revision=revision,
                config_revision=config.revision,
                captured_at=now,
                capabilities=capabilities,
                created_at=now,
                updated_at=now,
            )

        snapshot = await self._repository.create_snapshot(principal.tenant_id, server_id, build)
        state = await self._state(principal, server_id)
        await self._repository.put_state(
            state.model_copy(
                update={"capability_revision": snapshot.revision, "updated_at": now}
            )
        )
        return snapshot

    async def get_snapshot(
        self, principal: Principal, server_id: UUID, revision: int
    ) -> CapabilitySnapshot:
        await self._authorized(principal, server_id, ResourceAction.VIEW)
        snapshot = await self._repository.get_snapshot(
            principal.tenant_id, server_id, revision
        )
        if snapshot is None:
            raise CapabilitySnapshotNotFoundError(str(server_id), revision)
        return snapshot

    async def latest_snapshot(
        self, principal: Principal, server_id: UUID
    ) -> CapabilitySnapshot | None:
        await self._authorized(principal, server_id, ResourceAction.VIEW)
        return await self._repository.latest_snapshot(principal.tenant_id, server_id)

    async def enable(self, principal: Principal, server_id: UUID) -> McpServerState:
        await self._authorized(principal, server_id, ResourceAction.PUBLISH)
        config = await self.current_config(principal, server_id)
        if config.transport is McpTransport.STDIO:
            raise IsolatedWorkerRequiredError(str(server_id))

        state = await self._state(principal, server_id)
        if (
            state.verification is not VerificationState.VERIFIED
            or state.verified_config_revision != config.revision
        ):
            raise McpNotVerifiedError(str(server_id))
        if state.capability_revision is None:
            raise CapabilitiesNotSnapshottedError(str(server_id))
        return await self._repository.put_state(
            state.model_copy(
                update={
                    "enablement": EnablementState.ENABLED,
                    "updated_at": datetime.now(UTC),
                }
            )
        )

    async def disable(self, principal: Principal, server_id: UUID) -> McpServerState:
        await self._authorized(principal, server_id, ResourceAction.PUBLISH)
        state = await self._state(principal, server_id)
        return await self._repository.put_state(
            state.model_copy(
                update={
                    "enablement": EnablementState.DISABLED,
                    "updated_at": datetime.now(UTC),
                }
            )
        )

    async def binding_state(
        self,
        principal: Principal,
        *,
        server_id: UUID,
        config_revision: int,
        capability_revision: int,
        tool_name: str,
    ) -> McpBindingState:
        """Answers the tool registry's pre-call question about one binding.

        The capability hash returned is the one in the server's **newest**
        snapshot, not the one in the revision the binding names. Reading the
        pinned revision would return the very hash the binding already holds, so
        the comparison could never fail and drift would never be seen at call
        time.

        Reports rather than raises, including for a server this caller cannot
        see: the tool registry turns any negative answer into the same refusal,
        and a distinct error here would tell a caller that a server they have no
        access to exists.
        """
        blind = McpBindingState(
            server_enabled=False,
            config_current=False,
            current_capability_hash=None,
            reviewed_on_current_config=False,
        )
        server = await self._repository.get_server(principal.tenant_id, server_id)
        if server is None or not self._may(principal, server):
            return blind

        state = await self._repository.get_state(principal.tenant_id, server_id)
        if state is None:
            return blind

        reviewed = await self._repository.get_snapshot(
            principal.tenant_id, server_id, capability_revision
        )
        current = (
            None
            if state.capability_revision is None
            else await self._repository.get_snapshot(
                principal.tenant_id, server_id, state.capability_revision
            )
        )
        current_on_current_config = (
            current is not None and current.config_revision == server.config_revision
        )
        current_hashes = current.tool_schema_hashes() if current_on_current_config else {}
        return McpBindingState(
            # A stale capability pointer must fail closed even if the mutable
            # health/verification flags happen to say the server is usable.
            server_enabled=state.usable and current_on_current_config,
            config_current=server.config_revision == config_revision,
            current_capability_hash=current_hashes.get(tool_name),
            reviewed_on_current_config=(
                reviewed is not None and reviewed.config_revision == server.config_revision
            ),
        )

    async def current_tool_hashes(
        self, principal: Principal, *, server_id: UUID
    ) -> dict[str, str]:
        """Tool name to schema hash in the server's newest snapshot.

        The tool registry reconciles against this rather than against anything a
        caller supplies, so nobody can mark another department's tool as drifted
        by asserting a hash. This is an internal reconciliation port, not a
        resource-discovery API, so only a platform principal may call it.
        """
        if not principal.platform_admin:
            raise AuthorizationError("reading current MCP hashes is a platform operation")
        server = await self._repository.get_server(principal.tenant_id, server_id)
        if server is None:
            return {}
        state = await self._repository.get_state(principal.tenant_id, server_id)
        if state is None or state.capability_revision is None:
            return {}
        snapshot = await self._repository.get_snapshot(
            principal.tenant_id, server_id, state.capability_revision
        )
        if snapshot is None or snapshot.config_revision != server.config_revision:
            return {}
        return snapshot.tool_schema_hashes()

    async def _state(self, principal: Principal, server_id: UUID) -> McpServerState:
        state = await self._repository.get_state(principal.tenant_id, server_id)
        if state is None:
            raise McpServerNotFoundError(str(server_id))
        return state

    async def _authorized(
        self, principal: Principal, server_id: UUID, action: ResourceAction
    ) -> McpServerDefinition:
        server = await self._repository.get_server(principal.tenant_id, server_id)
        if server is None:
            raise McpServerNotFoundError(str(server_id))
        authorize(principal, server.acl, action, owner_id=server.created_by)
        return server

    def _may(self, principal: Principal, server: McpServerDefinition) -> bool:
        try:
            authorize(principal, server.acl, ResourceAction.VIEW, owner_id=server.created_by)
        except AuthorizationError:
            return False
        return True
