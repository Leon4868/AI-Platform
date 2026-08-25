import asyncio
from uuid import uuid4

import pytest

from app.core.errors import ConflictError
from app.core.idempotency import IdempotencyScope, InMemoryIdempotencyStore, request_fingerprint


def scope() -> IdempotencyScope:
    return IdempotencyScope(uuid4(), uuid4(), "knowledge.create", "same-command-001")


def test_replays_the_first_successful_result() -> None:
    async def scenario() -> None:
        store = InMemoryIdempotencyStore()
        command_scope = scope()
        calls = 0

        async def command() -> dict[str, list[str]]:
            nonlocal calls
            calls += 1
            return {"items": ["created"]}

        fingerprint = request_fingerprint({"name": "制度库"})
        first = await store.execute(command_scope, fingerprint, command)
        first["items"].append("caller-mutated")
        replayed = await store.execute(command_scope, fingerprint, command)

        assert replayed == {"items": ["created"]}
        assert calls == 1

    asyncio.run(scenario())


def test_rejects_reusing_a_key_for_a_different_request() -> None:
    async def scenario() -> None:
        store = InMemoryIdempotencyStore()
        command_scope = scope()

        async def command() -> str:
            return "created"

        await store.execute(command_scope, request_fingerprint({"name": "A"}), command)
        with pytest.raises(ConflictError):
            await store.execute(command_scope, request_fingerprint({"name": "B"}), command)

    asyncio.run(scenario())


def test_concurrent_retries_execute_once_and_failures_are_not_cached() -> None:
    async def scenario() -> None:
        store = InMemoryIdempotencyStore()
        command_scope = scope()
        calls = 0

        async def successful() -> int:
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return calls

        fingerprint = request_fingerprint({"title": "周报"})
        assert await asyncio.gather(
            store.execute(command_scope, fingerprint, successful),
            store.execute(command_scope, fingerprint, successful),
        ) == [1, 1]
        assert calls == 1

        retry_scope = scope()
        attempts = 0

        async def flaky() -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("temporary")
            return "recovered"

        with pytest.raises(RuntimeError):
            await store.execute(retry_scope, fingerprint, flaky)
        assert await store.execute(retry_scope, fingerprint, flaky) == "recovered"

    asyncio.run(scenario())


def test_fingerprint_is_stable_and_does_not_embed_binary_content() -> None:
    left = request_fingerprint({"data": b"enterprise secret", "values": {"b": 2, "a": 1}})
    right = request_fingerprint({"values": {"a": 1, "b": 2}, "data": b"enterprise secret"})

    assert left == right
    assert "enterprise secret" not in left
    assert len(left) == 64
