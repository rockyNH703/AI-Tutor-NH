"""The shared Knowledge Library is administered by the host operator, not by any user (synthetic data; *_test DB).

Global sources have no owner; the operator (root on the host, recorded by name like administrator grants) creates
them with their distribution-rights basis, sets their course and egress, imports their PDFs from host paths,
approves and publishes. The operator never reaches a user's private source, and every user can search what it
publishes.
"""

from __future__ import annotations

import hashlib
import importlib.util
import uuid
from pathlib import Path

from sqlalchemy import func, select

from app.db.models import DocumentFile, DocumentFileBlob, DocumentRevision, KnowledgeAdminGrant, KnowledgeSource, User
from app.knowledge import processing
from app.rag.embeddings import KeywordHashEmbeddings
from app.rag.store import KnowledgeStore
from app.rag.types import AccessScope
from scripts import knowledge_admin
from tests.test_knowledge_passages import DIMS, OSPF, _book, _owner, _source, _stage, _vector

_SPEC = importlib.util.spec_from_file_location(
    "knowledge_fixture_generator", Path(__file__).parent / "fixtures" / "knowledge" / "generate.py"
)
_GENERATOR = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_GENERATOR)
BASIS = "Owner confirmed distribution rights to all NetSec AI users (focused regression test)"


async def _cli(app, *argv: str) -> int:
    args = knowledge_admin._parser().parse_args([*argv, "--yes"])
    async with app.state.db.sessionmaker() as db:
        return await knowledge_admin._dispatch(db, args)


async def _shared(app, name: str = "Shared CCNA library", course: str | None = "ccna") -> KnowledgeSource:
    if course:
        await _cli(app, "course", course, "--name", course.upper(), "--operator")
    extra = ["--course", course] if course else []
    assert await _cli(app, "source", name, "--operator", "--reason", BASIS, *extra) == 0
    async with app.state.db.sessionmaker() as db:
        return await db.scalar(select(KnowledgeSource).where(KnowledgeSource.name == name))


def _pdf(path: Path, title: str) -> bytes:
    data = _GENERATOR._digital_pdf([[(54, 744, 14, title), (54, 700, 10, OSPF)]])
    path.write_bytes(data)
    return data


async def test_the_operator_runs_the_shared_library_without_any_user_owning_it(app, settings, tmp_path):
    assert await _cli(app, "source", "No basis", "--operator") == 2  # a global source needs its rights basis
    source = await _shared(app)
    assert (source.owner_user_id, source.visibility, source.rights_status, source.course) == (
        None,
        "global",
        "approved",
        "ccna",
    )
    assert BASIS in source.rights_basis and "operator" in source.rights_basis

    assert await _cli(app, "egress", str(source.id), "--kind", "embed", "--reason", "owner approved", "--operator") == 0
    data = _pdf(tmp_path / "CCNA Volume 1.pdf", "Synthetic CCNA Volume 1")
    (tmp_path / "notes.pdf").write_bytes(b"not a pdf at all")
    pdf = str(tmp_path / "CCNA Volume 1.pdf")
    assert await _cli(app, "import", str(source.id), "--pdf", pdf, "--operator") == 0
    assert await _cli(app, "import", str(source.id), "--pdf", pdf, "--operator") == 0  # the same book: kept once
    assert await _cli(app, "import", str(source.id), "--pdf", str(tmp_path / "notes.pdf"), "--operator") == 1

    async with app.state.db.sessionmaker() as db:
        files = list(await db.scalars(select(DocumentFile).where(DocumentFile.source_id == source.id)))
        assert len(files) == 1
        [file] = files
        blobs = b"".join(
            await db.scalars(
                select(DocumentFileBlob.data)
                .where(DocumentFileBlob.file_id == file.id)
                .order_by(DocumentFileBlob.ordinal)
            )
        )
        row = await db.get(KnowledgeSource, source.id)
    assert (file.status, file.created_by_user_id, file.filename) == ("ready", None, "CCNA Volume 1.pdf")
    assert blobs == data and file.sha256 == hashlib.sha256(data).hexdigest()
    assert row.embed_egress_approved_by_user_id is None
    assert row.embed_egress_basis.startswith("owner approved") and "operator" in row.embed_egress_basis
    assert await processing.queue_ready_files(app.state.db, settings, KeywordHashEmbeddings(DIMS, model="test")) == 1


async def test_the_operator_never_reaches_a_users_private_source(app, tmp_path):
    owner = await _owner(app)
    private = await _source(app, owner)
    pdf = tmp_path / "private.pdf"
    _pdf(pdf, "Someone's private notes")

    assert await _cli(app, "assign", str(private), "--course", "none", "--operator") == 2
    assert await _cli(app, "egress", str(private), "--kind", "embed", "--reason", "x", "--operator") == 2
    assert await _cli(app, "import", str(private), "--pdf", str(pdf), "--operator") == 2
    assert await _cli(app, "delete-source", str(private), "--reason", "x", "--operator") == 2
    book = await _book(app, private)
    assert await _cli(app, "publish", str(book.id), "--operator") == 2
    async with app.state.db.sessionmaker() as db:
        files = await db.scalar(select(func.count()).select_from(DocumentFile).where(DocumentFile.source_id == private))
        assert files == 1  # only the revision's own file; nothing imported
        assert (await db.get(DocumentRevision, book.id)).status == "ready_to_publish"


async def test_a_shared_book_the_operator_publishes_is_searchable_by_every_user(app):
    source = await _shared(app)
    book = await _book(app, source.id, status="needs_review")
    await _stage(app, book, OSPF, vectors={OSPF: _vector(1)})

    assert await _cli(app, "publish", str(book.id), "--operator") == 1  # not before review
    assert await _cli(app, "approve", str(book.id), "--operator") == 0
    assert await _cli(app, "publish", str(book.id), "--operator") == 0
    async with app.state.db.sessionmaker() as db:
        published = await db.get(DocumentRevision, book.id)
    assert (published.status, published.published_by_user_id) == ("published", None)

    store = KnowledgeStore(app.state.db)
    for scope in (AccessScope(user_id=await _owner(app)), AccessScope(user_id=await _owner(app)), AccessScope(None)):
        hits = await store.search(scope, _vector(1), embedding_model="test", k=5, courses=("ccna",))
        assert [hit.content for hit in hits] == [OSPF]
        assert hits[0].metadata["course"] == "ccna"
    assert await store.search(AccessScope(None), _vector(1), embedding_model="test", k=5, courses=("ccnp",)) == []


async def test_only_the_operator_imports_from_host_paths(app, tmp_path):
    source = await _shared(app, course=None)
    pdf = tmp_path / "book.pdf"
    _pdf(pdf, "Synthetic book")
    email = f"library-admin-{uuid.uuid4().hex}@example.com"
    async with app.state.db.sessionmaker() as db, db.begin():
        user = User(email=email, email_verified=True)
        db.add(user)
        await db.flush()
        db.add(KnowledgeAdminGrant(user_id=user.id, granted_by_operator="test", reason="focused regression test"))

    assert await _cli(app, "import", str(source.id), "--pdf", str(pdf), "--as", email) == 2
    assert await _cli(app, "assign", str(source.id), "--course", "none", "--as", email) == 2  # not theirs either
