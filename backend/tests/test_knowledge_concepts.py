"""The concept graph's deterministic layer and its use in retrieval (synthetic text; *_test database)."""

from __future__ import annotations

import math

from sqlalchemy import func, select

from app.db.models import (
    KnowledgeConcept,
    KnowledgeConceptAlias,
    KnowledgePassage,
    KnowledgePassageConcept,
    KnowledgeRelation,
)
from app.knowledge import concepts
from app.rag.lexical import build_lexical_query
from app.rag.store import KnowledgeStore
from app.rag.types import AccessScope
from scripts import eval_concepts
from tests.test_knowledge_passages import DIMS, _book, _owner, _publish, _source, _stage


def test_the_gold_set_meets_the_precision_gate_with_verbatim_evidence():
    report = eval_concepts.evaluate()

    assert report["goldFacts"] >= 120 and report["negativeSentences"] >= 30
    assert report["precision"] >= eval_concepts.PRECISION_GATE
    assert report["evidenceErrors"] == []
    assert report["recall"] >= 0.65  # a regression floor, not the goal: precision over recall


def test_short_acronyms_match_only_in_capitals_and_the_longest_mention_wins():
    assert [m.concept for m in concepts.mentions("The AD of a static route")] == [
        "administrative_distance",
        "static_route",
    ]
    assert concepts.mentions("as an ad hoc fix") == []
    assert [m.concept for m in concepts.mentions("the backup designated router and the DR")] == ["bdr", "dr"]
    assert concepts.query_concepts("Which router runs OSPF over TCP?") == ("ospf",)  # generic words left out


def test_the_renamed_sd_wan_controllers_are_the_same_concepts():
    text = "The SD-WAN Controller runs OMP, the SD-WAN Validator onboards edges and the SD-WAN Manager holds policy."

    assert [m.concept for m in concepts.mentions(text)] == ["vsmart", "omp", "vbond", "vmanage"]
    assert "vsmart" in concepts.query_concepts("What does the vSmart controller do?")
    assert concepts.mentions("SD-WAN controllers use OMP")[0].concept == "sd_wan"  # the plural is the whole family
    generic = "vBond is an SD-WAN controller that orchestrates onboarding"  # lowercase: the role, not the product
    assert [m.concept for m in concepts.mentions(generic)] == ["vbond", "sd_wan"]
    assert not [f for f in concepts.facts(generic) if f.object == "vsmart"]


def test_a_book_defined_acronym_is_read_only_when_its_letters_fit():
    for sentence in (
        "routers run the Shortest Path First algorithm (SPFA) on every change",
        "Routers run the Shortest Path First algorithm (SPFA) on every change",  # the shortest fitting long form
    ):
        assert concepts.definitions(sentence)[0][:2] == ("SPFA", "Shortest Path First algorithm")
    assert concepts.definitions("Some Random Words Here (XYZ) are not a definition") == []


def _vector(*weights: float) -> list[float]:
    norm = math.sqrt(sum(w * w for w in weights))
    return [w / norm for w in weights] + [0.0] * (DIMS - len(weights))


async def test_indexing_records_mentions_facts_with_evidence_and_learned_aliases_once(app):
    book = await _book(app, await _source(app, await _owner(app)))
    stated = "OSPF uses IP protocol 89 and elects a DR on broadcast segments."
    defined = "Routers run the Shortest Path First algorithm (SPFA) to build the tree."
    await _stage(app, book, stated, defined)

    for _ in range(2):  # indexing again replaces, never duplicates
        async with app.state.db.sessionmaker() as db, db.begin():
            await concepts.index_revision(db, book.id)

    async with app.state.db.sessionmaker() as db:
        passage = await db.scalar(select(KnowledgePassage).where(KnowledgePassage.content == stated))
        mentioned = set(
            await db.scalars(
                select(KnowledgeConcept.key)
                .join(KnowledgePassageConcept, KnowledgePassageConcept.concept_id == KnowledgeConcept.id)
                .where(KnowledgePassageConcept.passage_id == passage.id)
            )
        )
        relation = await db.scalar(select(KnowledgeRelation).where(KnowledgeRelation.passage_id == passage.id))
        relations = await db.scalar(select(func.count()).select_from(KnowledgeRelation))
        learned = await db.scalar(select(KnowledgeConceptAlias).where(KnowledgeConceptAlias.alias == "spfa"))
        learned_key = await db.scalar(select(KnowledgeConcept.key).where(KnowledgeConcept.id == learned.concept_id))
    assert mentioned == {"ospf", "dr"}
    assert (relation.attribute, relation.value, relation.method) == ("ip_protocol", "89", "pattern")
    assert stated[relation.evidence_start : relation.evidence_end] == "OSPF uses IP protocol 89"
    assert relations == 1
    assert (learned_key, learned.origin) == ("spf", "learned")


async def test_the_concept_leg_finds_what_dense_ranking_misses_and_keyword_noise_is_turned_away(app):
    reader = await _owner(app)
    book = await _book(app, await _source(app, reader))
    ospf = "OSPF routers elect a DR on every broadcast segment."
    eigrp = "EIGRP keeps a feasible successor as a loop-free backup."
    noise = "Voters elect a council every four years."
    port = "Interface Gi1/0/24 shows input CRC errors after the cable change."
    vectors = {eigrp: _vector(1, 0), ospf: _vector(0.6, 0.8), noise: _vector(0, 0, 1), port: _vector(0, 0, 0, 1)}
    await _stage(app, book, eigrp, ospf, noise, port, vectors=vectors)
    async with app.state.db.sessionmaker() as db, db.begin():
        await concepts.index_revision(db, book.id)
    await _publish(app, book)
    store, scope = KnowledgeStore(app.state.db), AccessScope(user_id=reader)

    async def search(question: str):
        return await store.hybrid_search(
            scope,
            _vector(1, 0),
            build_lexical_query(question),
            embedding_model="test",
            k=5,
            dense_candidates=1,  # dense alone sees only its nearest passage
            lexical_candidates=5,
            rrf_k=60,
            min_score=0.5,
            concept_keys=concepts.query_concepts(question),
        )

    found = await search("How does OSPF elect a DR?")
    # OSPF is found by the lexical and concept legs (dense alone missed it) and ranks first: two legs agree.
    # The noise shares only "elect" and has cosine 0: turned away by the floor.
    assert [hit.content for hit in found.hits] == [ospf, eigrp]
    assert found.hits[0].score == 0.6 and found.timings["concept_reason"] == "ok"
    named = await search("Gi1/0/24 CRC errors")
    assert port in [hit.content for hit in named.hits]  # the exact identifier admits it below the floor
