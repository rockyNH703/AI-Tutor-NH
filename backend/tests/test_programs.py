"""NH programs (app/programs; owner decisions D1-D8, plan rev 3, 2026-11-23).

The owner's invoice rows decide access on every request; a failed claim is one generic answer; a program chat is
bound to its program and month when it starts and never moved; a month's tutor searches only that month's documents
(every leg and the citation, never a fallback); a program chat is read only while its invoice opens it; an account an
invoice opens a program to sees NHprep only for that program's courses."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, func, select, text, update

from app.db.models import (
    Document,
    Message,
    NhInvoice,
    NhprepLesson,
    Program,
    ProgramModule,
    ProgramModuleCourse,
    ProgramModuleDocument,
)
from app.programs.prompt import ProgramContext, months_naming, tutor_block
from app.programs.service import normalize_invoice
from app.rag.embeddings import embedding_model_id
from app.rag.lexical import build_lexical_query
from app.rag.retriever import PgVectorRetriever
from app.rag.store import HybridSearchResult, KnowledgeStore
from app.rag.types import AccessScope
from scripts.eval_module_tutor import check as eval_check
from tests.conftest import ingest_docs, make_settings, parse_sse, register, stream_chat

PROGRAM = "networking-ai"  # Full Stack Networking with AI: month 1 CCNA core, month 2 CCNP ENCOR
TITLE = "Full Stack Networking with AI"
ENCOR = "Advanced Routing & Switching (CCNP ENCOR)"
LIBRARY = {
    "m1.txt": "VLAN trunking with 802.1Q tags between switches.",
    "m2.txt": "OSPF designated router election on broadcast networks.",
    "other.txt": "BGP path attributes and best path selection.",
}


@pytest.fixture
def settings():
    return make_settings(programs_mode="on")


async def invoice(app, number: str, slug: str = PROGRAM, **values) -> None:
    async with app.state.db.sessionmaker() as db:
        program_id = await db.scalar(select(Program.id).where(Program.slug == slug))
        db.add(NhInvoice(invoice_no=number, program_id=program_id, **values))
        await db.commit()


async def change(app, number: str, **values) -> None:
    async with app.state.db.sessionmaker() as db:
        await db.execute(update(NhInvoice).where(NhInvoice.invoice_no == number).values(**values))
        await db.commit()


async def module_id(app, slug: str, month: int) -> uuid.UUID:
    async with app.state.db.sessionmaker() as db:
        return await db.scalar(
            select(ProgramModule.id)
            .join(Program, Program.id == ProgramModule.program_id)
            .where(Program.slug == slug, ProgramModule.month == month)
        )


async def map_documents(app, slug: str, month: int, documents) -> None:
    target = await module_id(app, slug, month)
    async with app.state.db.sessionmaker() as db:
        db.add_all(ProgramModuleDocument(module_id=target, document_id=document) for document in documents)
        await db.commit()


async def library(app) -> dict[str, uuid.UUID]:
    await ingest_docs(app, LIBRARY, source="nh-library")
    async with app.state.db.sessionmaker() as db:
        return dict((await db.execute(select(Document.uri, Document.id))).all())


async def claim(client, headers, number: str):
    return await client.post("/v1/programs/claims", json={"invoiceNumber": number}, headers=headers)


async def enrolled(client, app, number: str = "NH-T-0001", slug: str = PROGRAM) -> dict:
    await invoice(app, number, slug)
    account = await register(client, app)
    assert (await claim(client, account["headers"], number)).status_code == 201
    return account


async def start(client, headers, message: str, *, program: str = PROGRAM, month: int | None = 2, mode=None):
    body: dict = {"message": message, "program": program}
    if month is not None:
        body["month"] = month
    if mode is not None:
        body["tutorMode"] = mode
    response = await client.post("/v1/chat/stream", json=body, headers=headers)
    return response, parse_sse(response.text) if response.status_code == 200 else []


def values_of(found, key: str) -> list:
    """Every value stored under `key` anywhere in a nested record."""
    if isinstance(found, dict):
        own = [found[key]] if key in found else []
        return own + [v for item in found.values() for v in values_of(item, key)]
    if isinstance(found, list):
        return [v for item in found for v in values_of(item, key)]
    return []


# ---------------------------------------------------------------- the curriculum and the switch
async def test_the_curriculum_is_the_owners_four_programs_and_29_months(app):
    async with app.state.db.sessionmaker() as db:
        slugs = list(await db.scalars(select(Program.slug).order_by(Program.position)))
        months = await db.scalar(select(func.count()).select_from(ProgramModule))
        encor = await db.scalar(
            select(ProgramModule.title)
            .join(Program, Program.id == ProgramModule.program_id)
            .where(Program.slug == PROGRAM, ProgramModule.month == 2)
        )
    assert slugs == ["cyber-cloud-security-ai", "network-security-ai", "networking-ai", "ccna-ccnp-enterprise"]
    assert months == 29
    assert encor == ENCOR


def test_invoice_numbers_are_kept_without_spaces_in_upper_case():
    assert normalize_invoice("  nh-2026 / 0012 ") == "NH-2026/0012"
    assert normalize_invoice("ab") is None
    assert normalize_invoice("NH#12") is None
    assert normalize_invoice("") is None


async def test_programs_mode_off_hides_every_program_route(client, app, settings):
    settings.programs_mode = "off"
    await invoice(app, "NH-OFF-1")
    headers = (await register(client, app))["headers"]
    listed = await client.get("/v1/programs", headers=headers)
    assert listed.status_code == 200 and listed.json() == {
        "enabled": False,
        "programs": [],
        "invoicePrompt": False,
        "claims": [],
    }
    assert (await claim(client, headers, "NH-OFF-1")).status_code == 404
    for url in (f"/v1/programs/{PROGRAM}", f"/v1/programs/{PROGRAM}/months/2", f"/v1/conversations?program={PROGRAM}"):
        assert (await client.get(url, headers=headers)).status_code == 404, url
    assert (await start(client, headers, "What is OSPF?"))[0].status_code == 404


# ---------------------------------------------------------------- claims
async def test_a_claim_opens_the_program_and_every_failure_is_one_generic_answer(client, app):
    await invoice(app, "NH-OK-1")
    await invoice(app, "NH-REVOKED", status="revoked")
    await invoice(app, "NH-REFUNDED", status="refunded")
    await invoice(app, "NH-EXPIRED", valid_until=datetime.now(UTC) - timedelta(days=1))
    first, second = await register(client, app), await register(client, app)
    assert (await client.get("/v1/programs", headers=first["headers"])).json() == {
        "enabled": True,
        "programs": [],
        "invoicePrompt": True,
        "claims": [],
    }

    claimed = await claim(client, first["headers"], " nh-ok-1 ")
    assert claimed.status_code == 201, claimed.text
    assert claimed.json() == {"status": "claimed", "program": {"slug": PROGRAM, "title": TITLE}}
    again = await claim(client, first["headers"], "NH-OK-1")
    assert again.status_code == 200 and again.json()["status"] == "already"

    refused = [
        await claim(client, second["headers"], number)
        for number in ("NH-OK-1", "NH-REVOKED", "NH-REFUNDED", "NH-EXPIRED", "NH-NOT-THERE")
    ]
    answers = {(r.status_code, r.json()["error"]["code"], r.json()["error"]["message"]) for r in refused}
    assert answers == {(404, "invoice_not_verified", "We couldn't verify this invoice.")}

    listed = (await client.get("/v1/programs", headers=first["headers"])).json()
    assert listed["invoicePrompt"] is False
    assert [program["slug"] for program in listed["programs"]] == [PROGRAM]
    assert [month["month"] for month in listed["programs"][0]["months"]] == list(range(1, 9))
    assert (await client.get("/v1/programs", headers=second["headers"])).json()["programs"] == []


async def test_invoice_attempts_are_limited_per_account(client, app):
    headers = (await register(client, app))["headers"]
    statuses = [(await claim(client, headers, f"NH-GUESS-{n}")).status_code for n in range(6)]
    assert statuses == [404] * 5 + [429]


async def test_two_accounts_claiming_one_invoice_at_once_leave_exactly_one_claim(client, app):
    await invoice(app, "NH-RACE-1")
    first, second = await register(client, app), await register(client, app)
    results = await asyncio.gather(
        claim(client, first["headers"], "NH-RACE-1"), claim(client, second["headers"], "NH-RACE-1")
    )
    assert sorted(result.status_code for result in results) == [201, 404]
    async with app.state.db.sessionmaker() as db:
        assert await db.scalar(text("SELECT count(*) FROM invoice_claims")) == 1


async def claims_of(client, headers) -> list[dict]:
    response = await client.get("/v1/programs", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["claims"]


async def test_the_list_carries_only_this_accounts_claims_newest_first_and_never_a_number(client, app):
    await invoice(app, "NH-MINE-1")
    await invoice(app, "NH-MINE-2", slug="network-security-ai")
    await invoice(app, "NH-THEIRS-1", slug="ccna-ccnp-enterprise")
    mine, theirs = await register(client, app), await register(client, app)
    assert (await claim(client, mine["headers"], "NH-MINE-1")).status_code == 201
    assert (await claim(client, mine["headers"], "NH-MINE-2")).status_code == 201
    assert (await claim(client, theirs["headers"], "NH-THEIRS-1")).status_code == 201

    listed = await client.get("/v1/programs", headers=mine["headers"])
    claims = listed.json()["claims"]
    assert [(c["program"]["slug"], c["state"]) for c in claims] == [
        ("network-security-ai", "active"),  # newest first
        (PROGRAM, "active"),
    ]
    assert claims[0]["program"]["title"] and claims[0]["claimedAt"] >= claims[1]["claimedAt"]
    assert set(claims[0]) == {"program", "state", "claimedAt"}
    assert "NH-MINE" not in listed.text and "NH-THEIRS" not in listed.text
    assert [c["program"]["slug"] for c in await claims_of(client, theirs["headers"])] == ["ccna-ccnp-enterprise"]
    # Two programs open: both listed, so the web app shows a card for each.
    assert sorted(p["slug"] for p in listed.json()["programs"]) == sorted(["network-security-ai", PROGRAM])

    # A refused claim (another account's invoice) adds nothing for the caller.
    assert (await claim(client, theirs["headers"], "NH-MINE-1")).status_code == 404
    assert [c["program"]["slug"] for c in await claims_of(client, theirs["headers"])] == ["ccna-ccnp-enterprise"]


@pytest.mark.parametrize(
    ("values", "state"),
    [
        ({"status": "revoked"}, "revoked"),
        ({"status": "refunded"}, "revoked"),
        ({"status": "expired"}, "expired"),
        ({"valid_until": datetime.now(UTC) - timedelta(minutes=1)}, "expired"),
    ],
)
async def test_an_ended_claim_keeps_its_state_and_opens_nothing(client, app, values, state):
    headers = (await enrolled(client, app, "NH-ENDS-1"))["headers"]
    assert [c["state"] for c in await claims_of(client, headers)] == ["active"]
    await change(app, "NH-ENDS-1", **values)
    listed = (await client.get("/v1/programs", headers=headers)).json()
    assert listed["programs"] == []
    assert [(c["program"]["slug"], c["state"]) for c in listed["claims"]] == [(PROGRAM, state)]
    assert (await client.get(f"/v1/programs/{PROGRAM}", headers=headers)).status_code == 404


async def test_continue_without_an_invoice_stops_the_prompt(client, app):
    headers = (await register(client, app))["headers"]
    assert (await client.post("/v1/programs/invoice-prompt/dismiss", headers=headers)).status_code == 204
    assert (await client.get("/v1/programs", headers=headers)).json()["invoicePrompt"] is False


async def test_editing_the_invoice_row_changes_access_on_the_next_request(client, app):
    headers = (await enrolled(client, app, "NH-EDIT-1"))["headers"]
    _, events = await start(client, headers, "What is the OSPF DR?")
    conversation = events[0][1]["conversationId"]

    async def opened() -> list[str]:
        return [program["slug"] for program in (await client.get("/v1/programs", headers=headers)).json()["programs"]]

    assert await opened() == [PROGRAM]
    await change(app, "NH-EDIT-1", status="revoked")
    assert await opened() == []
    for url in (
        f"/v1/programs/{PROGRAM}",
        f"/v1/programs/{PROGRAM}/months/2",
        f"/v1/conversations/{conversation}",
        f"/v1/conversations/{conversation}/messages",
        f"/v1/conversations?program={PROGRAM}&month=2",
    ):
        assert (await client.get(url, headers=headers)).status_code == 404, url
    assert (await stream_chat(client, headers, "and then?", conversation))[0].status_code == 404

    await change(app, "NH-EDIT-1", status="active")
    assert await opened() == [PROGRAM]
    assert (await client.get(f"/v1/conversations/{conversation}", headers=headers)).status_code == 200

    async with app.state.db.sessionmaker() as db:
        other = await db.scalar(select(Program.id).where(Program.slug == "network-security-ai"))
    await change(app, "NH-EDIT-1", program_id=other)
    assert await opened() == ["network-security-ai"]
    assert (await client.get(f"/v1/conversations/{conversation}", headers=headers)).status_code == 404

    await change(app, "NH-EDIT-1", valid_until=datetime.now(UTC) - timedelta(minutes=1))
    assert await opened() == []
    async with app.state.db.sessionmaker() as db:
        await db.execute(delete(NhInvoice).where(NhInvoice.invoice_no == "NH-EDIT-1"))
        await db.commit()
        assert await db.scalar(text("SELECT count(*) FROM invoice_claims")) == 0


# ---------------------------------------------------------------- program chats
async def test_a_month_chat_is_bound_when_it_starts_and_keeps_its_own_history(client, app):
    headers = (await enrolled(client, app))["headers"]
    response, events = await start(client, headers, "Explain the OSPF DR election")
    assert response.status_code == 200 and events[-1][0] == "done"
    meta = events[0][1]
    conversation = meta["conversationId"]
    assert meta["conversation"]["program"] == {
        "slug": PROGRAM,
        "title": TITLE,
        "month": 2,
        "monthTitle": ENCOR,
        "mode": "tutor",
    }
    follow, events = await stream_chat(client, headers, "And the BDR?", conversation)
    assert follow.status_code == 200 and events[-1][0] == "done"
    moved = await client.post(
        "/v1/chat/stream",
        json={"message": "x", "conversationId": conversation, "program": PROGRAM, "month": 4},
        headers=headers,
    )
    assert moved.status_code == 422

    general = (await client.get("/v1/conversations", headers=headers)).json()["items"]
    assert conversation not in [item["id"] for item in general]
    month2 = (await client.get(f"/v1/conversations?program={PROGRAM}&month=2", headers=headers)).json()["items"]
    assert [item["id"] for item in month2] == [conversation] and month2[0]["program"]["month"] == 2
    assert (await client.get(f"/v1/conversations?program={PROGRAM}&month=4", headers=headers)).json()["items"] == []
    assert (await client.get(f"/v1/conversations?program={PROGRAM}", headers=headers)).json()["items"] == []

    qna, events = await start(client, headers, "Which month covers SD-WAN?", month=None)
    assert qna.status_code == 200 and events[0][1]["conversation"]["program"]["month"] is None
    interview, events = await start(client, headers, "Start the interview", mode="interview")
    assert interview.status_code == 200 and events[0][1]["conversation"]["program"]["mode"] == "interview"


async def test_another_account_never_reaches_a_program_chat_or_a_program_it_has_no_invoice_for(client, app):
    owner = await enrolled(client, app, "NH-OWN-1")
    _, events = await start(client, owner["headers"], "What is STP?", month=1)
    conversation = events[0][1]["conversationId"]
    message = events[-1][1]["message"]["id"]
    same_program = await enrolled(client, app, "NH-OWN-2")
    stranger = await register(client, app)
    for headers in (same_program["headers"], stranger["headers"]):
        for url in (
            f"/v1/conversations/{conversation}",
            f"/v1/conversations/{conversation}/messages",
            f"/v1/messages/{message}/feedback",
        ):
            assert (await client.get(url, headers=headers)).status_code == 404, url
        assert (await stream_chat(client, headers, "continue", conversation))[0].status_code == 404
        regenerate = await client.post(f"/v1/conversations/{conversation}/regenerate/stream", headers=headers)
        assert regenerate.status_code == 404
    assert (await start(client, stranger["headers"], "hi"))[0].status_code == 404

    headers = owner["headers"]
    assert (await start(client, headers, "hi", program="network-security-ai"))[0].status_code == 404
    assert (await start(client, headers, "hi", month=9))[0].status_code == 404
    assert (await client.get("/v1/programs/network-security-ai", headers=headers)).status_code == 404
    assert (await client.get(f"/v1/programs/{PROGRAM}/months/9", headers=headers)).status_code == 404
    for url in (f"/v1/programs/{PROGRAM}/months/0", f"/v1/programs/{PROGRAM}/months/abc", "/v1/programs/Not_A_Slug"):
        assert (await client.get(url, headers=headers)).status_code in (404, 422), url
    interview = await client.post(
        "/v1/chat/stream", json={"message": "go", "program": PROGRAM, "tutorMode": "interview"}, headers=headers
    )
    assert interview.status_code == 422


# ---------------------------------------------------------------- month isolation
async def test_a_month_searches_only_its_documents_in_every_leg(client, app):
    ids = await library(app)
    owner = await register(client, app)
    user = uuid.UUID(owner["user"]["id"])
    await ingest_docs(app, {"mine.txt": "OSPF notes I wrote myself."}, source="my-notes", owner_user_id=user)
    async with app.state.db.sessionmaker() as db:
        mine = await db.scalar(select(Document.id).where(Document.uri == "mine.txt"))
    store, embeddings = app.state.knowledge, app.state.embeddings
    model = embedding_model_id(embeddings)
    month = AccessScope(user, documents=(ids["m2.txt"], mine))  # a private document mapped by mistake stays out
    for question in ("OSPF designated router election", "VLAN trunking", "BGP best path"):
        vector = await embeddings.aembed_query(question)
        dense = await store.search(month, vector, embedding_model=model, k=20, min_score=-1.0)
        hybrid = await store.hybrid_search(
            month,
            vector,
            build_lexical_query(question),
            embedding_model=model,
            k=20,
            dense_candidates=20,
            lexical_candidates=20,
            rrf_k=60,
            min_score=-1.0,
        )
        assert {hit.document_id for hit in (*dense, *hybrid.hits)} <= {ids["m2.txt"]}, question
    vector = await embeddings.aembed_query("OSPF")
    assert await store.search(AccessScope(user, documents=()), vector, embedding_model=model, k=20) == []
    assert not await store.has_chunks(AccessScope(user, documents=()), model)
    general = await store.search(AccessScope(user), vector, embedding_model=model, k=20, min_score=-1.0)
    assert {ids["m1.txt"], ids["m2.txt"], ids["other.txt"]} <= {hit.document_id for hit in general}


async def test_a_month_retriever_neither_follows_a_named_course_nor_falls_back(app):
    calls: list[tuple] = []

    class Store(KnowledgeStore):
        def __init__(self) -> None:  # no database: only what the retriever asks for
            pass

        async def hybrid_search(self, scope, vector, lexical, **options):
            calls.append(("hybrid", scope.documents is not None, options.get("courses")))
            return HybridSearchResult([], {})

        async def foundation_courses(self, courses):
            calls.append(("foundation", courses))
            return ("ccna",)

    retriever = PgVectorRetriever(
        store=Store(),
        embeddings=app.state.embeddings,
        scope=AccessScope(None, documents=(uuid.uuid4(),)),
        courses=["ccnp"],
        hybrid=True,
    )
    await retriever.ainvoke("A CCNP question about CCNA OSPF")
    assert calls == [("hybrid", True, None)]


async def test_a_month_tutor_is_told_its_month_and_sees_only_its_material(client, app, fake_llm):
    ids = await library(app)
    await map_documents(app, PROGRAM, 1, [ids["m1.txt"]])
    await map_documents(app, PROGRAM, 2, [ids["m2.txt"]])
    headers = (await enrolled(client, app))["headers"]
    for question in ("Tell me about VLAN trunking on CCNA switches", "OSPF designated router election"):
        response, events = await start(client, headers, question)
        assert response.status_code == 200 and events[-1][0] == "done", response.text
        sent = "\n".join(message.content for message in fake_llm.calls[-1])
        assert f"Month 2: {ENCOR}" in sent and "do not answer it, not even briefly" in sent
        assert LIBRARY["m1.txt"] not in sent and LIBRARY["other.txt"] not in sent
        async with app.state.db.sessionmaker() as db:
            stored = await db.scalar(
                select(Message.metadata_).where(Message.id == uuid.UUID(events[-1][1]["message"]["id"]))
            )
        assert set(values_of(stored, "documentId")) <= {str(ids["m2.txt"])}

    response, events = await start(client, headers, "BGP best path selection", month=None)  # QnA: every month
    async with app.state.db.sessionmaker() as db:
        stored = await db.scalar(
            select(Message.metadata_).where(Message.id == uuid.UUID(events[-1][1]["message"]["id"]))
        )
    assert set(values_of(stored, "documentId")) <= {str(ids["m1.txt"]), str(ids["m2.txt"])}

    plain, _ = await stream_chat(client, headers, "BGP best path selection")  # general chat: as before
    assert plain.status_code == 200
    assert "NetworkersHome (NH) tutor" not in "\n".join(message.content for message in fake_llm.calls[-1])


def test_a_month_tutor_points_to_other_months_and_labels_general_answers():
    context = ProgramContext(
        program_id=uuid.uuid4(),
        slug=PROGRAM,
        title=TITLE,
        month=2,
        month_title=ENCOR,
        mode="tutor",
        months=((1, "Networking Fundamentals & CCNA Core"), (2, ENCOR)),
    )
    block = tutor_block(context)
    assert "This isn't covered in your Month 2 material." in block
    assert "General explanation (not from your NH material)" in block
    assert "do not answer it, not even briefly" in block
    assert "- Month 1: Networking Fundamentals & CCNA Core" in block
    assert "mock interview" in tutor_block(replace(context, mode="interview"))
    assert "This isn't covered in your program material." in tutor_block(replace(context, month=None, month_title=None))


def test_the_program_qna_answers_from_material_or_says_it_is_not_covered_never_both():
    # Production audit, 2026-11-24: a Q&A reply answered from the material (with citations), then added
    # "This isn't covered in your program material" and a general section. The month tutor's ordered decision
    # (stop at the first step that applies) is what keeps it from doing both; Q&A has the same rule now.
    context = ProgramContext(
        program_id=uuid.uuid4(),
        slug=PROGRAM,
        title=TITLE,
        month=None,
        month_title=None,
        mode="tutor",
        months=((1, "Networking Fundamentals & CCNA Core"), (2, ENCOR)),
    )
    block = tutor_block(context)
    assert "stop at the first step that applies" in block
    covered = block.index("1. The reference material covers it")
    uncovered = block.index("This isn't covered in your program material.")
    assert covered < uncovered
    assert "never add a general section or say that anything is not covered" in block


NETWORK_SECURITY = (  # migration 0029's titles, verbatim
    (1, "Networking Foundations - CCNA 200-301"),
    (2, "CCNP ENCOR-350-401 (Core)"),
    (3, "CCNP ENARSI-300-410 (Advanced)"),
    (4, "CCNP SDWAN - Viptela Stack"),
    (5, "PaloAlto NGFW - Next-Generation Firewall"),
    (6, "FortiGate - NSE-4 - Next-Generation Firewall"),
    (7, "Cloud Firewalls, NOC & AI SOC, SASE, ZTA"),
    (8, "AI in Network Security + Project"),
)


@pytest.mark.parametrize(
    ("month", "question", "named"),
    [
        (2, "How do I configure VDOMs on a FortiGate?", [6]),
        (4, "How do Palo Alto zone protection profiles work?", [5]),
        (5, "How do I build a device template in Cisco vManage?", [4]),
        (6, "How does OMP advertise routes in Cisco SD-WAN?", [4]),
        (1, "What does ENARSI cover beyond ENCOR?", [2, 3]),
        # Broad words never name a month ("Advanced", "Security", "Network", "routing", "next-generation firewall").
        (2, "How do I use commit confirmed on Juniper Junos?", []),
        (6, "How does Cisco ISE posture assessment work?", []),
        (1, "What is an advanced next-generation firewall for network security routing?", []),
        # This month's own title names it: answered here, never pointed away.
        (6, "How does FortiGate HA work, compared with Palo Alto?", []),
    ],
)
def test_the_title_check_names_only_months_whose_title_names_the_topic(month, question, named):
    title = dict(NETWORK_SECURITY)[month]
    context = ProgramContext(uuid.uuid4(), "network-security-ai", TITLE, month, title, "tutor", NETWORK_SECURITY)
    assert [number for number, _ in months_naming(context, question)] == named
    block = tutor_block(context, question)
    if named:
        assert f"names the topic of Month {named[0]}:" in block
    else:
        assert "no other month's title names this question's topic" in block
    assert "do not answer it, not even briefly" in block


def test_module_tutor_eval_rules():
    """scripts/eval_module_tutor.py: a source outside the month fails `in`; a lesson instead of a pointer fails."""
    documents, courses = {"d1"}, {"c1"}
    mine = [{"documentId": "d1"}, {"source": "NHprep", "sourceId": "c1", "documentId": "lesson"}]
    assert eval_check("in", "answer", mine, documents, courses, None)
    assert not eval_check("in", "answer", [*mine, {"documentId": "d2"}], documents, courses, None)
    assert eval_check("other", "That is covered in Month 5: Palo Alto.", [], documents, courses, 5)
    assert not eval_check("other", "Month 5. " + "App-ID works like this. " * 60, [], documents, courses, 5)
    assert not eval_check("other", "That is covered in Month 15.", [], documents, courses, 5)
    reply = "This isn't covered in your Month 2 material. **General explanation (not from your NH material)**"
    assert eval_check("uncovered", reply, [], documents, courses, None)
    assert not eval_check("uncovered", "Here is how it works.", [], documents, courses, None)


# ---------------------------------------------------------------- NHprep for claim holders
async def test_claim_holders_see_nhprep_only_for_their_programs_courses():
    from tests.test_nhprep_api import ACL, STATIC, nhprep_app, seed

    async with nhprep_app(programs_mode="on") as (app, client):
        lessons = await seed(app)
        async with app.state.db.sessionmaker() as db:
            course = await db.scalar(select(NhprepLesson.course_id).where(NhprepLesson.id == lessons[STATIC]))
        target = await module_id(app, PROGRAM, 2)
        async with app.state.db.sessionmaker() as db:
            db.add(ProgramModuleCourse(module_id=target, course_id=course))
            await db.commit()
        holder = (await enrolled(client, app, "NH-VID-1"))["headers"]  # an example.com address: not NH by domain
        outsider = (await register(client, app))["headers"]

        assert (await client.get(f"/v1/nhprep/lessons/{lessons[STATIC]}", headers=holder)).status_code == 200
        assert (await client.get(f"/v1/nhprep/lessons/{lessons[ACL]}", headers=holder)).status_code == 404
        listed = await client.get(f"/v1/nhprep/lessons?ids={lessons[STATIC]},{lessons[ACL]}", headers=holder)
        assert [item["id"] for item in listed.json()["items"]] == [str(lessons[STATIC])]
        assert (await client.get(f"/v1/nhprep/lessons/{lessons[STATIC]}", headers=outsider)).status_code == 404
        month = (await client.get(f"/v1/programs/{PROGRAM}/months/2", headers=holder)).json()
        assert str(lessons[STATIC]) in month["lessonIds"] and str(lessons[ACL]) not in month["lessonIds"]
        assert month["material"]["lessons"] == len(month["lessonIds"])
