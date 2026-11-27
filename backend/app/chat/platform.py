"""The platform guide: what the model is told about the NetSec AI app itself and about this account.

Pure text from settings and zero-DB gates, no I/O. The caller (app/chat/service.py) reads the open program titles and
enrolled tracks and passes them in; everything else comes from the same helpers the routes use. Every UI label named
here is listed in LABELS with the web app file it comes from (tests/test_platform_guide.py checks each one there).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.access.students import is_nh_student
from app.files.service import MAX_ATTACHMENTS, QUOTA_BYTES, MiB
from app.friendzone.access import enabled as friend_zone_enabled
from app.learning.context import StudentContext
from app.nhprep.chat import nhprep_allows
from app.programs.access import programs_allow
from app.progress.learning import PRACTICE_ANSWERS
from app.settings import Settings
from app.voice.policy import speaks_their_language

_UI = "frontend/apps/app/src/"
_SIDEBAR = _UI + "components/sidebar.tsx"

SETTINGS_SECTIONS = (
    "Profile",
    "Engineering Profile",
    "Skills & Certification Goals",
    "Learning Preferences",
    "AI Assistant",
    "Memory",
    "Practice & Lab Time",
    "Practice & Goals",
    "Lab Tracking",
    "Practice Streak",
    "Notifications",
    "Appearance & Accessibility",
    "Privacy & Security",
    "Help & Support",
)
PROGRAM_AREAS = ("Course Module", "QnA", "Quizzes & Games", "Mock Interviews", "Videos & Links")
# Where each answer preference lives (frontend learning-labels.ts LEARNING_FIELDS / TUTOR_FIELDS), so "where do I change
# X" is answered from fact.
LEARNING_PREFERENCES = ("Reply language", "Depth", "Pace", "Difficulty", "Real-world examples", "Visual explanations")
AI_ASSISTANT = (
    "Tone",
    "Answer length",
    "How it answers problems",
    "Step-by-step",
    "Check what I know first",
    "Practice questions",
    "Correcting mistakes",
    "Highlight key ideas",
)

# (label, the web app file it appears in): the drift test reads each file.
LABELS: tuple[tuple[str, str], ...] = (
    *((label, _SIDEBAR) for label in ("New chat", "Skills & Certifications", "File Library")),
    *((label, _SIDEBAR) for label in ("Lab Activity", "My Programs", "Settings", "Archived chats", "Log out")),
    *((title, _UI + "features/settings/sections.ts") for title in SETTINGS_SECTIONS),
    *((label, _UI + "features/settings/learning-labels.ts") for label in (*LEARNING_PREFERENCES, *AI_ASSISTANT)),
    *(
        (label, _UI + "features/settings/help-section.tsx")
        for label in ("Help & FAQ", "About NetSec AI", "Contact support")
    ),
    *(
        (label, _UI + "features/conversations/chat-actions-menu.tsx")
        for label in ("Share", "Rename", "Pin", "Archive", "Delete")
    ),
    *(
        (label, _UI + "features/chat/message-actions.tsx")
        for label in ("Copy reply", "Regenerate reply", "Good reply", "Bad reply")
    ),
    ("Start a lab session", _UI + "features/study/study-control.tsx"),
    *((area, _UI + "features/programs/program-nav.ts") for area in PROGRAM_AREAS),
    *((label, _UI + "features/friendzone/quizzes-switch.tsx") for label in ("Practice", "Friend Zone")),
    *((label, _UI + "features/programs/invoice-flow.tsx") for label in ("NH enrolment", "Add an invoice")),
    *((label, _UI + "features/chat/composer.tsx") for label in ("Quiz me", "Voice mode")),
    *((label, _UI + "components/nav-items.ts") for label in ("Troubleshooter", "Summarizer")),
    ("Practice by domain", _UI + "features/my-learning/my-learning-view.tsx"),
    ("Knowledge Library", _UI + "components/app-shell.tsx"),
    ("Video isn't available in NetSec AI yet", _UI + "features/nhprep/video-player.tsx"),
)

# (what the guide calls them, MB, extensions): tests check these against app/files/service.py TYPES.
FILE_GROUPS: tuple[tuple[str, int, tuple[str, ...]], ...] = (
    ("PDF, DOCX, PPTX", 25, (".pdf", ".docx", ".pptx")),
    ("XLSX, PNG, JPG, WEBP", 10, (".xlsx", ".png", ".jpg", ".jpeg", ".webp")),
    ("CSV, TXT, MD", 2, (".csv", ".txt", ".md", ".markdown")),
)
REFUSED_FILES = (".log", ".cfg", ".conf")

ENROL = "Settings > Profile > NH enrolment > Add an invoice"
RULE = (
    "About the NetSec AI app: only for questions about the app or this account, never in wellbeing or personal "
    "replies. Not here? Say you don't know and point to Settings > Help & Support. Never invent features, courses, "
    "prices, permissions or account details."
)
# The guide's ceiling with every feature on (tests/test_platform_guide.py; the history budget reserves it). The title
# caps keep even the worst-case account lines under it: NH has four programs today.
PLATFORM_MAX_CHARS = 2_900
_TITLE_MAX, _TITLES_MAX = 40, 4


@dataclass(frozen=True)
class AccountAccess:
    """What this account can use. Only booleans and owner-written titles: never a name, address, invoice or id."""

    quiz_me: bool
    typed_quizzes: bool
    nhprep_lessons: bool
    programs: bool
    friend_zone: bool
    voice: bool
    memory_recall: bool
    program_titles: tuple[str, ...]
    track_titles: tuple[str, ...]


def account_access(
    user: Any,
    settings: Settings,
    *,
    student: StudentContext | None = None,
    program_titles: Sequence[str] = (),
    enrolled_books: Sequence[str] = (),
    track_titles: Sequence[str] = (),
) -> AccountAccess:
    """`program_titles`: the programs the account's invoices open now; `enrolled_books`: app.programs.access
    enrolled_books (it already applies PROGRAMS_MODE); `track_titles`: those tracks' titles."""
    email = getattr(user, "email", None) or ""
    bank_on = settings.question_bank_tests and settings.question_bank_turn_allows(email)
    return AccountAccess(
        # Exactly GET /v1/progress/bank (app/progress/router.py get_bank_access): the Quiz me button.
        quiz_me=bank_on and bool(enrolled_books),
        # The bank turn serves an entitled account (app/programs/access.py bank_scope).
        typed_quizzes=bank_on and (is_nh_student(user, settings) or bool(enrolled_books)),
        nhprep_lessons=nhprep_allows(user, settings),
        programs=programs_allow(user, settings),
        # Friend Zone's own gate plus its eligibility: an invoice opens a program now (app/friendzone/access.py).
        friend_zone=friend_zone_enabled(user, settings) and bool(program_titles),
        voice=settings.voice_rollout_allows(email) and speaks_their_language(student),
        memory_recall=settings.memory_enabled and settings.memory_recall_enabled,
        program_titles=tuple(program_titles),
        track_titles=tuple(track_titles),
    )


