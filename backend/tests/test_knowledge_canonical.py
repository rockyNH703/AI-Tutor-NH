"""Canonical passage identity (0025): the same text has one key however it was typeset; near-duplicates are near."""

from app.knowledge.canonical import content_key, hamming, normalize_text, simhash

OSPF = "OSPF is a link-state routing protocol that uses Dijkstra's algorithm to build its shortest-path tree."


def test_typesetting_differences_do_not_change_a_passage_identity():
    variants = [
        OSPF,
        "OSPF  is a link-state routing\nprotocol that uses Dijkstra's algorithm to build its shortest-path tree.",
        "ospf is a link-state routing proto-\ncol that uses Dijkstra's algorithm to build its shortest-path tree.",
        "OSPF is a link-state routing proto­col that uses Dijkstra's algorithm to build its shortest-path tree.",
        "ＯＳＰＦ is a link-state routing protocol that uses Dijkstra's algorithm to build its shortest-path tree.",
    ]
    assert {content_key(text) for text in variants} == {content_key(OSPF)}
    assert normalize_text(OSPF).startswith("ospf is a link-state")


def test_a_different_explanation_is_a_different_passage():
    other = "OSPF floods link-state advertisements so every router in an area holds the same database."
    assert content_key(other) != content_key(OSPF)


def test_a_revised_sentence_is_near_and_an_unrelated_one_is_far():
    revised = OSPF.replace("shortest-path tree", "shortest path tree")
    unrelated = "BGP selects a path by comparing attributes such as weight, local preference and AS path length."
    assert content_key(revised) != content_key(OSPF)
    assert hamming(simhash(OSPF), simhash(revised)) < hamming(simhash(OSPF), simhash(unrelated))
    assert hamming(simhash(OSPF), simhash(OSPF)) == 0


def test_the_simhash_fits_a_postgresql_bigint():
    for text in (OSPF, "", "a", "x " * 500):
        value = simhash(text)
        assert -(1 << 63) <= value < (1 << 63)
