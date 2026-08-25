"""Tool Registry policy.

Two lifecycles run side by side and never merge.

    draft ──verify──▶ verified ──publish──▶ published (terminal for the draft)
                                     │
                                     └──▶ ToolVersion, immutable, forever

    available ⇄ disabled            available/disabled ──▶ runtime_blocked ──▶ disabled
        └──────────┴──────────────────────────────────────▶ revoked (terminal)

Publishing creates a version; it never edits one. Withdrawing a tool changes its
*availability*, never its contract. That separation is what lets a published
version stay a faithful record of what was reviewed while still being switched
off in a second.

Availability is re-checked before every side effect, not once per run. The reason
to withdraw a tool in a hurry is that its next call is the damaging one.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.core.errors import AuthorizationError
from app.tools.access import Principal, ResourceAcl, ResourceAction, authorize
from app.tools.credentials import CredentialPurpose, CredentialResolver
from app.tools.errors import (
    AvailabilityTransitionError,
    DraftImmutableError,
    DraftTransitionError,
    McpBindingInvalidError,
    ToolCallBlockedError,
    ToolDraftNotFoundError,
    ToolNotBindableError,
    ToolNotFoundError,
    ToolVersionNotFoundError,
)
from app.tools.repository import PublishResult, ToolRepository
from app.tools.schemas import (
    BINDABLE,
    INVOCABLE,
    DraftStatus,
    HttpSource,
    McpBindingState,
    McpSource,
    SchemaDrift,
    Tool,
    ToolContract,
    ToolDraft,
    ToolRequest,
    ToolSourceType,
    ToolVersion,
    VersionAvailability,
    VersionAvailabilityRecord,
)

_DRAFT_TRANSITIONS: dict[DraftStatus, frozenset[DraftStatus]] = {
    DraftStatus.DRAFT: frozenset({DraftStatus.VERIFIED}),
    DraftStatus.VERIFIED: frozenset({DraftStatus.DRAFT, DraftStatus.PUBLISHED}),
    DraftStatus.PUBLISHED: frozenset(),
}

_AVAILABILITY_TRANSITIONS: dict[VersionAvailability, frozenset[VersionAvailability]] = {
    VersionAvailability.AVAILABLE: frozenset(
        {
            VersionAvailability.DISABLED,
            VersionAvailability.RUNTIME_BLOCKED,
            VersionAvailability.REVOKED,
        }
    ),
    VersionAvailability.DISABLED: frozenset(
        {
            VersionAvailability.AVAILABLE,
            VersionAvailability.RUNTIME_BLOCKED,
            VersionAvailability.REVOKED,
        }
    ),
    # Never straight back into service: clearing a platform-imposed block is a
    # second, deliberate decision.
    VersionAvailability.RUNTIME_BLOCKED: frozenset(
        {VersionAvailability.DISABLED, VersionAvailability.REVOKED}
    ),
    VersionAvailability.REVOKED: frozenset(),
}


class McpBindingPort:
    """What the tool registry needs to know from the MCP registry.

    Structural, so neither package imports the other: `McpRegistryService`
    satisfies it by shape.
    """

    async def binding_state(
        self,
        principal: Principal,
        *,
        server_id: UUID,
        config_revision: int,
        capability_revision: int,
        tool_name: str,
    ) -> McpBindingState: ...

    async def current_tool_hashes(
        self, principal: Principal, *, server_id: UUID
    ) -> Mapping[str, str]:
        """Tool name to schema hash in the server's newest snapshot.

        Read from the registry rather than accepted from a caller: a hash a
        caller supplies is an assertion about someone else's server, and acting
        on it would let anyone mark any tool as drifted.
        """


class ToolService:
    def __init__(
        self,
        repository: ToolRepository,
        *,
        credentials: CredentialResolver,
        mcp: McpBindingPort | None = None,
    ) -> None:
        self._repository = repository
        self._credentials = credentials
        self._mcp = mcp

    # ---------------------------------------------------------------- tools

    async def register(self, principal: Principal, payload: ToolRequest) -> Tool:
        _require_own_department(principal, payload.acl)
        now = datetime.now(UTC)
        return await self._repository.add_tool(
            Tool(
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

    async def get(self, principal: Principal, tool_id: UUID) -> Tool:
        return await self._authorized_tool(principal, tool_id, ResourceAction.VIEW)

    async def list(
        self, principal: Principal, *, limit: int = 20, offset: int = 0
    ) -> tuple[list[Tool], int]:
        tools, _ = await self._repository.list_tools(
            principal.tenant_id, limit=limit, offset=offset
        )
        visible = [tool for tool in tools if self._may(principal, tool, ResourceAction.VIEW)]
        return visible, len(visible)

    # --------------------------------------------------------------- drafts

    async def create_draft(
        self, principal: Principal, tool_id: UUID, contract: ToolContract
    ) -> ToolDraft:
        tool = await self._authorized_tool(principal, tool_id, ResourceAction.EDIT)
        await self._check_contract(principal, contract)
        now = datetime.now(UTC)
        return await self._repository.put_draft(
            ToolDraft(
                id=uuid4(),
                tenant_id=tool.tenant_id,
                tool_id=tool_id,
                contract=contract,
                status=DraftStatus.DRAFT,
                created_at=now,
                updated_at=now,
            )
        )

    async def update_draft(
        self, principal: Principal, draft_id: UUID, contract: ToolContract
    ) -> ToolDraft:
        draft = await self.get_draft(principal, draft_id)
        await self._authorized_tool(principal, draft.tool_id, ResourceAction.EDIT)
        if draft.status is DraftStatus.PUBLISHED:
            raise DraftImmutableError(draft_id=str(draft_id), status=draft.status.value)
        await self._check_contract(principal, contract)
        # A revision undoes review: what was verified is no longer what is here.
        return await self._repository.put_draft(
            draft.model_copy(
                update={
                    "contract": contract,
                    "status": DraftStatus.DRAFT,
                    "revision": draft.revision + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
        )

    async def get_draft(self, principal: Principal, draft_id: UUID) -> ToolDraft:
        draft = await self._repository.get_draft(principal.tenant_id, draft_id)
        if draft is None:
            raise ToolDraftNotFoundError(str(draft_id))
        await self._authorized_tool(principal, draft.tool_id, ResourceAction.VIEW)
        return draft

    async def verify_draft(self, principal: Principal, draft_id: UUID) -> ToolDraft:
        draft = await self.get_draft(principal, draft_id)
        await self._authorized_tool(principal, draft.tool_id, ResourceAction.APPROVE)
        _check_draft_transition(draft.status, DraftStatus.VERIFIED)
        return await self._repository.put_draft(
            draft.model_copy(
                update={
                    "status": DraftStatus.VERIFIED,
                    "revision": draft.revision + 1,
                    "updated_at": datetime.now(UTC),
                }
            )
        )

    # ------------------------------------------------------------- versions

    async def publish(self, principal: Principal, draft_id: UUID) -> ToolVersion:
        """Freezes a verified draft into a new immutable version.

        The checks below are a fast rejection, not the decision. The draft is
        re-read and re-checked inside the repository lock against the revision
        seen here, so two requests racing on the same draft produce one version
        and one conflict rather than two releases of the same contract.
        """
        seen = await self.get_draft(principal, draft_id)
        await self._authorized_tool(principal, seen.tool_id, ResourceAction.PUBLISH)
        _check_draft_transition(seen.status, DraftStatus.PUBLISHED)
        await self._check_contract(principal, seen.contract, publishing=True)
        now = datetime.now(UTC)

        def build(number: int, draft: ToolDraft) -> PublishResult:
            return PublishResult(
                version=ToolVersion(
                    id=uuid4(),
                    tenant_id=draft.tenant_id,
                    tool_id=draft.tool_id,
                    version=number,
                    contract=draft.contract,
                    source_draft_id=draft.id,
                    published_by=principal.actor_id,
                    created_at=now,
                    updated_at=now,
                ),
                availability=VersionAvailabilityRecord(
                    id=uuid4(),
                    tenant_id=draft.tenant_id,
                    tool_id=draft.tool_id,
                    version=number,
                    state=VersionAvailability.AVAILABLE,
                    created_at=now,
                    updated_at=now,
                ),
                draft=draft.model_copy(
                    update={
                        "status": DraftStatus.PUBLISHED,
                        "published_version": number,
                        "revision": draft.revision + 1,
                        "updated_at": now,
                    }
                ),
            )

        result = await self._repository.publish(
            principal.tenant_id, draft_id, seen.revision, build
        )
        return result.version

    async def get_version(
        self, principal: Principal, tool_id: UUID, version: int
    ) -> ToolVersion:
        await self._authorized_tool(principal, tool_id, ResourceAction.VIEW)
        found = await self._repository.get_version(principal.tenant_id, tool_id, version)
        if found is None:
            raise ToolVersionNotFoundError(str(tool_id), version)
        return found

    async def list_versions(self, principal: Principal, tool_id: UUID) -> list[ToolVersion]:
        await self._authorized_tool(principal, tool_id, ResourceAction.VIEW)
        return await self._repository.list_versions(principal.tenant_id, tool_id)

    # --------------------------------------------------------- availability

    async def availability(
        self, principal: Principal, tool_id: UUID, version: int
    ) -> VersionAvailabilityRecord:
        await self.get_version(principal, tool_id, version)
        record = await self._repository.get_availability(principal.tenant_id, tool_id, version)
        if record is None:
            raise ToolVersionNotFoundError(str(tool_id), version)
        return record

    async def set_availability(
        self,
        principal: Principal,
        tool_id: UUID,
        version: int,
        state: VersionAvailability,
        *,
        reason: str = "",
    ) -> VersionAvailabilityRecord:
        action = (
            ResourceAction.ADMIN
            if state is VersionAvailability.REVOKED
            else ResourceAction.PUBLISH
        )
        await self._authorized_tool(principal, tool_id, action)
        return await self._move_availability(principal, tool_id, version, state, reason=reason)

    # ----------------------------------------------------------- admission

    async def admit_binding(
        self, principal: Principal, tool_id: UUID, version: int
    ) -> ToolVersion:
        """May a new run bind to this version?"""
        await self._authorized_tool(principal, tool_id, ResourceAction.USE)
        record = await self.availability(principal, tool_id, version)
        if record.state not in BINDABLE:
            raise ToolNotBindableError(
                tool_id=str(tool_id), version=version, state=record.state.value
            )
        return await self.get_version(principal, tool_id, version)

    async def authorize_call(
        self, principal: Principal, tool_id: UUID, version: int
    ) -> ToolVersion:
        """Re-checks, immediately before a side effect, that this call may run.

        For an MCP tool the live server is consulted every time: enabled, still
        on the reviewed configuration, still reporting the reviewed schema. Drift
        found here also blocks the version, so the next caller is refused without
        another round trip.
        """
        await self._authorized_tool(principal, tool_id, ResourceAction.USE)
        record = await self.availability(principal, tool_id, version)
        if record.state not in INVOCABLE:
            raise ToolCallBlockedError(
                tool_id=str(tool_id), version=version, state=record.state.value
            )

        candidate = await self.get_version(principal, tool_id, version)
        source = candidate.contract.source
        if isinstance(source, McpSource):
            await self._assert_mcp_binding(principal, tool_id, version, source)
        return candidate

    # ---------------------------------------------------------------- drift

    async def reconcile_server(self, system: Principal, server_id: UUID) -> list[SchemaDrift]:
        """Re-checks every version bound to a server and blocks the drifted ones.

        Two things make this safe to let change other people's tools, and both
        are load-bearing:

        * It is reachable only by a **system principal**. Availability is a
          platform-wide safety control, and an ordinary caller must not be able
          to reach into a department they have no rights over and switch its
          tools off.
        * The hashes come from the **registry**, never from the caller. A
          caller-supplied hash is an assertion about someone else's server;
          acting on it would let anyone mark any tool as drifted by lying.

        Blocking rather than merely reporting is deliberate: a drifted contract
        is not stale documentation. The arguments were built from the reviewed
        schema, and a server that now expects something else may act on a
        misinterpreted call rather than reject it.
        """
        _require_system(system)
        if self._mcp is None:
            raise McpBindingInvalidError(
                detail="the MCP registry is not available, so bindings cannot be verified"
            )
        current = await self._mcp.current_tool_hashes(system, server_id=server_id)

        drifts: list[SchemaDrift] = []
        for version in await self._repository.all_versions(system.tenant_id):
            source = version.contract.source
            if not isinstance(source, McpSource) or source.server_id != server_id:
                continue
            record = await self._repository.get_availability(
                system.tenant_id, version.tool_id, version.version
            )
            if record is None or record.state is VersionAvailability.REVOKED:
                continue
            observed = current.get(source.tool_name)
            if observed == source.capability_hash:
                continue
            drifts.append(
                SchemaDrift(
                    tool_id=version.tool_id,
                    version=version.version,
                    tool_name=source.tool_name,
                    reviewed_schema_hash=source.capability_hash,
                    current_schema_hash=observed,
                )
            )
            if record.state is not VersionAvailability.RUNTIME_BLOCKED:
                await self._move_availability(
                    system,
                    version.tool_id,
                    version.version,
                    VersionAvailability.RUNTIME_BLOCKED,
                    reason=f"capability '{source.tool_name}' no longer matches the reviewed schema",
                )
        return drifts

    # -------------------------------------------------------------- helpers

    async def _assert_mcp_binding(
        self, principal: Principal, tool_id: UUID, version: int, source: McpSource
    ) -> None:
        if self._mcp is None:
            raise McpBindingInvalidError(
                detail="the MCP registry is not available, so the binding cannot be verified"
            )
        state = await self._mcp.binding_state(
            principal,
            server_id=source.server_id,
            config_revision=source.config_revision,
            capability_revision=source.capability_revision,
            tool_name=source.tool_name,
        )
        problem = _binding_problem(state, source)
        if problem is None:
            return
        await self._move_availability(
            principal, tool_id, version, VersionAvailability.RUNTIME_BLOCKED, reason=problem
        )
        raise McpBindingInvalidError(detail=problem)

    async def _move_availability(
        self,
        principal: Principal,
        tool_id: UUID,
        version: int,
        state: VersionAvailability,
        *,
        reason: str,
    ) -> VersionAvailabilityRecord:
        record = await self._repository.get_availability(
            principal.tenant_id, tool_id, version
        )
        if record is None:
            raise ToolVersionNotFoundError(str(tool_id), version)
        if state is not record.state and state not in _AVAILABILITY_TRANSITIONS[record.state]:
            raise AvailabilityTransitionError(
                current=record.state.value, requested=state.value
            )
        return await self._repository.put_availability(
            record.model_copy(
                update={"state": state, "reason": reason, "updated_at": datetime.now(UTC)}
            )
        )

    async def _check_contract(
        self, principal: Principal, contract: ToolContract, *, publishing: bool = False
    ) -> None:
        source = contract.source
        if isinstance(source, HttpSource) and source.credential_binding_id is not None:
            await self._credentials.validate(
                tenant_id=principal.tenant_id,
                binding_id=source.credential_binding_id,
                purpose=CredentialPurpose.HTTP_TOOL,
            )
        if publishing and isinstance(source, McpSource):
            if self._mcp is None:
                raise McpBindingInvalidError(
                    detail="the MCP registry is not available, so the binding cannot be verified"
                )
            state = await self._mcp.binding_state(
                principal,
                server_id=source.server_id,
                config_revision=source.config_revision,
                capability_revision=source.capability_revision,
                tool_name=source.tool_name,
            )
            problem = _binding_problem(state, source)
            if problem is not None:
                raise McpBindingInvalidError(detail=problem)

    async def _authorized_tool(
        self, principal: Principal, tool_id: UUID, action: ResourceAction
    ) -> Tool:
        tool = await self._repository.get_tool(principal.tenant_id, tool_id)
        if tool is None:
            raise ToolNotFoundError(str(tool_id))
        authorize(principal, tool.acl, action, owner_id=tool.created_by)
        return tool

    def _may(self, principal: Principal, tool: Tool, action: ResourceAction) -> bool:
        try:
            authorize(principal, tool.acl, action, owner_id=tool.created_by)
        except AuthorizationError:
            return False
        return True


def _binding_problem(state: McpBindingState, source: McpSource) -> str | None:
    """Compares the pinned contract against the server's *current* answer.

    Never against the snapshot the binding names — that one agrees with the
    binding by construction, so comparing the two proves only that the pin was
    written down correctly.
    """
    if not state.server_enabled:
        return "the MCP server is not enabled"
    if not state.config_current:
        return (
            f"the server has moved past configuration revision {source.config_revision}; "
            "the endpoint this tool was reviewed against is no longer in force"
        )
    if not state.reviewed_on_current_config:
        return (
            f"capability revision {source.capability_revision} was captured against a "
            "different configuration and no longer describes this server"
        )
    if state.current_capability_hash is None:
        return f"capability '{source.tool_name}' is no longer offered by the server"
    if state.current_capability_hash != source.capability_hash:
        return f"capability '{source.tool_name}' no longer matches the reviewed schema"
    return None


def _require_system(principal: Principal) -> None:
    if not principal.platform_admin:
        raise AuthorizationError(
            "reconciling tool availability is a platform operation"
        )


def _check_draft_transition(current: DraftStatus, target: DraftStatus) -> None:
    if target not in _DRAFT_TRANSITIONS[current]:
        raise DraftTransitionError(current=current.value, requested=target.value)


def _require_own_department(principal: Principal, acl: ResourceAcl) -> None:
    """A resource cannot be filed under a department the creator is not in.

    Otherwise the ACL is trivially bypassed: create the tool owned by whichever
    department you want authority over, and inherit owner rights on it.
    """
    if principal.platform_admin:
        return
    if acl.owner_department_id not in principal.department_ids:
        raise AuthorizationError(
            f"'{acl.owner_department_id}' is not a department you belong to"
        )
