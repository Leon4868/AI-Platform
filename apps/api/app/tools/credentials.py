"""Credential bindings: opaque handles, checked against the provider.

Business tables hold a binding id and nothing else. A URI would have a free-text
tail that a pasted credential fits into, and once one lands in a business table
it is in the backups, the replicas and every export taken since — a UUID cannot
carry a secret.

An id alone is not enough, though. A well-formed UUID says nothing about whether
that binding belongs to this tenant, is still active, or was issued for this kind
of use. Those are questions only the provider can answer, so the registry asks it
through this port instead of assuming.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from app.core.errors import DomainError


class CredentialPurpose(StrEnum):
    """What a binding was issued for.

    Checked so that a credential minted for one integration cannot be pointed at
    another: an MCP server binding must not become an HTTP tool's authorization
    header just because someone copied the id across.
    """

    HTTP_TOOL = "http_tool"
    MCP_SERVER = "mcp_server"


class CredentialBindingError(DomainError):
    def __init__(self, binding_id: UUID, detail: str) -> None:
        super().__init__(
            title="Credential binding is not usable",
            detail=f"Credential binding '{binding_id}' {detail}",
            status_code=422,
            error_code="credential_binding_invalid",
        )


class CredentialResolver(Protocol):
    async def validate(
        self, *, tenant_id: UUID, binding_id: UUID, purpose: CredentialPurpose
    ) -> None:
        """Raise `CredentialBindingError` unless the binding is usable here.

        Never returns the credential. Nothing in this package can, which is the
        property that keeps plaintext out of the registry entirely.
        """


@dataclass(frozen=True, slots=True)
class CredentialBinding:
    tenant_id: UUID
    purpose: CredentialPurpose
    active: bool = True


class InMemoryCredentialResolver:
    """Development resolver. A binding it has never seen is not usable."""

    def __init__(self, bindings: dict[UUID, CredentialBinding] | None = None) -> None:
        self._bindings = dict(bindings or {})

    def register(self, binding_id: UUID, binding: CredentialBinding) -> None:
        self._bindings[binding_id] = binding

    async def validate(
        self, *, tenant_id: UUID, binding_id: UUID, purpose: CredentialPurpose
    ) -> None:
        binding = self._bindings.get(binding_id)
        if binding is None:
            raise CredentialBindingError(binding_id, "does not exist")
        if binding.tenant_id != tenant_id:
            raise CredentialBindingError(binding_id, "belongs to another tenant")
        if not binding.active:
            raise CredentialBindingError(binding_id, "has been revoked")
        if binding.purpose is not purpose:
            raise CredentialBindingError(
                binding_id,
                f"was issued for '{binding.purpose.value}', not '{purpose.value}'",
            )
