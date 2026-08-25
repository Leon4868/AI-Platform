import asyncio
from copy import deepcopy
from typing import Generic, Protocol, TypeVar
from uuid import UUID

from app.core.schemas import Entity

TEntity = TypeVar("TEntity", bound=Entity)


class Repository(Protocol[TEntity]):
    async def add(self, entity: TEntity) -> TEntity: ...

    async def get(self, tenant_id: UUID, entity_id: UUID) -> TEntity | None: ...

    async def list(self, tenant_id: UUID, *, limit: int, offset: int) -> tuple[list[TEntity], int]: ...

    async def update(self, entity: TEntity) -> TEntity: ...

    async def delete(self, tenant_id: UUID, entity_id: UUID) -> bool: ...


class InMemoryRepository(Generic[TEntity]):
    """Development repository with tenant isolation and copy-on-read semantics."""

    def __init__(self) -> None:
        self._items: dict[UUID, TEntity] = {}
        self._lock = asyncio.Lock()

    async def add(self, entity: TEntity) -> TEntity:
        async with self._lock:
            if entity.id in self._items:
                raise ValueError(f"duplicate entity id: {entity.id}")
            self._items[entity.id] = deepcopy(entity)
        return deepcopy(entity)

    async def get(self, tenant_id: UUID, entity_id: UUID) -> TEntity | None:
        entity = self._items.get(entity_id)
        if entity is None or entity.tenant_id != tenant_id:
            return None
        return deepcopy(entity)

    async def list(self, tenant_id: UUID, *, limit: int, offset: int) -> tuple[list[TEntity], int]:
        items = [item for item in self._items.values() if item.tenant_id == tenant_id]
        items.sort(key=lambda item: (item.created_at, str(item.id)), reverse=True)
        return deepcopy(items[offset : offset + limit]), len(items)

    async def update(self, entity: TEntity) -> TEntity:
        async with self._lock:
            current = self._items.get(entity.id)
            if current is None or current.tenant_id != entity.tenant_id:
                raise KeyError(entity.id)
            self._items[entity.id] = deepcopy(entity)
        return deepcopy(entity)

    async def delete(self, tenant_id: UUID, entity_id: UUID) -> bool:
        async with self._lock:
            current = self._items.get(entity_id)
            if current is None or current.tenant_id != tenant_id:
                return False
            del self._items[entity_id]
            return True
