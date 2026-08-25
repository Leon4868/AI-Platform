"""Agent aggregate service and publication/run-time policy gates."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any, Protocol, TypeAlias
from uuid import UUID, uuid4

from pydantic import Field

from app.agents.errors import AgentConflictError, AgentNotFoundError, AgentResourceValidationError
from app.agents.repository import AgentRepository
from app.agents.schemas import (
    AgentAction,
    AgentCreate,
    AgentDefinition,
    AgentDraft,
    AgentDraftUpdate,
    AgentLifecycle,
    AgentResourceBindings,
    AgentRunAuthorization,
    AgentVersion,
    AgentVersionAvailability,
    ExactKnowledgeReference,
    ExactResourceReference,
    KnowledgeReference,
    OwnedWorkflowDraft,
    OwnedWorkflowVersion,
    ResourceKind,
    ResourcePublicationStatus,
    ResourceReference,
    ResourceRisk,
    VersionAvailabilityStatus,
    WorkflowDraftUpdate,
    WriteActionApproval,
    effective_child_permissions,
)
from app.core.errors import AuthorizationError
from app.core.schemas import ApiModel
from app.identity.schemas import Principal

PublishReference: TypeAlias = ExactResourceReference | ExactKnowledgeReference


class ResolvedResource(ApiModel):
    tenant_id: UUID
    kind: ResourceKind
    resource_id: UUID
    version: int = Field(ge=1, strict=True)
    publication_status: ResourcePublicationStatus
    availability: VersionAvailabilityStatus = VersionAvailabilityStatus.AVAILABLE
    authorized: bool
    risk: ResourceRisk = ResourceRisk.READ
    requires_external_network: bool = False


class ResolvedKnowledgeResource(ApiModel):
    tenant_id: UUID
    knowledge_base_id: UUID
    index_revision: int = Field(ge=1, strict=True)
    policy_version: int = Field(ge=1, strict=True)
    publication_status: ResourcePublicationStatus
    availability: VersionAvailabilityStatus = VersionAvailabilityStatus.AVAILABLE
    authorized: bool


ResolvedReference: TypeAlias = ResolvedResource | ResolvedKnowledgeResource


class AgentResourceResolver(Protocol):
    async def resolve(
        self, principal: Principal, reference: PublishReference
    ) -> ResolvedReference | None: ...


class AgentAuditEvent(ApiModel):
    id: UUID
    tenant_id: UUID
    actor_id: UUID
    action: str = Field(min_length=1, max_length=100)
    agent_id: UUID
    agent_version: int | None = Field(default=None, ge=1)
    occurred_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentAuditSink(Protocol):
    async def record(self, event: AgentAuditEvent) -> None: ...


class InMemoryAgentAuditSink:
    def __init__(self) -> None:
        self._events: list[AgentAuditEvent] = []
        self._lock = asyncio.Lock()

    async def record(self, event: AgentAuditEvent) -> None:
        async with self._lock:
            self._events.append(event.model_copy(deep=True))

    async def list(
        self, tenant_id: UUID, *, agent_id: UUID | None = None
    ) -> list[AgentAuditEvent]:
        return [
            event.model_copy(deep=True)
            for event in self._events
            if event.tenant_id == tenant_id and (agent_id is None or event.agent_id == agent_id)
        ]


class AgentService:
    def __init__(
        self,
        repository: AgentRepository,
        resource_resolver: AgentResourceResolver,
        audit_sink: AgentAuditSink,
    ) -> None:
        self._repository = repository
        self._resources = resource_resolver
        self._audit = audit_sink

    async def create_draft(self, principal: Principal, payload: AgentCreate) -> AgentDefinition:
        now = datetime.now(UTC)
        agent_id = uuid4()
        workflow_id = uuid4()
        workflow = OwnedWorkflowDraft(
            id=workflow_id,
            tenant_id=principal.tenant_id,
            agent_id=agent_id,
            owner_id=principal.user_id,
            definition={"nodes": [], "edges": []},
            updated_at=now,
        )
        agent = AgentDefinition(
            id=agent_id,
            tenant_id=principal.tenant_id,
            name=payload.name,
            description=payload.description,
            owner_department_id=payload.owner_department_id,
            created_by=principal.user_id,
            access=payload.access,
            owned_workflow_draft_id=workflow_id,
            aggregate_revision=1,
            draft=AgentDraft(updated_by=principal.user_id, updated_at=now),
            created_at=now,
            updated_at=now,
        )
        created, _ = await self._repository.create(agent, workflow)
        await self._record(
            principal,
            "agent.definition_created",
            created,
            metadata={
                "owned_workflow_draft_id": str(workflow_id),
                "aggregate_revision": created.aggregate_revision,
            },
        )
        return created

    async def get(
        self,
        principal: Principal,
        agent_id: UUID,
        *,
        action: AgentAction = AgentAction.USE,
    ) -> AgentDefinition:
        agent = await self._get_raw(principal.tenant_id, agent_id)
        self._require_action(principal, agent, action)
        return agent

    async def list(
        self, principal: Principal, *, limit: int = 50, offset: int = 0
    ) -> tuple[list[AgentDefinition], int]:
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("limit must be 1..100 and offset must be non-negative")
        candidates: list[AgentDefinition] = []
        cursor = 0
        total_candidates = 1
        while cursor < total_candidates:
            batch, total_candidates = await self._repository.list(
                principal.tenant_id, limit=100, offset=cursor
            )
            candidates.extend(batch)
            if not batch:
                break
            cursor += len(batch)
        visible = [
            agent
            for agent in candidates
            if self._allows(principal, agent, AgentAction.USE)
        ]
        return visible[offset : offset + limit], len(visible)

    async def update_draft(
        self, principal: Principal, agent_id: UUID, payload: AgentDraftUpdate
    ) -> AgentDefinition:
        current = await self.get(principal, agent_id, action=AgentAction.EDIT)
        self._assert_active(current)
        if payload.aggregate_revision != current.aggregate_revision:
            raise AgentConflictError(
                "agent aggregate revision changed: "
                f"expected {payload.aggregate_revision}, current {current.aggregate_revision}"
            )
        now = datetime.now(UTC)
        draft = AgentDraft(
            resources=payload.resources,
            limits=payload.limits,
            policy=payload.policy,
            change_summary=payload.change_summary,
            required_runtime_permissions=payload.required_runtime_permissions,
            updated_by=principal.user_id,
            updated_at=now,
        )
        updated = current.model_copy(
            update={
                "draft": draft,
                "aggregate_revision": current.aggregate_revision + 1,
                "has_unpublished_changes": True,
                "updated_at": now,
            }
        )
        stored = await self._repository.update(
            updated, expected_aggregate_revision=current.aggregate_revision
        )
        await self._record(
            principal,
            "agent.definition_updated",
            stored,
            metadata={"aggregate_revision": stored.aggregate_revision},
        )
        return stored

    async def update_owned_workflow(
        self, principal: Principal, agent_id: UUID, payload: WorkflowDraftUpdate
    ) -> AgentDefinition:
        current = await self.get(principal, agent_id, action=AgentAction.EDIT)
        self._assert_active(current)
        if payload.aggregate_revision != current.aggregate_revision:
            raise AgentConflictError(
                "agent aggregate revision changed: "
                f"expected {payload.aggregate_revision}, current {current.aggregate_revision}"
            )
        workflow = await self._repository.get_workflow_draft(
            principal.tenant_id, current.owned_workflow_draft_id
        )
        if workflow is None or workflow.agent_id != current.id:
            raise AgentConflictError("owned workflow no longer exists")
        now = datetime.now(UTC)
        updated_workflow = workflow.model_copy(
            update={"definition": payload.definition, "updated_at": now}, deep=True
        )
        updated_agent = current.model_copy(
            update={
                "aggregate_revision": current.aggregate_revision + 1,
                "has_unpublished_changes": True,
                "updated_at": now,
            }
        )
        stored, _ = await self._repository.update_workflow_draft(
            updated_agent,
            updated_workflow,
            expected_aggregate_revision=current.aggregate_revision,
        )
        await self._record(
            principal,
            "agent.workflow_draft_updated",
            stored,
            metadata={"aggregate_revision": stored.aggregate_revision},
        )
        return stored

    async def get_owned_workflow(
        self, principal: Principal, agent_id: UUID
    ) -> tuple[AgentDefinition, OwnedWorkflowDraft]:
        agent = await self.get(principal, agent_id, action=AgentAction.EDIT)
        workflow = await self._repository.get_workflow_draft(
            principal.tenant_id, agent.owned_workflow_draft_id
        )
        if workflow is None or workflow.agent_id != agent.id:
            raise AgentConflictError("owned workflow no longer exists")
        return agent, workflow

    async def publish(self, principal: Principal, agent_id: UUID) -> AgentVersion:
        current = await self.get(principal, agent_id, action=AgentAction.PUBLISH)
        self._assert_active(current)
        if not current.has_unpublished_changes:
            raise AgentConflictError("Agent definition has no unpublished changes")
        workflow = await self._repository.get_workflow_draft(
            principal.tenant_id, current.owned_workflow_draft_id
        )
        if workflow is None or workflow.agent_id != current.id:
            raise AgentConflictError("owned workflow no longer exists")

        exact, resolved = await self._validate_bindings(principal, current.draft.resources)
        self._validate_policy(current, resolved)
        now = datetime.now(UTC)
        version_number = (current.published_version or 0) + 1
        workflow_reference = ExactResourceReference(
            kind=ResourceKind.WORKFLOW,
            resource_id=workflow.id,
            version=version_number,
        )
        snapshot = AgentVersion(
            agent_id=current.id,
            tenant_id=current.tenant_id,
            version=version_number,
            name=current.name,
            description=current.description,
            owner_department_id=current.owner_department_id,
            workflow=workflow_reference,
            prompt=exact[ResourceKind.PROMPT][0],
            model=exact[ResourceKind.MODEL][0],
            knowledge=tuple(exact[ResourceKind.KNOWLEDGE]),
            skills=tuple(exact[ResourceKind.SKILL]),
            tools=tuple(exact[ResourceKind.TOOL]),
            limits=current.draft.limits.model_copy(deep=True),
            policy=current.draft.policy.model_copy(deep=True),
            change_summary=current.draft.change_summary,
            required_runtime_permissions=current.draft.required_runtime_permissions,
            published_by=principal.user_id,
            published_at=now,
        )
        workflow_version = OwnedWorkflowVersion(
            workflow_id=workflow.id,
            tenant_id=current.tenant_id,
            agent_id=current.id,
            version=version_number,
            source_aggregate_revision=current.aggregate_revision,
            definition=workflow.definition,
            published_at=now,
        )
        published = current.model_copy(
            update={
                "published_version": version_number,
                "has_unpublished_changes": False,
                "aggregate_revision": current.aggregate_revision + 1,
                "updated_at": now,
            }
        )
        await self._repository.publish(
            published,
            snapshot,
            workflow_version,
            expected_aggregate_revision=current.aggregate_revision,
        )
        await self._record(
            principal,
            "agent.version_published",
            published,
            version=version_number,
            metadata={
                "workflow_version": workflow_version.version,
                "resource_count": snapshot.all_resource_count(),
            },
        )
        return snapshot

    async def set_version_availability(
        self,
        principal: Principal,
        agent_id: UUID,
        version: int,
        status: VersionAvailabilityStatus,
    ) -> AgentVersionAvailability:
        agent = await self.get(principal, agent_id, action=AgentAction.ADMIN)
        if await self._repository.get_version(principal.tenant_id, agent_id, version) is None:
            raise AgentNotFoundError(str(agent_id))
        overlay = AgentVersionAvailability(
            agent_id=agent_id,
            tenant_id=principal.tenant_id,
            version=version,
            status=status,
            updated_by=principal.user_id,
            updated_at=datetime.now(UTC),
        )
        stored = await self._repository.set_version_availability(overlay)
        await self._record(
            principal,
            "agent.version_availability_changed",
            agent,
            version=version,
            metadata={"status": status.value},
        )
        return stored

    async def get_version(
        self, principal: Principal, agent_id: UUID, version: int
    ) -> AgentVersion:
        await self.get(principal, agent_id, action=AgentAction.USE)
        snapshot = await self._repository.get_version(principal.tenant_id, agent_id, version)
        if snapshot is None:
            raise AgentNotFoundError(str(agent_id))
        return snapshot

    async def list_versions(self, principal: Principal, agent_id: UUID) -> list[AgentVersion]:
        await self.get(principal, agent_id, action=AgentAction.USE)
        return await self._repository.list_versions(principal.tenant_id, agent_id)

    async def validate_new_run(
        self,
        principal: Principal,
        agent_id: UUID,
        *,
        version: int | None = None,
        parent_permission_snapshot: frozenset[str] = frozenset(),
    ) -> AgentRunAuthorization:
        agent = await self.get(principal, agent_id, action=AgentAction.USE)
        self._assert_active(agent)
        selected = version or agent.published_version
        if selected is None:
            raise AgentConflictError("Agent has no published version")
        snapshot = await self._repository.get_version(principal.tenant_id, agent_id, selected)
        availability = await self._repository.get_version_availability(
            principal.tenant_id, agent_id, selected
        )
        if snapshot is None or availability is None:
            raise AgentNotFoundError(str(agent_id))
        if availability.status in {
            VersionAvailabilityStatus.DISABLED,
            VersionAvailabilityStatus.REVOKED,
            VersionAvailabilityStatus.ARCHIVED,
        }:
            raise AgentResourceValidationError(
                [{"code": "agent_version_unavailable", "status": availability.status.value}]
            )
        references: list[PublishReference] = [*snapshot.resource_references(), *snapshot.knowledge]
        resolved = await asyncio.gather(
            *(self._resources.resolve(principal, reference) for reference in references)
        )
        errors = self._resolution_errors(principal, references, list(resolved), for_new_run=True)
        if errors:
            raise AgentResourceValidationError(errors)
        return AgentRunAuthorization(
            version=snapshot,
            effective_permissions=effective_child_permissions(
                parent_permission_snapshot, snapshot.required_runtime_permissions
            ),
            data_scopes=agent.access.data_scopes,
        )

    async def authorize_side_effect(
        self,
        principal: Principal,
        agent_id: UUID,
        version: int,
        reference: PublishReference,
    ) -> ResolvedReference:
        """Re-authorize immediately before each external/write side effect."""

        await self.get(principal, agent_id, action=AgentAction.USE)
        availability = await self._repository.get_version_availability(
            principal.tenant_id, agent_id, version
        )
        if availability is None:
            raise AgentNotFoundError(str(agent_id))
        if availability.status is VersionAvailabilityStatus.REVOKED:
            raise AgentResourceValidationError(
                [{"code": "agent_version_revoked", "version": version}]
            )
        resolved = await self._resources.resolve(principal, reference)
        errors = self._resolution_errors(principal, [reference], [resolved], for_new_run=False)
        if errors:
            raise AgentResourceValidationError(errors)
        assert resolved is not None
        return resolved

    async def _validate_bindings(
        self, principal: Principal, bindings: AgentResourceBindings
    ) -> tuple[
        dict[ResourceKind, list[ExactResourceReference | ExactKnowledgeReference]],
        list[ResolvedReference],
    ]:
        errors: list[dict[str, Any]] = []
        for kind, value in (
            (ResourceKind.PROMPT, bindings.prompt),
            (ResourceKind.MODEL, bindings.model),
        ):
            if value is None:
                errors.append({"code": "required_resource_missing", "resourceKind": kind.value})

        exact: dict[ResourceKind, list[ExactResourceReference | ExactKnowledgeReference]] = {
            kind: [] for kind in ResourceKind
        }
        references: list[PublishReference] = []
        for reference in bindings.resource_references():
            if not reference.is_exact:
                errors.append(self._not_exact_error(reference))
                continue
            pinned = ExactResourceReference(
                kind=reference.kind,
                resource_id=reference.resource_id,
                version=reference.version,
            )
            exact[reference.kind].append(pinned)
            references.append(pinned)
        for reference in bindings.knowledge:
            if not reference.is_exact:
                errors.append(
                    {
                        "code": "resource_version_not_exact",
                        "resourceKind": ResourceKind.KNOWLEDGE.value,
                        "resourceId": str(reference.knowledge_base_id),
                        "indexRevision": reference.index_revision,
                        "policyVersion": reference.policy_version,
                    }
                )
                continue
            pinned_knowledge = ExactKnowledgeReference(
                knowledge_base_id=reference.knowledge_base_id,
                index_revision=reference.index_revision,
                policy_version=reference.policy_version,
            )
            exact[ResourceKind.KNOWLEDGE].append(pinned_knowledge)
            references.append(pinned_knowledge)
        if errors:
            raise AgentResourceValidationError(errors)

        resolved = await asyncio.gather(
            *(self._resources.resolve(principal, reference) for reference in references)
        )
        errors = self._resolution_errors(principal, references, list(resolved), for_new_run=True)
        if errors:
            raise AgentResourceValidationError(errors)
        return exact, [item for item in resolved if item is not None]

    @staticmethod
    def _resolution_errors(
        principal: Principal,
        references: list[PublishReference],
        resolved: list[ResolvedReference | None],
        *,
        for_new_run: bool,
    ) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        for requested, resource in zip(references, resolved, strict=True):
            identity = AgentService._identity(requested)
            if resource is None:
                errors.append({"code": "resource_not_found", **identity})
                continue
            if not AgentService._resolution_matches(requested, resource):
                errors.append({"code": "resource_resolution_mismatch", **identity})
                continue
            if resource.tenant_id != principal.tenant_id or not resource.authorized:
                errors.append({"code": "resource_not_authorized", **identity})
                continue
            if resource.publication_status is not ResourcePublicationStatus.PUBLISHED:
                errors.append({"code": "resource_not_published", **identity})
                continue
            if resource.availability is VersionAvailabilityStatus.REVOKED:
                errors.append({"code": "resource_revoked", **identity})
            elif for_new_run and resource.availability in {
                VersionAvailabilityStatus.DISABLED,
                VersionAvailabilityStatus.ARCHIVED,
            }:
                errors.append(
                    {"code": "resource_unavailable_for_new_run", **identity, "status": resource.availability.value}
                )
        return errors

    @staticmethod
    def _validate_policy(agent: AgentDefinition, resources: list[ResolvedReference]) -> None:
        errors: list[dict[str, Any]] = []
        tools = [
            item
            for item in resources
            if isinstance(item, ResolvedResource) and item.kind is ResourceKind.TOOL
        ]
        if tools and agent.draft.limits.max_tool_calls == 0:
            errors.append({"code": "tool_calls_disabled_but_tools_bound"})
        for resource in tools:
            identity = {
                "resourceKind": resource.kind.value,
                "resourceId": str(resource.resource_id),
                "version": resource.version,
            }
            if resource.requires_external_network and not agent.draft.policy.allow_external_network:
                errors.append({"code": "external_network_not_allowed", **identity})
            if (
                resource.risk in {ResourceRisk.WRITE, ResourceRisk.DESTRUCTIVE}
                and agent.draft.policy.write_action_approval is WriteActionApproval.NEVER
            ):
                errors.append({"code": "write_action_approval_required", **identity})
        if errors:
            raise AgentResourceValidationError(errors)

    async def _get_raw(self, tenant_id: UUID, agent_id: UUID) -> AgentDefinition:
        agent = await self._repository.get(tenant_id, agent_id)
        if agent is None:
            raise AgentNotFoundError(str(agent_id))
        return agent

    @staticmethod
    def _assert_active(agent: AgentDefinition) -> None:
        if agent.lifecycle is AgentLifecycle.ARCHIVED:
            raise AgentConflictError("archived Agent definitions cannot be changed or run")

    @staticmethod
    def _allows(principal: Principal, agent: AgentDefinition, action: AgentAction) -> bool:
        return agent.access.allows(
            user_id=principal.user_id,
            department_ids=principal.department_ids,
            creator_id=agent.created_by,
            owner_department_id=agent.owner_department_id,
            action=action,
        )

    @classmethod
    def _require_action(
        cls, principal: Principal, agent: AgentDefinition, action: AgentAction
    ) -> None:
        if not cls._allows(principal, agent, action):
            raise AuthorizationError(f"Agent action '{action.value}' is not permitted")

    @staticmethod
    def _identity(reference: PublishReference) -> dict[str, Any]:
        if isinstance(reference, ExactKnowledgeReference):
            return {
                "resourceKind": ResourceKind.KNOWLEDGE.value,
                "resourceId": str(reference.knowledge_base_id),
                "indexRevision": reference.index_revision,
                "policyVersion": reference.policy_version,
            }
        return {
            "resourceKind": reference.kind.value,
            "resourceId": str(reference.resource_id),
            "version": reference.version,
        }

    @staticmethod
    def _resolution_matches(
        requested: PublishReference, resource: ResolvedReference
    ) -> bool:
        if isinstance(requested, ExactKnowledgeReference):
            return (
                isinstance(resource, ResolvedKnowledgeResource)
                and resource.knowledge_base_id == requested.knowledge_base_id
                and resource.index_revision == requested.index_revision
                and resource.policy_version == requested.policy_version
            )
        return (
            isinstance(resource, ResolvedResource)
            and resource.kind is requested.kind
            and resource.resource_id == requested.resource_id
            and resource.version == requested.version
        )

    @staticmethod
    def _not_exact_error(reference: ResourceReference) -> dict[str, Any]:
        return {
            "code": "resource_version_not_exact",
            "resourceKind": reference.kind.value,
            "resourceId": str(reference.resource_id),
            "version": reference.version,
        }

    async def _record(
        self,
        principal: Principal,
        action: str,
        agent: AgentDefinition,
        *,
        version: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        await self._audit.record(
            AgentAuditEvent(
                id=uuid4(),
                tenant_id=principal.tenant_id,
                actor_id=principal.user_id,
                action=action,
                agent_id=agent.id,
                agent_version=version,
                occurred_at=datetime.now(UTC),
                metadata=metadata or {},
            )
        )
