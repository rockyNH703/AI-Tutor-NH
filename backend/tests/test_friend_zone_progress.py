"""NH Friend Zone, Phase 2 (owner-approved R-P2 and Rematch, CD16; U1 option ii, CD18): XP, levels, badges, caps,
rematch, profile statistics, the XP history, and hash-bound AI key checks."""

from __future__ import annotations

import subprocess
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    FzBattle,
    FzProfile,
    FzXpEvent,
    Program,
    ProgramModule,
    ProgramModuleBook,
    QbKeyCheck,
    QbQuestion,
    UserNotification,
)
from app.friendzone import battles, friends, progress
from app.qbank.importer import import_bank
from scripts import bind_key_checks
from tests.conftest import make_settings
from tests.test_friend_zone_battles import answer, at, bank, key_of, pair, started, state  # noqa: F401 (bank)
from tests.test_friend_zone_relations import befriend, block
from tests.test_programs import enrolled
from tests.test_qbank_certification import netsec_bank

BASE = "/v1/friend-zone"


@pytest.fixture
def settings():
    return make_settings(programs_mode="on", friend_zone_mode="on")


@pytest.fixture(autouse=True)
def no_floor(monkeypatch):
    monkeypatch.setattr(friends, "SEARCH_FLOOR_SECONDS", 0.0)


async def play(client, app, a: dict, b: dict, battle_id: str, *, a_right: bool = True, b_right: bool = False):
    """Play every round: each player answers right or wrong, then the round closes."""
    length = (await state(client, a, battle_id))["length"]
    for round_ in range(1, length + 1):
        await at(app, battle_id, round_, "opened_at", -1)
        key = await key_of(app, battle_id, round_)
        wrong = "A" if key != "A" else "B"
        await answer(client, a, battle_id, round_, key if a_right else wrong)
        await answer(client, b, battle_id, round_, key if b_right else wrong)
        await at(app, battle_id, round_, "closes_at", -0.1)
    return await state(client, a, battle_id)


async def xp_of(app, who: dict) -> dict[str, int]:
    async with app.state.db.sessionmaker() as db:
        rows = await db.execute(
            select(FzXpEvent.reason, func.sum(FzXpEvent.amount))
            .where(FzXpEvent.user_id == uuid.UUID(who["user"]["id"]))
            .group_by(FzXpEvent.reason)
        )
        return dict(rows.all())


async def kinds(app, who: dict) -> list[str]:
    async with app.state.db.sessionmaker() as db:
        rows = await db.scalars(
            select(UserNotification.kind).where(
                UserNotification.user_id == uuid.UUID(who["user"]["id"]),
                UserNotification.kind.in_(("badge_unlocked", "level_up", "xp_milestone")),
            )
        )
        return sorted(rows)


async def rematch(client, who: dict, battle_id: str, request_id: str | None = None):
    body = {"requestId": request_id or str(uuid.uuid4())}
    return await client.post(f"{BASE}/battles/{battle_id}/rematch", json=body, headers=who["headers"])


def test_levels_follow_fifty_n_n_minus_one():
    assert [progress.level_for(xp) for xp in (0, 99, 100, 299, 300, 599, 600)] == [1, 1, 2, 2, 3, 3, 4]
    assert [progress.level_floor(n) for n in (1, 2, 3, 4)] == [0, 100, 300, 600]


async def test_a_won_battle_gives_xp_a_badge_and_stats_once(client, app, bank):  # noqa: F811
    a, b = await pair(client, app, 401)
    battle_id = await started(client, a, b)
    done = await play(client, app, a, b, battle_id)
    assert (done["status"], done["result"], done["me"]["winner"]) == ("completed", "points", True)
    assert await xp_of(app, a) == {"battle": 20, "win": 30}
    assert await xp_of(app, b) == {"battle": 20}  # answered every round, so rewarded; no win
    assert await kinds(app, a) == ["badge_unlocked"]
    async with app.state.db.sessionmaker() as db:  # replaying the award changes nothing
        me = await db.scalar(select(FzProfile).where(FzProfile.user_id == uuid.UUID(a["user"]["id"])))
        loaded = await battles.load(db, uuid.UUID(battle_id), me, lock=True)
        await progress.on_battle_completed(db, loaded.battle, loaded.players)
        await db.commit()
    assert await xp_of(app, a) == {"battle": 20, "win": 30}

    mine = (await client.get(f"{BASE}/players/{a['player']}", headers=a["headers"])).json()["stats"]
    assert (mine["battles"], mine["wins"], mine["losses"], mine["xp"], mine["level"]) == (1, 1, 0, 50, 1)
    assert [badge["key"] for badge in mine["badges"]] == ["first_battle"] and mine["nextLevelAt"] == 100
    hidden = (await client.get(f"{BASE}/players/{a['player']}", headers=b["headers"])).json()
    assert hidden["stats"] is None  # a friend sees stats only when they are shown
    profile = (await client.get(f"{BASE}/me", headers=a["headers"])).json()["profile"]
    edit = {"version": profile["version"], "set": {"showStats": True}}
    assert (await client.patch(f"{BASE}/me", json=edit, headers=a["headers"])).status_code == 200
    shown = (await client.get(f"{BASE}/players/{a['player']}", headers=b["headers"])).json()["stats"]
    assert shown["wins"] == 1 and shown["xp"] == 50

    history = (await client.get(f"{BASE}/me/xp", headers=a["headers"])).json()
    assert history["xp"] == 50 and sorted(item["reason"] for item in history["items"]) == ["battle", "win"]


