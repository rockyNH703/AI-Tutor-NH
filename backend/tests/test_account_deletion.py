"""Deleting an account removes everything that belongs to it — proven, not assumed.

The tables are discovered from the database itself (every foreign key that points at
users.id), so a table added later is covered without anyone remembering to extend this
test. Rows keyed by the email address rather than the user id are checked by address.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.models import FileChunk, UserFile
from app.files import processing
from app.notifications.outbox import WEEKLY_SUMMARY, queue_email
from app.rag.errors import EmbeddingError
from tests.conftest import PASSWORD, SpyEmbeddings, image_bytes, ingest_docs, register, sign_in, stream_chat

USER_REFERENCES = text(
    """
    SELECT kcu.table_name, kcu.column_name
    FROM information_schema.referential_constraints rc
    JOIN information_schema.key_column_usage kcu
      ON kcu.constraint_name = rc.constraint_name AND kcu.constraint_schema = rc.constraint_schema
    JOIN information_schema.key_column_usage ref
      ON ref.constraint_name = rc.unique_constraint_name AND ref.constraint_schema = rc.unique_constraint_schema
    WHERE ref.table_name = 'users' AND ref.column_name = 'id' AND kcu.table_schema = 'public'
    ORDER BY 1, 2
    """
)


async def _rows_referencing(database: str, user_id: str, email: str) -> dict[str, int]:
    engine = create_async_engine(database)
    try:
        async with engine.connect() as conn:
            references = (await conn.execute(USER_REFERENCES)).all()
            counts = {}
            for table, column in references:
                counts[f"{table}.{column}"] = await conn.scalar(
                    text(f'SELECT count(*) FROM "{table}" WHERE "{column}" = :id'), {"id": uuid.UUID(user_id)}
                )
            counts["users"] = await conn.scalar(text("SELECT count(*) FROM users WHERE id = :id"), {"id": user_id})
            counts["auth_verifications (by address)"] = await conn.scalar(
                text("SELECT count(*) FROM auth_verifications WHERE identifier LIKE '%-otp-' || :email"),
                {"email": email.lower()},
            )
            counts["email_outbox (by address)"] = await conn.scalar(
                text("SELECT count(*) FROM email_outbox WHERE to_address = :email"), {"email": email}
            )
            return counts
    finally:
        await engine.dispose()


async def _knowledge_library_rows(app, user: uuid.UUID) -> None:
    """The user as every Knowledge Library actor: admin, owner, grantee, uploader, issuer, reviewer, publisher."""
    statements = [
        "INSERT INTO knowledge_admin_grants (user_id, granted_by_user_id, reason, revoked_by_user_id) "
        "VALUES (:u, :u, 'test', :u)",
        "INSERT INTO knowledge_sources (id, name, owner_user_id, rights_approved_by_user_id, "
        "embed_egress_approved_at, embed_egress_basis, embed_egress_approved_by_user_id, "
        "extract_egress_approved_at, extract_egress_basis, extract_egress_approved_by_user_id) "
        "VALUES (:s, :name, :u, :u, clock_timestamp(), 'test', :u, clock_timestamp(), 'test', :u)",
        "INSERT INTO knowledge_source_user_grants (source_id, user_id, granted_by_user_id, revoked_by_user_id) "
        "VALUES (:s, :u, :u, :u)",
        "INSERT INTO knowledge_import_batches (owner_user_id, issued_by_user_id, source_id, secret_hash, expires_at) "
        "VALUES (:u, :u, :s, :hash, now() + interval '1 hour')",
        "INSERT INTO document_files (id, source_id, filename, size_bytes, received_bytes, sha256, status, "
        "created_by_user_id) VALUES (:f1, :s, 'a.pdf', 1, 1, :sha1, 'ready', :u)",
        "INSERT INTO document_files (id, source_id, filename, size_bytes, received_bytes, sha256, status, "
        "created_by_user_id) VALUES (:f2, :s, 'b.pdf', 1, 1, :sha2, 'ready', :u)",
        "INSERT INTO documents (id, source_id, uri, title, content_hash, ingest_signature) "
        "VALUES (:d, :s, 'knowledge-file:a', 'A', :sha1, :sha1)",
        "INSERT INTO document_revisions (document_id, source_id, file_id, revision_number, processing_signature, "
        "parser_name, parser_version, parser_config_hash, chunking_config_hash, embedding_model, "
        "embedding_dimensions, created_by_user_id, reviewed_by_user_id, published_by_user_id) "
        "VALUES (:d, :s, :f1, 1, :sha1, 'p', 'v', :sha1, :sha1, 'm', 1536, :u, :u, :u)",
        "INSERT INTO knowledge_duplicate_reviews (file_id, candidate_file_id, classification, confidence, "
        "reviewed_by_user_id) VALUES (:f1, :f2, 'exact', 100, :u)",
    ]
    values = {
        "u": user,
        "s": uuid.uuid4(),
        "name": f"knowledge-{uuid.uuid4().hex[:8]}",
        "hash": uuid.uuid4().hex * 2,
        "f1": uuid.uuid4(),
        "f2": uuid.uuid4(),
        "sha1": "a" * 64,
        "sha2": "b" * 64,
        "d": uuid.uuid4(),
    }
    async with app.state.db.sessionmaker() as db:
        for statement in statements:
            await db.execute(text(statement), values)
        await db.commit()


async def test_deleting_an_account_leaves_no_row_that_belongs_to_it(client, app, database):
    account = await register(client, app)
    headers = account["headers"]
    user_id = account["user"]["id"]

    # Something in every place a user can own data today.
    _, events = await stream_chat(client, headers, "Something to remember")  # conversation, messages, usage
    message_id = events[-1][1]["message"]["id"]
    memory = await client.post(
        "/v1/memories",
        json={"kind": "learning_context", "content": "Use train examples for probability."},
        headers=headers,
    )
    assert memory.status_code == 201, memory.text
    assert (
        await client.put(
            "/v1/memories/preferences",
            json={"automaticProposals": True, "expectedVersion": 0},
            headers=headers,
        )
    ).status_code == 200
    async with app.state.db.sessionmaker() as db:
        await db.execute(
            text(
                "INSERT INTO memory_deletion_markers (user_id, content_hmac, policy_version, reason) "
                "VALUES (:u, :h, 'v1', 'delete')"
            ),
            {"u": uuid.UUID(user_id), "h": "e" * 64},
        )
        await db.execute(
            text(
                """
                INSERT INTO memory_proposals (
                    user_id, conversation_id, source_message_id, assistant_message_id, operation, kind,
                    content, content_hash, confidence, evidence_reason, source_hash, observed_at,
                    extractor_provider, extractor_model, prompt_version, schema_version, processor_version,
                    idempotency_key, consent_epoch
                ) VALUES (
                    :u, :c, :s, :a, 'add', 'learning_context', 'Account deletion proposal', :h,
                    0.8, 'test', :h, now(), 'test', 'test', 'v1', 'v1', 'v1', :i, 0
                ) RETURNING id
                """
            ),
            {
                "u": uuid.UUID(user_id),
                "c": uuid.UUID(events[0][1]["conversationId"]),
                "s": uuid.UUID(events[0][1]["userMessage"]["id"]),
                "a": uuid.UUID(events[-1][1]["message"]["id"]),
                "h": "f" * 64,
                "i": "1" * 64,
            },
        )
        await db.execute(
            text(
                """
                INSERT INTO memory_extraction_jobs (
                    user_id, conversation_id, source_message_id, assistant_message_id, source_hash,
                    idempotency_key, consent_epoch, source_created_at, processor_version,
                    extractor_provider, extractor_model, prompt_version, schema_version
                ) VALUES (:u, :c, :s, :a, :h, :i, 0, now(), 'v1', 'test', 'test', 'v1', 'v1')
                """
            ),
            {
                "u": uuid.UUID(user_id),
                "c": uuid.UUID(events[0][1]["conversationId"]),
                "s": uuid.UUID(events[0][1]["userMessage"]["id"]),
                "a": uuid.UUID(events[-1][1]["message"]["id"]),
                "h": "2" * 64,
                "i": "3" * 64,
            },
        )
        await db.execute(
            text(
                """
                INSERT INTO memory_command_receipts (
                    user_id, conversation_id, message_id, client_request_id, command_kind, request_hash
                ) VALUES (:u, :c, :m, 'account-delete-test', 'remember', :h)
                """
            ),
            {
                "u": uuid.UUID(user_id),
                "c": uuid.UUID(events[0][1]["conversationId"]),
                "m": uuid.UUID(events[0][1]["userMessage"]["id"]),
                "h": "4" * 64,
            },
        )
        await db.commit()
    feedback = await client.put(f"/v1/messages/{message_id}/feedback", json={"rating": "up"}, headers=headers)
    assert feedback.status_code == 200
    conversation_id = events[0][1]["conversationId"]
    shared = await client.post(f"/v1/conversations/{conversation_id}/share", headers=headers)  # a shared link
    assert shared.status_code == 201, shared.text
    assert (await client.patch("/v1/me/settings", json={"preferences": {"k": "v"}}, headers=headers)).status_code == 200
    other_headers = (await sign_in(app, account["email"]))["headers"]  # a second session
    await ingest_docs(
        app,
        {"private.md": "# Private notes\n\nMine only."},
        source=f"private-{uuid.uuid4().hex[:8]}",
        owner_user_id=uuid.UUID(user_id),
    )
    async with app.state.db.sessionmaker() as db:  # a pending Better Auth code, by address
        await db.execute(
            text(
                "INSERT INTO auth_verifications (identifier, value, expires_at) "
                "VALUES (:i, 'hash:0', now() + interval '15 minutes')"
            ),
            {"i": f"forget-password-otp-{account['email']}"},
        )
        await db.execute(  # an authenticator secret and recovery codes (two-step sign-in, set up)
            text("INSERT INTO auth_two_factors (user_id, secret, backup_codes) VALUES (:u, 'enc', 'enc')"),
            {"u": uuid.UUID(user_id)},
        )
        practice_set = await db.scalar(  # a practice set from the question bank (its served questions go with it)
            text(
                "INSERT INTO qb_sets (user_id, request_id, mode, filters, requested) "
                "VALUES (:u, gen_random_uuid(), 'test', '{}', 1) RETURNING id"
            ),
            {"u": uuid.UUID(user_id)},
        )
        await db.execute(  # a practice exam on that set (app/exams)
            text(
                "INSERT INTO practice_exams (user_id, request_id, set_id, program_id, month, topic_ids, difficulty, "
                "length, minutes, paper, ends_at) "
                "SELECT :u, gen_random_uuid(), :s, id, 1, ARRAY['T1'], 'mixed', 1, 1, ARRAY['Q1'], "
                "now() + interval '1 minute' FROM programs WHERE slug = 'networking-ai'"
            ),
            {"u": uuid.UUID(user_id), "s": practice_set},
        )
        await db.execute(  # an NH invoice this student claimed (app/programs): the claim goes, the invoice stays
            text(
                "INSERT INTO nh_invoices (invoice_no, program_id) "
                "SELECT 'NH-DELETION-TEST', id FROM programs WHERE slug = 'networking-ai'"
            )
        )
        await db.execute(
            text(
                "INSERT INTO invoice_claims (user_id, invoice_id) "
                "SELECT :u, id FROM nh_invoices WHERE invoice_no = 'NH-DELETION-TEST'"
            ),
            {"u": uuid.UUID(user_id)},
        )
        await db.execute(  # a Friend Zone profile (app/friendzone): its friends, requests and battles hang off it
            text("INSERT INTO fz_profiles (user_id, cohort, display_name) VALUES (:u, 'live', 'Deletion test')"),
            {"u": uuid.UUID(user_id)},
        )
        await queue_email(  # an email waiting in the outbox for this student
            db,
            kind=WEEKLY_SUMMARY,
            to=account["email"],
            subject="Your week",
            body="...",
            send_before=datetime.now(UTC) + timedelta(days=1),
            user_id=uuid.UUID(user_id),
        )
        await db.commit()
    photo = await client.put("/v1/me/avatar", content=image_bytes(), headers={**headers, "Content-Type": "image/png"})
    assert photo.status_code == 200, photo.text
    change = await client.post(  # a pending change-email code, bound to the user
        "/v1/me/email/change",
        json={"newEmail": f"moved-{uuid.uuid4().hex[:8]}@example.com", "password": PASSWORD},
        headers=headers,
    )
    assert change.status_code == 202, change.text
    learning = [  # learner profile, subjects, preferences, Engineering Profile
        ("PATCH", "/v1/learning/profile", {"version": 0, "set": {"grade": "class_10", "targetExam": "Boards"}}),
        ("PUT", "/v1/learning/subjects", {"version": 0, "items": [{"code": "mathematics", "isPriority": True}]}),
        ("PATCH", "/v1/learning/preferences", {"version": 0, "set": {"pace": "slow"}}),
        ("PATCH", "/v1/engineering/profile", {"version": 0, "set": {"role": "soc_analyst", "platforms": ["splunk"]}}),
    ]
    for method, url, body in learning:
        response = await client.request(method, url, json=body, headers=headers)
        assert response.status_code == 200, (url, response.text)
    preferences = await client.patch(
        "/v1/notifications/preferences", json={"version": 0, "set": {"weeklySummary": True}}, headers=headers
    )
    assert preferences.status_code == 200, preferences.text
    appearance = await client.patch("/v1/me/appearance", json={"version": 0, "set": {"theme": "dark"}}, headers=headers)
    assert appearance.status_code == 200, appearance.text
    support = await client.post(
        "/v1/support/requests",
        json={"requestId": str(uuid.uuid4()), "kind": "question", "subject": "Help", "message": "How does this work?"},
        headers=headers,
    )
    assert support.status_code == 201, support.text
    notice = await client.post(  # a security notice in the bell (Better Auth reports a password change)
        "/internal/auth/events",
        json={"kind": "password_changed", "userId": user_id},
        headers={"X-Internal-Token": app.state.settings.auth_internal_token.get_secret_value()},
    )
    assert notice.status_code == 202
    uploaded = await client.post(  # a library file, read into chunks and attached to a message
        "/v1/files",
        params={"filename": "notes.txt"},
        content=b"Notes about the water cycle: evaporation, condensation and precipitation.",
        headers={**headers, "Content-Type": "application/octet-stream"},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert await app.state.file_worker.run_once()
    attached = await client.post(
        "/v1/chat/stream", json={"message": "Explain this", "attachmentIds": [uploaded.json()["id"]]}, headers=headers
    )
    assert attached.status_code == 200, attached.text
    tab = str(uuid.uuid4())  # a study session, and a beat (app time too)
    studying = await client.post("/v1/study/session", json={"clientId": tab}, headers=headers)
    assert studying.status_code == 200, studying.text
    beat = {"beatId": str(uuid.uuid4()), "clientId": tab, "wasActive": False, "nowActive": True}
    beat = await client.post("/v1/study/beat", json={**beat, "studyView": True, "visible": True}, headers=headers)
    assert beat.status_code == 200, beat.text

    await _knowledge_library_rows(app, uuid.UUID(user_id))

    before = await _rows_referencing(database, user_id, account["email"])
    missing = [name for name, count in before.items() if name != "users" and count == 0]
    assert not missing, f"the test created no data in {missing}; extend it so deletion is really exercised"

    deleted = await client.request("DELETE", "/v1/me", json={"password": PASSWORD}, headers=headers)
    assert deleted.status_code == 204, deleted.text

    after = await _rows_referencing(database, user_id, account["email"])
    assert {name: count for name, count in after.items() if count} == {}, after

    # Every session of the account is gone: neither token opens anything any more.
    for token_headers in (headers, other_headers):
        for url in ("/v1/me", "/v1/conversations", "/v1/me/settings"):
            assert (await client.get(url, headers=token_headers)).status_code == 401, url


async def test_account_deletion_stops_embedding_retries_after_the_cascade(client, app, monkeypatch):
    monkeypatch.setattr(processing, "EMBED_RETRY_DELAYS_S", (0,))
    monkeypatch.setattr(processing.random, "random", lambda: 0.0)
    account = await register(client, app)
    uploaded = await client.post(
        "/v1/files",
        params={"filename": "notes.txt"},
        content=b"Private network notes that must not leave after account deletion.",
        headers={**account["headers"], "Content-Type": "application/octet-stream"},
    )
    assert uploaded.status_code == 201, uploaded.text
    file_id = uuid.UUID(uploaded.json()["id"])
    worker = app.state.file_worker

    class BarrierEmbeddings(SpyEmbeddings):
        def __init__(self, inner):
            super().__init__(inner)
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def aembed_documents(self, texts):
            self.calls += 1
            self.entered.set()
            await self.release.wait()
            raise EmbeddingError("embedding_failed", "provider down")

    embeddings = BarrierEmbeddings(worker.embeddings)
    monkeypatch.setattr(worker, "embeddings", embeddings)
    processing_task = asyncio.create_task(worker.run_once())
    await embeddings.entered.wait()
    deleted = await client.request("DELETE", "/v1/me", json={"password": PASSWORD}, headers=account["headers"])
    assert deleted.status_code == 204, deleted.text
    embeddings.release.set()
    assert await processing_task

    assert embeddings.calls == 1
    async with app.state.db.sessionmaker() as db:
        assert await db.get(UserFile, file_id) is None
        assert list(await db.scalars(select(FileChunk).where(FileChunk.file_id == file_id))) == []


async def test_a_code_of_a_deleted_account_dies_with_it(client, app, database):
    """Better Auth's codes are keyed by address: one left behind would still work on a new account
    registered with the same address."""
    account = await register(client, app)
    engine = create_async_engine(database)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO auth_verifications (identifier, value, expires_at) "
                    "VALUES (:i, 'hash:0', now() + interval '15 minutes')"
                ),
                {"i": f"forget-password-otp-{account['email']}"},
            )
        deleted = await client.request("DELETE", "/v1/me", json={"password": PASSWORD}, headers=account["headers"])
        assert deleted.status_code == 204
        async with engine.connect() as conn:
            assert (await conn.execute(text("SELECT count(*) FROM auth_verifications"))).scalar_one() == 0
    finally:
        await engine.dispose()
