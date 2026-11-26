"""Pin, archive and share chats (app/conversations: router, repository, shares)."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from app.conversations.shares import WITHHELD_REPLY, token_hash
from app.db.models import ConversationShare, InvoiceClaim, Message, MessageAttachment, NhInvoice
from tests.conftest import register, stream_chat
from tests.test_chat import _log_blob

NOT_SHARED = {"shared": False, "sharedAt": None, "messageCount": None}


async def _chat(client, headers, title: str) -> str:
    return (await client.post("/v1/conversations", json={"title": title}, headers=headers)).json()["id"]


async def _ids(client, headers, view: str | None = None) -> list[str]:
    query = f"?view={view}" if view else ""
    return [c["id"] for c in (await client.get(f"/v1/conversations{query}", headers=headers)).json()["items"]]


# ------------------------------------------------------------------ pin and archive


async def test_pin_and_archive_move_a_chat_between_the_lists_and_persist(client, app):
    h = (await register(client, app))["headers"]
    a, b, c = [await _chat(client, h, name) for name in ("a", "b", "c")]

    r = await client.patch(f"/v1/conversations/{a}", json={"pinned": True}, headers=h)
    assert r.status_code == 200 and r.json()["pinnedAt"] and r.json()["archivedAt"] is None
    assert await _ids(client, h, "pinned") == [a]
    assert a in await _ids(client, h)  # history keeps every chat that is not archived, pinned ones too

    r = await client.patch(f"/v1/conversations/{b}", json={"archived": True}, headers=h)
    assert r.status_code == 200 and r.json()["archivedAt"]
    assert b not in await _ids(client, h) and await _ids(client, h, "archived") == [b]

    # Archiving a pinned chat unpins it; a chat is never in both lists.
    r = await client.patch(f"/v1/conversations/{a}", json={"archived": True}, headers=h)
    assert r.json()["pinnedAt"] is None and r.json()["archivedAt"]
    assert await _ids(client, h, "pinned") == []
    assert set(await _ids(client, h, "archived")) == {a, b}
    # Pinning an archived chat is refused (restore it first); both at once too, and nothing at all.
    assert (await client.patch(f"/v1/conversations/{a}", json={"pinned": True}, headers=h)).status_code == 422
    body = {"pinned": True, "archived": True}
    assert (await client.patch(f"/v1/conversations/{c}", json=body, headers=h)).status_code == 422
    assert (await client.patch(f"/v1/conversations/{c}", json={}, headers=h)).status_code == 422

    # Restore: back in the history; repeating it changes nothing.
    for _ in range(2):
        r = await client.patch(f"/v1/conversations/{a}", json={"archived": False}, headers=h)
        assert r.status_code == 200 and r.json()["archivedAt"] is None
    assert a in await _ids(client, h) and a not in await _ids(client, h, "archived")

    # Pinning twice keeps the first time; unpinning puts it back with the others.
    first = (await client.patch(f"/v1/conversations/{c}", json={"pinned": True}, headers=h)).json()["pinnedAt"]
    again = (await client.patch(f"/v1/conversations/{c}", json={"pinned": True}, headers=h)).json()["pinnedAt"]
    assert first == again
    unpinned = await client.patch(f"/v1/conversations/{c}", json={"pinned": False}, headers=h)
    assert unpinned.json()["pinnedAt"] is None

    # A rename alone still works (the old request shape).
    renamed = await client.patch(f"/v1/conversations/{c}", json={"title": "Renamed"}, headers=h)
    assert renamed.json()["title"] == "Renamed"


async def test_pin_archive_and_share_are_the_owners_only(client, app):
    owner = (await register(client, app))["headers"]
    other = (await register(client, app))["headers"]
    cid = await _chat(client, owner, "mine")
    await stream_chat(client, owner, "hello", conversation_id=cid)
    for body in ({"pinned": True}, {"archived": True}, {"title": "taken"}):
        assert (await client.patch(f"/v1/conversations/{cid}", json=body, headers=other)).status_code == 404
    assert (await client.post(f"/v1/conversations/{cid}/share", headers=other)).status_code == 404
    assert (await client.get(f"/v1/conversations/{cid}/share", headers=other)).status_code == 404
    assert (await client.delete(f"/v1/conversations/{cid}/share", headers=other)).status_code == 404
    assert cid not in await _ids(client, other, "archived")
    assert (await client.post(f"/v1/conversations/{cid}/share")).status_code == 401


# ------------------------------------------------------------------ sharing


async def test_a_shared_link_is_a_read_only_snapshot_that_revoking_ends(client, app):
    h = (await register(client, app))["headers"]
    cid = await _chat(client, h, "OSPF notes")
    await stream_chat(client, h, "What is OSPF?", conversation_id=cid)
    assert (await client.get(f"/v1/conversations/{cid}/share", headers=h)).json() == NOT_SHARED

    created = await client.post(f"/v1/conversations/{cid}/share", headers=h)
    assert created.status_code == 201
    token = created.json()["token"]
    assert len(token) == 32 and cid not in token and created.json()["messageCount"] == 2
    status = (await client.get(f"/v1/conversations/{cid}/share", headers=h)).json()
    assert status["shared"] is True and "token" not in status

    public = await client.get(f"/v1/shared/{token}")  # no sign-in
    assert public.status_code == 200
    assert public.headers["cache-control"] == "no-store" and "noindex" in public.headers["x-robots-tag"]
    body = public.json()
    assert body["title"] == "OSPF notes"
    assert [(m["role"], m["content"]) for m in body["messages"]] == [
        ("user", "What is OSPF?"),
        ("assistant", "You said: What is OSPF?"),
    ]
    # Only what a reader needs: no ids, owner, metadata or file names.
    assert set(body) == {"title", "sharedAt", "messages"}
    assert all(set(m) == {"role", "content", "createdAt", "attachments"} for m in body["messages"])

    # A snapshot: a later message is not in the old link.
    await stream_chat(client, h, "And BGP?", conversation_id=cid)
    assert len((await client.get(f"/v1/shared/{token}")).json()["messages"]) == 2

    # Only the hash is stored.
    async with app.state.db.sessionmaker() as db:
        stored = await db.scalar(select(ConversationShare.token_hash).where(ConversationShare.conversation_id == cid))
    assert stored == token_hash(token) and stored != token

    # Update link: a new token with the new messages; the old one stops at once.
    newer = (await client.post(f"/v1/conversations/{cid}/share", headers=h)).json()
    assert newer["token"] != token and newer["messageCount"] == 4
    assert (await client.get(f"/v1/shared/{token}")).status_code == 404
    assert (await client.get(f"/v1/shared/{newer['token']}")).status_code == 200

    # Revoke: the link stops at once; revoking again is fine.
    for _ in range(2):
        assert (await client.delete(f"/v1/conversations/{cid}/share", headers=h)).status_code == 204
    assert (await client.get(f"/v1/shared/{newer['token']}")).status_code == 404
    assert (await client.get(f"/v1/conversations/{cid}/share", headers=h)).json() == NOT_SHARED


async def test_no_log_line_holds_a_share_token(client, app, caplog):
    """The token is the link's only credential: the access log keeps `/v1/shared/<token>`, never the token."""
    caplog.set_level(logging.INFO)
    h = (await register(client, app))["headers"]
    cid = await _chat(client, h, "Firewall notes")
    await stream_chat(client, h, "What is a stateful firewall?", conversation_id=cid)
    token = (await client.post(f"/v1/conversations/{cid}/share", headers=h)).json()["token"]
    unknown = "A" * 32
    assert (await client.get(f"/v1/shared/{token}")).status_code == 200
    assert (await client.get(f"/v1/shared/{unknown}")).status_code == 404

    blob = _log_blob(caplog.records)
    assert "/v1/shared/<token>" in blob
    assert token not in blob and unknown not in blob