async def test_void_and_capped_battles_give_no_xp(client, app, bank):  # noqa: F811
    a, b = await pair(client, app, 411)
    voided = await started(client, a, b)
    await at(app, voided, 1, "opened_at", -1)
    await answer(client, a, voided, 1, "A")
    await block(client, b, a)
    assert (await state(client, a, voided))["result"] == "void"
    assert await xp_of(app, a) == {}

    c, d = await pair(client, app, 413)
    async with app.state.db.sessionmaker() as db:  # five rewarded battles today already: the cap is reached
        user = uuid.UUID(c["user"]["id"])
        for _ in range(progress.REWARDED_PER_DAY):
            db.add(FzXpEvent(user_id=user, cohort="live", reason="battle", ref_id=str(uuid.uuid4()), amount=20))
        await db.commit()
    battle_id = await started(client, c, d)
    await play(client, app, c, d, battle_id)
    assert await xp_of(app, c) == {"battle": 20 * progress.REWARDED_PER_DAY}  # nothing more for c
    assert await xp_of(app, d) == {"battle": 20}


async def test_a_rematch_is_a_new_invitation_with_fresh_questions(client, app, bank):  # noqa: F811
    a, b = await pair(client, app, 421)
    first = await started(client, a, b)
    await play(client, app, a, b, first)
    request_id = str(uuid.uuid4())
    created = await rematch(client, b, first, request_id)
    assert created.status_code == 201, created.text
    again = created.json()
    assert again["status"] == "invited" and again["me"]["role"] == "challenger"
    assert (await rematch(client, b, first, request_id)).json()["battleId"] == again["battleId"]  # idempotent
    assert (await rematch(client, a, first)).json()["error"]["code"] == "battle_exists"  # one open battle per pair
    async with app.state.db.sessionmaker() as db:
        invite = await db.scalar(
            select(UserNotification.title).where(
                UserNotification.user_id == uuid.UUID(a["user"]["id"]), UserNotification.kind == "battle_invite"
            )
        )
        assert invite == "Rematch invitation"
    second = again["battleId"]
    await client.post(f"{BASE}/battles/{second}/accept", headers=a["headers"])
    await client.post(f"{BASE}/battles/{second}/ready", headers=b["headers"])
    async with app.state.db.sessionmaker() as db:
        rows = await db.scalars(select(FzBattle).where(FzBattle.id.in_([uuid.UUID(first), uuid.UUID(second)])))
        asked = {str(row.id): set(row.question_ids) for row in rows}
        assert not asked[first] & asked[second]  # fresh questions: the pool holds enough
        assert await db.scalar(select(FzBattle.rematch_of).where(FzBattle.id == uuid.UUID(second))) == uuid.UUID(first)
    stranger, _ = await pair(client, app, 425)
    assert (await rematch(client, stranger, first)).status_code == 404  # not their battle


async def test_qa_members_never_earn_xp(client, app, bank):  # noqa: F811
    accounts = [await enrolled(client, app, number) for number in ("NH-FZ-0431", "NH-FZ-0432")]
    app.state.settings = make_settings(
        programs_mode="on", friend_zone_mode="on", friend_zone_qa_emails=",".join(a["email"] for a in accounts)
    )
    qa = []
    for account in accounts:
        joined = await client.post(
            f"{BASE}/me", json={"displayName": "QA member", "discoverable": True}, headers=account["headers"]
        )
        qa.append({**account, "player": joined.json()["playerId"]})
    await befriend(client, qa[0], qa[1])
    battle_id = await started(client, qa[0], qa[1])
    assert (await play(client, app, qa[0], qa[1], battle_id))["status"] == "completed"
    assert await xp_of(app, qa[0]) == {} and await xp_of(app, qa[1]) == {}
    async with app.state.db.sessionmaker() as db:  # and the database refuses one outright
        db.add(FzXpEvent(user_id=uuid.UUID(qa[0]["user"]["id"]), cohort="qa", reason="battle", ref_id="x", amount=20))
        with pytest.raises(IntegrityError):
            await db.commit()


