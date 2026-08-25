"""Atomic in-memory storage contract for Agent and owned Workflow releases."""

from __future__ import annotations

import asyncio
from typing import Protocol
from uuid import UUID

from app.agents.errors import AgentConflictError, AgentVersionImmutableError
from app.agents.schemas import (
    AgentDefinition,
    AgentVersion,
    AgentVersionAvailability,
    OwnedWorkflowDraft,
    OwnedWorkflowVersion,
)


class AgentRepository(Protocol):
    async def create(
        self, agent: AgentDefinition, workflow: OwnedWorkflowDraft
    ) -> tuple[AgentDefinition, OwnedWorkflowDraft]: ...

    async def get(self, tenant_id: UUID, agent_id: UUID) -> AgentDefinition | None: ...

    async def list(
        self, tenant_id: UUID, *, limit: int, offset: int
    ) -> tuple[list[AgentDefinition], int]: ...

    async def update(
        self, agent: AgentDefinition, *, expected_aggregate_revision: int
    ) -> AgentDefinition: ...

    async def get_workflow_draft(
        self, tenant_id: UUID, workflow_id: UUID
    ) -> OwnedWorkflowDraft | None: ...

    async def update_workflow_draft(
        self,
        agent: AgentDefinition,
        workflow: OwnedWorkflowDraft,
        *,
        expected_aggregate_revision: int,
    ) -> tuple[AgentDefinition, OwnedWorkflowDraft]: ...

    async def publish(
        self,
        agent: AgentDefinition,
        version: AgentVersion,
        workflow_version: OwnedWorkflowVersion,
        *,
        expected_aggregate_revision: int,
    ) -> AgentDefinition: ...

    async def get_version(
        self, tenant_id: UUID, agent_id: UUID, version: int
    ) -> AgentVersion | None: ...

    async def list_versions(self, tenant_id: UUID, agent_id: UUID) -> list[AgentVersion]: ...

    async def get_workflow_version(
        self, tenant_id: UUID, workflow_id: UUID, version: int
    ) -> OwnedWorkflowVersion | None: ...

    async def get_version_availability(
        self, tenant_id: UUID, agent_id: UUID, version: int
    ) -> AgentVersionAvailability | None: ...

    async def set_version_availability(
        self, availability: AgentVersionAvailability
    ) -> AgentVersionAvailability: ...


