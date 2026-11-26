"""Which program months a Knowledge Library document belongs to, for this account (app/programs/access.py
enrolled_months): the owner's mapping, only for the programs this account's claimed invoices open now, under
PROGRAMS_MODE. A context block's label names them (app/rag/context.py), so a reply can say where the topic is taught."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models import Program, ProgramModule, User
from app.programs.access import enrolled_months
from tests.conftest import make_settings, register
from tests.test_programs import ENCOR, PROGRAM, TITLE, change, claim, enrolled, invoice, library, map_documents

SECURITY = "network-security-ai"


@pytest.fixture
def settings():
    return make_settings(programs_mode="on")


async def months_of(app, email: str, settings=None, program_id: uuid.UUID | None = None) -> dict[str, str]:
    async with app.state.db.sessionmaker() as db:
        user = await db.scalar(select(User).where(User.email == email))
        return await enrolled_months(db, user, settings or app.state.settings, program_id)


async def program(app, slug: str) -> tuple[uuid.UUID, str, str]:
    """The program's id, title and month 1 title."""
    async with app.state.db.sessionmaker() as db:
        row = (
            await db.execute(
                select(Program.id, Program.title, ProgramModule.title)
                .join(ProgramModule, ProgramModule.program_id == Program.id)
                .where(Program.slug == slug, ProgramModule.month == 1)
            )
        ).one()
    return row[0], row[1], row[2]


async def mapped_library(app) -> dict[str, uuid.UUID]:
    documents = await library(app)
    await map_documents(app, PROGRAM, 1, [documents["m1.txt"]])
    await map_documents(app, PROGRAM, 2, [documents["m1.txt"], documents["m2.txt"]])
    await map_documents(app, SECURITY, 1, [documents["other.txt"]])
    return documents


async def test_an_enrolled_account_gets_each_mapped_documents_months_of_its_open_program(client, app):
    documents = await mapped_library(app)
    _, _, first = await program(app, PROGRAM)
    account = await enrolled(client, app, "NH-MON-1")

    months = await months_of(app, account["email"])

    assert months == {
        str(documents["m1.txt"]): f"Month 1: {first} | Month 2: {ENCOR}",
        str(documents["m2.txt"]): f"Month 2: {ENCOR}",
    }  # other.txt belongs to a program this account's invoice does not open


async def test_two_open_programs_prefix_the_program_unless_one_program_is_asked_for(client, app):
    documents = await mapped_library(app)
    security_id, security_title, security_first = await program(app, SECURITY)
    networking_id, _, first = await program(app, PROGRAM)
    account = await enrolled(client, app, "NH-MON-2")
    await invoice(app, "NH-MON-3", SECURITY)
    assert (await claim(client, account["headers"], "NH-MON-3")).status_code == 201

    every = await months_of(app, account["email"])
    one = await months_of(app, account["email"], program_id=networking_id)

    assert every == {
        str(documents["m1.txt"]): f"{TITLE}, Month 1: {first} | {TITLE}, Month 2: {ENCOR}",
        str(documents["m2.txt"]): f"{TITLE}, Month 2: {ENCOR}",
        str(documents["other.txt"]): f"{security_title}, Month 1: {security_first}",
    }
    assert one == {
        str(documents["m1.txt"]): f"Month 1: {first} | Month 2: {ENCOR}",
        str(documents["m2.txt"]): f"Month 2: {ENCOR}",
    }
    assert await months_of(app, account["email"], program_id=security_id) == {
        str(documents["other.txt"]): f"Month 1: {security_first}"
    }


async def test_a_document_names_at_most_three_months_in_program_order(client, app):
    documents = await library(app)
    for month in (4, 2, 3, 1):
        await map_documents(app, PROGRAM, month, [documents["m1.txt"]])
    account = await enrolled(client, app, "NH-MON-4")

    named = (await months_of(app, account["email"]))[str(documents["m1.txt"])]

    assert [part.split(":")[0] for part in named.split(" | ")] == ["Month 1", "Month 2", "Month 3"]


async def test_no_months_without_an_open_program_of_this_account(client, app):
    await mapped_library(app)
    security_id, _, _ = await program(app, SECURITY)
    holder = await enrolled(client, app, "NH-MON-5")
    outsider = await register(client, app)
    await invoice(app, "NH-MON-6")  # an invoice nobody claimed
    assert await months_of(app, holder["email"])

    assert await months_of(app, holder["email"], make_settings(programs_mode="off")) == {}
    assert await months_of(app, outsider["email"]) == {}  # another account's program, and an unclaimed invoice
    assert await months_of(app, holder["email"], program_id=security_id) == {}  # a program not open to it
    assert await months_of(app, holder["email"], program_id=uuid.uuid4()) == {}

    await change(app, "NH-MON-5", status="revoked")
    assert await months_of(app, holder["email"]) == {}
    await change(app, "NH-MON-5", status="active", valid_until=datetime.now(UTC) - timedelta(minutes=1))
    assert await months_of(app, holder["email"]) == {}
    await change(app, "NH-MON-5", valid_until=datetime.now(UTC) + timedelta(days=1))
    assert await months_of(app, holder["email"])


async def test_a_chat_labels_passages_with_the_accounts_months_only_where_they_help(client, app, fake_llm):
    """General chat: the enrolled account's months on mapped passages; another account: none; a month chat: none
    (its material is that month's); the program's QnA: its own program's months (it is told to say which month)."""
    from tests.conftest import stream_chat
    from tests.test_programs import start

    await mapped_library(app)
    _, _, first = await program(app, PROGRAM)
    account = await enrolled(client, app, "NH-MON-7")
    stranger = await register(client, app)

    def sent() -> str:
        return "\n".join(message.content for message in fake_llm.calls[-1])

    response, _ = await stream_chat(client, account["headers"], "Explain VLAN trunking with 802.1Q tags")
    assert response.status_code == 200
    assert f"your_nh_months: Month 1: {first} | Month 2: {ENCOR.replace('(', '').replace(')', '')}" in sent()

    response, _ = await stream_chat(client, stranger["headers"], "Explain VLAN trunking with 802.1Q tags")
    assert response.status_code == 200
    assert "your_nh_months" not in sent()

    response, _ = await start(client, account["headers"], "OSPF designated router election", month=2)
    assert response.status_code == 200
    assert "your_nh_months" not in sent()

    response, _ = await start(client, account["headers"], "VLAN trunking with 802.1Q tags", month=None)
    assert response.status_code == 200
    assert f"your_nh_months: Month 1: {first} | Month 2: {ENCOR.replace('(', '').replace(')', '')}" in sent()
