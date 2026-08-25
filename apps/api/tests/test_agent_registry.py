"""Agent aggregate, publication, ACL, and run-gate domain tests."""

import asyncio
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.agents.errors import AgentConflictError, AgentNotFoundError, AgentResourceValidationError
from app.agents.repository import InMemoryAgentRepository
from app.agents.schemas import (
    AgentAccessPolicy,
    AgentAction,
    AgentCreate,
    AgentDraftUpdate,
    AgentLimits,
    AgentPolicy,
    AgentResourceBindings,
    ExactKnowledgeReference,
    ExactResourceReference,
    KnowledgeReference,
    ResourceKind,
    ResourcePublicationStatus,
    ResourceReference,
    ResourceRisk,
    VersionAvailabilityStatus,
    WorkflowDraftUpdate,
    WriteActionApproval,
    effective_child_permissions,
)
from app.agents.service import (
    AgentService,
    InMemoryAgentAuditSink,
    ResolvedKnowledgeResource,
    ResolvedResource,
)
from app.core.errors import AuthorizationError
from app.identity.schemas import Principal


def principal(
    *, tenant_id: UUID | None = None, user_id: UUID | None = None, departments: set[str] | None = None
) -> Principal:
    return Principal(
        user_id=user_id or uuid4(),
        tenant_id=tenant_id or uuid4(),
        display_name="Agent tester",
        department_ids=frozenset(departments or {"dept-ai"}),
    )


def reference(kind: ResourceKind, version: int | str | None = 1) -> ResourceReference:
    return ResourceReference(kind=kind, resource_id=uuid4(), version=version)


def key(reference: ExactResourceReference | ExactKnowledgeReference) -> tuple:
    if isinstance(reference, ExactKnowledgeReference):
        return (
            ResourceKind.KNOWLEDGE,
            reference.knowledge_base_id,
            reference.index_revision,
            reference.policy_version,
        )
    return (reference.kind, reference.resource_id, reference.version)


class Resolver:
    def __init__(self) -> None:
        self.results: dict[tuple, ResolvedResource | ResolvedKnowledgeResource | None] = {}

    def register(
        self,
        actor: Principal,
        item: ResourceReference | KnowledgeReference,
        *,
        publication: ResourcePublicationStatus = ResourcePublicationStatus.PUBLISHED,
        availability: VersionAvailabilityStatus = VersionAvailabilityStatus.AVAILABLE,
        authorized: bool = True,
        tenant_id: UUID | None = None,
        risk: ResourceRisk = ResourceRisk.READ,
        external: bool = False,
    ) -> None:
        if isinstance(item, KnowledgeReference):
            assert item.is_exact
            exact = ExactKnowledgeReference(
                knowledge_base_id=item.knowledge_base_id,
                index_revision=item.index_revision,
                policy_version=item.policy_version,
            )
            self.results[key(exact)] = ResolvedKnowledgeResource(
                tenant_id=tenant_id or actor.tenant_id,
                knowledge_base_id=exact.knowledge_base_id,
                index_revision=exact.index_revision,
                policy_version=exact.policy_version,
                publication_status=publication,
                availability=availability,
                authorized=authorized,
            )
            return
        assert item.is_exact
        exact = ExactResourceReference(
            kind=item.kind, resource_id=item.resource_id, version=item.version
        )
        self.results[key(exact)] = ResolvedResource(
            tenant_id=tenant_id or actor.tenant_id,
            kind=exact.kind,
            resource_id=exact.resource_id,
            version=exact.version,
            publication_status=publication,
            availability=availability,
            authorized=authorized,
            risk=risk,
            requires_external_network=external,
        )

    async def resolve(
        self, actor: Principal, item: ExactResourceReference | ExactKnowledgeReference
    ) -> ResolvedResource | ResolvedKnowledgeResource | None:
        del actor
        return self.results.get(key(item))


def harness() -> tuple[AgentService, InMemoryAgentRepository, Resolver, InMemoryAgentAuditSink]:
    repository = InMemoryAgentRepository()
    resolver = Resolver()
    audit = InMemoryAgentAuditSink()
    return AgentService(repository, resolver, audit), repository, resolver, audit


