"""Reusable idempotent-command storage for phase-one write APIs."""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol, TypeVar
from uuid import UUID

from app.core.errors import ConflictError

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class IdempotencyScope:
    tenant_id: UUID
    actor_id: UUID
    operation: str
    key: str


@dataclass(slots=True)
class _StoredResult:
    fingerprint: str
    value: Any


class IdempotencyStore(Protocol):
    async def execute(
        self,
        scope: IdempotencyScope,
        fingerprint: str,
        command: Callable[[], Awaitable[T]],
    ) -> T: ...


class InMemoryIdempotencyStore:
    """Serializes equal commands and replays their first successful result.

    The API is deliberately storage-neutral: a PostgreSQL adapter can later
    implement the same behavior with a unique constraint and transaction.
    Failed commands are never cached, so a caller may safely retry them.
    """

    def __init__(self) -> None:
        self._results: dict[IdempotencyScope, _StoredResult] = {}
        self._locks: dict[IdempotencyScope, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def execute(
        self,
        scope: IdempotencyScope,
        fingerprint: str,
        command: Callable[[], Awaitable[T]],
    ) -> T:
        lock = await self._lock_for(scope)
        async with lock:
            stored = self._results.get(scope)
            if stored is not None:
                if stored.fingerprint != fingerprint:
                    raise ConflictError(
                        "Idempotency-Key was already used with a different request"
                    )
                return deepcopy(stored.value)

            value = await command()
            self._results[scope] = _StoredResult(
                fingerprint=fingerprint,
                value=deepcopy(value),
            )
            return value

    async def _lock_for(self, scope: IdempotencyScope) -> asyncio.Lock:
        async with self._guard:
            return self._locks.setdefault(scope, asyncio.Lock())


def request_fingerprint(payload: Any) -> str:
    """Return a stable, non-reversible digest for a command payload."""

    normalized = _json_value(payload)
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        # Preserve native Decimal/datetime values until they can be reduced to
        # one semantic representation. JSON-mode dumps keep spelling and UTC
        # offsets, which makes equal retries hash differently.
        return _json_value(value.model_dump(mode="python", by_alias=True))
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized = [_json_value(item) for item in value]
        return sorted(normalized, key=_canonical_json)
    if isinstance(value, datetime):
        normalized = value.astimezone(UTC) if value.tzinfo is not None else value
        return {"datetime": normalized.isoformat()}
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("idempotency payload decimals must be finite")
        normalized = Decimal(0) if value == 0 else value.normalize()
        return {"decimal": format(normalized, "f")}
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, bytes):
        return {"sha256": hashlib.sha256(value).hexdigest(), "size": len(value)}
    return value


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
