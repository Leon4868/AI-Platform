from dataclasses import dataclass
from typing import Any, Protocol

import httpx


@dataclass(frozen=True, slots=True)
class JsonHttpResponse:
    status_code: int
    headers: dict[str, str]
    payload: Any


class ModelHttpTransportError(RuntimeError):
    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class ModelHttpTransport(Protocol):
    async def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> JsonHttpResponse: ...


class HttpxModelHttpTransport:
    """Small HTTP boundary; response bodies never appear in raised errors."""

    async def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_seconds: float,
    ) -> JsonHttpResponse:
        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise ModelHttpTransportError("timeout") from exc
        except httpx.RequestError as exc:
            raise ModelHttpTransportError("network") from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise ModelHttpTransportError("invalid_json") from exc
        return JsonHttpResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            payload=body,
        )