async def create_configured(
    service: AgentService,
    resolver: Resolver,
    actor: Principal,
    *,
    tool_risk: ResourceRisk = ResourceRisk.READ,
    external: bool = False,
    approval: WriteActionApproval = WriteActionApproval.ON_WRITE,
    allow_external: bool = False,
    max_tool_calls: int = 20,
    required_permissions: frozenset[str] = frozenset({"knowledge.read", "tool.call"}),
):
    agent = await service.create_draft(
        actor,
        AgentCreate(name="Policy assistant", owner_department_id="dept-ai"),
    )
    prompt = reference(ResourceKind.PROMPT)
    model = reference(ResourceKind.MODEL)
    skill = reference(ResourceKind.SKILL)
    tool = reference(ResourceKind.TOOL)
    knowledge = KnowledgeReference(
        knowledge_base_id=uuid4(), index_revision=7, policy_version=3
    )
    for item in (prompt, model, skill, knowledge):
        resolver.register(actor, item)
    resolver.register(actor, tool, risk=tool_risk, external=external)
    agent = await service.update_draft(
        actor,
        agent.id,
        AgentDraftUpdate(
            aggregate_revision=agent.aggregate_revision,
            resources=AgentResourceBindings(
                prompt=prompt,
                model=model,
                knowledge=(knowledge,),
                skills=(skill,),
                tools=(tool,),
            ),
            limits=AgentLimits(max_tool_calls=max_tool_calls, max_cost=Decimal("5.25")),
            policy=AgentPolicy(
                allow_external_network=allow_external,
                write_action_approval=approval,
            ),
            change_summary="initial publish",
            required_runtime_permissions=required_permissions,
        ),
    )
    agent = await service.update_owned_workflow(
        actor,
        agent.id,
        WorkflowDraftUpdate(
            aggregate_revision=agent.aggregate_revision,
            definition={"nodes": [{"id": "input"}, {"id": "output"}]},
        ),
    )
    return agent, tool


def test_create_atomically_owns_one_private_workflow() -> None:
    async def scenario() -> None:
        service, repository, _, audit = harness()
        actor = principal()
        agent = await service.create_draft(
            actor, AgentCreate(name="Draft", owner_department_id="dept-ai")
        )

        workflow = await repository.get_workflow_draft(
            actor.tenant_id, agent.owned_workflow_draft_id
        )
        assert workflow is not None
        assert workflow.agent_id == agent.id
        assert workflow.visibility == "private"
        assert agent.aggregate_revision == 1
        assert agent.has_unpublished_changes is True
        assert agent.published_version is None
        events = await audit.list(actor.tenant_id, agent_id=agent.id)
        assert events[0].metadata["owned_workflow_draft_id"] == str(workflow.id)

    asyncio.run(scenario())


def test_acl_pagination_scans_beyond_first_repository_batch() -> None:
    async def scenario() -> None:
        service, _, _, _ = harness()
        actor = principal()
        for index in range(101):
            await service.create_draft(
                actor,
                AgentCreate(name=f"Agent {index}", owner_department_id="dept-ai"),
            )

        tail, total = await service.list(actor, limit=10, offset=100)
        assert total == 101
        assert len(tail) == 1

    asyncio.run(scenario())


def test_publish_atomically_locks_agent_workflow_and_all_resources() -> None:
    async def scenario() -> None:
        service, repository, resolver, audit = harness()
        actor = principal()
        agent, _ = await create_configured(service, resolver, actor)

        version = await service.publish(actor, agent.id)
        current = await service.get(actor, agent.id)
        workflow = await repository.get_workflow_version(
            actor.tenant_id, agent.owned_workflow_draft_id, version.version
        )

        assert version.version == 1
        assert version.workflow.resource_id == agent.owned_workflow_draft_id
        assert version.workflow.version == 1
        assert version.knowledge[0].index_revision == 7
        assert version.knowledge[0].policy_version == 3
        assert workflow is not None and workflow.agent_id == agent.id
        assert workflow.source_aggregate_revision == agent.aggregate_revision
        assert current.published_version == 1
        assert current.has_unpublished_changes is False
        assert current.aggregate_revision == agent.aggregate_revision + 1
        assert (await audit.list(actor.tenant_id, agent_id=agent.id))[-1].action == (
            "agent.version_published"
        )

        with pytest.raises(ValidationError):
            version.name = "mutated"

    asyncio.run(scenario())


def test_published_content_is_copy_isolated_and_a_second_publish_needs_changes() -> None:
    async def scenario() -> None:
        service, repository, resolver, _ = harness()
        actor = principal()
        agent, _ = await create_configured(service, resolver, actor)
        first = await service.publish(actor, agent.id)

        with pytest.raises(AgentConflictError):
            await service.publish(actor, agent.id)

        workflow = await repository.get_workflow_version(
            actor.tenant_id, agent.owned_workflow_draft_id, 1
        )
        assert workflow is not None
        workflow.definition["nodes"] = []
        stored_again = await repository.get_workflow_version(
            actor.tenant_id, agent.owned_workflow_draft_id, 1
        )
        assert stored_again is not None and stored_again.definition["nodes"]

        current = await service.get(actor, agent.id)
        changed = await service.update_owned_workflow(
            actor,
            agent.id,
            WorkflowDraftUpdate(
                aggregate_revision=current.aggregate_revision,
                definition={"nodes": [{"id": "new"}]},
            ),
        )
        second = await service.publish(actor, changed.id)
        assert second.version == 2
        assert first.version == 1
        assert (await service.list_versions(actor, agent.id))[0].version == 2

    asyncio.run(scenario())


