from collections.abc import Callable

from fastapi import Depends, Request

from app.core.errors import AuthorizationError
from app.identity.schemas import Permission, Principal


async def get_principal(request: Request) -> Principal:
    return await request.app.state.container.identity_provider.authenticate(request)


def require(permission: Permission) -> Callable[..., Principal]:
    async def dependency(principal: Principal = Depends(get_principal)) -> Principal:
        if not principal.has(permission):
            raise AuthorizationError()
        return principal

    return dependency
