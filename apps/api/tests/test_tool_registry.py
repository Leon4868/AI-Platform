"""Tool Registry: draft/version separation, availability, ACL, SSRF and drift."""

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.core.errors import AuthorizationError
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
from app.tools.errors import (
    AvailabilityTransitionError,
    ConcurrentPublishError,
    DraftImmutableError,
    DraftTransitionError,
    McpBindingInvalidError,
    ToolCallBlockedError,
    ToolNotBindableError,
    ToolNotFoundError,
)
from app.tools.repository import InMemoryToolRepository
from app.tools.schemas import (
    ApprovalPolicy,
    BuiltinSource,
    HttpSource,
    McpBindingState,
    McpSource,
    RiskLevel,
    ToolContract,
    ToolEffect,
    ToolRequest,
    VersionAvailability,
    compute_schema_hash,
)
from app.tools.service import ToolService

TENANT = UUID("00000000-0000-4000-8000-000000000010")
DESIGN = "dept-design"
SERVER = uuid4()
CAPABILITY_HASH = "reviewed-hash"


def healthy_binding(**overrides) -> McpBindingState:
    payload: dict[str, Any] = {
        "server_enabled": True,
        "config_current": True,
        "current_capability_hash": CAPABILITY_HASH,
        "reviewed_on_current_config": True,
    }
    return McpBindingState(**{**payload, **overrides})


class FakeMcp:
    """Stands in for the MCP registry, with the answer under test's control."""

    def __init__(self, state: McpBindingState | None = None) -> None:
        self.state = state or healthy_binding()
        self.hashes: dict[str, str] = {"search_orders": CAPABILITY_HASH}

    async def binding_state(self, principal, **kwargs) -> McpBindingState:
        return self.state

    async def current_tool_hashes(self, principal, *, server_id) -> dict[str, str]:
        return dict(self.hashes)


def resolver(*, purpose: CredentialPurpose = CredentialPurpose.HTTP_TOOL) -> tuple:
    binding = uuid4()
    store = InMemoryCredentialResolver(
        {binding: CredentialBinding(tenant_id=TENANT, purpose=purpose)}
    )
    return store, binding


