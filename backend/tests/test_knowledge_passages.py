"""Canonical passages (0025): a text stored and indexed once, its provenance, duplicate reviews, publication and
retrieval. Synthetic text only; PostgreSQL + pgvector in the isolated *_test database."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import delete, func, select, update

from app.chat.assistant import course_for_goal
from app.db.models import (
    Document,
    DocumentFile,
    DocumentRevision,
    KnowledgeAdminGrant,
    KnowledgeCourse,
    KnowledgeDuplicateReview,
    KnowledgeEdition,
    KnowledgePassage,
    KnowledgePassageEmbedding,
    KnowledgePassageOccurrence,
    KnowledgePassageSource,
    KnowledgeSource,
    Message,
    User,
)
from app.errors import Conflict
from app.knowledge import passages, processing
from app.knowledge.canonical import content_key
from app.knowledge.protocol import ExtractedDocument
from app.knowledge.publication import build_chunks
from app.knowledge.worker import RevisionClaim
from app.rag.embeddings import KeywordHashEmbeddings
from app.rag.lexical import LexicalQuery
from app.rag.retriever import PgVectorRetriever, named_courses
from app.rag.store import KnowledgeStore
from app.rag.types import AccessScope
from scripts import knowledge_admin

from .conftest import register

DIMS = 1536
OSPF = "OSPF elects a designated router and a backup designated router on every broadcast multi-access segment."
EIGRP = "EIGRP runs the DUAL algorithm and keeps a feasible successor as a loop-free backup path to each prefix."


def _vector(n: int) -> list[float]:
    vector = [0.0] * DIMS
    vector[n] = 1.0
    return vector


def _pages(*texts: str, pages: list[int] | None = None) -> ExtractedDocument:
    """One element per text, each on its own page unless `pages` says which page holds it."""
    on_page = pages or [index + 1 for index in range(len(texts))]
    elements = tuple(
        {
            "sequence": index,
            "element_type": "paragraph",
            "content": content,
            "physical_page": on_page[index],
            "printed_page_label": str(on_page[index]),
            "section_path": ["Chapter 1 Routing"],
            "metadata": {},
        }
        for index, content in enumerate(texts)
    )
    return ExtractedDocument(
        document_sha256="0" * 64,
        page_count=len(texts),
        parser="synthetic/3",
        elements=elements,
        warnings=(),
        content_sha256="1" * 64,
        document_metadata={"isbn13": ["9780135792735"], "edition_number": 2, "volume": "1"},
    )


def _book_pages(count: int) -> list[str]:
    return [
        f"{OSPF} Section {n} covers hello and dead timers, router priority and area {n} adjacency."
        for n in range(count)
    ]


async def _owner(app) -> uuid.UUID:
    async with app.state.db.sessionmaker() as db, db.begin():
        user = User(email=f"passages-{uuid.uuid4().hex}@example.com", email_verified=True)
        db.add(user)
        await db.flush()
        return user.id


async def _source(app, owner_id: uuid.UUID, *, egress: bool = True) -> uuid.UUID:
    approval = (
        {
            "embed_egress_approved_at": datetime.now(UTC),
            "embed_egress_basis": "focused regression test",
            "embed_egress_approved_by_user_id": owner_id,
        }
        if egress
        else {}
    )
    async with app.state.db.sessionmaker() as db, db.begin():
        source = KnowledgeSource(name=f"passages-{uuid.uuid4().hex}", owner_user_id=owner_id, **approval)
        db.add(source)
        await db.flush()
        return source.id


async def _book(
    app,
    source_id: uuid.UUID,
    *,
    status: str = "ready_to_publish",
    attempts: int = 0,
    document_id: uuid.UUID | None = None,
    file_id: uuid.UUID | None = None,
    revision_number: int = 1,
) -> DocumentRevision:
    digest = uuid.uuid4().hex * 2
    async with app.state.db.sessionmaker() as db, db.begin():
        if document_id is None:
            document = Document(
                source_id=source_id,
                uri=f"knowledge-file:{uuid.uuid4()}",
                title="CCNA 200-301 Official Cert Guide Volume 1",
                content_hash=digest,
                ingest_signature=digest,
            )
            db.add(document)
            await db.flush()
            document_id = document.id
        if file_id is None:
            file = DocumentFile(
                source_id=source_id, filename="book.pdf", size_bytes=1, received_bytes=1, sha256=digest, status="ready"
            )
            db.add(file)
            await db.flush()
            file_id = file.id
        revision = DocumentRevision(
            document_id=document_id,
            source_id=source_id,
            file_id=file_id,
            revision_number=revision_number,
            status=status,
            processing_signature=uuid.uuid4().hex * 2,
            parser_name="test",
            parser_version="1",
            parser_config_hash=digest,
            chunking_config_hash=digest,
            embedding_model="test",
            embedding_dimensions=DIMS,
            attempts=attempts,
        )
        db.add(revision)
        await db.flush()
        return revision


async def _stage(
    app,
    revision: DocumentRevision,
    *texts: str,
    vectors: dict[str, list[float]] | None = None,
    pages: list[int] | None = None,
) -> None:
    """Stage, fingerprint and embed a synthetic book the way the worker does (vectors per text, default by order)."""
    extracted = _pages(*texts, pages=pages)
    async with app.state.db.sessionmaker() as db, db.begin():
        await passages.stage_passages(db, revision, build_chunks(extracted))
        await passages.fingerprint_file(db, revision.file_id, passages.pages_of(extracted.elements))
        missing = await passages.missing_embeddings(db, revision.id, "test")
        await passages.store_embeddings(
            db, [(pid, (vectors or {}).get(text, _vector(1 + n))) for n, (pid, text) in enumerate(missing)], "test"
        )


async def _publish(app, revision: DocumentRevision) -> int:
    async with app.state.db.sessionmaker() as db, db.begin():
        owner = (await db.get(KnowledgeSource, revision.source_id)).owner_user_id
        return await passages.publish(db, revision.id, owner)


async def _count(app, statement) -> int:
    async with app.state.db.sessionmaker() as db:
        return int(await db.scalar(statement))


def _passage_rows(text: str):
    return select(func.count()).select_from(KnowledgePassage).where(KnowledgePassage.content_key == content_key(text))


# ----------------------------------------------------------------------------- stored and indexed once
async def test_one_text_in_three_books_of_two_sources_is_stored_and_embedded_once(app):
    owner = await _owner(app)
    first, second = await _source(app, owner), await _source(app, owner)
    books = [await _book(app, first), await _book(app, first), await _book(app, second)]
    for n, book in enumerate(books):
        await _stage(app, book, OSPF, f"Unique page {n}: static routes need a next hop or an exit interface.")

    assert await _count(app, _passage_rows(OSPF)) == 1
    async with app.state.db.sessionmaker() as db:
        passage_id = await db.scalar(
            select(KnowledgePassage.id).where(KnowledgePassage.content_key == content_key(OSPF))
        )
        vectors = await db.scalar(
            select(func.count())
            .select_from(KnowledgePassageEmbedding)
            .where(KnowledgePassageEmbedding.passage_id == passage_id)
        )
        occurrences = await db.scalar(
            select(func.count())
            .select_from(KnowledgePassageOccurrence)
            .where(KnowledgePassageOccurrence.passage_id == passage_id)
        )
    assert (vectors, occurrences) == (1, 3)


async def test_a_second_book_only_embeds_the_text_the_library_does_not_have(app):
    owner = await _owner(app)
    source = await _source(app, owner)
    first, second = await _book(app, source), await _book(app, source)
    await _stage(app, first, OSPF)
    async with app.state.db.sessionmaker() as db, db.begin():
        await passages.stage_passages(db, second, build_chunks(_pages(OSPF, EIGRP)))
        missing = await passages.missing_embeddings(db, second.id, "test")
    assert [text for _, text in missing] == [EIGRP]


async def test_parallel_books_with_the_same_text_converge_on_one_passage(app):
    owner = await _owner(app)
    books = [await _book(app, await _source(app, owner)) for _ in range(12)]

    async def stage(book):
        async with app.state.db.sessionmaker() as db, db.begin():
            await passages.stage_passages(db, book, build_chunks(_pages(OSPF, EIGRP)))

    await asyncio.gather(*(stage(book) for book in books))

    assert await _count(app, _passage_rows(OSPF)) == 1
    assert await _count(app, _passage_rows(EIGRP)) == 1
    assert await _count(app, select(func.count()).select_from(KnowledgePassageOccurrence)) == 24


# ----------------------------------------------------------------------------- duplicates and publication
async def test_a_renamed_copy_in_another_source_waits_for_review_before_publication(app):
    owner = await _owner(app)
    original = await _book(app, await _source(app, owner))
    await _stage(app, original, *_book_pages(5))
    assert await _publish(app, original) == 5

    copy = await _book(app, await _source(app, owner))  # other bytes (renamed, metadata changed), same pages
    await _stage(app, copy, *_book_pages(5))
    assert await _publish(app, copy) == 0  # held, and the review it found is kept for the operator

    async with app.state.db.sessionmaker() as db, db.begin():
        held = await db.get(DocumentRevision, copy.id)
        assert held.status == "needs_review" and "duplicate_review_pending" in held.quality_report["warnings"]
        review = await db.scalar(
            select(KnowledgeDuplicateReview).where(KnowledgeDuplicateReview.file_id == copy.file_id)
        )
        assert (review.classification, review.decision) == ("confirmed_content", "pending")
        review.decision = "keep_separate"
        held.status = "ready_to_publish"  # what `approve` does
    assert await _publish(app, copy) == 5  # the decision stands when the evidence is refreshed
    assert await _count(app, select(func.count()).select_from(KnowledgePassage)) == 5  # still once each


async def test_refreshing_a_reviews_evidence_keeps_who_decided_it(app):
    owner = await _owner(app)
    original = await _book(app, await _source(app, owner))
    await _stage(app, original, *_book_pages(5))
    await _publish(app, original)
    copy = await _book(app, await _source(app, owner))
    await _stage(app, copy, *_book_pages(5))
    assert await _publish(app, copy) == 0
    async with app.state.db.sessionmaker() as db, db.begin():
        review = await db.scalar(
            select(KnowledgeDuplicateReview).where(KnowledgeDuplicateReview.file_id == copy.file_id)
        )
        review.decision = "keep_separate"
        review.signals = {**review.signals, "decidedBy": "operator root"}  # what `decide --operator` records
        (await db.get(DocumentRevision, copy.id)).status = "ready_to_publish"

    assert await _publish(app, copy) == 5  # publication re-classifies the copy under its lock

    async with app.state.db.sessionmaker() as db:
        review = await db.scalar(
            select(KnowledgeDuplicateReview).where(KnowledgeDuplicateReview.file_id == copy.file_id)
        )
    assert review.signals["decidedBy"] == "operator root"
    assert review.signals["sharedPages"] == 5  # the evidence itself is refreshed


def test_pages_classify_volumes_editions_and_copies_without_deleting_anything():
    keys = [f"{n:064x}" for n in range(20)]
    assert passages.classify_pages(keys[:10], keys, False)[0] == "subset"
    assert passages.classify_pages(keys, keys[:10], False)[0] == "superset"
    assert passages.classify_pages(keys[:10], keys[:10], False)[0] == "confirmed_content"
    assert passages.classify_pages(keys[:10], keys[:10], True)[0] == "exact"
    edition = keys[:5] + [f"{n:064x}" for n in range(100, 105)]
    assert passages.classify_pages(keys[:10], edition, False)[0] == "different_edition"
    assert passages.classify_pages(keys[:10], keys[10:20], False)[0] == "inconclusive"


def test_editions_and_volumes_of_one_book_share_a_work_key():
    assert passages.work_key("CCNA 200-301 Official Cert Guide, Volume 1") == passages.work_key(
        "CCNA 200-301 Official Cert Guide Volume 2"
    )
    assert passages.work_key("CCNP ENCOR Second Edition") == passages.work_key("CCNP ENCOR 3rd Edition")


async def test_a_new_revision_retires_the_old_one_and_withdraws_text_it_dropped(app):
    owner = await _owner(app)
    source = await _source(app, owner)
    old = await _book(app, source)
    await _stage(app, old, OSPF, EIGRP)
    await _publish(app, old)
    new = await _book(app, source, document_id=old.document_id, file_id=old.file_id, revision_number=2)
    await _stage(app, new, OSPF)
    await _publish(app, new)

    async with app.state.db.sessionmaker() as db:
        assert (await db.get(DocumentRevision, old.id)).status == "retired"
        live = set(
            await db.scalars(
                select(KnowledgePassage.content)
                .join(KnowledgePassageSource, KnowledgePassageSource.passage_id == KnowledgePassage.id)
                .where(KnowledgePassageSource.source_id == source)
            )
        )
    assert live == {OSPF}


async def test_revoked_rights_refuse_publication(app):
    owner = await _owner(app)
    source = await _source(app, owner)
    book = await _book(app, source)
    await _stage(app, book, OSPF)
    async with app.state.db.sessionmaker() as db, db.begin():
        await db.execute(
            update(KnowledgeSource).where(KnowledgeSource.id == source).values(rights_revoked_at=datetime.now(UTC))
        )
    with pytest.raises(Conflict) as refused:
        await _publish(app, book)
    assert refused.value.code == "rights_not_active"


async def test_deleting_a_book_purges_only_the_text_no_other_book_carries(app):
    owner = await _owner(app)
    kept, removed = await _book(app, await _source(app, owner)), await _book(app, await _source(app, owner))
    await _stage(app, kept, OSPF)
    await _stage(app, removed, OSPF, EIGRP)
    async with app.state.db.sessionmaker() as db, db.begin():
        await db.execute(delete(Document).where(Document.id == removed.document_id))
        assert await passages.purge_orphans(db) == 1

    assert await _count(app, _passage_rows(OSPF)) == 1
    assert await _count(app, _passage_rows(EIGRP)) == 0
    assert await _count(app, select(func.count()).select_from(KnowledgePassageEmbedding)) == 1


# ----------------------------------------------------------------------------- the worker
class _Counting(KeywordHashEmbeddings):
    def __init__(self):
        super().__init__(DIMS, model="test")
        self.texts: list[str] = []

    async def aembed_documents(self, texts):
        self.texts.extend(texts)
        return await super().aembed_documents(texts)


async def test_the_worker_reuses_vectors_and_holds_a_second_copy_for_review(app, settings, monkeypatch):
    owner = await _owner(app)
    pages = _book_pages(4)

    async def extracted(*_args):
        return _pages(*pages)

    monkeypatch.setattr(processing, "extract_revision", extracted)
    first = await _book(app, await _source(app, owner), status="extracting", attempts=1)
    embeddings = _Counting()
    claim = RevisionClaim(first.id, 1)
    assert await processing.process_revision(app.state.db, settings, embeddings, claim) == "ready_to_publish"
    assert len(embeddings.texts) == 4

    second = await _book(app, await _source(app, owner), status="extracting", attempts=1)
    again = _Counting()
    claim = RevisionClaim(second.id, 1)
    assert await processing.process_revision(app.state.db, settings, again, claim) == "needs_review"
    assert again.texts == []  # every passage already had a vector for this model
    async with app.state.db.sessionmaker() as db:
        stored = await db.get(DocumentRevision, second.id)
        edition = await db.get(KnowledgeEdition, second.document_id)
    assert stored.quality_report["reusedVectors"] == stored.quality_report["passages"] == 4
    assert "duplicate_review_pending" in stored.quality_report["warnings"]
    assert (edition.isbn13, edition.edition_number, edition.volume) == (["9780135792735"], 2, "1")


# ----------------------------------------------------------------------------- retrieval
async def test_search_returns_a_live_passage_with_its_book_page_and_section_only_to_who_may_read_it(app):
    reader, other = await _owner(app), await _owner(app)
    mine, theirs = await _source(app, reader), await _source(app, other)
    book = await _book(app, mine)
    await _stage(app, book, OSPF, vectors={OSPF: _vector(1)})
    await _publish(app, book)
    foreign = await _book(app, theirs)
    await _stage(app, foreign, EIGRP, vectors={EIGRP: _vector(2)})
    await _publish(app, foreign)
    store, scope = KnowledgeStore(app.state.db), AccessScope(user_id=reader)

    hits = await store.search(scope, _vector(2), embedding_model="test", k=5)

    assert [hit.content for hit in hits] == [OSPF]  # the other owner's nearer passage is never a candidate
    hit = hits[0]
    assert (hit.document_id, hit.source_id) == (book.document_id, mine)
    assert hit.metadata["revision_id"] == str(book.id)
    assert (hit.metadata["physical_page"], hit.metadata["printed_page_label"]) == (1, "1")
    assert hit.metadata["section_path"] == ["Chapter 1 Routing"]
    assert await store.has_chunks(scope, "test")

    lexical = await store.hybrid_search(
        scope,
        _vector(1),
        LexicalQuery(("designated", "router")),
        embedding_model="test",
        k=5,
        dense_candidates=5,
        lexical_candidates=5,
        rrf_k=60,
    )
    assert lexical.timings["lexical_reason"] == "ok"
    assert [hit.content for hit in lexical.hits] == [OSPF]

    async with app.state.db.sessionmaker() as db, db.begin():
        await db.execute(
            update(KnowledgeSource).where(KnowledgeSource.id == mine).values(rights_revoked_at=datetime.now(UTC))
        )
    assert await store.search(scope, _vector(1), embedding_model="test", k=5) == []
    assert not await store.has_chunks(scope, "test")


async def test_hybrid_search_keeps_every_relevant_passage_of_a_page(app):
    """Two passages of one printed page both come back (the prompt then gives the page one block and one source)."""
    reader = await _owner(app)
    book = await _book(app, await _source(app, reader))
    first = " ".join([OSPF] + ["Hello packets carry the router priority on the segment."] * 15)
    second = " ".join([OSPF] + ["The designated router listens on the AllDRouters address."] * 15)
    await _stage(
        app,
        book,
        first,
        second,
        EIGRP,
        pages=[1, 1, 2],
        vectors={first: _vector(1), second: _vector(1), EIGRP: _vector(1)},
    )
    await _publish(app, book)

    result = await KnowledgeStore(app.state.db).hybrid_search(
        AccessScope(user_id=reader),
        _vector(1),
        LexicalQuery(("designated", "router")),
        embedding_model="test",
        k=5,
        dense_candidates=5,
        lexical_candidates=5,
        rrf_k=60,
    )

    assert sorted(hit.metadata["physical_page"] for hit in result.hits) == [1, 1, 2]


async def test_a_staged_book_is_not_searchable_until_it_is_published(app):
    reader = await _owner(app)
    book = await _book(app, await _source(app, reader))
    await _stage(app, book, OSPF, vectors={OSPF: _vector(1)})

    store = KnowledgeStore(app.state.db)
    assert await store.search(AccessScope(user_id=reader), _vector(1), embedding_model="test", k=5) == []


# ----------------------------------------------------------------------------- operator CLI and egress
async def _admin(app) -> tuple[uuid.UUID, str]:
    email = f"library-admin-{uuid.uuid4().hex}@example.com"
    async with app.state.db.sessionmaker() as db, db.begin():
        user = User(email=email, email_verified=True)
        db.add(user)
        await db.flush()
        db.add(KnowledgeAdminGrant(user_id=user.id, granted_by_operator="test", reason="focused regression test"))
        return user.id, email


async def _cli(app, *argv: str) -> int:
    args = knowledge_admin._parser().parse_args([*argv, "--yes"])
    async with app.state.db.sessionmaker() as db:
        if args.action in knowledge_admin.LIBRARY_ACTIONS:
            return await knowledge_admin._library(db, args)
        return await knowledge_admin._revision(db, args)


async def _source_row(app, source_id: uuid.UUID) -> KnowledgeSource:
    async with app.state.db.sessionmaker() as db:
        return await db.get(KnowledgeSource, source_id)


async def test_courses_and_egress_are_set_by_an_administrator_on_their_own_sources_only(app):
    admin_id, admin = await _admin(app)
    _, intruder = await _admin(app)
    source = await _source(app, admin_id, egress=False)

    # Courses are the shared library's boundaries for every user: only the host operator sets them.
    assert await _cli(app, "course", "ccna", "--name", "CCNA", "--builds-on", "ccnp", "--as", admin) == 2
    assert await _cli(app, "course", "ccna", "--name", "CCNA", "--level", "1", "--operator") == 0
    assert await _cli(app, "course", "ccnp", "--name", "CCNP", "--level", "2", "--builds-on", "ccna", "--operator") == 0
    assert await _cli(app, "course", "ccie", "--name", "x", "--builds-on", "ccie", "--operator") == 1  # not itself
    assert await _cli(app, "assign", str(source), "--course", "nope", "--as", admin) == 2
    assert await _cli(app, "assign", str(source), "--course", "ccnp", "--as", intruder) == 2
    assert await _cli(app, "assign", str(source), "--course", "ccnp", "--as", admin) == 0
    assert (await _source_row(app, source)).course == "ccnp"

    assert await _cli(app, "egress", str(source), "--kind", "embed", "--as", admin) == 2  # the basis is required
    assert await _cli(app, "egress", str(source), "--kind", "embed", "--reason", "owner approved", "--as", admin) == 0
    row = await _source_row(app, source)
    assert (row.embed_egress_basis, row.embed_egress_approved_by_user_id) == ("owner approved", admin_id)
    assert row.extract_egress_approved_at is None
    assert await _cli(app, "egress", str(source), "--kind", "embed", "--withdraw", "--as", admin) == 0
    assert (await _source_row(app, source)).embed_egress_approved_at is None


async def test_no_book_text_is_queued_or_embedded_without_the_owners_egress_approval(app, settings):
    admin_id, admin = await _admin(app)
    source = await _source(app, admin_id, egress=False)
    async with app.state.db.sessionmaker() as db, db.begin():
        db.add(
            DocumentFile(
                source_id=source, filename="b.pdf", size_bytes=1, received_bytes=1, sha256="e" * 64, status="ready"
            )
        )
    embeddings = KeywordHashEmbeddings(DIMS, model="test")

    assert await processing.queue_ready_files(app.state.db, settings, embeddings) == 0
    assert await _cli(app, "egress", str(source), "--kind", "embed", "--reason", "owner approved", "--as", admin) == 0
    assert await processing.queue_ready_files(app.state.db, settings, embeddings) == 1

    embedding = await _book(app, source, status="embedding", attempts=1)
    assert await _cli(app, "egress", str(source), "--kind", "embed", "--withdraw", "--as", admin) == 0
    counting = _Counting()
    with pytest.raises(RuntimeError, match="egress_not_approved"):
        await processing._embed_batch(app.state.db, RevisionClaim(embedding.id, 1), counting, ["text"])
    assert counting.texts == []


async def test_the_operator_reviews_a_copy_and_a_withheld_copy_stays_unpublished(app, capsys):
    admin_id, admin = await _admin(app)
    original = await _book(app, await _source(app, admin_id))
    await _stage(app, original, *_book_pages(4))
    assert await _cli(app, "publish", str(original.id), "--as", admin) == 0
    copy = await _book(app, await _source(app, admin_id))
    await _stage(app, copy, *_book_pages(4))

    assert await _cli(app, "publish", str(copy.id), "--as", admin) == 1  # held
    capsys.readouterr()
    assert await _cli(app, "duplicates", "--as", admin) == 0
    listing = capsys.readouterr().out
    assert "confirmed_content" in listing and "decision=pending" in listing and OSPF not in listing
    review_id = listing.split()[0]

    assert await _cli(app, "decide", review_id, "--decision", "reject", "--as", admin) == 0
    assert await _cli(app, "approve", str(copy.id), "--as", admin) == 0
    assert await _cli(app, "publish", str(copy.id), "--as", admin) == 1
    assert "duplicate_withheld" in capsys.readouterr().err
    async with app.state.db.sessionmaker() as db:
        assert (await db.get(DocumentRevision, copy.id)).status == "ready_to_publish"


async def test_deleting_a_source_purges_its_text_and_keeps_text_another_book_carries(app):
    admin_id, admin = await _admin(app)
    kept_source, removed_source = await _source(app, admin_id), await _source(app, admin_id)
    kept, removed = await _book(app, kept_source), await _book(app, removed_source)
    await _stage(app, kept, OSPF)
    await _stage(app, removed, OSPF, EIGRP)
    await _publish(app, removed)

    assert await _cli(app, "delete-source", str(removed_source), "--as", admin) == 2  # a reason is required
    assert await _cli(app, "delete-source", str(removed_source), "--reason", "test", "--as", admin) == 0

    assert await _source_row(app, removed_source) is None
    assert await _count(app, _passage_rows(OSPF)) == 1
    assert await _count(app, _passage_rows(EIGRP)) == 0
    assert await _count(app, select(func.count()).select_from(KnowledgePassageSource)) == 0


# ----------------------------------------------------------------------------- course boundaries
BGP = "BGP compares weight, local preference, AS path length and origin before the MED to choose the best path."


async def _courses(app) -> None:
    async with app.state.db.sessionmaker() as db, db.begin():
        db.add(KnowledgeCourse(slug="ccna", name="CCNA", level=1))
        await db.flush()
        db.add(KnowledgeCourse(slug="ccnp", name="CCNP", level=2, builds_on="ccna"))


async def _published(app, owner: uuid.UUID, course: str | None, text: str, n: int) -> uuid.UUID:
    source = await _source(app, owner)
    async with app.state.db.sessionmaker() as db, db.begin():
        await db.execute(update(KnowledgeSource).where(KnowledgeSource.id == source).values(course=course))
    book = await _book(app, source)
    await _stage(app, book, text, vectors={text: _vector(n)})
    await _publish(app, book)
    return source


class _Fixed(KeywordHashEmbeddings):
    def __init__(self, vector: list[float]):
        super().__init__(DIMS, model="test")
        self.vector = vector

    async def aembed_query(self, text):
        return self.vector


async def test_a_course_turn_sees_its_course_and_general_material_never_the_other_course(app):
    reader = await _owner(app)
    await _courses(app)
    await _published(app, reader, "ccna", OSPF, 1)
    await _published(app, reader, "ccnp", EIGRP, 2)
    await _published(app, reader, None, BGP, 3)
    store, scope = KnowledgeStore(app.state.db), AccessScope(user_id=reader)

    async def seen(courses):
        hits = await store.search(scope, _vector(1), embedding_model="test", k=10, courses=courses)
        return {hit.content: hit.metadata.get("course") for hit in hits}

    assert await seen(("ccna",)) == {OSPF: "ccna", BGP: None}
    assert await seen(("ccnp",)) == {EIGRP: "ccnp", BGP: None}
    assert await seen(None) == {OSPF: "ccna", EIGRP: "ccnp", BGP: None}


async def test_ccnp_draws_on_its_ccna_foundation_only_when_short_and_labels_it(app):
    reader = await _owner(app)
    await _courses(app)
    await _published(app, reader, "ccna", OSPF, 1)
    await _published(app, reader, "ccnp", EIGRP, 2)
    await _published(app, reader, None, BGP, 3)

    def retriever(k):
        return PgVectorRetriever(
            store=KnowledgeStore(app.state.db),
            embeddings=_Fixed(_vector(2)),
            scope=AccessScope(user_id=reader),
            k=k,
            courses=["ccnp"],
        )

    short = await retriever(3).ainvoke("hello and dead timers")
    assert [doc.page_content for doc in short] == [EIGRP, BGP, OSPF]  # primary first, the foundation fills in
    assert short[2].metadata["attributes"]["course_role"] == "foundation"
    assert "course_role" not in short[0].metadata["attributes"]
    enough = await retriever(2).ainvoke("hello and dead timers")
    assert [doc.page_content for doc in enough] == [EIGRP, BGP]
    named = await retriever(3).ainvoke("For the CCNA exam: hello and dead timers")
    assert {doc.page_content for doc in named} == {OSPF, BGP}  # the question's course wins over the profile


def test_a_course_named_in_the_question_and_the_profile_goal_set_the_boundary():
    assert named_courses("For CCNP ENCOR, how does OSPF elect a DR?") == ("ccnp",)
    assert named_courses("ccie lab tips") == ("ccnp",)
    assert named_courses("CCNA vs CCNP depth") == ("ccna", "ccnp")
    assert named_courses("how does OSPF elect a DR?") == ()
    assert course_for_goal("CCNA") == ("ccna",)
    assert course_for_goal("CCNP Security") == course_for_goal("CCIE Enterprise Infrastructure") == ("ccnp",)
    assert course_for_goal("AWS Solutions Architect – Associate") == ("aws",)
    assert course_for_goal("Microsoft Azure Administrator (AZ-104)") == ("azure",)
    assert course_for_goal("EC-Council CPENT") == ("cpent",)
    assert course_for_goal("EC-Council CEH") == ("ceh",)
    assert course_for_goal("Palo Alto Networks PCNSE") == ("paloalto",)
    assert course_for_goal("Fortinet Certified Professional (FCP)") == ("fortigate",)
    assert course_for_goal("CompTIA Network+") == course_for_goal(None) == ()


# ----------------------------------------------------------------------------- citations after revocation
async def test_a_cited_excerpt_is_withheld_once_its_source_is_revoked_or_its_book_deleted(client, app):
    account = await register(client, app)
    owner = uuid.UUID(account["user"]["id"])
    book = await _book(app, await _source(app, owner))
    created = await client.post("/v1/conversations", json={}, headers=account["headers"])
    conversation_id = uuid.UUID(created.json()["id"])
    citation = {"ref": "1", "kind": "source", "title": "Synthetic guide", "documentId": str(book.document_id)}
    async with app.state.db.sessionmaker() as db, db.begin():
        db.add(
            Message(
                conversation_id=conversation_id,
                role="assistant",
                content="Answer [1]",
                metadata_={"citations": [{**citation, "excerpt": OSPF}]},
            )
        )

    async def shown():
        page = await client.get(f"/v1/conversations/{conversation_id}/messages", headers=account["headers"])
        assert page.status_code == 200
        return page.json()["items"][-1]["metadata"]["citations"][0]

    assert (await shown())["excerpt"] == OSPF
    async with app.state.db.sessionmaker() as db, db.begin():
        await db.execute(
            update(KnowledgeSource)
            .where(KnowledgeSource.id == book.source_id)
            .values(rights_revoked_at=datetime.now(UTC))
        )
    assert await shown() == {**citation, "withdrawn": True}  # the reference stays, the book's text does not
    async with app.state.db.sessionmaker() as db, db.begin():
        await db.execute(
            update(KnowledgeSource).where(KnowledgeSource.id == book.source_id).values(rights_revoked_at=None)
        )
        await db.execute(delete(Document).where(Document.id == book.document_id))
    assert "excerpt" not in await shown()
    async with app.state.db.sessionmaker() as db:
        stored = await db.scalar(select(Message).where(Message.conversation_id == conversation_id))
    assert stored.metadata_["citations"][0]["excerpt"] == OSPF  # nothing was deleted from the reply itself
