"""MCP server, configuration, state and capability-snapshot storage.

Config revisions and capability revisions are both allocated and written under
one lock. Reading "the highest so far" and then inserting are two steps, and two
concurrent syncs both see revision 3 free — the second overwriting a snapshot a
published tool may already be pinned to.

A PostgreSQL implementation cannot get that from row locks alone: the row being
allocated does not exist yet. It needs an advisory lock on the logical key
`(tenant_id, server_id)`, `SERIALIZABLE` with retry, or the server row taken
`FOR UPDATE` as the parent so every insert beneath it serialises behind it.
"""

import asyncio
from collections import defaultdict
from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from app.mcp_registry.schemas import (
    CapabilitySnapshot,
    McpConfigRevision,
    McpServerDefinition,
    McpServerState,
)


class McpRegistryRepository(Protocol):
    async def add_server(self, server: McpServerDefinition) -> McpServerDefinition: ...

    async def get_server(
        self, tenant_id: UUID, server_id: UUID
    ) -> McpServerDefinition | None: ...

    async def list_servers(
        self, tenant_id: UUID, *, limit: int, offset: int
    ) -> tuple[list[McpServerDefinition], int]: ...

    async def create_config(
        self,
        tenant_id: UUID,
        server_id: UUID,
        build: Callable[[int], McpConfigRevision],
    ) -> McpConfigRevision:
        """Allocate the next configuration revision and store it."""

    async def get_config(
        self, tenant_id: UUID, server_id: UUID, revision: int
    ) -> McpConfigRevision | None: ...

    async def current_config(
        self, tenant_id: UUID, server_id: UUID
    ) -> McpConfigRevision | None: ...

    async def get_state(self, tenant_id: UUID, server_id: UUID) -> McpServerState | None: ...

    async def put_state(self, state: McpServerState) -> McpServerState: ...

    async def create_snapshot(
        self,
        tenant_id: UUID,
        server_id: UUID,
        build: Callable[[int], CapabilitySnapshot],
    ) -> CapabilitySnapshot:
        """Allocate the next capability revision and store the snapshot."""

    async def get_snapshot(
        self, tenant_id: UUID, server_id: UUID, revision: int
    ) -> CapabilitySnapshot | None: ...

    async def latest_snapshot(
        self, tenant_id: UUID, server_id: UUID
    ) -> CapabilitySnapshot | None: ...


class InMemoryMcpRegistryRepository:
    """Development store with tenant isolation and copy-on-read semantics."""

    def __init__(self) -> None:
        self._servers: dict[UUID, McpServerDefinition] = {}
        self._configs: dict[UUID, dict[int, McpConfigRevision]] = defaultdict(dict)
        self._states: dict[UUID, McpServerState] = {}
        self._snapshots: dict[UUID, dict[int, CapabilitySnapshot]] = defaultdict(dict)
        self._lock = asyncio.Lock()

    async def add_server(self, server: McpServerDefinition) -> McpServerDefinition:
        async with self._lock:
            if server.id in self._servers:
                raise ValueError(f"duplicate MCP server id: {server.id}")
            self._servers[server.id] = server.model_copy(deep=True)
        return server.model_copy(deep=True)

    async def get_server(
        self, tenant_id: UUID, server_id: UUID
    ) -> McpServerDefinition | None:
        server = self._servers.get(server_id)
        if server is None or server.tenant_id != tenant_id:
            return None
        return server.model_copy(deep=True)

    async def list_servers(
        self, tenant_id: UUID, *, limit: int, offset: int
    ) -> tuple[list[McpServerDefinition], int]:
        servers = [item for item in self._servers.values() if item.tenant_id == tenant_id]
        servers.sort(key=lambda item: (item.created_at, str(item.id)), reverse=True)
        page = [item.model_copy(deep=True) for item in servers[offset : offset + limit]]
        return page, len(servers)

    async def create_config(
        self,
        tenant_id: UUID,
        server_id: UUID,
        build: Callable[[int], McpConfigRevision],
    ) -> McpConfigRevision:
        async with self._lock:
            server = self._require(tenant_id, server_id)
            revisions = self._configs[server_id]
            revision = max(revisions, default=0) + 1
            built = build(revision)
            revisions[revision] = built.model_copy(deep=True)
            self._servers[server_id] = server.model_copy(update={"config_revision": revision})
        return built.model_copy(deep=True)

    async def get_config(
        self, tenant_id: UUID, server_id: UUID, revision: int
    ) -> McpConfigRevision | None:
        if self._servers.get(server_id) is None or (
            self._servers[server_id].tenant_id != tenant_id
        ):
            return None
        found = self._configs[server_id].get(revision)
        return None if found is None else found.model_copy(deep=True)

    async def current_config(
        self, tenant_id: UUID, server_id: UUID
    ) -> McpConfigRevision | None:
        server = await self.get_server(tenant_id, server_id)
        if server is None or server.config_revision == 0:
            return None
        return await self.get_config(tenant_id, server_id, server.config_revision)

    async def get_state(self, tenant_id: UUID, server_id: UUID) -> McpServerState | None:
        state = self._states.get(server_id)
        if state is None or state.tenant_id != tenant_id:
            return None
        return state.model_copy(deep=True)

    async def put_state(self, state: McpServerState) -> McpServerState:
        async with self._lock:
            self._require(state.tenant_id, state.server_id)
            self._states[state.server_id] = state.model_copy(deep=True)
        return state.model_copy(deep=True)

    async def create_snapshot(
        self,
        tenant_id: UUID,
        server_id: UUID,
        build: Callable[[int], CapabilitySnapshot],
    ) -> CapabilitySnapshot:
        async with self._lock:
            self._require(tenant_id, server_id)
            revisions = self._snapshots[server_id]
            revision = max(revisions, default=0) + 1
            built = build(revision)
            revisions[revision] = built.model_copy(deep=True)
        return built.model_copy(deep=True)

    async def get_snapshot(
        self, tenant_id: UUID, server_id: UUID, revision: int
    ) -> CapabilitySnapshot | None:
        server = self._servers.get(server_id)
        if server is None or server.tenant_id != tenant_id:
            return None
        found = self._snapshots[server_id].get(revision)
        return None if found is None else found.model_copy(deep=True)

    async def latest_snapshot(
        self, tenant_id: UUID, server_id: UUID
    ) -> CapabilitySnapshot | None:
        server = self._servers.get(server_id)
        if server is None or server.tenant_id != tenant_id:
            return None
        revisions = self._snapshots[server_id]
        if not revisions:
            return None
        return revisions[max(revisions)].model_copy(deep=True)

    def _require(self, tenant_id: UUID, server_id: UUID) -> McpServerDefinition:
        server = self._servers.get(server_id)
        if server is None or server.tenant_id != tenant_id:
            raise KeyError(server_id)
        return server
