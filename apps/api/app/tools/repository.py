"""Tool, draft, version and availability storage.

`publish` is the operation that has to be indivisible. It allocates a version
number, writes the immutable version, marks the draft spent and opens the
availability record — four writes that must all land or none. Split them and a
crash leaves a version nothing can reach, or an availability row pointing at a
version that was never written.

Allocating the number separately is the same bug one level down: read "highest
so far", then insert, and two publishes both read 2 and both write 3, the second
overwriting a contract someone is already bound to.

A PostgreSQL implementation needs one transaction around all of it, with the
tool row taken `FOR UPDATE` so the allocation and the inserts share a lock.
"""

import asyncio
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.tools.errors import ConcurrentPublishError
from app.tools.schemas import (
    DraftStatus,
    Tool,
    ToolDraft,
    ToolVersion,
    VersionAvailabilityRecord,
)


@dataclass(frozen=True, slots=True)
class PublishResult:
    version: ToolVersion
    availability: VersionAvailabilityRecord
    draft: ToolDraft


class ToolRepository(Protocol):
    async def add_tool(self, tool: Tool) -> Tool: ...

    async def get_tool(self, tenant_id: UUID, tool_id: UUID) -> Tool | None: ...

    async def list_tools(
        self, tenant_id: UUID, *, limit: int, offset: int
    ) -> tuple[list[Tool], int]: ...

    async def put_draft(self, draft: ToolDraft) -> ToolDraft: ...

    async def get_draft(self, tenant_id: UUID, draft_id: UUID) -> ToolDraft | None: ...

    async def publish(
        self,
        tenant_id: UUID,
        draft_id: UUID,
        expected_revision: int,
        build: Callable[[int, ToolDraft], PublishResult],
    ) -> PublishResult:
        """Re-read the draft, check it is still publishable, then commit.

        The draft is read *inside* the lock and compared against
        `expected_revision`; `build` is handed that fresh copy. A caller's own
        earlier read is not evidence — it is exactly what a concurrent publish
        invalidates. Raises `ConcurrentPublishError` when the check fails.
        """

    async def get_version(
        self, tenant_id: UUID, tool_id: UUID, version: int
    ) -> ToolVersion | None: ...

    async def list_versions(self, tenant_id: UUID, tool_id: UUID) -> list[ToolVersion]: ...

    async def all_versions(self, tenant_id: UUID) -> list[ToolVersion]: ...

    async def get_availability(
        self, tenant_id: UUID, tool_id: UUID, version: int
    ) -> VersionAvailabilityRecord | None: ...

    async def put_availability(
        self, record: VersionAvailabilityRecord
    ) -> VersionAvailabilityRecord: ...


class InMemoryToolRepository:
    """Development store with tenant isolation and copy-on-read semantics."""

    def __init__(self) -> None:
        self._tools: dict[UUID, Tool] = {}
        self._drafts: dict[UUID, ToolDraft] = {}
        self._versions: dict[UUID, dict[int, ToolVersion]] = defaultdict(dict)
        self._availability: dict[tuple[UUID, int], VersionAvailabilityRecord] = {}
        self._lock = asyncio.Lock()

    async def add_tool(self, tool: Tool) -> Tool:
        async with self._lock:
            if tool.id in self._tools:
                raise ValueError(f"duplicate tool id: {tool.id}")
            self._tools[tool.id] = tool.model_copy(deep=True)
        return tool.model_copy(deep=True)

    async def get_tool(self, tenant_id: UUID, tool_id: UUID) -> Tool | None:
        tool = self._tools.get(tool_id)
        if tool is None or tool.tenant_id != tenant_id:
            return None
        return tool.model_copy(deep=True)

    async def list_tools(
        self, tenant_id: UUID, *, limit: int, offset: int
    ) -> tuple[list[Tool], int]:
        tools = [tool for tool in self._tools.values() if tool.tenant_id == tenant_id]
        tools.sort(key=lambda tool: (tool.created_at, str(tool.id)), reverse=True)
        page = [tool.model_copy(deep=True) for tool in tools[offset : offset + limit]]
        return page, len(tools)

    async def put_draft(self, draft: ToolDraft) -> ToolDraft:
        async with self._lock:
            tool = self._tools.get(draft.tool_id)
            if tool is None or tool.tenant_id != draft.tenant_id:
                raise KeyError(draft.tool_id)
            self._drafts[draft.id] = draft.model_copy(deep=True)
        return draft.model_copy(deep=True)

    async def get_draft(self, tenant_id: UUID, draft_id: UUID) -> ToolDraft | None:
        draft = self._drafts.get(draft_id)
        if draft is None or draft.tenant_id != tenant_id:
            return None
        return draft.model_copy(deep=True)

    async def publish(
        self,
        tenant_id: UUID,
        draft_id: UUID,
        expected_revision: int,
        build: Callable[[int, ToolDraft], PublishResult],
    ) -> PublishResult:
        async with self._lock:
            draft = self._drafts.get(draft_id)
            if draft is None or draft.tenant_id != tenant_id:
                raise KeyError(draft_id)
            if (
                draft.status is not DraftStatus.VERIFIED
                or draft.revision != expected_revision
            ):
                raise ConcurrentPublishError(draft_id=str(draft_id))

            tool = self._tools.get(draft.tool_id)
            if tool is None or tool.tenant_id != tenant_id:
                raise KeyError(draft.tool_id)
            versions = self._versions[draft.tool_id]
            number = max(versions, default=0) + 1
            result = build(number, draft.model_copy(deep=True))

            versions[number] = result.version.model_copy(deep=True)
            self._availability[(draft.tool_id, number)] = result.availability.model_copy(
                deep=True
            )
            self._drafts[result.draft.id] = result.draft.model_copy(deep=True)
            self._tools[draft.tool_id] = tool.model_copy(update={"latest_version": number})
        return result

    async def get_version(
        self, tenant_id: UUID, tool_id: UUID, version: int
    ) -> ToolVersion | None:
        tool = self._tools.get(tool_id)
        if tool is None or tool.tenant_id != tenant_id:
            return None
        found = self._versions[tool_id].get(version)
        return None if found is None else found.model_copy(deep=True)

    async def list_versions(self, tenant_id: UUID, tool_id: UUID) -> list[ToolVersion]:
        tool = self._tools.get(tool_id)
        if tool is None or tool.tenant_id != tenant_id:
            return []
        return [item.model_copy(deep=True) for _, item in sorted(self._versions[tool_id].items())]

    async def all_versions(self, tenant_id: UUID) -> list[ToolVersion]:
        return [
            version.model_copy(deep=True)
            for tool_id, versions in self._versions.items()
            if (tool := self._tools.get(tool_id)) is not None and tool.tenant_id == tenant_id
            for version in versions.values()
        ]

    async def get_availability(
        self, tenant_id: UUID, tool_id: UUID, version: int
    ) -> VersionAvailabilityRecord | None:
        tool = self._tools.get(tool_id)
        if tool is None or tool.tenant_id != tenant_id:
            return None
        record = self._availability.get((tool_id, version))
        return None if record is None else record.model_copy(deep=True)

    async def put_availability(
        self, record: VersionAvailabilityRecord
    ) -> VersionAvailabilityRecord:
        async with self._lock:
            tool = self._tools.get(record.tool_id)
            if tool is None or tool.tenant_id != record.tenant_id:
                raise KeyError(record.tool_id)
            self._availability[(record.tool_id, record.version)] = record.model_copy(deep=True)
        return record.model_copy(deep=True)