async def test_deleting_the_chat_or_the_account_ends_its_link(client, app):
    account = await register(client, app)
    h = account["headers"]
    cid = await _chat(client, h, "to delete")
    await stream_chat(client, h, "hi", conversation_id=cid)
    token = (await client.post(f"/v1/conversations/{cid}/share", headers=h)).json()["token"]
    assert (await client.delete(f"/v1/conversations/{cid}", headers=h)).status_code == 204
    assert (await client.get(f"/v1/shared/{token}")).status_code == 404

    cid = await _chat(client, h, "account")
    await stream_chat(client, h, "hi", conversation_id=cid)
    token = (await client.post(f"/v1/conversations/{cid}/share", headers=h)).json()["token"]
    async with app.state.db.sessionmaker() as db:
        await db.execute(text("DELETE FROM users WHERE id = :id"), {"id": account["user"]["id"]})
        await db.commit()
    assert (await client.get(f"/v1/shared/{token}")).status_code == 404


async def test_a_shared_chat_withholds_nh_material(client, app):
    h = (await register(client, app))["headers"]
    cid = await _chat(client, h, "mixed")
    await stream_chat(client, h, "first", conversation_id=cid)
    async with app.state.db.sessionmaker() as db:
        reply = await db.scalar(select(Message).where(Message.conversation_id == cid, Message.role == "assistant"))
        reply.metadata_ = {**reply.metadata_, "nhprep": {"lessons": []}}
        await db.commit()
    token = (await client.post(f"/v1/conversations/{cid}/share", headers=h)).json()["token"]
    messages = (await client.get(f"/v1/shared/{token}")).json()["messages"]
    assert messages[1]["content"] == WITHHELD_REPLY