def _titles(titles: Sequence[str]) -> str:
    """Owner-written titles on one line: whitespace collapsed (no line breaks, so no headings), each one capped."""
    cleaned = (" ".join(str(title).split())[:_TITLE_MAX] for title in titles[:_TITLES_MAX])
    return "; ".join(title for title in cleaned if title)


def account_facts(access: AccountAccess, *, program_chat: bool = False) -> list[str]:
    """Short lines of what this account can use; a program chat leaves out the program and track lists."""
    lines = []
    if access.programs and not program_chat:
        titles = _titles(access.program_titles)
        lines.append(f"My Programs: {titles}." if titles else f"My Programs: none yet ({ENROL} opens one).")
    if access.quiz_me:
        lines.append("Quiz me button.")
    if access.typed_quizzes:
        tracks = "" if program_chat else _titles(access.track_titles)
        lines.append(f"Quizzes in the chat{f' ({tracks})' if tracks else ''}.")
        lines.append("Practice by domain in Skills & Certifications.")
    if access.nhprep_lessons:
        lines.append("NHprep lesson cards.")
    if access.friend_zone:
        lines.append("Friend Zone.")
    if access.voice:
        lines.append("Voice mode.")
    if access.memory_recall:
        lines.append("Saved memories are used in later answers.")
    return lines