def service(*, mcp: FakeMcp | None = None, credentials=None) -> ToolService:
    return ToolService(
        InMemoryToolRepository(),
        credentials=credentials or InMemoryCredentialResolver(),
        mcp=mcp or FakeMcp(),
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
    """A bare ACL: the owning department owns it and nothing more."""
    payload: dict[str, Any] = {"owner_department_id": DESIGN}
    return ResourceAcl(**{**payload, **overrides})


def publishable_acl(**overrides) -> ResourceAcl:
    """Owning plus an explicit delegation of review and release authority."""
    grants = [
        AclGrant(
            subject_type=SubjectType.DEPARTMENT,
            subject_id=DESIGN,
            actions=frozenset({ResourceAction.APPROVE, ResourceAction.PUBLISH}),
        )
    ]
    return acl(**{"grants": grants, **overrides})


def tool_request(**overrides) -> ToolRequest:
    payload: dict[str, Any] = {"name": "订单查询", "acl": publishable_acl()}
    return ToolRequest(**{**payload, **overrides})


def schema(field: str = "orderId") -> dict[str, Any]:
    return {"type": "object", "properties": {field: {"type": "string"}}, "required": [field]}


def contract(**overrides) -> ToolContract:
    payload: dict[str, Any] = {
        "source": BuiltinSource(implementation="orders.lookup"),
        "description": "查询订单",
        "input_schema": schema(),
        "output_schema": {"type": "object"},
        "effect": ToolEffect.READ,
        "risk": RiskLevel.LOW,
        "approval_policy": ApprovalPolicy.NEVER,
        "timeout_seconds": 30,
        "egress_allowlist": [],
    }
    return ToolContract(**{**payload, **overrides})


def mcp_contract(**overrides) -> ToolContract:
    payload: dict[str, Any] = {
        "source": McpSource(
            server_id=SERVER,
            config_revision=1,
            capability_revision=1,
            capability_hash=CAPABILITY_HASH,
            tool_name="search_orders",
        ),
        "egress_allowlist": ["mcp.example.com"],
    }
    return contract(**{**payload, **overrides})


async def publish(tools: ToolService, principal: Principal, body: ToolContract | None = None):
    tool = await tools.register(principal, tool_request())
    draft = await tools.create_draft(principal, tool.id, body or contract())
    await tools.verify_draft(principal, draft.id)
    version = await tools.publish(principal, draft.id)
    return tool, version


# ------------------------------------------------------------ draft/version


def test_publishing_creates_a_version_and_spends_the_draft() -> None:
    async def scenario() -> None:
        tools, principal = service(), owner()
        tool = await tools.register(principal, tool_request())
        draft = await tools.create_draft(principal, tool.id, contract())
        await tools.verify_draft(principal, draft.id)

        version = await tools.publish(principal, draft.id)
        spent = await tools.get_draft(principal, draft.id)

        assert version.version == 1
        assert version.source_draft_id == draft.id
        assert spent.status.value == "published"
        assert spent.published_version == 1

        # A spent draft is history, not a working copy.
        with pytest.raises(DraftImmutableError):
            await tools.update_draft(principal, draft.id, contract())
        with pytest.raises(DraftTransitionError):
            await tools.publish(principal, draft.id)

    asyncio.run(scenario())


def test_a_published_version_is_never_rewritten_by_a_later_publish() -> None:
    async def scenario() -> None:
        tools, principal = service(), owner()
        tool, first = await publish(tools, principal)

        second_draft = await tools.create_draft(
            principal, tool.id, contract(input_schema=schema("customerId"))
        )
        await tools.verify_draft(principal, second_draft.id)
        second = await tools.publish(principal, second_draft.id)

        assert (first.version, second.version) == (1, 2)
        replayed = await tools.get_version(principal, tool.id, 1)
        assert replayed.contract.input_schema == schema()
        assert replayed.schema_hash == first.schema_hash

    asyncio.run(scenario())


def test_publishing_without_review_is_refused() -> None:
    async def scenario() -> None:
        tools, principal = service(), owner()
        tool = await tools.register(principal, tool_request())
        draft = await tools.create_draft(principal, tool.id, contract())

        with pytest.raises(DraftTransitionError):
            await tools.publish(principal, draft.id)

    asyncio.run(scenario())


def test_editing_a_verified_draft_withdraws_its_review() -> None:
    async def scenario() -> None:
        tools, principal = service(), owner()
        tool = await tools.register(principal, tool_request())
        draft = await tools.create_draft(principal, tool.id, contract())
        await tools.verify_draft(principal, draft.id)

        revised = await tools.update_draft(
            principal, draft.id, contract(input_schema=schema("customerId"))
        )
        assert revised.status.value == "draft"

        with pytest.raises(DraftTransitionError):
            await tools.publish(principal, draft.id)

    asyncio.run(scenario())


def test_concurrent_publishes_never_share_a_version_number() -> None:
    """Allocating the number and writing the version is one step, not two."""

    async def scenario() -> None:
        tools, principal = service(), owner()
        tool = await tools.register(principal, tool_request())
        drafts = []
        for _ in range(10):
            draft = await tools.create_draft(principal, tool.id, contract())
            await tools.verify_draft(principal, draft.id)
            drafts.append(draft)

        versions = await asyncio.gather(
            *(tools.publish(principal, draft.id) for draft in drafts)
        )

        assert sorted(item.version for item in versions) == list(range(1, 11))
        assert len(await tools.list_versions(principal, tool.id)) == 10

    asyncio.run(scenario())


# -------------------------------------------------------------- availability


def test_a_fresh_version_is_available_and_bindable() -> None:
    async def scenario() -> None:
        tools, principal = service(), owner()
        tool, version = await publish(tools, principal)

        record = await tools.availability(principal, tool.id, version.version)
        assert record.state is VersionAvailability.AVAILABLE
        assert await tools.admit_binding(principal, tool.id, version.version)
        assert await tools.authorize_call(principal, tool.id, version.version)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "state",
    [
        VersionAvailability.DISABLED,
        VersionAvailability.RUNTIME_BLOCKED,
        VersionAvailability.REVOKED,
    ],
)
def test_anything_but_available_refuses_the_very_next_call(state) -> None:
    """Withdrawal takes effect now, not when the current runs happen to finish.

    Letting a withdrawn tool keep serving in-flight runs sounds humane until the
    reason for withdrawing it is that its calls are doing damage — and that is
    the only reason anyone withdraws one in a hurry.
    """

    async def scenario() -> None:
        tools, principal = service(), owner(platform_admin=True)
        tool, version = await publish(tools, principal)
        await tools.set_availability(principal, tool.id, version.version, state)

        with pytest.raises(ToolNotBindableError):
            await tools.admit_binding(principal, tool.id, version.version)
        with pytest.raises(ToolCallBlockedError):
            await tools.authorize_call(principal, tool.id, version.version)

    asyncio.run(scenario())