# ------------------------------------------------------------------ U1, option ii: hash-bound AI checks
def _git(root, *args: str) -> str:
    who = ("-c", "user.email=test@example.com", "-c", "user.name=test")
    done = subprocess.run(["git", "-C", str(root), *who, *args], capture_output=True, text=True, check=True)
    return done.stdout.strip()


async def test_ai_checks_bind_only_unchanged_content_and_count_only_under_the_policy(app, tmp_path):
    root = netsec_bank(tmp_path / "qbank")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "the verified bank")
    verified = _git(tmp_path, "rev-parse", "HEAD")
    later = root / "content" / "ccna" / "CCNA-D3" / "questions.jsonl"
    later.write_text(later.read_text(encoding="utf-8") + "\n", encoding="utf-8")  # same questions, a later commit
    _git(tmp_path, "commit", "-qam", "a later change to one file")
    async with app.state.db.sessionmaker() as db:
        assert (await import_bank(db, root, apply=True)).applied
        found, skipped = bind_key_checks.candidates(root, verified)
        assert found and skipped["file changed since the verification commit"] > 0
        assert not any(question_id.startswith("CCNA-D3") for question_id in found)
        counts = await bind_key_checks.bind(db, found, verified, apply=True)
        assert counts["bound now"] == len(found) == await db.scalar(select(func.count()).select_from(QbKeyCheck))

        module = await db.scalar(
            select(ProgramModule.id)
            .join(Program, Program.id == ProgramModule.program_id)
            .where(Program.slug == "networking-ai", ProgramModule.month == 1)
        )
        db.add(ProgramModuleBook(module_id=module, book_id="ccna"))
        await db.commit()
        mcq = select(func.count()).select_from(QbQuestion).where(QbQuestion.question_format == "mcq")
        assert await battles.pool_size(db, module, None, "mixed", "human_only") == 0  # nothing approved by a person
        ai_pool = await battles.pool_size(db, module, None, "mixed", "human_or_ai")
        assert ai_pool == await db.scalar(mcq.where(QbQuestion.question_id.in_(list(found)))) > 0
        edited = await db.scalar(
            select(QbQuestion.question_id).where(
                QbQuestion.question_id.in_(list(found)), QbQuestion.question_format == "mcq"
            )
        )
        await db.execute(  # an edit changes the content hash: its binding no longer counts
            text("UPDATE qb_questions SET content_hash = repeat('f', 64) WHERE question_id = :q"), {"q": edited}
        )
        await db.commit()
        assert await battles.pool_size(db, module, None, "mixed", "human_or_ai") == ai_pool - 1


async def test_the_player_who_forfeits_earns_nothing(client, app, bank):  # noqa: F811
    """Phase 2 security review: answering four rounds and then walking away must not farm XP."""
    a, b = await pair(client, app, 441)
    battle_id = (
        await client.post(
            f"{BASE}/battles",
            json={
                "opponentId": b["player"],
                "program": "networking-ai",
                "month": 1,
                "length": 10,
                "requestId": str(uuid.uuid4()),
            },
            headers=a["headers"],
        )
    ).json()["battleId"]
    await client.post(f"{BASE}/battles/{battle_id}/accept", headers=b["headers"])
    await client.post(f"{BASE}/battles/{battle_id}/ready", headers=a["headers"])
    for round_ in range(1, 8):
        await at(app, battle_id, round_, "opened_at", -1)
        key = await key_of(app, battle_id, round_)
        await answer(client, a, battle_id, round_, key)
        if round_ <= 4:
            await answer(client, b, battle_id, round_, key)
        await at(app, battle_id, round_, "closes_at", -0.1)
    done = await state(client, a, battle_id)
    assert (done["status"], done["result"], done["me"]["winner"]) == ("completed", "forfeit", True)
    assert await xp_of(app, b) == {}  # answered 4 of 7 counted rounds, then forfeited
    assert await xp_of(app, a) == {"battle": 20}  # the remaining player: completion only, never win XP


def test_the_verification_commit_must_be_a_hash():
    for bad in ("HEAD", "--output=/tmp/x", "-c", "main", "7d8610", "zzzzzzz"):
        with pytest.raises(SystemExit):
            bind_key_checks.resolve(".", bad)