@pytest.mark.parametrize("selector", ["latest", "^3", None])
def test_publish_rejects_non_exact_resource_and_knowledge_versions(selector) -> None:
    async def scenario() -> None:
        service, _, resolver, _ = harness()
        actor = principal()
        agent = await service.create_draft(
            actor, AgentCreate(name="Bad selector", owner_department_id="dept-ai")
        )
        agent = await service.update_draft(
            actor,
            agent.id,
            AgentDraftUpdate(
                aggregate_revision=agent.aggregate_revision,
                resources=AgentResourceBindings(
                    prompt=reference(ResourceKind.PROMPT, selector),
                    model=reference(ResourceKind.MODEL),
                    knowledge=(
                        KnowledgeReference(
                            knowledge_base_id=uuid4(),
                            index_revision=selector,
                            policy_version=1,
                        ),
                    ),
                ),
            ),
        )
        resolver.register(actor, agent.draft.resources.model)

        with pytest.raises(AgentResourceValidationError) as invalid:
            await service.publish(actor, agent.id)
        assert {error["code"] for error in invalid.value.errors} == {
            "resource_version_not_exact"
        }

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("missing", "resource_not_found"),
        ("draft", "resource_not_published"),
        ("unauthorized", "resource_not_authorized"),
        ("other_tenant", "resource_not_authorized"),
    ],
)
def test_publish_rejects_unusable_resources(mode: str, expected: str) -> None:
    async def scenario() -> None:
        service, _, resolver, _ = harness()
        actor = principal()
        agent = await service.create_draft(
            actor, AgentCreate(name="Resource gate", owner_department_id="dept-ai")
        )
        prompt = reference(ResourceKind.PROMPT)
        model = reference(ResourceKind.MODEL)
        resolver.register(actor, model)
        if mode == "draft":
            resolver.register(actor, prompt, publication=ResourcePublicationStatus.DRAFT)
        elif mode == "unauthorized":
            resolver.register(actor, prompt, authorized=False)
        elif mode == "other_tenant":
            resolver.register(actor, prompt, tenant_id=uuid4())
        agent = await service.update_draft(
            actor,
            agent.id,
            AgentDraftUpdate(
                aggregate_revision=agent.aggregate_revision,
                resources=AgentResourceBindings(prompt=prompt, model=model),
            ),
        )

        with pytest.raises(AgentResourceValidationError) as invalid:
            await service.publish(actor, agent.id)
        assert expected in {item["code"] for item in invalid.value.errors}

    asyncio.run(scenario())


def test_tenant_isolation_and_use_edit_publish_admin_acl_are_distinct() -> None:
    async def scenario() -> None:
        service, _, _, _ = harness()
        tenant = uuid4()
        owner = principal(tenant_id=tenant)
        editor = principal(tenant_id=tenant, departments={"other"})
        outsider = principal(tenant_id=tenant, departments={"other"})
        access = AgentAccessPolicy(
            user_grants={
                editor.user_id: frozenset({AgentAction.USE, AgentAction.EDIT}),
            },
            data_scopes=frozenset({"project:alpha"}),
        )
        agent = await service.create_draft(
            owner,
            AgentCreate(name="ACL", owner_department_id="dept-ai", access=access),
        )

        assert (await service.get(editor, agent.id)).id == agent.id
        with pytest.raises(AuthorizationError):
            await service.get(outsider, agent.id)
        with pytest.raises(AuthorizationError):
            await service.publish(editor, agent.id)
        with pytest.raises(AuthorizationError):
            await service.set_version_availability(
                editor, agent.id, 1, VersionAvailabilityStatus.DISABLED
            )
        with pytest.raises(AgentNotFoundError):
            await service.get(principal(), agent.id)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_steps": 0},
        {"max_tool_calls": -1},
        {"max_run_seconds": 0},
        {"max_cost": Decimal("0")},
    ],
)
def test_execution_limits_are_positive_and_bounded(kwargs: dict) -> None:
    with pytest.raises(ValidationError):
        AgentLimits(**kwargs)


