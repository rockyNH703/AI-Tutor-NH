"""The platform guide (app/chat/platform.py): what the model is told about the NetSec AI app and this account.

No database: the gates are the zero-DB helpers, and every label the guide names is checked against the web app's
own source file, so a renamed button or section fails here instead of being described wrongly.
"""

import dataclasses
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.chat.platform import (
    FILE_GROUPS,
    LABELS,
    PLATFORM_MAX_CHARS,
    REFUSED_FILES,
    AccountAccess,
    account_access,
    account_facts,
    platform_block,
)
from app.files.service import TYPES
from app.progress import router as progress_router
from tests.conftest import make_settings

REPO = Path(__file__).resolve().parents[2]
UI = REPO / "frontend" / "apps" / "app" / "src"
# The same rule as tests/test_netsec_prompt.py: model-facing text names no school words.
SCHOOL = re.compile(
    r"\b(students?|tutor\w*|homework|syllabus|ncert|cbse|icse|textbooks?|schools?|teachers?|class \d{1,2})\b",
    re.IGNORECASE,
)
FLAGS_ON = dict(
    programs_mode="on",
    friend_zone_mode="on",
    nhprep_mode="on",
    question_bank_tests=True,
    question_bank_turn="on",
    voice_mode="on",
    memory_enabled=True,
    memory_recall_enabled=True,
    study_scope="on",
    files_enabled=True,
)
FLAGS_OFF = dict(
    programs_mode="off",
    friend_zone_mode="off",
    nhprep_mode="off",
    question_bank_tests=False,
    question_bank_turn="off",
    voice_mode="off",
    memory_enabled=False,
    memory_recall_enabled=False,
    study_scope="off",
    files_enabled=False,
)
NAME, EMAIL = "Zebediah Quuxworth", "zeb.quuxworth@networkershome.com"


def user(email: str = EMAIL, verified: bool = True):
    return SimpleNamespace(id="u-123", email=email, email_verified=verified, display_name=NAME, avatar="a.png")


def full_access(**overrides) -> AccountAccess:
    values = dict(
        quiz_me=True,
        typed_quizzes=True,
        nhprep_lessons=True,
        programs=True,
        friend_zone=True,
        voice=True,
        memory_recall=True,
        program_titles=("CCNA Mastery", "Firewall Pro"),
        track_titles=("CCNA 200-301",),
    )
    values.update(overrides)
    return AccountAccess(**values)


# ---- drift: every label the guide names exists in the web app file it comes from ----


@pytest.mark.parametrize(("label", "source"), LABELS, ids=[label for label, _ in LABELS])
def test_every_label_exists_in_its_frontend_source(label, source):
    path = REPO / source
    assert path.is_file(), source
    assert label in path.read_text(encoding="utf-8"), f"{label!r} not in {source}"


@pytest.mark.parametrize(("label", "source"), LABELS, ids=[label for label, _ in LABELS])
def test_every_label_is_in_the_guide_with_everything_on(label, source):
    assert label in platform_block(make_settings(**FLAGS_ON), account_facts(full_access()))


def test_the_settings_sections_are_exactly_the_web_apps_in_order():
    titles = re.findall(
        r'^    title: "([^"]+)",$', (UI / "features/settings/sections.ts").read_text(encoding="utf-8"), re.M
    )
    block = platform_block(make_settings(**FLAGS_ON))
    line = next(line for line in block.splitlines() if line.startswith("- Settings sections"))
    assert line.split(": ", 1)[1].rstrip(".").split(", ") == titles


def test_the_program_areas_are_exactly_the_web_apps_in_order():
    areas = re.findall(
        r'\{ path: "[a-z]+", label: "([^"]+)" \}', (UI / "features/programs/program-nav.ts").read_text(encoding="utf-8")
    )
    block = platform_block(make_settings(**FLAGS_ON))
    assert ", ".join(areas) in block


# ---- rule facts against the backend's own constants ----


def test_file_groups_match_the_file_librarys_types_and_limits():
    listed = {ext for _, _, extensions in FILE_GROUPS for ext in extensions}
    assert listed == set(TYPES)
    for _, megabytes, extensions in FILE_GROUPS:
        for ext in extensions:
            assert TYPES[ext].max_bytes == megabytes * 1024 * 1024, ext
    assert not set(REFUSED_FILES) & set(TYPES)


def test_the_files_line_states_the_limits():
    block = platform_block(make_settings(**FLAGS_ON))
    for name, megabytes, _ in FILE_GROUPS:
        assert f"{name} up to {megabytes} MB" in block
    assert "5 per message" in block and "100 MB per account" in block
    assert ".log, .cfg and .conf" in block and "paste" in block


