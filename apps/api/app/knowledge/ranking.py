"""Side-effect free ranking primitives for hybrid knowledge retrieval."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Mapping, Protocol, Sequence, runtime_checkable

DEFAULT_RRF_RANK_CONSTANT = 60.0


@dataclass(frozen=True, slots=True)
class RankedChunk:
    """A fused result with enough detail to explain its score."""

    chunk_id: str
    fusion_score: float
    lexical_rank: int | None
    vector_rank: int | None
    lexical_contribution: float
    vector_contribution: float
    reranker_score: float | None = None


def reciprocal_rank_fusion(
    lexical_chunk_ids: Sequence[str],
    vector_chunk_ids: Sequence[str],
    *,
    top_k: int,
    rank_constant: float = DEFAULT_RRF_RANK_CONSTANT,
) -> list[RankedChunk]:
    """Fuse lexical and vector rankings using equal-weight RRF.

    A duplicate chunk id contributes only its first occurrence in each route.
    Unique positions are dense, so duplicate provider rows do not penalize later
    chunks. Equal fusion scores preserve the first appearance across the lexical
    route followed by the vector route, making ties deterministic without
    inventing a relevance signal.
    """

    _validate_top_k(top_k)
    _validate_rank_constant(rank_constant)
    lexical_ranks = _dense_unique_ranks(lexical_chunk_ids, route="lexical")
    vector_ranks = _dense_unique_ranks(vector_chunk_ids, route="vector")

    first_seen: dict[str, int] = {}
    for chunk_id in (*lexical_ranks, *vector_ranks):
        first_seen.setdefault(chunk_id, len(first_seen))

    fused: list[RankedChunk] = []
    for chunk_id in first_seen:
        lexical_rank = lexical_ranks.get(chunk_id)
        vector_rank = vector_ranks.get(chunk_id)
        lexical_contribution = _reciprocal_contribution(lexical_rank, rank_constant)
        vector_contribution = _reciprocal_contribution(vector_rank, rank_constant)
        fused.append(
            RankedChunk(
                chunk_id=chunk_id,
                fusion_score=lexical_contribution + vector_contribution,
                lexical_rank=lexical_rank,
                vector_rank=vector_rank,
                lexical_contribution=lexical_contribution,
                vector_contribution=vector_contribution,
            )
        )

    fused.sort(key=lambda item: (-item.fusion_score, first_seen[item.chunk_id]))
    return fused[:top_k]


@runtime_checkable
class Reranker(Protocol):
    async def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[RankedChunk],
        top_k: int,
    ) -> list[RankedChunk]: ...


class NoOpReranker:
    """Production-safe pass-through used when no reranking provider is enabled."""

    async def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[RankedChunk],
        top_k: int,
    ) -> list[RankedChunk]:
        del query
        _validate_top_k(top_k)
        return list(candidates[:top_k])


class DeterministicReranker:
    """Test double that applies explicit scores without external model calls.

    Explicitly scored chunks sort first by descending score. Ties and chunks
    absent from ``scores_by_chunk_id`` preserve their incoming order.
    """

    def __init__(self, scores_by_chunk_id: Mapping[str, float]) -> None:
        self._scores = {
            chunk_id: _validate_reranker_score(chunk_id, score)
            for chunk_id, score in scores_by_chunk_id.items()
        }

    async def rerank(
        self,
        *,
        query: str,
        candidates: Sequence[RankedChunk],
        top_k: int,
    ) -> list[RankedChunk]:
        del query
        _validate_top_k(top_k)
        annotated = [
            replace(candidate, reranker_score=self._scores.get(candidate.chunk_id))
            for candidate in candidates
        ]
        original_order = {id(candidate): position for position, candidate in enumerate(annotated)}
        annotated.sort(
            key=lambda candidate: (
                candidate.reranker_score is None,
                -(candidate.reranker_score or 0.0),
                original_order[id(candidate)],
            )
        )
        return annotated[:top_k]


def _dense_unique_ranks(chunk_ids: Sequence[str], *, route: str) -> dict[str, int]:
    ranks: dict[str, int] = {}
    for chunk_id in chunk_ids:
        if not isinstance(chunk_id, str) or not chunk_id.strip():
            raise ValueError(f"{route} chunk ids must be non-empty strings")
        if chunk_id not in ranks:
            ranks[chunk_id] = len(ranks) + 1
    return ranks


def _reciprocal_contribution(rank: int | None, rank_constant: float) -> float:
    return 0.0 if rank is None else 1.0 / (rank_constant + rank)


def _validate_top_k(top_k: int) -> None:
    if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1:
        raise ValueError("top_k must be a positive integer")


def _validate_rank_constant(rank_constant: float) -> None:
    if (
        isinstance(rank_constant, bool)
        or not isinstance(rank_constant, (int, float))
        or not math.isfinite(rank_constant)
        or rank_constant <= 0
    ):
        raise ValueError("rank_constant must be a finite positive number")


def _validate_reranker_score(chunk_id: str, score: float) -> float:
    if not isinstance(chunk_id, str) or not chunk_id.strip():
        raise ValueError("reranker score keys must be non-empty chunk ids")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
        raise ValueError(f"reranker score for {chunk_id!r} must be a finite number")
    return float(score)
