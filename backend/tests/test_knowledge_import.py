"""Knowledge Library import capabilities and resumable upload invariants."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.db.models import DocumentFile, KnowledgeAdminGrant, KnowledgeImportBatch, KnowledgeSource, User
from tests.conftest import register


async def _user(app, email):
    async with app.state.db.sessionmaker() as db:
        return await db.scalar(select(User).where(User.email == email))


async def _grant_admin(app, user_id):
    async with app.state.db.sessionmaker() as db, db.begin():
        db.add(KnowledgeAdminGrant(user_id=user_id, granted_by_operator="test", reason="focused regression test"))


async def _source(app, owner_id):
    async with app.state.db.sessionmaker() as db, db.begin():
        source = KnowledgeSource(name=f"owner-{owner_id}", owner_user_id=owner_id)
        db.add(source)
        await db.flush()
        return source.id


async def _create_batch(client, app, *, expected_file_count=None, expected_total_bytes=None):
    registration = await register(client, app)
    user = await _user(app, registration["email"])
    await _grant_admin(app, user.id)
    source_id = await _source(app, user.id)
    payload = {"sourceId": str(source_id)}
    if expected_file_count is not None:
        payload["expectedFileCount"] = expected_file_count
    if expected_total_bytes is not None:
        payload["expectedTotalBytes"] = expected_total_bytes
    created = await client.post("/v1/knowledge/imports", json=payload, headers=registration["headers"])
    assert created.status_code == 201
    return created.json()["token"], source_id


async def _create_file(client, token, content, *, filename="synthetic.pdf"):
    return await client.post(
        "/v1/knowledge/imports/files",
        json={
            "filename": filename,
            "sizeBytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
        headers={"Authorization": f"Bearer {token}"},
    )


async def _upload(client, token, file_id, content, *, offset=0):
    return await client.put(
        f"/v1/knowledge/imports/files/{file_id}/content?offset={offset}",
        content=content,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"},
    )


async def test_only_database_granted_admin_can_create_import_batch(client, app):
    first = await register(client, app)
    user = await _user(app, first["email"])
    source_id = await _source(app, user.id)
    denied = await client.post(
        "/v1/knowledge/imports",
        json={"sourceId": str(source_id), "expectedFileCount": 1},
        headers=first["headers"],
    )
    assert denied.status_code == 404
    await _grant_admin(app, user.id)
    allowed = await client.post(
        "/v1/knowledge/imports",
        json={"sourceId": str(source_id), "expectedFileCount": 1},
        headers=first["headers"],
    )
    assert allowed.status_code == 201
    assert allowed.json()["token"] and allowed.json()["nextOffset"] == 0


async def test_import_token_is_scoped_resumable_and_sha_verified(client, app):
    first = await register(client, app)
    user = await _user(app, first["email"])
    await _grant_admin(app, user.id)
    source_id = await _source(app, user.id)
    created = await client.post(
        "/v1/knowledge/imports",
        json={"sourceId": str(source_id), "expectedFileCount": 1},
        headers=first["headers"],
    )
    token = created.json()["token"]
    content = b"%PDF-1.7\nsynthetic knowledge fixture\n%%EOF\n"
    digest = hashlib.sha256(content).hexdigest()
    file_response = await client.post(
        "/v1/knowledge/imports/files",
        json={"filename": "synthetic.pdf", "sizeBytes": len(content), "sha256": digest},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert file_response.status_code == 201
    file_id = file_response.json()["id"]
    wrong = await client.put(
        f"/v1/knowledge/imports/files/{file_id}/content?offset=1",
        content=content,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"},
    )
    assert wrong.status_code == 409 and wrong.json()["error"]["code"] == "upload_offset_conflict"
    uploaded = await client.put(
        f"/v1/knowledge/imports/files/{file_id}/content?offset=0",
        content=content,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"},
    )
    assert uploaded.status_code == 200 and uploaded.json()["nextOffset"] == len(content)
    finished = await client.post(
        f"/v1/knowledge/imports/files/{file_id}/finalize",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert finished.status_code == 200 and finished.json()["status"] == "ready"
    leaked = await client.get(f"/v1/knowledge/imports/files/{file_id}", headers={"Authorization": "Bearer wrong-token"})
    assert leaked.status_code == 401


async def test_non_final_chunk_must_be_exactly_one_megabyte(client, app):
    token, _ = await _create_batch(client, app, expected_file_count=1)
    content = b"a" * (1024 * 1024 + 1)
    created = await _create_file(client, token, content)
    assert created.status_code == 201

    rejected = await _upload(client, token, created.json()["id"], content[: 512 * 1024])

    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "invalid_upload_chunk_size"


async def test_finalize_is_idempotent_and_counts_once(client, app):
    token, _ = await _create_batch(client, app, expected_file_count=1)
    content = b"%PDF-1.7\nidempotent\n%%EOF\n"
    created = await _create_file(client, token, content)
    file_id = created.json()["id"]
    assert (await _upload(client, token, file_id, content)).status_code == 200

    first = await client.post(
        f"/v1/knowledge/imports/files/{file_id}/finalize", headers={"Authorization": f"Bearer {token}"}
    )
    second = await client.post(
        f"/v1/knowledge/imports/files/{file_id}/finalize", headers={"Authorization": f"Bearer {token}"}
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == second.json()["status"] == "ready"
    async with app.state.db.sessionmaker() as db:
        file = await db.get(DocumentFile, file_id)
        batch = await db.get(KnowledgeImportBatch, file.import_batch_id)
        assert batch.received_file_count == 1
        assert batch.received_total_bytes == len(content)


async def test_expired_upload_can_be_replaced(client, app):
    first_token, source_id = await _create_batch(client, app, expected_file_count=1)
    content = b"%PDF-1.7\nresume replacement\n%%EOF\n"
    first = await _create_file(client, first_token, content)
    assert first.status_code == 201
    first_file_id = first.json()["id"]
    async with app.state.db.sessionmaker() as db, db.begin():
        file = await db.get(DocumentFile, first_file_id)
        batch = await db.get(KnowledgeImportBatch, file.import_batch_id)
        batch.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        user_id = batch.owner_user_id

    token = "replacement-token"
    replacement_batch = KnowledgeImportBatch(
        owner_user_id=user_id,
        issued_by_user_id=user_id,
        source_id=source_id,
        secret_hash=hashlib.sha256(token.encode("ascii")).hexdigest(),
        expected_file_count=1,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    async with app.state.db.sessionmaker() as db, db.begin():
        db.add(replacement_batch)

    replacement = await _create_file(client, token, content)

    assert replacement.status_code == 201
    assert replacement.json()["id"] != first_file_id
    async with app.state.db.sessionmaker() as db:
        assert await db.get(DocumentFile, first_file_id) is None


async def test_batch_byte_limit_counts_uploading_files(client, app):
    first = b"a" * 700_000
    second = b"b" * 400_000
    token, _ = await _create_batch(client, app, expected_file_count=2, expected_total_bytes=1_000_000)
    accepted = await _create_file(client, token, first, filename="first.pdf")
    rejected = await _create_file(client, token, second, filename="second.pdf")

    assert accepted.status_code == 201
    assert rejected.status_code == 413
    assert rejected.json()["error"]["code"] == "batch_byte_limit"


async def test_same_batch_registration_resumes_and_active_other_batch_conflicts(client, app):
    token, source_id = await _create_batch(client, app, expected_file_count=1)
    content = b"%PDF-1.7\nresume\n%%EOF\n"
    first = await _create_file(client, token, content)
    resumed = await _create_file(client, token, content)

    assert first.status_code == 201
    assert resumed.status_code == 200
    assert resumed.json()["id"] == first.json()["id"]

    async with app.state.db.sessionmaker() as db:
        original_batch = await db.get(
            KnowledgeImportBatch, (await db.get(DocumentFile, first.json()["id"])).import_batch_id
        )
        user_id = original_batch.owner_user_id
    other_token = "other-active-token"
    async with app.state.db.sessionmaker() as db, db.begin():
        db.add(
            KnowledgeImportBatch(
                owner_user_id=user_id,
                issued_by_user_id=user_id,
                source_id=source_id,
                secret_hash=hashlib.sha256(other_token.encode("ascii")).hexdigest(),
                expected_file_count=1,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )

    conflicted = await _create_file(client, other_token, content)

    assert conflicted.status_code == 409
    assert conflicted.json()["error"]["code"] == "upload_in_progress"


async def test_ready_duplicate_is_reused_within_source(client, app):
    token, _ = await _create_batch(client, app, expected_file_count=1)
    content = b"%PDF-1.7\nready duplicate\n%%EOF\n"
    created = await _create_file(client, token, content)
    file_id = created.json()["id"]
    assert (await _upload(client, token, file_id, content)).status_code == 200
    assert (
        await client.post(
            f"/v1/knowledge/imports/files/{file_id}/finalize",
            headers={"Authorization": f"Bearer {token}"},
        )
    ).status_code == 200

    duplicate = await _create_file(client, token, content)

    assert duplicate.status_code == 200
    assert duplicate.json()["id"] == file_id
    assert duplicate.json()["duplicateOfFileId"] == file_id
