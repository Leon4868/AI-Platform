"""Domain model for the MCP Server Registry.

Four things are kept apart that one record would have merged:

* **`McpServerDefinition`** — identity, ownership and access control. Stable.
* **`McpConfigRevision`** — where the server is and how it is reached. Immutable
  once written: a tool approved against one endpoint must not silently inherit
  that approval when someone re-points it somewhere else.
* **`McpServerState`** — verification, enablement and health, as three separate
  facts. They move independently: a server can be verified but not enabled, or
  enabled and currently unhealthy, and squeezing that into one enum forces a
  health blip to erase an administrative decision.
* **`CapabilitySnapshot`** — what the server said it could do at one instant.
  Immutable, so a published tool keeps resolving to the schemas it was reviewed
  against and any change surfaces as drift instead of a silent substitution.
"""

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.core.schemas import ApiModel, Entity
from app.tools.access import ResourceAcl
from app.tools.endpoints import covered_by, validated_host
from app.tools.schemas import CredentialBindingId, EgressHost, compute_schema_hash


class McpTransport(StrEnum):
    STREAMABLE_HTTP = "streamable_http"
    STDIO = "stdio"


class VerificationState(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"


class EnablementState(StrEnum):
    DISABLED = "disabled"
    ENABLED = "enabled"


class HealthState(StrEnum):
    UNKNOWN = "unknown"
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"


class McpCapabilityKind(StrEnum):
    TOOL = "tool"
    RESOURCE = "resource"
    PROMPT = "prompt"


class McpServerRequest(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1_000)
    acl: ResourceAcl


class McpConfigRequest(ApiModel):
    transport: McpTransport
    endpoint: str | None = Field(default=None, min_length=8, max_length=500)
    credential_binding_id: CredentialBindingId | None = None
    egress_allowlist: list[EgressHost] = Field(default_factory=list, max_length=50)
    requires_isolated_worker: bool = False

    @model_validator(mode="after")
    def _coherent(self) -> "McpConfigRequest":
        _validate_transport(self)
        return self


class McpServerDefinition(Entity):
    """Identity and ownership. Connection details live on a config revision."""

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1_000)
    acl: ResourceAcl
    created_by: UUID
    config_revision: int = Field(default=0, ge=0)


class McpConfigRevision(Entity):
    """Immutable connection configuration. Never holds a credential value."""

    server_id: UUID
    revision: int = Field(ge=1)
    transport: McpTransport
    endpoint: str | None = Field(default=None, min_length=8, max_length=500)
    credential_binding_id: CredentialBindingId | None = None
    egress_allowlist: list[EgressHost] = Field(default_factory=list, max_length=50)
    requires_isolated_worker: bool = False

    @model_validator(mode="after")
    def _coherent(self) -> "McpConfigRevision":
        _validate_transport(self)
        return self


class McpServerState(Entity):
    """Three independent facts about a server, plus the capability pointer."""

    server_id: UUID
    verification: VerificationState = VerificationState.UNVERIFIED
    enablement: EnablementState = EnablementState.DISABLED
    health: HealthState = HealthState.UNKNOWN
    verified_config_revision: int | None = Field(default=None, ge=1)
    capability_revision: int | None = Field(default=None, ge=1)
    last_health_check_at: AwareDatetime | None = None

    @property
    def usable(self) -> bool:
        """Enabled, verified, and not currently known to be down."""
        return (
            self.enablement is EnablementState.ENABLED
            and self.verification is VerificationState.VERIFIED
            and self.health is not HealthState.UNHEALTHY
        )


class McpCapability(ApiModel):
    """One thing a server reported. Its schema is frozen with the snapshot."""

    kind: McpCapabilityKind
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=2_000)
    annotations: dict[str, Any] = Field(default_factory=dict)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)

    @property
    def schema_hash(self) -> str:
        """Covers the prose too, not only the JSON schemas.

        A server that keeps the shape but rewrites the description or the
        annotations has changed what the model is told this tool does. That is a
        behavioural change, and it has to read as drift.
        """
        return compute_schema_hash(
            name=self.name,
            description=self.description,
            input_schema=self.input_schema,
            output_schema=self.output_schema,
            annotations=self.annotations,
        )


class CapabilitySnapshot(Entity):
    """Immutable record of one sync, tied to the configuration it was taken on."""

    server_id: UUID
    revision: int = Field(ge=1)
    config_revision: int = Field(ge=1)
    captured_at: AwareDatetime
    capabilities: list[McpCapability] = Field(max_length=500)

    @model_validator(mode="after")
    def _unique_names(self) -> "CapabilitySnapshot":
        seen = {(item.kind, item.name) for item in self.capabilities}
        if len(seen) != len(self.capabilities):
            raise ValueError("capability names must be unique within a kind")
        return self

    def tool_schema_hashes(self) -> dict[str, str]:
        """Tool name to schema hash — the input a drift check compares against."""
        return {
            item.name: item.schema_hash
            for item in self.capabilities
            if item.kind is McpCapabilityKind.TOOL
        }


class HealthReport(ApiModel):
    """The outcome of a connection test, as observed by whoever ran it.

    The registry records health; it never performs the probe itself, which keeps
    the domain free of network access.
    """

    healthy: bool
    checked_at: AwareDatetime
    detail: str = Field(default="", max_length=1_000)


def _validate_transport(config: "McpConfigRequest | McpConfigRevision") -> None:
    if config.transport is McpTransport.STDIO:
        if not config.requires_isolated_worker:
            raise ValueError("a stdio server must be marked as requiring an isolated worker")
        if config.endpoint is not None:
            raise ValueError("a stdio server is launched as a process and has no endpoint")
        return

    if config.endpoint is None:
        raise ValueError("a streamable_http server requires an endpoint")
    host = validated_host(config.endpoint)
    if not config.egress_allowlist:
        raise ValueError("a streamable_http server must declare an egress allowlist")
    if not covered_by(host, config.egress_allowlist):
        raise ValueError(f"'{host}' is not covered by the server's egress allowlist")