def test_a_runtime_block_cannot_be_lifted_in_one_step() -> None:
    async def scenario() -> None:
        tools, principal = service(), owner(platform_admin=True)
        tool, version = await publish(tools, principal)
        await tools.set_availability(
            principal, tool.id, version.version, VersionAvailability.RUNTIME_BLOCKED
        )

        with pytest.raises(AvailabilityTransitionError):
            await tools.set_availability(
                principal, tool.id, version.version, VersionAvailability.AVAILABLE
            )

        await tools.set_availability(
            principal, tool.id, version.version, VersionAvailability.DISABLED
        )
        restored = await tools.set_availability(
            principal, tool.id, version.version, VersionAvailability.AVAILABLE
        )
        assert restored.state is VersionAvailability.AVAILABLE

    asyncio.run(scenario())


def test_revocation_is_terminal() -> None:
    async def scenario() -> None:
        tools, principal = service(), owner(platform_admin=True)
        tool, version = await publish(tools, principal)
        await tools.set_availability(
            principal, tool.id, version.version, VersionAvailability.REVOKED
        )

        with pytest.raises(AvailabilityTransitionError):
            await tools.set_availability(
                principal, tool.id, version.version, VersionAvailability.AVAILABLE
            )

    asyncio.run(scenario())


def test_revoking_requires_admin_while_disabling_needs_publish() -> None:
    async def scenario() -> None:
        tools = service()
        publisher = owner(
            department_ids=frozenset({DESIGN}),
        )
        tool = await tools.register(publisher, tool_request(acl=publishable_acl()))
        draft = await tools.create_draft(publisher, tool.id, contract())
        await tools.verify_draft(publisher, draft.id)
        version = await tools.publish(publisher, draft.id)

        assert await tools.set_availability(
            publisher, tool.id, version.version, VersionAvailability.DISABLED
        )
        with pytest.raises(AuthorizationError):
            await tools.set_availability(
                publisher, tool.id, version.version, VersionAvailability.REVOKED
            )

    asyncio.run(scenario())


# ---------------------------------------------------------------------- ACL


def test_being_in_the_tenant_is_not_permission_to_edit() -> None:
    async def scenario() -> None:
        tools = service()
        creator = owner()
        stranger = outsider()
        tool = await tools.register(creator, tool_request(acl=acl(data_scope=DataScope.TENANT)))

        # Tenant scope grants reach, not authority.
        assert await tools.get(stranger, tool.id)
        with pytest.raises(AuthorizationError):
            await tools.create_draft(stranger, tool.id, contract())

    asyncio.run(scenario())


def test_a_department_scoped_tool_is_invisible_outside_its_department() -> None:
    async def scenario() -> None:
        tools = service()
        creator = owner()
        stranger = outsider()
        tool = await tools.register(creator, tool_request())

        with pytest.raises(AuthorizationError):
            await tools.get(stranger, tool.id)
        assert (await tools.list(stranger))[1] == 0
        assert (await tools.list(creator))[1] == 1

    asyncio.run(scenario())