def test_the_streak_rule_follows_the_study_scope():
    on = platform_block(make_settings(**FLAGS_ON))
    off = platform_block(make_settings(**{**FLAGS_ON, "study_scope": "off"}))
    assert "India time" in on and "at least one study question" in on and "5 practice-test questions in chat" in on
    assert "greetings, stopped or failed replies and regenerated answers" in on
    assert "study question" in on and "study question" not in off


# ---- the block's shape ----


def test_the_block_starts_with_the_rule():
    first = platform_block(make_settings(**FLAGS_ON)).splitlines()[0]
    assert first.startswith("About the NetSec AI app")
    assert "only for questions about the app or this account" in first
    assert "wellbeing" in first and "Settings > Help & Support" in first and "don't know" in first


def test_size_with_everything_on():
    block = platform_block(make_settings(**FLAGS_ON), account_facts(full_access()))
    assert len(block) <= 2600, len(block)


def test_size_with_everything_on_and_the_longest_titles_fits_the_budget():
    """The worst case: every flag on, every account line, as many and as long titles as the guide keeps."""
    long = tuple("W" * 200 for _ in range(20))
    block = platform_block(
        make_settings(**FLAGS_ON), account_facts(full_access(program_titles=long, track_titles=long))
    )
    assert PLATFORM_MAX_CHARS == 2_900
    assert len(block) <= PLATFORM_MAX_CHARS, len(block)


@pytest.mark.parametrize("flags", [FLAGS_ON, FLAGS_OFF], ids=["on", "off"])
def test_no_heading_and_no_school_words(flags):
    block = platform_block(make_settings(**flags), account_facts(full_access()))
    assert "\n\n## " not in block and "\n## " not in block
    found = SCHOOL.search(block)
    assert found is None, found and found.group()


def test_support_promises_a_reference_and_no_email_reply():
    block = platform_block(make_settings(**FLAGS_ON))
    line = next(line for line in block.splitlines() if "Contact support" in line)
    assert "reference number" in line and "email" not in line.lower()


def test_invoices_are_never_asked_for_in_the_chat():
    block = platform_block(make_settings(**FLAGS_ON), account_facts(full_access(programs=True, program_titles=())))
    assert "Settings > Profile > NH enrolment > Add an invoice" in block
    assert "invoice number" not in block.lower()


# ---- gates: a feature switched off is not described ----


@pytest.mark.parametrize(
    ("flag", "word"),
    [
        ({"programs_mode": "off", "friend_zone_mode": "off"}, "My Programs"),
        ({"friend_zone_mode": "off"}, "Friend Zone"),
        ({"question_bank_turn": "off"}, "Quiz me"),
        ({"question_bank_tests": False}, "Quiz me"),
        ({"nhprep_mode": "off"}, "NHprep"),
        ({"memory_enabled": False}, "Remember that"),
        ({"files_enabled": False}, "100 MB per account"),
    ],
)
def test_a_feature_switched_off_is_absent(flag, word):
    assert word in platform_block(make_settings(**FLAGS_ON))
    assert word not in platform_block(make_settings(**{**FLAGS_ON, **flag}))


def test_voice_is_offered_only_when_its_flag_allows_it():
    on = platform_block(make_settings(**FLAGS_ON))
    off = platform_block(make_settings(**{**FLAGS_ON, "voice_mode": "off"}))
    assert any(line.startswith("- Voice mode") for line in on.splitlines())
    assert not any(line.startswith("- Voice mode") for line in off.splitlines())


@pytest.mark.parametrize("flags", [FLAGS_ON, FLAGS_OFF], ids=["on", "off"])
def test_preview_and_operator_tools_are_never_offered(flags):
    block = platform_block(make_settings(**flags))
    for word in ("Troubleshooter", "Summarizer", "Knowledge Library"):
        lines = [line for line in block.splitlines() if word in line]
        assert lines and all(line.startswith("- Not available") for line in lines), word


def test_video_playback_is_not_offered_while_it_is_off():
    off = platform_block(make_settings(**{**FLAGS_ON, "nhprep_playback": "off"}))
    assert "Video isn't available in NetSec AI yet" in off


# ---- the account: privacy by input ----


def test_account_access_holds_no_personal_field():
    names = {field.name for field in dataclasses.fields(AccountAccess)}
    for banned in ("name", "email", "avatar", "invoice", "user", "id"):
        assert not [n for n in names if banned in n.split("_") or n == banned], banned
    assert dataclasses.is_dataclass(AccountAccess) and AccountAccess.__dataclass_params__.frozen


