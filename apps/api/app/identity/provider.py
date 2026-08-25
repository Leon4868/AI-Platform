from typing import Protocol

from fastapi import Request

from app.identity.schemas import Principal


class IdentityProvider(Protocol):
    async def authenticate(self, request: Request) -> Principal: ...