def test_clearance_gates_a_restricted_resource() -> None:
    async def scenario() -> None:
        tools = service()
        creator = owner(security_clearance=SecurityLevel.RESTRICTED)
        tool = await tools.register(
            creator,
            tool_request(acl=acl(security_level=SecurityLevel.RESTRICTED, data_scope=DataScope.TENANT)),
        )

        cleared = outsider(security_clearance=SecurityLevel.RESTRICTED)
        uncleared = outsider(security_clearance=SecurityLevel.INTERNAL)

        assert await tools.get(cleared, tool.id)
        with pytest.raises(AuthorizationError):
            await tools.get(uncleared, tool.id)

    asyncio.run(scenario())


def test_owning_a_tool_does_not_confer_the_right_to_publish_it() -> None:
    async def scenario() -> None:
        tools, creator = service(), owner()
        tool = await tools.register(creator, tool_request(acl=acl()))
        draft = await tools.create_draft(creator, tool.id, contract())

        with pytest.raises(AuthorizationError):
            await tools.verify_draft(creator, draft.id)

    asyncio.run(scenario())


def test_an_explicit_grant_reaches_a_project_member() -> None:
    async def scenario() -> None:
        tools, creator = service(), owner()
        collaborator = outsider(project_ids=frozenset({"proj-alpha"}))
        tool = await tools.register(
            creator,
            tool_request(
                acl=acl(
                    data_scope=DataScope.PROJECT,
                    project_ids=frozenset({"proj-alpha"}),
                    grants=[
                        AclGrant(
                            subject_type=SubjectType.USER,
                            subject_id=str(collaborator.actor_id),
                            actions=frozenset({ResourceAction.EDIT}),
                        )
                    ],
                )
            ),
        )

        assert await tools.get(collaborator, tool.id)
        assert await tools.create_draft(collaborator, tool.id, contract())

    asyncio.run(scenario())


def test_a_tool_cannot_be_filed_under_someone_elses_department() -> None:
    async def scenario() -> None:
        tools = service()
        with pytest.raises(AuthorizationError):
            await tools.register(outsider(), tool_request())

    asyncio.run(scenario())


def test_tools_are_isolated_per_tenant() -> None:
    async def scenario() -> None:
        tools, creator = service(), owner()
        tool = await tools.register(creator, tool_request())
        intruder = owner(tenant_id=uuid4())

        with pytest.raises(ToolNotFoundError):
            await tools.get(intruder, tool.id)

    asyncio.run(scenario())


# ------------------------------------------------------------------- SSRF


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.example.com",
        "https://127.0.0.1/v1",
        "https://169.254.169.254/latest",
        "https://localhost/v1",
        "https://payments.internal/v1",
        "https://metadata.google.internal/v1",
        "https://user:secret@api.example.com/v1",
        "https://api.example.com/v1#token=abc",
    ],
)
def test_an_unacceptable_http_target_is_refused(base_url: str) -> None:
    with pytest.raises(ValidationError):
        HttpSource(base_url=base_url, operation="getOrder")


def test_redirects_are_not_followed() -> None:
    with pytest.raises(ValidationError):
        HttpSource(
            base_url="https://api.example.com", operation="getOrder", allow_redirects=True
        )


def test_an_http_host_must_be_inside_its_own_allowlist() -> None:
    source = HttpSource(base_url="https://api.example.com/v1", operation="getOrder")

    with pytest.raises(ValidationError):
        contract(source=source, egress_allowlist=["other.example.com"])
    with pytest.raises(ValidationError):
        # A wildcard covers sub-domains, not the apex it is written against.
        contract(source=source, egress_allowlist=["*.api.example.com"])

    assert contract(source=source, egress_allowlist=["*.example.com"])


# ------------------------------------------------------------- credentials