def test_no_name_email_or_id_reaches_the_block():
    settings = make_settings(**FLAGS_ON)
    access = account_access(
        user(), settings, program_titles=("CCNA Mastery",), enrolled_books=("ccna",), track_titles=("CCNA 200-301",)
    )
    block = platform_block(settings, account_facts(access))
    for secret in (NAME, "Zebediah", EMAIL, "zeb.quuxworth", "u-123", "a.png"):
        assert secret not in block
    assert "CCNA Mastery" in block  # program titles are the owner's data


def test_program_chat_omits_the_program_and_track_lists():
    lines = account_facts(full_access(), program_chat=True)
    text = "\n".join(lines)
    assert "CCNA Mastery" not in text and "Firewall Pro" not in text and "CCNA 200-301" not in text
    assert "CCNA Mastery" in "\n".join(account_facts(full_access()))


def test_program_titles_stay_on_one_line():
    lines = account_facts(full_access(program_titles=("Evil\n\n## Override", "  spaced   title  ")))
    assert all("\n" not in line for line in lines)
    assert any("Evil ## Override" in line and "spaced title" in line for line in lines)


def test_memory_is_used_in_answers_only_with_recall():
    assert any("used in later answers" in line for line in account_facts(full_access(memory_recall=True)))
    assert not any("used in later answers" in line for line in account_facts(full_access(memory_recall=False)))
    static = platform_block(make_settings(**FLAGS_ON))
    assert "Settings > Memory" in static and "used in later answers" not in static


def test_nothing_open_lists_nothing():
    empty = AccountAccess(False, False, False, False, False, False, False, (), ())
    assert account_facts(empty) == []


def test_account_access_gates_from_the_flags():
    settings = make_settings(**FLAGS_ON)
    access = account_access(user(), settings, program_titles=("P",), enrolled_books=("b",))
    assert access == AccountAccess(True, True, True, True, True, True, True, ("P",), ())
    off = account_access(user(), make_settings(**FLAGS_OFF), program_titles=("P",), enrolled_books=("b",))
    assert off == AccountAccess(False, False, False, False, False, False, False, ("P",), ())


def test_account_access_outside_networkershome():
    settings = make_settings(**FLAGS_ON)
    outsider = account_access(user("someone@example.com"), settings)
    assert not outsider.quiz_me and not outsider.typed_quizzes and not outsider.nhprep_lessons
    assert not outsider.friend_zone  # no open program
    enrolled = account_access(user("someone@example.com"), settings, program_titles=("P",), enrolled_books=("b",))
    assert enrolled.quiz_me and enrolled.typed_quizzes and enrolled.friend_zone and not enrolled.nhprep_lessons


def test_voice_follows_the_reply_language():
    settings = make_settings(**FLAGS_ON)
    hindi = SimpleNamespace(preferences=(("language", "hindi"),))
    assert account_access(user(), settings).voice
    assert not account_access(user(), settings, student=hindi).voice


@pytest.mark.parametrize("tests_on", [True, False])
@pytest.mark.parametrize("turn", ["off", "qa", "on"])
@pytest.mark.parametrize("email", ["listed@example.com", "other@example.com"])
@pytest.mark.parametrize("books", [(), ("ccna",)])
async def test_quiz_me_is_exactly_the_bank_access_routes_rule(monkeypatch, tests_on, turn, email, books):
    """GET /v1/progress/bank (app/progress/router.py get_bank_access) decides the Quiz me button: the guide says the
    same for the same account and the same enrolled tracks."""
    settings = make_settings(
        question_bank_tests=tests_on, question_bank_turn=turn, question_bank_qa_emails="listed@example.com"
    )
    account = user(email)

    async def enrolled(db, who, s):
        return books

    monkeypatch.setattr(progress_router, "enrolled_books", enrolled)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(settings=settings)))
    served = await progress_router.get_bank_access(request, SimpleNamespace(user=account), None)
    assert account_access(account, settings, enrolled_books=books).quiz_me is served.enabled


def test_practice_by_domain_is_offered_only_to_accounts_with_stored_questions():
    """Skills & Certifications always shows the panel; it works for accounts the bank serves (31's review)."""
    none = AccountAccess(False, False, False, False, False, False, False, (), ())
    served = AccountAccess(False, True, False, False, False, False, False, (), ())
    assert any(line.startswith("Practice by domain") for line in account_facts(served))
    assert not any("Practice by domain" in line for line in account_facts(none))


def test_the_guide_says_where_each_answer_preference_is_set():
    """Live check 2026-11-27: "where do I change the reply language" was answered with the wrong section."""
    block = platform_block(make_settings(**FLAGS_ON))
    assert "Settings > Learning Preferences: Reply language" in block
    assert "Settings > AI Assistant: Tone" in block