def _files_line() -> str:
    groups = "; ".join(f"{name} up to {megabytes} MB" for name, megabytes, _ in FILE_GROUPS)
    refused = f"{', '.join(REFUSED_FILES[:-1])} and {REFUSED_FILES[-1]}"
    return (
        f"- Files (File Library or {MAX_ATTACHMENTS} per message, {QUOTA_BYTES // MiB} MB per account): "
        f"{groups}; {refused} refused (paste text)."
    )


def platform_block(settings: Settings, account: Sequence[str] = ()) -> str:
    """The guide, gated by the flags that switch each feature on, then what this account can use."""
    programs = settings.programs_mode != "off"
    friend_zone = programs and settings.friend_zone_mode != "off"
    nhprep = settings.nhprep_mode != "off"
    voice = settings.voice_mode != "off"
    question = "study question" if settings.study_scope == "on" else "real question"
    lines = [
        RULE,
        "- Sidebar: New chat, Skills & Certifications, File Library, Lab Activity (Start a lab session)"
        f"{', My Programs' if programs else ''}; account menu: Settings, Archived chats, Log out.",
        f"- Settings sections: {', '.join(SETTINGS_SECTIONS)}.",
        f"- Settings > Learning Preferences: {', '.join(LEARNING_PREFERENCES)}. Settings > AI Assistant: "
        f"{', '.join(AI_ASSISTANT)}.",
        "- Settings > Help & Support: Help & FAQ, About NetSec AI, Contact support (gives a reference number).",
        "- Chat menu: Share, Rename, Pin, Archive, Delete. Reply: Copy reply, Regenerate reply, Good reply, Bad reply.",
        f"- Practice streak: a day (India time) counts when NetSec AI fully answered at least one {question} of "
        f"the user's, or they answered {PRACTICE_ANSWERS} practice-test questions in chat; greetings, stopped or "
        "failed replies and regenerated answers don't.",
    ]
    if settings.files_enabled:
        lines.append(_files_line())
    if settings.memory_enabled:
        lines.append("- Memory: 'Remember that …' saves to Settings > Memory; 'Forget that …' removes.")
    if settings.question_bank_tests and settings.question_bank_turn != "off":
        lines.append(
            "- Quizzes: ask in chat (NetworkersHome accounts, NH program tracks); the Quiz me button needs an NH "
            "invoice for a program with quiz tracks."
        )
    if programs:
        switch = " Quizzes & Games: Practice | Friend Zone." if friend_zone else ""
        lines.append(f"- My Programs: {', '.join(PROGRAM_AREAS)}. Open one: {ENROL}.{switch}")
    if nhprep:
        lines.append("- NHprep lesson cards: NetworkersHome accounts.")
    if (programs or nhprep) and settings.nhprep_playback == "off":
        lines.append('- Videos: "Video isn\'t available in NetSec AI yet."')
    if voice:
        lines.append("- Voice mode (message box): English replies only.")
    lines.append(
        f"- Not available: Troubleshooter, Summarizer{'' if voice else ', Voice mode'}; "
        "Knowledge Library (operators only)."
    )
    lines.append("This account:")
    lines.extend(f"- {line}" for line in account or ("Only the places above.",))
    return "\n".join(lines)
