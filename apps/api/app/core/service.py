from typing import Generic, TypeVar
from uuid import UUID

from app.core.errors import NotFoundError
from app.core.repository import Repository
from app.core.schemas import Entity

TEntity = TypeVar("TEntity", bound=Entity)


class ReadService(Generic[TEntity]):
    def __init__(self, repository: Repository[TEntity], resource_name: str) -> None:
        self._repository = repository
        self._resource_name = resource_name

    async def get(self, tenant_id: UUID, entity_id: UUID) -> TEntity:
        entity = await self._repository.get(tenant_id, entity_id)
        if entity is None:
            raise NotFoundError(self._resource_name, str(entity_id))
        return entity

    async def list(self, tenant_id: UUID, *, limit: int, offset: int) -> tuple[list[TEntity], int]:
        return await self._repository.list(tenant_id, limit=limit, offset=offset)