def test_a_credential_binding_is_checked_not_merely_well_formed() -> None:
    async def scenario() -> None:
        store, binding = resolver()
        tools = service(credentials=store)
        principal = owner()
        tool = await tools.register(principal, tool_request())
        source = HttpSource(
            base_url="https://api.example.com/v1",
            operation="getOrder",
            credential_binding_id=binding,
        )
        body = contract(source=source, egress_allowlist=["*.example.com"])

        assert await tools.create_draft(principal, tool.id, body)

        # A syntactically perfect UUID the provider has never heard of.
        unknown = contract(
            source=HttpSource(
                base_url="https://api.example.com/v1",
                operation="getOrder",
                credential_binding_id=uuid4(),
            ),
            egress_allowlist=["*.example.com"],
        )
        with pytest.raises(CredentialBindingError):
            await tools.create_draft(principal, tool.id, unknown)

    asyncio.run(scenario())


def test_a_binding_from_another_tenant_or_purpose_is_refused() -> None:
    async def scenario() -> None:
        foreign = uuid4()
        wrong_purpose = uuid4()
        revoked = uuid4()
        store = InMemoryCredentialResolver(
            {
                foreign: CredentialBinding(
                    tenant_id=uuid4(), purpose=CredentialPurpose.HTTP_TOOL
                ),
                wrong_purpose: CredentialBinding(
                    tenant_id=TENANT, purpose=CredentialPurpose.MCP_SERVER
                ),
                revoked: CredentialBinding(
                    tenant_id=TENANT, purpose=CredentialPurpose.HTTP_TOOL, active=False
                ),
            }
        )
        tools = service(credentials=store)
        principal = owner()
        tool = await tools.register(principal, tool_request())

        for binding in (foreign, wrong_purpose, revoked):
            body = contract(
                source=HttpSource(
                    base_url="https://api.example.com/v1",
                    operation="getOrder",
                    credential_binding_id=binding,
                ),
                egress_allowlist=["*.example.com"],
            )
            with pytest.raises(CredentialBindingError):
                await tools.create_draft(principal, tool.id, body)

    asyncio.run(scenario())


def test_a_credential_value_cannot_be_expressed_at_all() -> None:
    for pasted in ("sk-live-abc123", "vault://kv/orders#token", "hunter2"):
        with pytest.raises(ValidationError):
            HttpSource(
                base_url="https://api.example.com",
                operation="getOrder",
                credential_binding_id=pasted,
            )


# -------------------------------------------------------------- contract


@pytest.mark.parametrize("effect", [ToolEffect.WRITE, ToolEffect.DESTRUCTIVE])
def test_a_non_idempotent_write_cannot_be_retried(effect: ToolEffect) -> None:
    """A retry of a non-idempotent write is a second write.

    The caller cannot tell a lost response from an unperformed one, so the safe
    reading of "no answer" is that it happened.
    """
    with pytest.raises(ValidationError):
        contract(
            effect=effect,
            approval_policy=ApprovalPolicy.ON_WRITE,
            idempotent=False,
            max_retries=2,
        )

    assert contract(
        effect=effect,
        approval_policy=ApprovalPolicy.ON_WRITE,
        idempotent=True,
        max_retries=2,
    )
    assert contract(
        effect=effect,
        approval_policy=ApprovalPolicy.ON_WRITE,
        idempotent=False,
        max_retries=0,
    )


@pytest.mark.parametrize("effect", [ToolEffect.WRITE, ToolEffect.DESTRUCTIVE])
def test_a_writing_tool_cannot_waive_approval(effect: ToolEffect) -> None:
    with pytest.raises(ValidationError):
        contract(effect=effect, approval_policy=ApprovalPolicy.NEVER)


def test_effect_and_risk_are_independent_judgements() -> None:
    critical_read = contract(
        effect=ToolEffect.READ, risk=RiskLevel.CRITICAL, approval_policy=ApprovalPolicy.ALWAYS
    )
    low_write = contract(
        effect=ToolEffect.WRITE, risk=RiskLevel.LOW, approval_policy=ApprovalPolicy.ON_WRITE
    )

    assert (critical_read.effect, critical_read.risk) == (ToolEffect.READ, RiskLevel.CRITICAL)
    assert (low_write.effect, low_write.risk) == (ToolEffect.WRITE, RiskLevel.LOW)

    with pytest.raises(ValidationError):
        contract(risk=RiskLevel.CRITICAL, approval_policy=ApprovalPolicy.ON_WRITE)