@pytest.mark.parametrize(
    ("risk", "external", "approval", "allow_external", "max_calls", "code"),
    [
        (ResourceRisk.WRITE, False, WriteActionApproval.NEVER, False, 20, "write_action_approval_required"),
        (ResourceRisk.READ, True, WriteActionApproval.ON_WRITE, False, 20, "external_network_not_allowed"),
        (ResourceRisk.READ, False, WriteActionApproval.ON_WRITE, False, 0, "tool_calls_disabled_but_tools_bound"),
    ],
)
def test_publish_enforces_tool_network_approval_and_call_limits(
    risk, external, approval, allow_external, max_calls, code
) -> None:
    async def scenario() -> None:
        service, _, resolver, _ = harness()
        actor = principal()
        agent, _ = await create_configured(
            service,
            resolver,
            actor,
            tool_risk=risk,
            external=external,
            approval=approval,
            allow_external=allow_external,
            max_tool_calls=max_calls,
        )
        with pytest.raises(AgentResourceValidationError) as invalid:
            await service.publish(actor, agent.id)
        assert code in {item["code"] for item in invalid.value.errors}

    asyncio.run(scenario())


def test_nested_agent_permissions_are_always_an_intersection() -> None:
    assert effective_child_permissions(
        frozenset({"knowledge.read", "asset.write"}),
        frozenset({"knowledge.read", "tool.call"}),
    ) == frozenset({"knowledge.read"})

    async def scenario() -> None:
        service, _, resolver, _ = harness()
        actor = principal()
        agent, _ = await create_configured(service, resolver, actor)
        version = await service.publish(actor, agent.id)
        authorized = await service.validate_new_run(
            actor,
            agent.id,
            version=version.version,
            parent_permission_snapshot=frozenset({"knowledge.read", "asset.write"}),
        )
        assert authorized.effective_permissions == frozenset({"knowledge.read"})

    asyncio.run(scenario())


def test_availability_overlay_does_not_mutate_version_and_kill_switch_is_rechecked() -> None:
    async def scenario() -> None:
        service, repository, resolver, _ = harness()
        actor = principal()
        agent, _ = await create_configured(service, resolver, actor)
        version = await service.publish(actor, agent.id)
        original = version.model_dump()

        await service.set_version_availability(
            actor, agent.id, version.version, VersionAvailabilityStatus.DISABLED
        )
        with pytest.raises(AgentResourceValidationError) as disabled:
            await service.validate_new_run(actor, agent.id, version=version.version)
        assert disabled.value.errors[0]["code"] == "agent_version_unavailable"

        await service.set_version_availability(
            actor, agent.id, version.version, VersionAvailabilityStatus.AVAILABLE
        )
        tool = version.tools[0]
        resolver.results[key(tool)] = resolver.results[key(tool)].model_copy(
            update={"availability": VersionAvailabilityStatus.DISABLED}
        )
        with pytest.raises(AgentResourceValidationError) as resource_disabled:
            await service.validate_new_run(actor, agent.id, version=version.version)
        assert resource_disabled.value.errors[0]["code"] == "resource_unavailable_for_new_run"

        # Normal disable does not kill an already-started run.
        assert (await service.authorize_side_effect(actor, agent.id, version.version, tool)) is not None
        resolver.results[key(tool)] = resolver.results[key(tool)].model_copy(
            update={"availability": VersionAvailabilityStatus.REVOKED}
        )
        with pytest.raises(AgentResourceValidationError) as revoked:
            await service.authorize_side_effect(actor, agent.id, version.version, tool)
        assert revoked.value.errors[0]["code"] == "resource_revoked"

        stored = await repository.get_version(actor.tenant_id, agent.id, version.version)
        assert stored is not None and stored.model_dump() == original

    asyncio.run(scenario())


def test_concurrent_publications_cannot_create_duplicate_versions() -> None:
    async def scenario() -> None:
        service, _, resolver, _ = harness()
        actor = principal()
        agent, _ = await create_configured(service, resolver, actor)
        outcomes = await asyncio.gather(
            service.publish(actor, agent.id),
            service.publish(actor, agent.id),
            return_exceptions=True,
        )
        assert sum(not isinstance(item, BaseException) for item in outcomes) == 1
        assert sum(isinstance(item, AgentConflictError) for item in outcomes) == 1
        assert len(await service.list_versions(actor, agent.id)) == 1

    asyncio.run(scenario())


def test_standalone_workflow_reference_is_not_an_agent_binding() -> None:
    with pytest.raises(ValidationError):
        AgentResourceBindings(
            workflow=reference(ResourceKind.WORKFLOW),
            prompt=reference(ResourceKind.PROMPT),
            model=reference(ResourceKind.MODEL),
        )
