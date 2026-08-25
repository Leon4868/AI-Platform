"""Deterministic, side-effect free behaviour for every workflow node type.

A handler derives its output from the node definition and the values flowing
into it and nothing else, so the same graph plus the same input always produces
the same outputs. No handler performs I/O: the runtime never reaches a model
provider, a retriever or a tool endpoint. Replacing a handler with a real
adapter is the single extension point a production runtime needs.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from app.workflows.schemas import NodeType, WorkflowNode

MOCK_PROVIDER = "mock"
MAX_RETRIEVED_CHUNKS = 10


class UnsupportedNodeTypeError(Exception):
    def __init__(self, node_type: NodeType) -> None:
        super().__init__(f"No runtime handler is registered for node type '{node_type}'")
        self.node_type = node_type


@dataclass(slots=True, frozen=True)
class NodeContext:
    node: WorkflowNode
    run_input: dict[str, Any]
    upstream: dict[str, dict[str, Any]]
    outgoing_labels: tuple[str | None, ...] = ()

    @property
    def config(self) -> dict[str, Any]:
        return self.node.config

    @property
    def seed(self) -> str:
        """Stable hex digest of everything this node can observe."""
        material = "\x1f".join(
            [
                self.node.id,
                self.node.type,
                _canonical(self.node.config),
                _canonical(self.upstream),
                _canonical(self.run_input),
            ]
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


def evaluate(context: NodeContext) -> dict[str, Any]:
    handler = _HANDLERS.get(context.node.type)
    if handler is None:
        raise UnsupportedNodeTypeError(context.node.type)
    return handler(context)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


def _text(value: Any, fallback: str = "") -> str:
    return value if isinstance(value, str) else fallback


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _start(context: NodeContext) -> dict[str, Any]:
    return {"input": context.run_input}


def _model(context: NodeContext) -> dict[str, Any]:
    prompt = _text(context.config.get("prompt"))
    content = f"[{context.node.id}] {context.seed[:32]}"
    return {
        "provider": MOCK_PROVIDER,
        "model": _text(context.config.get("model"), "mock-model"),
        "prompt": prompt,
        "content": content,
        "finish_reason": "stop",
        "usage": {
            "input_tokens": len(prompt) // 4 + 1,
            "output_tokens": len(content) // 4 + 1,
        },
    }


def _knowledge_retrieval(context: NodeContext) -> dict[str, Any]:
    raw_top_k = context.config.get("top_k", 3)
    top_k = raw_top_k if isinstance(raw_top_k, int) and not isinstance(raw_top_k, bool) else 3
    top_k = max(1, min(top_k, MAX_RETRIEVED_CHUNKS))
    seed = context.seed
    chunks = [
        {
            "chunk_id": str(uuid5(NAMESPACE_URL, f"chunk:{seed}:{index}")),
            "score": round(0.9 - index * 0.05, 4),
            "content": f"[{context.node.id}#{index}] {seed[index * 4 : index * 4 + 16]}",
        }
        for index in range(top_k)
    ]
    return {
        "provider": MOCK_PROVIDER,
        "query": _text(context.config.get("query")),
        "knowledge_base_ids": _string_list(context.config.get("knowledge_base_ids")),
        "chunks": chunks,
    }


def _tool(context: NodeContext) -> dict[str, Any]:
    arguments = context.config.get("arguments")
    return {
        "provider": MOCK_PROVIDER,
        "tool": _text(context.config.get("tool"), context.node.id),
        "arguments": arguments if isinstance(arguments, dict) else {},
        "result": {"status": "ok", "digest": context.seed[:32]},
    }


def _approval(context: NodeContext) -> dict[str, Any]:
    """Payload of the approval request. The decision is merged in by the
    executor once a human answers."""
    return {
        "prompt": _text(context.config.get("prompt"), context.node.name),
        "approvers": _string_list(context.config.get("approvers")),
    }


def _condition(context: NodeContext) -> dict[str, Any]:
    labels = context.outgoing_labels
    requested = context.config.get("branch")
    branch = requested if requested in labels else (labels[0] if labels else None)
    return {"branch": branch, "candidates": list(labels)}


def _parallel(context: NodeContext) -> dict[str, Any]:
    return {"branches": list(context.outgoing_labels)}


def _asset_commit(context: NodeContext) -> dict[str, Any]:
    seed = context.seed
    return {
        "provider": MOCK_PROVIDER,
        "asset_id": str(uuid5(NAMESPACE_URL, f"asset:{seed}")),
        "name": _text(context.config.get("name"), context.node.name),
        "checksum": seed,
    }


def _end(context: NodeContext) -> dict[str, Any]:
    return {"result": context.upstream}


_HANDLERS: dict[NodeType, Callable[[NodeContext], dict[str, Any]]] = {
    NodeType.START: _start,
    NodeType.MODEL: _model,
    NodeType.KNOWLEDGE_RETRIEVAL: _knowledge_retrieval,
    NodeType.TOOL: _tool,
    NodeType.APPROVAL: _approval,
    NodeType.CONDITION: _condition,
    NodeType.PARALLEL: _parallel,
    NodeType.ASSET_COMMIT: _asset_commit,
    NodeType.END: _end,
}
