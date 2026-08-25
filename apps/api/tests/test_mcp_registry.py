"""MCP Server Registry: immutable config revisions, independent state, snapshots, ACL."""

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.core.errors import AuthorizationError
from app.mcp_registry.errors import (
    CapabilitiesNotSnapshottedError,
    CapabilitySnapshotNotFoundError,
    IsolatedWorkerRequiredError,
    McpNotConfiguredError,
    McpNotVerifiedError,
    McpServerNotFoundError,
)
from app.mcp_registry.repository import InMemoryMcpRegistryRepository
from app.mcp_registry.schemas import (
    EnablementState,
    HealthReport,
    HealthState,
    McpCapability,
    McpCapabilityKind,
    McpConfigRequest,
    McpServerRequest,
    McpTransport,
    VerificationState,
)
from app.mcp_registry.service import McpRegistryService
from app.tools.access import (
    AclGrant,
    DataScope,
    Principal,
    ResourceAcl,
    ResourceAction,
    SecurityLevel,
    SubjectType,
)
from app.tools.credentials import (
    CredentialBinding,
    CredentialBindingError,
    CredentialPurpose,
    InMemoryCredentialResolver,
)

TENANT = UUID("00000000-0000-4000-8000-000000000010")
DESIGN = "dept-design"
NOW = datetime(2026, 8, 25, 10, 0, tzinfo=UTC)
LATER = datetime(2026, 8, 25, 11, 0, tzinfo=UTC)


def service(credentials=None) -> McpRegistryService:
    return McpRegistryService(
        InMemoryMcpRegistryRepository(),
        credentials=credentials or InMemoryCredentialResolver(),
    )


def owner(**overrides) -> Principal:
    payload: dict[str, Any] = {
        "tenant_id": TENANT,
        "actor_id": uuid4(),
        "department_ids": frozenset({DESIGN}),
        "security_clearance": SecurityLevel.CONFIDENTIAL,
    }
    return Principal(**{**payload, **overrides})


def outsider(**overrides) -> Principal:
    payload: dict[str, Any] = {
        "tenant_id": TENANT,
        "actor_id": uuid4(),
        "department_ids": frozenset({"dept-sales"}),
        "security_clearance": SecurityLevel.CONFIDENTIAL,
    }
    return Principal(**{**payload, **overrides})


def acl(**overrides) -> ResourceAcl:
    return ResourceAcl(**{"owner_department_id": DESIGN, **overrides})


def publishable_acl(**overrides) -> ResourceAcl:
    grants = [
        AclGrant(
            subject_type=SubjectType.DEPARTMENT,
            subject_id=DESIGN,
            actions=frozenset({ResourceAction.APPROVE, ResourceAction.PUBLISH}),
        )
    ]
    return acl(**{"grants": grants, **overrides})


def server_request(**overrides) -> McpServerRequest:
    return McpServerRequest(**{"name": "订单 MCP", "acl": publishable_acl(), **overrides})


def http_config(**overrides) -> McpConfigRequest:
    payload: dict[str, Any] = {
        "transport": McpTransport.STREAMABLE_HTTP,
        "endpoint": "https://mcp.example.com/stream",
        "egress_allowlist": ["mcp.example.com"],
    }
    return McpConfigRequest(**{**payload, **overrides})


def stdio_config(**overrides) -> McpConfigRequest:
    payload: dict[str, Any] = {
        "transport": McpTransport.STDIO,
        "requires_isolated_worker": True,
    }
    return McpConfigRequest(**{**payload, **overrides})


def capability(name: str = "search_orders", **overrides) -> McpCapability:
    payload: dict[str, Any] = {
        "kind": McpCapabilityKind.TOOL,
        "name": name,
        "description": "检索订单",
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
    }
    return McpCapability(**{**payload, **overrides})


async def ready_server(mcp: McpRegistryService, principal: Principal):
    server = await mcp.register(principal, server_request())
    await mcp.set_config(principal, server.id, http_config())
    await mcp.record_health(principal, server.id, HealthReport(healthy=True, checked_at=NOW))
    await mcp.save_snapshot(principal, server.id, [capability()])
    await mcp.enable(principal, server.id)
    return server


# ---------------------------------------------------------------- transport