class InMemoryAgentRepository:
    """Tenant-isolated, copy-on-read store with atomic version creation."""

    def __init__(self) -> None:
        self._agents: dict[UUID, AgentDefinition] = {}
        self._workflow_drafts: dict[UUID, OwnedWorkflowDraft] = {}
        self._versions: dict[tuple[UUID, UUID, int], AgentVersion] = {}
        self._workflow_versions: dict[tuple[UUID, UUID, int], OwnedWorkflowVersion] = {}
        self._availability: dict[tuple[UUID, UUID, int], AgentVersionAvailability] = {}
        self._lock = asyncio.Lock()

    async def create(
        self, agent: AgentDefinition, workflow: OwnedWorkflowDraft
    ) -> tuple[AgentDefinition, OwnedWorkflowDraft]:
        async with self._lock:
            if agent.id in self._agents or workflow.id in self._workflow_drafts:
                raise AgentConflictError("agent or owned workflow already exists")
            if (
                workflow.agent_id != agent.id
                or workflow.tenant_id != agent.tenant_id
                or workflow.id != agent.owned_workflow_draft_id
            ):
                raise AgentConflictError("owned workflow does not belong to the Agent")
            self._agents[agent.id] = agent.model_copy(deep=True)
            self._workflow_drafts[workflow.id] = workflow.model_copy(deep=True)
        return agent.model_copy(deep=True), workflow.model_copy(deep=True)

    async def get(self, tenant_id: UUID, agent_id: UUID) -> AgentDefinition | None:
        agent = self._agents.get(agent_id)
        if agent is None or agent.tenant_id != tenant_id:
            return None
        return agent.model_copy(deep=True)

    async def list(
        self, tenant_id: UUID, *, limit: int, offset: int
    ) -> tuple[list[AgentDefinition], int]:
        agents = [agent for agent in self._agents.values() if agent.tenant_id == tenant_id]
        agents.sort(key=lambda item: (item.created_at, str(item.id)), reverse=True)
        return [item.model_copy(deep=True) for item in agents[offset : offset + limit]], len(agents)

    async def update(
        self, agent: AgentDefinition, *, expected_aggregate_revision: int
    ) -> AgentDefinition:
        async with self._lock:
            current = self._checked_agent(agent)
            if current.aggregate_revision != expected_aggregate_revision:
                raise AgentConflictError(
                    "agent aggregate revision changed: "
                    f"expected {expected_aggregate_revision}, current {current.aggregate_revision}"
                )
            self._agents[agent.id] = agent.model_copy(deep=True)
        return agent.model_copy(deep=True)

    async def get_workflow_draft(
        self, tenant_id: UUID, workflow_id: UUID
    ) -> OwnedWorkflowDraft | None:
        workflow = self._workflow_drafts.get(workflow_id)
        if workflow is None or workflow.tenant_id != tenant_id:
            return None
        return workflow.model_copy(deep=True)

    async def update_workflow_draft(
        self,
        agent: AgentDefinition,
        workflow: OwnedWorkflowDraft,
        *,
        expected_aggregate_revision: int,
    ) -> tuple[AgentDefinition, OwnedWorkflowDraft]:
        async with self._lock:
            current_agent = self._checked_agent(agent)
            current_workflow = self._workflow_drafts.get(workflow.id)
            if current_workflow is None or current_workflow.tenant_id != workflow.tenant_id:
                raise AgentConflictError("owned workflow no longer exists")
            if current_agent.aggregate_revision != expected_aggregate_revision:
                raise AgentConflictError(
                    "agent aggregate revision changed: "
                    f"expected {expected_aggregate_revision}, current {current_agent.aggregate_revision}"
                )
            if workflow.agent_id != current_agent.id or workflow.id != current_agent.owned_workflow_draft_id:
                raise AgentConflictError("owned workflow does not belong to the Agent")
            self._agents[agent.id] = agent.model_copy(deep=True)
            self._workflow_drafts[workflow.id] = workflow.model_copy(deep=True)
        return agent.model_copy(deep=True), workflow.model_copy(deep=True)

    async def publish(
        self,
        agent: AgentDefinition,
        version: AgentVersion,
        workflow_version: OwnedWorkflowVersion,
        *,
        expected_aggregate_revision: int,
    ) -> AgentDefinition:
        agent_key = (version.tenant_id, version.agent_id, version.version)
        workflow_key = (
            workflow_version.tenant_id,
            workflow_version.workflow_id,
            workflow_version.version,
        )
        async with self._lock:
            current = self._checked_agent(agent)
            workflow = self._workflow_drafts.get(agent.owned_workflow_draft_id)
            if workflow is None or workflow.tenant_id != agent.tenant_id:
                raise AgentConflictError("owned workflow no longer exists")
            if current.aggregate_revision != expected_aggregate_revision:
                raise AgentConflictError("Agent aggregate changed while publication was being validated")
            if agent_key in self._versions or workflow_key in self._workflow_versions:
                raise AgentVersionImmutableError(str(version.agent_id), version.version)
            if (
                version.workflow.resource_id != workflow.id
                or workflow_version.workflow_id != workflow.id
                or workflow_version.agent_id != agent.id
                or version.version != workflow_version.version
            ):
                raise AgentConflictError("AgentVersion and WorkflowVersion ownership mismatch")
            self._versions[agent_key] = version.model_copy(deep=True)
            self._workflow_versions[workflow_key] = workflow_version.model_copy(deep=True)
            self._availability[agent_key] = AgentVersionAvailability(
                agent_id=version.agent_id,
                tenant_id=version.tenant_id,
                version=version.version,
                updated_by=version.published_by,
                updated_at=version.published_at,
            )
            self._agents[agent.id] = agent.model_copy(deep=True)
        return agent.model_copy(deep=True)

    async def get_version(
        self, tenant_id: UUID, agent_id: UUID, version: int
    ) -> AgentVersion | None:
        value = self._versions.get((tenant_id, agent_id, version))
        return None if value is None else value.model_copy(deep=True)

    async def list_versions(self, tenant_id: UUID, agent_id: UUID) -> list[AgentVersion]:
        if await self.get(tenant_id, agent_id) is None:
            return []
        values = [
            value
            for (stored_tenant, stored_agent, _), value in self._versions.items()
            if stored_tenant == tenant_id and stored_agent == agent_id
        ]
        values.sort(key=lambda item: item.version, reverse=True)
        return [item.model_copy(deep=True) for item in values]

    async def get_workflow_version(
        self, tenant_id: UUID, workflow_id: UUID, version: int
    ) -> OwnedWorkflowVersion | None:
        value = self._workflow_versions.get((tenant_id, workflow_id, version))
        return None if value is None else value.model_copy(deep=True)

    async def get_version_availability(
        self, tenant_id: UUID, agent_id: UUID, version: int
    ) -> AgentVersionAvailability | None:
        value = self._availability.get((tenant_id, agent_id, version))
        return None if value is None else value.model_copy(deep=True)

    async def set_version_availability(
        self, availability: AgentVersionAvailability
    ) -> AgentVersionAvailability:
        key = (availability.tenant_id, availability.agent_id, availability.version)
        async with self._lock:
            if key not in self._versions:
                raise AgentConflictError("cannot set availability for a missing AgentVersion")
            self._availability[key] = availability.model_copy(deep=True)
        return availability.model_copy(deep=True)

    def _checked_agent(self, agent: AgentDefinition) -> AgentDefinition:
        current = self._agents.get(agent.id)
        if current is None or current.tenant_id != agent.tenant_id:
            raise AgentConflictError(f"agent '{agent.id}' no longer exists")
        return current
