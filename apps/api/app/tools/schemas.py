"""Domain model for the Tool Registry.

Three things are kept apart that a single `status` field would have merged:

* **`ToolDraft`** is the editable proposal. It changes as often as review needs.
* **`ToolVersion`** is the contract that was published. Nothing ever rewrites
  one — a published version is what running workflows were reviewed against.
* **`VersionAvailability`** is whether that contract may be used *right now*.
  Availability changes constantly: withdrawn, revoked, blocked by drift. Storing
  it on the version would mean editing an immutable record to express it.

Two more ideas are kept apart:

* **effect** is what the tool does to the world — read, write, destructive. It
  decides whether a human must approve.
* **risk** is how much damage a wrong call causes — low through critical. It
  decides how much scrutiny review and monitoring apply.

A read-only tool can still be critical (it may expose the entire salary table),
and a write can be low risk (a scratch note). Collapsing the two loses one of
those judgements.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, computed_field, model_validator

from app.core.idempotency import request_fingerprint
from app.core.schemas import ApiModel, Entity
from app.tools.access import ResourceAcl
from app.tools.endpoints import covered_by, validated_host

HOST_PATTERN = r"^(\*\.)?[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"

EgressHost = Annotated[str, StringConstraints(pattern=HOST_PATTERN, max_length=253)]

CredentialBindingId = UUID
"""An opaque handle to a binding held by the credential provider.

Deliberately a bare identifier and not a URI: a URI has a free-text tail that a
pasted credential fits into. See `app.tools.credentials` for why the id alone is
still checked before use.
"""


class ToolSourceType(StrEnum):
    BUILTIN = "builtin"
    HTTP = "http"
    MCP = "mcp"


class ToolEffect(StrEnum):
    READ = "read"
    WRITE = "write"
    DESTRUCTIVE = "destructive"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ApprovalPolicy(StrEnum):
    NEVER = "never"
    ON_WRITE = "on_write"
    ALWAYS = "always"


class DraftStatus(StrEnum):
    DRAFT = "draft"
    VERIFIED = "verified"
    PUBLISHED = "published"
    """Terminal for the draft: it produced a version and is now history."""


class VersionAvailability(StrEnum):
    AVAILABLE = "available"

    DISABLED = "disabled"
    """Withdrawn from service. Reversible, and effective immediately."""

    RUNTIME_BLOCKED = "runtime_blocked"
    """Stopped by the platform — drift, or an emergency stop."""

    REVOKED = "revoked"
    """Permanently withdrawn. Terminal: a replacement means a new version."""


BINDABLE = frozenset({VersionAvailability.AVAILABLE})
"""What a new run may bind to."""

INVOCABLE = frozenset({VersionAvailability.AVAILABLE})
"""What may be called.