def test_a_streamable_http_config_needs_an_endpoint_inside_its_allowlist() -> None:
    with pytest.raises(ValidationError):
        http_config(endpoint=None)
    with pytest.raises(ValidationError):
        http_config(egress_allowlist=[])
    with pytest.raises(ValidationError):
        http_config(egress_allowlist=["other.example.com"])

    assert http_config(egress_allowlist=["*.example.com"])


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://mcp.example.com/stream",
        "https://127.0.0.1/stream",
        "https://169.254.169.254/stream",
        "https://localhost/stream",
        "https://mcp.internal/stream",
        "https://user:secret@mcp.example.com/stream",
        "https://mcp.example.com/stream#token=abc",
    ],
)
def test_an_unacceptable_endpoint_is_refused(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        http_config(endpoint=endpoint)


def test_a_stdio_config_must_declare_that_it_needs_an_isolated_worker() -> None:
    with pytest.raises(ValidationError):
        stdio_config(requires_isolated_worker=False)
    with pytest.raises(ValidationError):
        stdio_config(endpoint="https://mcp.example.com/stream")

    assert stdio_config().requires_isolated_worker is True


def test_a_stdio_server_cannot_be_enabled_in_this_phase() -> None:
    """The flag records the plan; it is not itself a sandbox."""

    async def scenario() -> None:
        mcp, principal = service(), owner()
        server = await mcp.register(principal, server_request())
        await mcp.set_config(principal, server.id, stdio_config())
        await mcp.record_health(
            principal, server.id, HealthReport(healthy=True, checked_at=NOW)
        )
        await mcp.save_snapshot(principal, server.id, [capability()])

        with pytest.raises(IsolatedWorkerRequiredError):
            await mcp.enable(principal, server.id)

    asyncio.run(scenario())


# ------------------------------------------------------ config immutability


def test_config_revisions_accumulate_and_are_never_rewritten() -> None:
    async def scenario() -> None:
        mcp, principal = service(), owner()
        server = await mcp.register(principal, server_request())

        first = await mcp.set_config(principal, server.id, http_config())
        second = await mcp.set_config(
            principal,
            server.id,
            http_config(endpoint="https://mcp2.example.com/stream", egress_allowlist=["mcp2.example.com"]),
        )

        assert (first.revision, second.revision) == (1, 2)
        replayed = await mcp.get_config(principal, server.id, 1)
        assert replayed.endpoint == "https://mcp.example.com/stream"
        assert (await mcp.current_config(principal, server.id)).revision == 2

    asyncio.run(scenario())


def test_re_pointing_the_endpoint_withdraws_verification_and_enablement() -> None:
    """A new address has not been tested just because the old one was."""

    async def scenario() -> None:
        mcp, principal = service(), owner()
        server = await ready_server(mcp, principal)
        assert (await mcp.state(principal, server.id)).usable is True

        await mcp.set_config(
            principal,
            server.id,
            http_config(endpoint="https://elsewhere.example.com/stream", egress_allowlist=["elsewhere.example.com"]),
        )

        state = await mcp.state(principal, server.id)
        assert state.verification is VerificationState.UNVERIFIED
        assert state.enablement is EnablementState.DISABLED
        assert state.capability_revision is None
        assert state.usable is False

        with pytest.raises(McpNotVerifiedError):
            await mcp.enable(principal, server.id)

    asyncio.run(scenario())


def test_a_server_without_a_configuration_cannot_do_anything() -> None:
    async def scenario() -> None:
        mcp, principal = service(), owner()
        server = await mcp.register(principal, server_request())

        with pytest.raises(McpNotConfiguredError):
            await mcp.current_config(principal, server.id)
        with pytest.raises(McpNotConfiguredError):
            await mcp.enable(principal, server.id)

    asyncio.run(scenario())


# ----------------------------------------------------- independent state


def test_verification_enablement_and_health_move_independently() -> None:
    async def scenario() -> None:
        mcp, principal = service(), owner()
        server = await ready_server(mcp, principal)

        # A health blip must not erase the administrative decision.
        sick = await mcp.record_health(
            principal, server.id, HealthReport(healthy=False, checked_at=LATER)
        )
        assert sick.health is HealthState.UNHEALTHY
        assert sick.enablement is EnablementState.ENABLED
        assert sick.verification is VerificationState.VERIFIED
        assert sick.usable is False

        recovered = await mcp.record_health(
            principal, server.id, HealthReport(healthy=True, checked_at=LATER)
        )
        assert recovered.usable is True

        # Disabling is an administrative act, and health is unaffected by it.
        off = await mcp.disable(principal, server.id)
        assert off.enablement is EnablementState.DISABLED
        assert off.health is HealthState.HEALTHY
        assert off.usable is False

    asyncio.run(scenario())


def test_enabling_requires_verification_and_a_snapshot() -> None:
    async def scenario() -> None:
        mcp, principal = service(), owner()
        server = await mcp.register(principal, server_request())
        await mcp.set_config(principal, server.id, http_config())

        with pytest.raises(McpNotVerifiedError):
            await mcp.enable(principal, server.id)

        await mcp.record_health(
            principal, server.id, HealthReport(healthy=True, checked_at=NOW)
        )
        with pytest.raises(CapabilitiesNotSnapshottedError):
            await mcp.enable(principal, server.id)

        await mcp.save_snapshot(principal, server.id, [capability()])
        assert (await mcp.enable(principal, server.id)).enablement is EnablementState.ENABLED

    asyncio.run(scenario())


# ------------------------------------------------------------- snapshots


def test_snapshots_are_immutable_and_tied_to_a_configuration() -> None:
    async def scenario() -> None:
        mcp, principal = service(), owner()
        server = await mcp.register(principal, server_request())
        await mcp.set_config(principal, server.id, http_config())

        first = await mcp.save_snapshot(principal, server.id, [capability()])
        second = await mcp.save_snapshot(
            principal, server.id, [capability(description="检索并导出订单")]
        )

        assert (first.revision, second.revision) == (1, 2)
        assert first.config_revision == second.config_revision == 1

        replayed = await mcp.get_snapshot(principal, server.id, 1)
        assert replayed.capabilities[0].description == "检索订单"
        assert replayed.tool_schema_hashes() != second.tool_schema_hashes()

    asyncio.run(scenario())


def test_concurrent_syncs_never_share_a_revision() -> None:
    """Allocating the revision and writing the snapshot is one step, not two."""

    async def scenario() -> None:
        mcp, principal = service(), owner()
        server = await mcp.register(principal, server_request())
        await mcp.set_config(principal, server.id, http_config())

        snapshots = await asyncio.gather(
            *(mcp.save_snapshot(principal, server.id, [capability()]) for _ in range(10))
        )

        assert sorted(item.revision for item in snapshots) == list(range(1, 11))

    asyncio.run(scenario())


def test_the_capability_hash_covers_the_prose_and_annotations() -> None:
    baseline = capability().schema_hash

    assert capability(description="别的说明").schema_hash != baseline
    assert capability(annotations={"destructiveHint": True}).schema_hash != baseline
    assert capability(name="other_tool").schema_hash != baseline
    assert capability().schema_hash == baseline


def test_only_tools_appear_in_the_drift_input() -> None:
    async def scenario() -> None:
        mcp, principal = service(), owner()
        server = await mcp.register(principal, server_request())
        await mcp.set_config(principal, server.id, http_config())

        snapshot = await mcp.save_snapshot(
            principal,
            server.id,
            [
                capability(),
                capability(name="order_policy", kind=McpCapabilityKind.RESOURCE),
                capability(name="summarise", kind=McpCapabilityKind.PROMPT),
            ],
        )

        assert set(snapshot.tool_schema_hashes()) == {"search_orders"}

    asyncio.run(scenario())


def test_duplicate_capability_names_are_rejected() -> None:
    async def scenario() -> None:
        mcp, principal = service(), owner()
        server = await mcp.register(principal, server_request())
        await mcp.set_config(principal, server.id, http_config())

        with pytest.raises(ValidationError):
            await mcp.save_snapshot(principal, server.id, [capability(), capability()])

        # The same name under a different kind is a different capability.
        snapshot = await mcp.save_snapshot(
            principal, server.id, [capability(), capability(kind=McpCapabilityKind.RESOURCE)]
        )
        assert len(snapshot.capabilities) == 2

    asyncio.run(scenario())


def test_a_missing_revision_of_an_existing_server_is_reported_as_such() -> None:
    async def scenario() -> None:
        mcp, principal = service(), owner()
        server = await mcp.register(principal, server_request())
        await mcp.set_config(principal, server.id, http_config())

        with pytest.raises(CapabilitySnapshotNotFoundError):
            await mcp.get_snapshot(principal, server.id, 7)
        assert await mcp.latest_snapshot(principal, server.id) is None

    asyncio.run(scenario())


# ---------------------------------------------------------- binding state


def test_binding_state_answers_the_tool_registrys_question() -> None:
    async def scenario() -> None:
        mcp, principal = service(), owner()
        server = await ready_server(mcp, principal)
        snapshot = await mcp.get_snapshot(principal, server.id, 1)
        expected = snapshot.tool_schema_hashes()["search_orders"]

        healthy = await mcp.binding_state(
            principal,
            server_id=server.id,
            config_revision=1,
            capability_revision=1,
            tool_name="search_orders",
        )
        assert healthy.server_enabled is True
        assert healthy.config_current is True
        assert healthy.reviewed_on_current_config is True
        assert healthy.current_capability_hash == expected

        missing = await mcp.binding_state(
            principal,
            server_id=server.id,
            config_revision=1,
            capability_revision=1,
            tool_name="gone",
        )
        assert missing.current_capability_hash is None

        stale = await mcp.binding_state(
            principal,
            server_id=server.id,
            config_revision=99,
            capability_revision=1,
            tool_name="search_orders",
        )
        assert stale.config_current is False

    asyncio.run(scenario())


def test_binding_state_reports_the_newest_hash_not_the_pinned_one() -> None:
    """The counterexample: a check that reads the pinned revision cannot fail.

    A tool pinned to capability revision 1 must be told what the server says
    *now*. Answering from revision 1 would return the very hash the binding
    already holds, so drift would be invisible at call time no matter how far
    the server had moved.
    """

    async def scenario() -> None:
        mcp, principal = service(), owner()
        server = await ready_server(mcp, principal)
        pinned = (await mcp.get_snapshot(principal, server.id, 1)).tool_schema_hashes()[
            "search_orders"
        ]

        await mcp.save_snapshot(
            principal, server.id, [capability(description="含退款明细")]
        )

        state = await mcp.binding_state(
            principal,
            server_id=server.id,
            config_revision=1,
            capability_revision=1,
            tool_name="search_orders",
        )

        assert state.current_capability_hash != pinned
        assert state.current_capability_hash == (
            await mcp.current_tool_hashes(
                owner(platform_admin=True), server_id=server.id
            )
        )["search_orders"]

    asyncio.run(scenario())


def test_a_snapshot_from_a_replaced_configuration_no_longer_describes_the_server() -> None:
    async def scenario() -> None:
        mcp, principal = service(), owner()
        server = await ready_server(mcp, principal)
        await mcp.set_config(
            principal,
            server.id,
            http_config(
                endpoint="https://elsewhere.example.com/stream",
                egress_allowlist=["elsewhere.example.com"],
            ),
        )

        state = await mcp.binding_state(
            principal,
            server_id=server.id,
            config_revision=1,
            capability_revision=1,
            tool_name="search_orders",
        )

        assert state.config_current is False
        assert state.reviewed_on_current_config is False
        assert state.current_capability_hash is None

    asyncio.run(scenario())


def test_a_stale_capability_pointer_fails_closed_after_config_replacement() -> None:
    """A racing sync must not make an old endpoint's schema current again."""

    async def scenario() -> None:
        repository = InMemoryMcpRegistryRepository()
        mcp = McpRegistryService(
            repository, credentials=InMemoryCredentialResolver()
        )
        principal = owner()
        server = await ready_server(mcp, principal)
        await mcp.set_config(
            principal,
            server.id,
            http_config(
                endpoint="https://elsewhere.example.com/stream",
                egress_allowlist=["elsewhere.example.com"],
            ),
        )

        # Simulate the losing half of a set-config/save-snapshot race: mutable
        # state points back at snapshot 1 even though that snapshot belongs to
        # configuration 1 and the server is now on configuration 2.
        state = await mcp.state(principal, server.id)
        await repository.put_state(
            state.model_copy(
                update={
                    "verification": VerificationState.VERIFIED,
                    "verified_config_revision": 2,
                    "enablement": EnablementState.ENABLED,
                    "health": HealthState.HEALTHY,
                    "capability_revision": 1,
                }
            )
        )

        binding = await mcp.binding_state(
            principal,
            server_id=server.id,
            config_revision=2,
            capability_revision=1,
            tool_name="search_orders",
        )
        assert binding.server_enabled is False
        assert binding.current_capability_hash is None
        assert await mcp.current_tool_hashes(
            owner(platform_admin=True), server_id=server.id
        ) == {}

    asyncio.run(scenario())


def test_current_tool_hashes_follow_the_newest_snapshot() -> None:
    async def scenario() -> None:
        mcp, principal = service(), owner()
        platform = owner(platform_admin=True)
        server = await mcp.register(principal, server_request())
        await mcp.set_config(principal, server.id, http_config())

        assert await mcp.current_tool_hashes(platform, server_id=server.id) == {}

        await mcp.save_snapshot(principal, server.id, [capability()])
        first = await mcp.current_tool_hashes(platform, server_id=server.id)

        await mcp.save_snapshot(principal, server.id, [capability(name="other_tool")])
        second = await mcp.current_tool_hashes(platform, server_id=server.id)

        assert set(first) == {"search_orders"}
        assert set(second) == {"other_tool"}

    asyncio.run(scenario())


def test_current_tool_hashes_is_an_internal_platform_port() -> None:
    async def scenario() -> None:
        mcp, principal = service(), owner()
        server = await ready_server(mcp, principal)

        with pytest.raises(AuthorizationError):
            await mcp.current_tool_hashes(principal, server_id=server.id)
        with pytest.raises(AuthorizationError):
            await mcp.current_tool_hashes(outsider(), server_id=server.id)

    asyncio.run(scenario())


def test_a_disabled_server_reports_itself_unusable() -> None:
    async def scenario() -> None:
        mcp, principal = service(), owner()
        server = await ready_server(mcp, principal)
        await mcp.disable(principal, server.id)

        state = await mcp.binding_state(
            principal,
            server_id=server.id,
            config_revision=1,
            capability_revision=1,
            tool_name="search_orders",
        )
        assert state.server_enabled is False

    asyncio.run(scenario())


def test_binding_state_does_not_reveal_servers_the_caller_cannot_see() -> None:
    """A distinct answer here would confirm that a hidden server exists."""

    async def scenario() -> None:
        mcp = service()
        creator = owner()
        server = await ready_server(mcp, creator)

        hidden = await mcp.binding_state(
            outsider(),
            server_id=server.id,
            config_revision=1,
            capability_revision=1,
            tool_name="search_orders",
        )
        absent = await mcp.binding_state(
            outsider(),
            server_id=uuid4(),
            config_revision=1,
            capability_revision=1,
            tool_name="search_orders",
        )
        assert hidden == absent

    asyncio.run(scenario())


# --------------------------------------------------------------- ACL / auth


def test_a_department_scoped_server_is_invisible_outside_its_department() -> None:
    async def scenario() -> None:
        mcp, creator = service(), owner()
        server = await mcp.register(creator, server_request())

        with pytest.raises(AuthorizationError):
            await mcp.get(outsider(), server.id)
        assert (await mcp.list(outsider()))[1] == 0
        assert (await mcp.list(creator))[1] == 1

    asyncio.run(scenario())


def test_tenant_reach_does_not_confer_the_right_to_reconfigure() -> None:
    async def scenario() -> None:
        mcp, creator = service(), owner()
        stranger = outsider()
        server = await mcp.register(
            creator, server_request(acl=publishable_acl(data_scope=DataScope.TENANT))
        )

        assert await mcp.get(stranger, server.id)
        with pytest.raises(AuthorizationError):
            await mcp.set_config(stranger, server.id, http_config())

    asyncio.run(scenario())


def test_enabling_requires_the_publish_action() -> None:
    async def scenario() -> None:
        mcp, creator = service(), owner()
        server = await mcp.register(creator, server_request(acl=acl()))
        await mcp.set_config(creator, server.id, http_config())
        await mcp.record_health(creator, server.id, HealthReport(healthy=True, checked_at=NOW))
        await mcp.save_snapshot(creator, server.id, [capability()])

        with pytest.raises(AuthorizationError):
            await mcp.enable(creator, server.id)

    asyncio.run(scenario())


def test_a_server_cannot_be_filed_under_someone_elses_department() -> None:
    async def scenario() -> None:
        with pytest.raises(AuthorizationError):
            await service().register(outsider(), server_request())

    asyncio.run(scenario())


def test_servers_are_isolated_per_tenant() -> None:
    async def scenario() -> None:
        mcp, creator = service(), owner()
        server = await mcp.register(creator, server_request())

        with pytest.raises(McpServerNotFoundError):
            await mcp.get(owner(tenant_id=uuid4()), server.id)

    asyncio.run(scenario())


# --------------------------------------------------------------- credentials


def test_a_credential_binding_is_checked_against_the_provider() -> None:
    async def scenario() -> None:
        good = uuid4()
        wrong_purpose = uuid4()
        store = InMemoryCredentialResolver(
            {
                good: CredentialBinding(tenant_id=TENANT, purpose=CredentialPurpose.MCP_SERVER),
                wrong_purpose: CredentialBinding(
                    tenant_id=TENANT, purpose=CredentialPurpose.HTTP_TOOL
                ),
            }
        )
        mcp, principal = service(store), owner()
        server = await mcp.register(principal, server_request())

        assert await mcp.set_config(
            principal, server.id, http_config(credential_binding_id=good)
        )

        for binding in (wrong_purpose, uuid4()):
            with pytest.raises(CredentialBindingError):
                await mcp.set_config(
                    principal, server.id, http_config(credential_binding_id=binding)
                )

    asyncio.run(scenario())


def test_a_credential_value_cannot_be_expressed_at_all() -> None:
    for pasted in ("sk-live-abc123", "vault://kv/mcp#token", "hunter2"):
        with pytest.raises(ValidationError):
            http_config(credential_binding_id=pasted)