async def test_nothing_to_share_and_bad_tokens(client, app):
    h = (await register(client, app))["headers"]
    empty = await _chat(client, h, "empty")
    r = await client.post(f"/v1/conversations/{empty}/share", headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "nothing_to_share"
    for bad in ("short", "x" * 33, "a" * 31 + "!", str(uuid.uuid4())):
        assert (await client.get(f"/v1/shared/{bad}")).status_code in (404, 422)
    assert (await client.get(f"/v1/shared/{'A' * 32}")).status_code == 404  # well-formed, unknown


async def test_two_shares_at_once_leave_one_live_link(client, app):
    h = (await register(client, app))["headers"]
    cid = await _chat(client, h, "race")
    await stream_chat(client, h, "hi", conversation_id=cid)
    results = await asyncio.gather(*[client.post(f"/v1/conversations/{cid}/share", headers=h) for _ in range(3)])
    assert all(r.status_code == 201 for r in results)
    live = []
    for result in results:
        if (await client.get(f"/v1/shared/{result.json()['token']}")).status_code == 200:
            live.append(result.json()["token"])
    assert len(live) == 1


async def test_program_chats_are_not_shared_pinned_or_archived(client, app):
    account = await register(client, app)
    h = account["headers"]
    cid = await _chat(client, h, "Month 1 tutor")
    await stream_chat(client, h, "What is VLSM?", conversation_id=cid)
    async with app.state.db.sessionmaker() as db:  # a program chat, as a program turn binds it
        program_id = await db.scalar(text("SELECT id FROM programs ORDER BY position LIMIT 1"))
        await db.execute(
            text("UPDATE conversations SET program_id = :p, mode = 'tutor' WHERE id = :id"),
            {"p": program_id, "id": cid},
        )
        await db.commit()

    # Without the program open (no claimed invoice), the chat isn't there at all.
    assert (await client.post(f"/v1/conversations/{cid}/share", headers=h)).status_code == 404

    async with app.state.db.sessionmaker() as db:  # an enrolled student: the invoice is claimed
        invoice = NhInvoice(invoice_no="NHTEST-SHARE-1", program_id=program_id)
        db.add(invoice)
        await db.flush()
        db.add(InvoiceClaim(user_id=uuid.UUID(account["user"]["id"]), invoice_id=invoice.id))
        await db.commit()
    for method in ("get", "post"):
        r = await getattr(client, method)(f"/v1/conversations/{cid}/share", headers=h)
        assert r.status_code == 422 and r.json()["error"]["code"] == "not_shareable"
    for change in ({"pinned": True}, {"archived": True}):
        r = await client.patch(f"/v1/conversations/{cid}", json=change, headers=h)
        assert r.status_code == 422 and r.json()["error"]["code"] == "not_movable"


async def test_a_shared_chat_shows_how_many_files_were_attached_never_which(client, app):
    h = (await register(client, app))["headers"]
    cid = await _chat(client, h, "Config review")
    await stream_chat(client, h, "Review this config", conversation_id=cid)
    async with app.state.db.sessionmaker() as db:
        question = await db.scalar(select(Message).where(Message.conversation_id == cid, Message.role == "user"))
        for position, name in enumerate(("core-router-secrets.txt", "edge-fw.conf")):
            db.add(MessageAttachment(message_id=question.id, filename=name, position=position))
        await db.commit()
    token = (await client.post(f"/v1/conversations/{cid}/share", headers=h)).json()["token"]
    public = await client.get(f"/v1/shared/{token}")
    assert [m["attachments"] for m in public.json()["messages"]] == [2, 0]
    assert "core-router-secrets" not in public.text and "edge-fw" not in public.text


async def test_a_long_chat_shares_its_most_recent_400_messages_in_order(client, app):
    h = (await register(client, app))["headers"]
    cid = await _chat(client, h, "Long lab")
    start = datetime(2026, 1, 1, tzinfo=UTC)
    async with app.state.db.sessionmaker() as db:
        for n in range(450):
            role = "user" if n % 2 == 0 else "assistant"
            db.add(
                Message(
                    conversation_id=uuid.UUID(cid),
                    role=role,
                    content=f"message {n}",
                    status="complete",
                    created_at=start + timedelta(seconds=n),
                )
            )
        await db.commit()
    created = (await client.post(f"/v1/conversations/{cid}/share", headers=h)).json()
    assert created["messageCount"] == 400
    contents = [m["content"] for m in (await client.get(f"/v1/shared/{created['token']}")).json()["messages"]]
    assert len(contents) == 400 and contents[0] == "message 50" and contents[-1] == "message 449"


async def test_making_links_and_reading_them_are_rate_limited(client, app):
    h = (await register(client, app))["headers"]
    cid = await _chat(client, h, "Limits")
    await stream_chat(client, h, "hi", conversation_id=cid)
    made = [(await client.post(f"/v1/conversations/{cid}/share", headers=h)) for _ in range(21)]
    assert [r.status_code for r in made[:20]] == [201] * 20
    assert made[20].status_code == 429 and made[20].json()["error"]["code"] == "rate_limited"
    token = made[19].json()["token"]
    reads = [(await client.get(f"/v1/shared/{token}")).status_code for _ in range(121)]
    assert reads[:120] == [200] * 120 and reads[120] == 429
