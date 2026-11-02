"""Pure tests for bounded lexical queries and reciprocal-rank fusion."""

from dataclasses import dataclass

import pytest

from app.rag.lexical import (
    MAX_QUERY_INPUT_CHARS,
    MAX_QUERY_TOKEN_CHARS,
    MAX_QUERY_TOKENS,
    build_lexical_query,
    reciprocal_rank_fusion,
)


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    dense_score: float
    metadata: dict[str, object]


def candidate(candidate_id: str, dense_score: float = 0.0, **metadata: object) -> Candidate:
    return Candidate(candidate_id, dense_score, metadata)


def test_engineering_identifiers_remain_plain_and_exact_after_case_normalization():
    query = build_lexical_query("CVE-2024-3094 IOS-XE 17.9.3 Gi0/1 10.0.0.1/24")
    assert query is not None
    assert query.tokens == ("cve-2024-3094", "ios-xe", "17.9.3", "gi0/1", "10.0.0.1/24")
    assert query.text == "cve-2024-3094 ios-xe 17.9.3 gi0/1 10.0.0.1/24"


def test_tsquery_punctuation_is_only_a_boundary_and_never_emitted_as_syntax():
    query = build_lexical_query("foo & bar | !baz:* (qux) <-> alpha::beta 'gamma'")
    assert query is not None
    assert query.tokens == ("foo", "bar", "baz", "qux", "alpha", "beta", "gamma")
    assert not any(symbol in query.text for symbol in "&|!:*()'<>")


def test_unicode_is_nfkc_normalized_casefolded_and_nul_safe():
    query = build_lexical_query("ＦＷ Cafe\u0301 STRASSE\x00Gi０/１")
    assert query is not None
    assert query.tokens == ("fw", "café", "strasse", "gi0/1")


def test_repeated_and_edge_identifier_punctuation_is_bounded():
    query = build_lexical_query("--edge-- foo..bar /inside/ end- a///b")
    assert query is not None
    assert query.tokens == ("edge", "foo", "bar", "inside", "end", "a", "b")


def test_duplicate_tokens_are_removed_in_first_seen_order():
    query = build_lexical_query("BGP bgp OSPF BGP ospf ISIS")
    assert query is not None
    assert query.tokens == ("bgp", "ospf", "isis")


@pytest.mark.parametrize("text", ["", " \t\r\n", "\x00", "& | ! :* () -- ... ///"])
def test_empty_or_punctuation_only_input_has_no_query(text):
    assert build_lexical_query(text) is None


def test_non_string_input_is_rejected():
    with pytest.raises(TypeError, match="text must be a string"):
        build_lexical_query(None)  # type: ignore[arg-type]


def test_pathological_input_is_bounded_by_input_token_count_and_token_length():
    long_token = "A" * (MAX_QUERY_INPUT_CHARS + 500)
    query = build_lexical_query(long_token)
    assert query is not None
    assert query.tokens == ("a" * MAX_QUERY_TOKEN_CHARS,)

    many = build_lexical_query(" ".join(f"token{index}" for index in range(MAX_QUERY_TOKENS + 50)))
    assert many is not None
    assert len(many.tokens) == MAX_QUERY_TOKENS
    assert many.tokens[-1] == f"token{MAX_QUERY_TOKENS - 1}"


def test_rrf_uses_ranks_not_candidate_scores_and_preserves_original_objects():
    high_dense = candidate("a", 0.99, source="dense", nested={"kept": True})
    low_dense = candidate("b", -100.0, source="dense")
    fused = reciprocal_rank_fusion([[low_dense, high_dense]], key=lambda item: item.candidate_id)
    assert [result.item.candidate_id for result in fused] == ["b", "a"]
    assert fused[0].item is low_dense
    assert fused[1].item is high_dense
    assert fused[1].item.dense_score == 0.99
    assert fused[1].item.metadata == {"source": "dense", "nested": {"kept": True}}


def test_rrf_duplicate_ids_across_legs_accumulate_and_retain_first_payload():
    dense_shared = candidate("shared", 0.91, leg="dense")
    lexical_shared = candidate("shared", 0.12, leg="lexical")
    dense_only = candidate("dense-only", 0.8)
    lexical_only = candidate("lexical-only", 0.7)
    fused = reciprocal_rank_fusion(
        [[dense_shared, dense_only], [lexical_only, lexical_shared]],
        key=lambda item: item.candidate_id,
    )
    assert [result.item.candidate_id for result in fused] == ["shared", "lexical-only", "dense-only"]
    assert fused[0].item is dense_shared
    assert fused[0].item.metadata == {"leg": "dense"}


def test_rrf_duplicate_id_within_one_leg_contributes_only_at_first_rank():
    first = candidate("same", 0.7, occurrence="first")
    duplicate = candidate("same", 0.1, occurrence="duplicate")
    other = candidate("other", 0.5)
    fused = reciprocal_rank_fusion([[first, duplicate, other]], key=lambda item: item.candidate_id, k=10)
    assert [result.item.candidate_id for result in fused] == ["same", "other"]
    assert fused[0].item is first
    assert fused[0].rrf_score == pytest.approx(1 / 11)
    assert fused[1].rrf_score == pytest.approx(1 / 13)


def test_rrf_equal_scores_have_stable_first_occurrence_ties():
    a = candidate("a")
    b = candidate("b")
    same_rank_prefers_earlier_rank = [candidate("dense"), candidate("target")]
    lexical_target = candidate("target")
    fused = reciprocal_rank_fusion(
        [same_rank_prefers_earlier_rank, [lexical_target]],
        key=lambda item: item.candidate_id,
    )
    assert [result.item.candidate_id for result in fused] == ["target", "dense"]

    rankings = [[a, b], [b, a]]
    expected = ["a", "b"]
    for _ in range(5):
        fused = reciprocal_rank_fusion(rankings, key=lambda item: item.candidate_id)
        assert [result.item.candidate_id for result in fused] == expected
        assert fused[0].rrf_score == fused[1].rrf_score


def test_rrf_candidate_and_output_bounds_are_enforced():
    values = [candidate(str(index)) for index in range(10)]
    fused = reciprocal_rank_fusion([values], key=lambda item: item.candidate_id, candidates_per_ranking=4, limit=2)
    assert [result.item.candidate_id for result in fused] == ["0", "1"]


@pytest.mark.parametrize(
    ("argument", "value"),
    [
        ("k", 0),
        ("k", -1),
        ("k", True),
        ("limit", 0),
        ("limit", -3),
        ("candidates_per_ranking", 0),
        ("candidates_per_ranking", False),
    ],
)
def test_rrf_rejects_non_positive_or_boolean_bounds(argument, value):
    kwargs = {argument: value}
    with pytest.raises(ValueError, match=f"{argument} must be a positive integer"):
        reciprocal_rank_fusion([], key=lambda item: item, **kwargs)


def test_rrf_empty_rankings_return_empty_tuple():
    assert reciprocal_rank_fusion([], key=lambda item: item) == ()
    assert reciprocal_rank_fusion([[]], key=lambda item: item) == ()
