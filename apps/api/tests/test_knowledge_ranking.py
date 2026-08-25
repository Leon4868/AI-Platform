import pytest

from app.knowledge.ranking import (
    DeterministicReranker,
    NoOpReranker,
    RankedChunk,
    Reranker,
    reciprocal_rank_fusion,
)


def test_rrf_combines_routes_and_exposes_score_contributions() -> None:
    results = reciprocal_rank_fusion(
        ["lexical-only", "shared", "tail"],
        ["shared", "vector-only", "tail"],
        top_k=4,
        rank_constant=60,
    )

    assert [item.chunk_id for item in results] == [
        "shared",
        "tail",
        "lexical-only",
        "vector-only",
    ]
    shared = results[0]
    assert shared.lexical_rank == 2
    assert shared.vector_rank == 1
    assert shared.lexical_contribution == pytest.approx(1 / 62)
    assert shared.vector_contribution == pytest.approx(1 / 61)
    assert shared.fusion_score == pytest.approx(1 / 62 + 1 / 61)


@pytest.mark.parametrize(
    ("lexical", "vector", "expected"),
    [
        (["a", "b"], [], ["a", "b"]),
        ([], ["v1", "v2"], ["v1", "v2"]),
        ([], [], []),
    ],
)
def test_rrf_handles_one_or_both_missing_routes(
    lexical: list[str],
    vector: list[str],
    expected: list[str],
) -> None:
    results = reciprocal_rank_fusion(lexical, vector, top_k=5)

    assert [item.chunk_id for item in results] == expected
    if lexical and not vector:
        assert all(item.vector_rank is None and item.vector_contribution == 0 for item in results)
    if vector and not lexical:
        assert all(item.lexical_rank is None and item.lexical_contribution == 0 for item in results)


def test_rrf_deduplicates_each_route_with_dense_first_occurrence_ranks() -> None:
    results = reciprocal_rank_fusion(
        ["a", "a", "b", "a"],
        ["b", "b", "c"],
        top_k=3,
        rank_constant=10,
    )

    assert [item.chunk_id for item in results] == ["b", "a", "c"]
    by_id = {item.chunk_id: item for item in results}
    assert by_id["a"].lexical_rank == 1
    assert by_id["b"].lexical_rank == 2
    assert by_id["b"].vector_rank == 1
    assert by_id["c"].vector_rank == 2


def test_rrf_has_a_stable_first_seen_tie_break() -> None:
    first = reciprocal_rank_fusion(["lex"], ["vec"], top_k=2)
    second = reciprocal_rank_fusion(["lex"], ["vec"], top_k=2)

    assert first == second
    assert first[0].fusion_score == pytest.approx(first[1].fusion_score)
    assert [item.chunk_id for item in first] == ["lex", "vec"]


@pytest.mark.parametrize("top_k", [0, -1, True, 1.5, "2"])
def test_rrf_rejects_invalid_top_k(top_k: object) -> None:
    with pytest.raises(ValueError, match="top_k"):
        reciprocal_rank_fusion(["a"], ["b"], top_k=top_k)  # type: ignore[arg-type]


@pytest.mark.parametrize("rank_constant", [0, -1, True, float("inf"), float("nan")])
def test_rrf_rejects_invalid_rank_constant(rank_constant: float) -> None:
    with pytest.raises(ValueError, match="rank_constant"):
        reciprocal_rank_fusion(["a"], [], top_k=1, rank_constant=rank_constant)


@pytest.mark.parametrize("route", [([""], []), ([], ["   "])])
def test_rrf_rejects_blank_chunk_ids(route: tuple[list[str], list[str]]) -> None:
    with pytest.raises(ValueError, match="chunk ids"):
        reciprocal_rank_fusion(*route, top_k=1)


@pytest.mark.asyncio
async def test_noop_reranker_preserves_order_and_honours_top_k() -> None:
    candidates = reciprocal_rank_fusion(["a", "b", "c"], [], top_k=3)
    reranker = NoOpReranker()

    assert isinstance(reranker, Reranker)
    reranked = await reranker.rerank(query="policy", candidates=candidates, top_k=2)
    assert reranked == candidates[:2]
    assert reranked is not candidates


@pytest.mark.asyncio
async def test_deterministic_reranker_uses_explicit_scores_and_stable_ties() -> None:
    candidates = reciprocal_rank_fusion(["a", "b", "c", "d"], [], top_k=4)
    reranker = DeterministicReranker({"c": 0.9, "b": 0.9, "d": 0.2})

    assert isinstance(reranker, Reranker)
    reranked = await reranker.rerank(query="ignored by test double", candidates=candidates, top_k=4)

    assert [item.chunk_id for item in reranked] == ["b", "c", "d", "a"]
    assert [item.reranker_score for item in reranked] == [0.9, 0.9, 0.2, None]
    assert reranked[0].fusion_score == candidates[1].fusion_score


@pytest.mark.parametrize("score", [True, float("inf"), float("nan")])
def test_deterministic_reranker_rejects_non_finite_scores(score: float) -> None:
    with pytest.raises(ValueError, match="finite number"):
        DeterministicReranker({"chunk": score})


@pytest.mark.asyncio
async def test_rerankers_validate_top_k() -> None:
    candidate = RankedChunk("a", 1.0, 1, None, 1.0, 0.0)
    with pytest.raises(ValueError, match="top_k"):
        await NoOpReranker().rerank(query="q", candidates=[candidate], top_k=0)
