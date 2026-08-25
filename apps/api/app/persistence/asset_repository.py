from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Delete, Select

from app.assets.schemas import Asset
from app.core.tables import AssetRecord
from app.persistence.mappers import apply_asset, asset_from_record, asset_to_record


def select_asset(tenant_id: UUID, entity_id: UUID) -> Select:
    return select(AssetRecord).where(
        AssetRecord.tenant_id == tenant_id,
        AssetRecord.id == entity_id,
    )


def list_assets(tenant_id: UUID, *, limit: int, offset: int) -> Select:
    return (
        select(AssetRecord)
        .where(AssetRecord.tenant_id == tenant_id)
        .order_by(AssetRecord.created_at.desc(), AssetRecord.id.desc())
        .limit(limit)
        .offset(offset)
    )


def delete_asset(tenant_id: UUID, entity_id: UUID) -> Delete:
    return delete(AssetRecord).where(
        AssetRecord.tenant_id == tenant_id,
        AssetRecord.id == entity_id,
    )


class SQLAlchemyAssetRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = session_factory

    async def add(self, entity: Asset) -> Asset:
        async with self._sessions() as session:
            record = asset_to_record(entity)
            session.add(record)
            await session.commit()
            await session.refresh(record)
            return asset_from_record(record)

    async def get(self, tenant_id: UUID, entity_id: UUID) -> Asset | None:
        async with self._sessions() as session:
            record = (await session.execute(select_asset(tenant_id, entity_id))).scalar_one_or_none()
            return None if record is None else asset_from_record(record)

    async def list(
        self,
        tenant_id: UUID,
        *,
        limit: int,
        offset: int,
    ) -> tuple[list[Asset], int]:
        async with self._sessions() as session:
            count = await session.scalar(
                select(func.count()).select_from(AssetRecord).where(AssetRecord.tenant_id == tenant_id)
            )
            rows = (await session.scalars(list_assets(tenant_id, limit=limit, offset=offset))).all()
            return [asset_from_record(row) for row in rows], int(count or 0)

    async def update(self, entity: Asset) -> Asset:
        async with self._sessions() as session:
            record = (
                await session.execute(select_asset(entity.tenant_id, entity.id))
            ).scalar_one_or_none()
            if record is None:
                raise KeyError(entity.id)
            apply_asset(record, entity)
            await session.commit()
            await session.refresh(record)
            return asset_from_record(record)

    async def delete(self, tenant_id: UUID, entity_id: UUID) -> bool:
        async with self._sessions() as session:
            result = await session.execute(delete_asset(tenant_id, entity_id))
            await session.commit()
            return bool(result.rowcount)