def test_the_schema_hash_covers_the_prose_and_annotations() -> None:
    baseline = contract().schema_hash

    assert baseline == compute_schema_hash(
        name="orders.lookup",
        description="查询订单",
        input_schema=schema(),
        output_schema={"type": "object"},
        annotations={},
    )
    assert contract(description="查询并导出订单").schema_hash != baseline
    assert contract(annotations={"readOnlyHint": False}).schema_hash != baseline


def test_egress_matches_whether_the_tool_leaves_the_platform() -> None:
    with pytest.raises(ValidationError):
        contract(egress_allowlist=["api.example.com"])
    with pytest.raises(ValidationError):
        mcp_contract(egress_allowlist=[])


# ------------------------------------------------------------- MCP binding


def test_publishing_an_mcp_tool_validates_the_binding() -> None:
    async def scenario() -> None:
        mcp = FakeMcp(healthy_binding(server_enabled=False))
        tools, principal = service(mcp=mcp), owner()
        tool = await tools.register(principal, tool_request())
        draft = await tools.create_draft(principal, tool.id, mcp_contract())
        await tools.verify_draft(principal, draft.id)

        with pytest.raises(McpBindingInvalidError):
            await tools.publish(principal, draft.id)

        mcp.state = healthy_binding()
        assert await tools.publish(principal, draft.id)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "state",
    [
        healthy_binding(server_enabled=False),
        healthy_binding(config_current=False),
        healthy_binding(reviewed_on_current_config=False),
        healthy_binding(current_capability_hash=None),
        healthy_binding(current_capability_hash="moved-on"),
    ],
)
def test_a_broken_binding_blocks_the_call_and_the_version(state) -> None:
    async def scenario() -> None:
        mcp = FakeMcp()
        tools, principal = service(mcp=mcp), owner()
        tool, version = await publish(tools, principal, mcp_contract())

        mcp.state = state
        with pytest.raises(McpBindingInvalidError):
            await tools.authorize_call(principal, tool.id, version.version)

        # The next caller is refused without another round trip.
        record = await tools.availability(principal, tool.id, version.version)
        assert record.state is VersionAvailability.RUNTIME_BLOCKED
        assert record.reason
        with pytest.raises(ToolCallBlockedError):
            await tools.authorize_call(principal, tool.id, version.version)

    asyncio.run(scenario())


def system() -> Principal:
    return Principal(tenant_id=TENANT, actor_id=uuid4(), platform_admin=True)


def test_reconciling_blocks_every_drifted_version() -> None:
    async def scenario() -> None:
        mcp = FakeMcp()
        tools, principal = service(mcp=mcp), owner()
        tool, version = await publish(tools, principal, mcp_contract())

        assert await tools.reconcile_server(system(), SERVER) == []

        mcp.hashes = {"search_orders": "moved-on"}
        drifted = await tools.reconcile_server(system(), SERVER)

        assert len(drifted) == 1
        assert drifted[0].current_schema_hash == "moved-on"
        assert (
            await tools.availability(principal, tool.id, version.version)
        ).state is VersionAvailability.RUNTIME_BLOCKED

        # The published contract itself is untouched.
        assert (
            await tools.get_version(principal, tool.id, version.version)
        ).contract.source.capability_hash == CAPABILITY_HASH

    asyncio.run(scenario())


def test_reconciling_reads_hashes_from_the_registry_not_the_caller() -> None:
    """The counterexample: no caller-supplied hash can mark a tool as drifted.

    The registry still reports the reviewed hash, so nothing drifts no matter
    what anyone claims — there is no parameter left to lie through.
    """

    async def scenario() -> None:
        mcp = FakeMcp()
        tools, principal = service(mcp=mcp), owner()
        tool, version = await publish(tools, principal, mcp_contract())

        assert await tools.reconcile_server(system(), SERVER) == []
        assert (
            await tools.availability(principal, tool.id, version.version)
        ).state is VersionAvailability.AVAILABLE

    asyncio.run(scenario())


