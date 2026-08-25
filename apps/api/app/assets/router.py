from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request, Response

from app.assets.schemas import AssetView
from app.assets.policy import can_read_resource
from app.core.errors import NotFoundError
from app.core.storage import InMemoryObjectStorage
from app.identity.dependencies import require
from app.identity.schemas import Permission, Principal

router = APIRouter(prefix="/assets", tags=["assets"])


@router.get("/downloads/{token}", include_in_schema=False)
async def download_development_asset(token: str, request: Request) -> Response:
    """Capability URL for local in-memory storage; S3 uses provider-signed URLs."""
    storage = request.app.state.container.object_storage
    if not isinstance(storage, InMemoryObjectStorage):
        raise NotFoundError("download", token)
    try:
        content, content_type = await storage.resolve_download_token(token)
    except KeyError as exc:
        raise NotFoundError("download", token) from exc
    return Response(
        content=content,
        media_type=content_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/{assetId}", response_model=AssetView, response_model_exclude_none=True)
async def get_asset(
    asset_id: Annotated[UUID, Path(alias="assetId")],
    principal: Annotated[Principal, Depends(require(Permission.ASSET_READ))],
    request: Request,
) -> AssetView:
    asset = await request.app.state.container.asset_service.get(principal.tenant_id, asset_id)
    if not can_read_resource(
        principal,
        creator_id=asset.creator_id,
        owner_department_id=asset.owner_department_id,
        project_id=asset.project_id,
        data_scope=asset.data_scope,
        security_level=asset.security_level,
    ):
        raise NotFoundError("asset", str(asset_id))
    view = AssetView.of(asset)
    if asset.storage_uri:
        view = view.model_copy(
            update={
                "storage_uri": await request.app.state.container.object_storage.create_download_url(
                    asset.storage_uri
                )
            }
        )
    return view