Identical to `BINDABLE` on purpose. Letting a withdrawn tool keep serving runs
already in flight sounds humane until the reason for withdrawing it is that its
calls are doing damage — and that is the only reason anyone withdraws one in a
hurry. Refusal is immediate; a run that loses its tool fails visibly.
"""


def compute_schema_hash(
    *,
    name: str,
    description: str,
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
    annotations: dict[str, Any],
) -> str:
    """Stable digest of everything a reviewer read before approving.

    Name, description and annotations are inside the digest, not just the JSON
    schemas: a server that keeps the same shape but rewrites the description has
    changed what the model will be told the tool does, which is a change in
    behaviour even though every field validates the same.
    """
    return request_fingerprint(
        {
            "name": name,
            "description": description,
            "input": input_schema,
            "output": output_schema,
            "annotations": annotations,
        }
    )


class BuiltinSource(ApiModel):
    """A platform-controlled implementation. Runs in-process, reaches nothing."""

    kind: Literal["builtin"] = "builtin"
    implementation: str = Field(min_length=1, max_length=200)

    @property
    def contract_name(self) -> str:
        return self.implementation


class HttpSource(ApiModel):
    kind: Literal["http"] = "http"
    base_url: str = Field(min_length=8, max_length=500)
    operation: str = Field(min_length=1, max_length=200)
    credential_binding_id: CredentialBindingId | None = None
    allow_redirects: bool = False

    @property
    def contract_name(self) -> str:
        return self.operation

    @property
    def host(self) -> str:
        return validated_host(self.base_url)

    @model_validator(mode="after")
    def _reachable_target(self) -> "HttpSource":
        validated_host(self.base_url)
        if self.allow_redirects:
            raise ValueError(
                "redirects are not followed in this phase: the destination is not reviewed"
            )
        return self


class McpSource(ApiModel):
    """A capability frozen at one revision of one server configuration.

    All three pins matter. `config_revision` fixes *where* the call goes, so a
    re-pointed endpoint cannot inherit an old approval. `capability_revision`
    fixes *which* sync was reviewed. `capability_hash` fixes *what* it said, so
    the binding can be checked against a live server without trusting either
    number.
    """

    kind: Literal["mcp"] = "mcp"
    server_id: UUID
    config_revision: int = Field(ge=1)
    capability_revision: int = Field(ge=1)
    capability_hash: str = Field(min_length=1, max_length=200)
    tool_name: str = Field(min_length=1, max_length=200)

    @property
    def contract_name(self) -> str:
        return self.tool_name


ToolSource = Annotated[BuiltinSource | HttpSource | McpSource, Field(discriminator="kind")]


@dataclass(frozen=True, slots=True)
class McpBindingState:
    """What the MCP registry says about a binding, right now.

    Every field is answered from the server's *current* state, never from the
    revision the binding names. Looking up the pinned snapshot and comparing it
    to the pinned hash proves nothing: those two agree by construction, and a
    check that cannot fail is not a check.
    """

    server_enabled: bool

    config_current: bool
    """Whether the binding's `config_revision` is still the server's current one."""

    current_capability_hash: str | None
    """The tool's hash in the server's *newest* snapshot, or None if it is gone."""

    reviewed_on_current_config: bool
    """Whether the snapshot the tool was reviewed on belongs to that same config.

    A snapshot taken against a since-replaced endpoint describes a different
    server, however faithfully it was reviewed at the time.
    """


class ToolContract(ApiModel):
    """The reviewable substance of a tool, shared by drafts and versions."""

    source: ToolSource
    description: str = Field(default="", max_length=2_000)
    annotations: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    effect: ToolEffect
    risk: RiskLevel
    approval_policy: ApprovalPolicy
    timeout_seconds: int = Field(ge=1, le=600)
    max_retries: int = Field(default=0, ge=0, le=5)
    max_concurrency: int = Field(default=1, ge=1, le=100)
    idempotent: bool = False
    egress_allowlist: list[EgressHost] = Field(default_factory=list, max_length=50)
    change_note: str = Field(default="", max_length=2_000)

    @property
    def source_type(self) -> ToolSourceType:
        return ToolSourceType(self.source.kind)

    @property
    def schema_hash(self) -> str:
        return compute_schema_hash(
            name=self.source.contract_name,
            description=self.description,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
            annotations=self.annotations,
        )

    @model_validator(mode="after")
    def _coherent(self) -> "ToolContract":
        if self.effect is not ToolEffect.READ and self.approval_policy is ApprovalPolicy.NEVER:
            raise ValueError("a write or destructive tool requires human approval")
        if self.risk is RiskLevel.CRITICAL and self.approval_policy is not ApprovalPolicy.ALWAYS:
            raise ValueError("a critical-risk tool requires approval on every call")
        if (
            self.effect is not ToolEffect.READ
            and not self.idempotent
            and self.max_retries > 0
        ):
            # A retry of a non-idempotent write is a second write. The caller
            # cannot tell a lost response from an unperformed one, so the safe
            # reading of "no answer" is that it happened.
            raise ValueError(
                "a non-idempotent write or destructive tool cannot be retried automatically"
            )
        if self.source_type is ToolSourceType.BUILTIN and self.egress_allowlist:
            raise ValueError("a builtin tool does not leave the platform and has no egress")
        if self.source_type is not ToolSourceType.BUILTIN and not self.egress_allowlist:
            raise ValueError("an outbound tool must declare an egress allowlist")
        if isinstance(self.source, HttpSource) and not covered_by(
            self.source.host, self.egress_allowlist
        ):
            raise ValueError(
                f"'{self.source.host}' is not covered by the tool's egress allowlist"
            )
        return self


class Tool(Entity):
    """Identity, ownership and access control. The contract lives elsewhere."""

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1_000)
    acl: ResourceAcl
    created_by: UUID
    latest_version: int = Field(default=0, ge=0)


class ToolRequest(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1_000)
    acl: ResourceAcl


class ToolDraft(Entity):
    """The editable proposal. Becomes a version, and is never one itself."""

    tool_id: UUID
    contract: ToolContract
    status: DraftStatus = DraftStatus.DRAFT
    published_version: int | None = Field(default=None, ge=1)
    revision: int = Field(default=1, ge=1)
    """Bumped on every change. Publishing names the revision it reviewed, so a
    draft edited in the meantime cannot be published by a stale request."""


class ToolVersion(Entity):
    """A published contract. Immutable — nothing in the service rewrites one."""

    tool_id: UUID
    version: int = Field(ge=1)
    contract: ToolContract
    source_draft_id: UUID
    published_by: UUID

    @computed_field  # type: ignore[prop-decorator]
    @property
    def schema_hash(self) -> str:
        return self.contract.schema_hash

    @property
    def source_type(self) -> ToolSourceType:
        return self.contract.source_type


class VersionAvailabilityRecord(Entity):
    """Whether one published version may be used, and why not if not."""

    tool_id: UUID
    version: int = Field(ge=1)
    state: VersionAvailability = VersionAvailability.AVAILABLE
    reason: str = Field(default="", max_length=500)


class SchemaDrift(ApiModel):
    """A published tool no longer matches what its server now reports."""

    tool_id: UUID
    version: int
    tool_name: str
    reviewed_schema_hash: str
    current_schema_hash: str | None
    """None when the capability disappeared from the server entirely."""