def test_an_ordinary_caller_cannot_reconcile_anyone_elses_tools() -> None:
    """The counterexample: reaching into another department to switch tools off."""

    async def scenario() -> None:
        mcp = FakeMcp()
        tools, creator = service(mcp=mcp), owner()
        tool, version = await publish(tools, creator, mcp_contract())
        mcp.hashes = {"search_orders": "moved-on"}

        for intruder in (outsider(), creator):
            with pytest.raises(AuthorizationError):
                await tools.reconcile_server(intruder, SERVER)

        assert (
            await tools.availability(creator, tool.id, version.version)
        ).state is VersionAvailability.AVAILABLE

    asyncio.run(scenario())


def test_an_intruder_cannot_set_availability_on_a_tool_they_cannot_see() -> None:
    async def scenario() -> None:
        tools, creator = service(), owner()
        tool, version = await publish(tools, creator)

        with pytest.raises((AuthorizationError, ToolNotFoundError)):
            await tools.set_availability(
                outsider(), tool.id, version.version, VersionAvailability.RUNTIME_BLOCKED
            )

        assert (
            await tools.availability(creator, tool.id, version.version)
        ).state is VersionAvailability.AVAILABLE

    asyncio.run(scenario())


def test_a_withdrawn_capability_counts_as_drift() -> None:
    async def scenario() -> None:
        mcp = FakeMcp()
        tools, principal = service(mcp=mcp), owner()
        await publish(tools, principal, mcp_contract())

        mcp.hashes = {}
        drifted = await tools.reconcile_server(system(), SERVER)

        assert len(drifted) == 1
        assert drifted[0].current_schema_hash is None

    asyncio.run(scenario())


def test_reconciling_ignores_other_servers_and_builtins() -> None:
    async def scenario() -> None:
        mcp = FakeMcp()
        tools, principal = service(mcp=mcp), owner()
        await publish(tools, principal)
        elsewhere = mcp_contract(
            source=McpSource(
                server_id=uuid4(),
                config_revision=1,
                capability_revision=1,
                capability_hash=CAPABILITY_HASH,
                tool_name="search_orders",
            )
        )
        await publish(tools, principal, elsewhere)

        mcp.hashes = {}
        assert await tools.reconcile_server(system(), SERVER) == []

    asyncio.run(scenario())


def test_only_one_publish_of_a_draft_can_win() -> None:
    """The counterexample: the same draft racing itself into v1 and v2."""

    async def scenario() -> None:
        tools, principal = service(), owner()
        tool = await tools.register(principal, tool_request())
        draft = await tools.create_draft(principal, tool.id, contract())
        await tools.verify_draft(principal, draft.id)

        outcomes = await asyncio.gather(
            *(tools.publish(principal, draft.id) for _ in range(8)),
            return_exceptions=True,
        )

        published = [item for item in outcomes if not isinstance(item, BaseException)]
        assert len(published) == 1
        assert published[0].version == 1
        assert all(
            isinstance(item, (ConcurrentPublishError, DraftTransitionError))
            for item in outcomes
            if isinstance(item, BaseException)
        )
        assert len(await tools.list_versions(principal, tool.id)) == 1

    asyncio.run(scenario())


def test_a_draft_edited_after_review_cannot_be_published_by_a_stale_request() -> None:
    async def scenario() -> None:
        tools, principal = service(), owner()
        tool = await tools.register(principal, tool_request())
        draft = await tools.create_draft(principal, tool.id, contract())
        await tools.verify_draft(principal, draft.id)
        await tools.update_draft(principal, draft.id, contract(description="改了"))

        with pytest.raises(DraftTransitionError):
            await tools.publish(principal, draft.id)
        assert await tools.list_versions(principal, tool.id) == []

    asyncio.run(scenario())
