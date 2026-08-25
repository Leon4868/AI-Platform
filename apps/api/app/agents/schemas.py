"""Pure domain contracts for mutable Agent definitions and immutable releases."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import ConfigDict, Field, StringConstraints, model_validator

from app.core.schemas import ApiModel, Entity

ExactVersion = Annotated[int, Field(ge=1, strict=True)]
VersionSelector = ExactVersion | Annotated[str, StringConstraints(min_length=1, max_length=64)] | None


class AgentLifecycle(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class VersionAvailabilityStatus(StrEnum):
    AVAILABLE = "available"
    DEPRECATED = "deprecated"
    DISABLED = "disabled"
    REVOKED = "revoked"
    ARCHIVED = "archived"


class AgentAction(StrEnum):
    USE = "use"
    EDIT = "edit"
    PUBLISH = "publish"
    ADMIN = "admin"


class ResourceKind(StrEnum):
    WORKFLOW = "workflow"
    PROMPT = "prompt"
    MODEL = "model"
    KNOWLEDGE = "knowledge"
    SKILL = "skill"
    TOOL = "tool"


class ResourcePublicationStatus(StrEnum):
    DRAFT = "draft"
    VERIFIED = "verified"
    PUBLISHED = "published"


class ResourceRisk(StrEnum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class WriteActionApproval(StrEnum):
    NEVER = "never"
    ON_WRITE = "on_write"
    ALWAYS = "always"


class ResourceReference(ApiModel):
    """A draft selector; publication only accepts a positive integer version."""

    kind: ResourceKind
    resource_id: UUID
    version: VersionSelector

    @property
    def is_exact(self) -> bool:
        return isinstance(self.version, int) and not isinstance(self.version, bool)


class ExactResourceReference(ApiModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, frozen=True)

    kind: ResourceKind
    resource_id: UUID
    version: ExactVersion


class KnowledgeReference(ApiModel):
    """Knowledge binds both the content index and the ACL policy snapshot."""

    knowledge_base_id: UUID
    index_revision: VersionSelector
    policy_version: VersionSelector

    @property
    def is_exact(self) -> bool:
        return all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in (self.index_revision, self.policy_version)
        )


class ExactKnowledgeReference(ApiModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, frozen=True)

    knowledge_base_id: UUID
    index_revision: ExactVersion
    policy_version: ExactVersion


class AgentAccessPolicy(ApiModel):
    """Management-plane ACL plus the data scopes an Agent may request."""

    user_grants: dict[UUID, frozenset[AgentAction]] = Field(default_factory=dict)
    department_grants: dict[str, frozenset[AgentAction]] = Field(default_factory=dict)
    owner_department_actions: frozenset[AgentAction] = frozenset({AgentAction.USE})
    data_scopes: frozenset[str] = Field(default_factory=frozenset)

    def allows(
        self,
        *,
        user_id: UUID,
        department_ids: frozenset[str],
        creator_id: UUID,
        owner_department_id: str,
        action: AgentAction,
    ) -> bool:
        if user_id == creator_id:
            return True
        actions = set(self.user_grants.get(user_id, frozenset()))
        if owner_department_id in department_ids:
            actions.update(self.owner_department_actions)
        for department_id in department_ids:
            actions.update(self.department_grants.get(department_id, frozenset()))
        return AgentAction.ADMIN in actions or action in actions


class AgentResourceBindings(ApiModel):
    """External assets used by the Agent's private owned Workflow.

    Workflow is deliberately absent: standalone templates are not executable
    Agents. A private WorkflowDraft is created with the Agent, then snapshotted
    in the same transaction as AgentVersion.
    """

    prompt: ResourceReference | None = None
    model: ResourceReference | None = None
    knowledge: tuple[KnowledgeReference, ...] = ()
    skills: tuple[ResourceReference, ...] = ()
    tools: tuple[ResourceReference, ...] = ()

    @model_validator(mode="after")
    def _correct_kinds_and_unique_references(self) -> "AgentResourceBindings":
        for field_name, reference, expected in (
            ("prompt", self.prompt, ResourceKind.PROMPT),
            ("model", self.model, ResourceKind.MODEL),
        ):
            if reference is not None and reference.kind is not expected:
                raise ValueError(f"{field_name} must contain a {expected.value} reference")
        for field_name, references, expected in (
            ("skills", self.skills, ResourceKind.SKILL),
            ("tools", self.tools, ResourceKind.TOOL),
        ):
            if any(reference.kind is not expected for reference in references):
                raise ValueError(f"{field_name} must contain only {expected.value} references")
            keys = [(reference.resource_id, str(reference.version)) for reference in references]
            if len(keys) != len(set(keys)):
                raise ValueError(f"{field_name} references must be unique")
        knowledge_keys = [
            (item.knowledge_base_id, str(item.index_revision), str(item.policy_version))
            for item in self.knowledge
        ]
        if len(knowledge_keys) != len(set(knowledge_keys)):
            raise ValueError("knowledge references must be unique")
        return self

    def resource_references(self) -> tuple[ResourceReference, ...]:
        required = tuple(item for item in (self.prompt, self.model) if item is not None)
        return required + self.skills + self.tools


class AgentLimits(ApiModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, frozen=True)

    max_steps: int = Field(default=30, ge=1, le=1_000)
    max_tool_calls: int = Field(default=20, ge=0, le=1_000)
    max_run_seconds: int = Field(default=300, ge=1, le=86_400)
    max_cost: Decimal = Field(default=Decimal("10"), gt=0, max_digits=18, decimal_places=6)


class AgentPolicy(ApiModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, frozen=True)

    allow_external_network: bool = False
    write_action_approval: WriteActionApproval = WriteActionApproval.ON_WRITE


class AgentCreate(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    owner_department_id: str = Field(min_length=1, max_length=128)
    access: AgentAccessPolicy = Field(default_factory=AgentAccessPolicy)


class AgentDraftUpdate(ApiModel):
    resources: AgentResourceBindings
    limits: AgentLimits = Field(default_factory=AgentLimits)
    policy: AgentPolicy = Field(default_factory=AgentPolicy)
    change_summary: str = Field(default="", max_length=1_000)
    required_runtime_permissions: frozenset[str] = Field(default_factory=frozenset)
    aggregate_revision: int = Field(ge=1)


class AgentDraft(ApiModel):
    resources: AgentResourceBindings = Field(default_factory=AgentResourceBindings)
    limits: AgentLimits = Field(default_factory=AgentLimits)
    policy: AgentPolicy = Field(default_factory=AgentPolicy)
    change_summary: str = Field(default="", max_length=1_000)
    required_runtime_permissions: frozenset[str] = Field(default_factory=frozenset)
    updated_by: UUID
    updated_at: datetime


class OwnedWorkflowDraft(ApiModel):
    id: UUID
    tenant_id: UUID
    agent_id: UUID
    owner_id: UUID
    visibility: str = Field(default="private", pattern=r"^private$")
    definition: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime


class OwnedWorkflowVersion(ApiModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, frozen=True)

    workflow_id: UUID
    tenant_id: UUID
    agent_id: UUID
    version: ExactVersion
    source_aggregate_revision: int = Field(ge=1)
    definition: dict[str, Any]
    published_at: datetime


class WorkflowDraftUpdate(ApiModel):
    aggregate_revision: int = Field(ge=1)
    definition: dict[str, Any]


class AgentDefinition(Entity):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    owner_department_id: str = Field(min_length=1, max_length=128)
    created_by: UUID
    access: AgentAccessPolicy = Field(default_factory=AgentAccessPolicy)
    lifecycle: AgentLifecycle = AgentLifecycle.ACTIVE
    owned_workflow_draft_id: UUID
    aggregate_revision: int = Field(ge=1)
    has_unpublished_changes: bool = True
    published_version: int | None = Field(default=None, ge=1)
    draft: AgentDraft


Agent = AgentDefinition


class AgentVersion(ApiModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True, frozen=True)

    agent_id: UUID
    tenant_id: UUID
    version: ExactVersion
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)
    owner_department_id: str = Field(min_length=1, max_length=128)
    workflow: ExactResourceReference
    prompt: ExactResourceReference
    model: ExactResourceReference
    knowledge: tuple[ExactKnowledgeReference, ...] = ()
    skills: tuple[ExactResourceReference, ...] = ()
    tools: tuple[ExactResourceReference, ...] = ()
    limits: AgentLimits
    policy: AgentPolicy
    change_summary: str = Field(default="", max_length=1_000)
    required_runtime_permissions: frozenset[str] = Field(default_factory=frozenset)
    published_by: UUID
    published_at: datetime

    def resource_references(self) -> tuple[ExactResourceReference, ...]:
        return (self.prompt, self.model, *self.skills, *self.tools)

    def all_resource_count(self) -> int:
        return 1 + len(self.resource_references()) + len(self.knowledge)


class AgentVersionAvailability(ApiModel):
    """Mutable operational overlay; never changes AgentVersion content."""

    agent_id: UUID
    tenant_id: UUID
    version: ExactVersion
    status: VersionAvailabilityStatus = VersionAvailabilityStatus.AVAILABLE
    updated_by: UUID
    updated_at: datetime


class AgentRunAuthorization(ApiModel):
    version: AgentVersion
    effective_permissions: frozenset[str]
    data_scopes: frozenset[str]


def effective_child_permissions(
    parent_permission_snapshot: frozenset[str],
    child_required_permissions: frozenset[str],
) -> frozenset[str]:
    """A nested Agent can only narrow the parent's run-time authority."""

    return parent_permission_snapshot & child_required_permissions
