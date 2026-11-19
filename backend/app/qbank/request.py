"""Whether a chat message asks the certification bank for a quiz, and what it asks for: pure rules, no model.

The grammar is strict and verb-led on purpose. A quiz request is answered by the app itself (app/chat/bank_turn.py),
so reading an ordinary question as a quiz request would replace a real answer. Anything unclear is left to the
normal turn. Recognised:
- "quiz me / test me (on X)", "test my knowledge of X", "can you quiz me on X?";
- "give / ask / send me [N] [hard] questions / MCQs (on X)";
- "N MCQs on X", "practice questions on X", "CCNA questions on X", "quiz on X", "start a quiz / mock test (on X)";
- Hinglish: "ospf pe 5 mcq do";
- "next 5", "5 more", "more questions": the next batch of the latest test.
Refused: questions about something ("what/how/why …", "I have questions about …"), labs and other kinds of
testing (pen/unit/ping/speed test, test lab), exam logistics (date, fee, voucher) and flashcards or interview
questions (NHprep's cards).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

DEFAULT_COUNT = 5
MAX_COUNT = 30

_TYPOS = {
    "qiuz": "quiz", "quizz": "quiz", "quize": "quiz", "quis": "quiz", "tset": "test", "tets": "test",
    "mcq's": "mcqs", "mcqz": "mcqs", "mcqss": "mcqs", "questons": "questions", "quesitons": "questions",
    "qustions": "questions", "questins": "questions", "quetions": "questions", "questiosn": "questions",
    "quiestions": "questions", "qs": "questions", "giv": "give", "gimme": "give me", "plz": "please",
}  # fmt: skip
_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "fifteen": 15, "twenty": 20, "thirty": 30, "a few": 5, "few": 5, "some": DEFAULT_COUNT, "a couple of": 2,
}  # fmt: skip
_DIFFICULTY = {
    "easy": "easy", "basic": "easy", "simple": "easy", "beginner": "easy",
    "medium": "medium", "moderate": "medium", "intermediate": "medium",
    "hard": "hard", "tough": "hard", "difficult": "hard", "advanced": "hard", "tricky": "hard",
    "challenging": "hard", "expert": "hard",
}  # fmt: skip
# Track words -> catalog.CERTIFICATIONS codes. Longer phrases first.
_TRACKS = (
    ("ccnp enterprise", "ccnp_enterprise"), ("ccnp security", "ccnp_security"),
    ("ccnp data center", "ccnp_data_center"),
    ("ccie security", "ccie_security"), ("ccie enterprise", "ccie_enterprise"), ("ccie", "ccie_enterprise"),
    ("encor", "ccnp_enterprise"), ("enarsi", "ccnp_enterprise"), ("ccnp", "ccnp_enterprise"), ("scor", "ccnp_security"),
    ("ccna", "ccna"), ("jncia", "jncia"), ("jncis", "jncis"), ("pcnse", "pcnse"), ("palo alto", "pcnse"),
    ("fortinet", "fortinet_fcp"), ("fortigate", "fortinet_fcp"), ("network+", "comptia_network"),
    ("security+", "comptia_security"), ("cysa+", "comptia_cysa"), ("pentest+", "comptia_pentest"), ("ceh", "ceh"),
    ("oscp", "oscp"), ("cissp", "cissp"), ("aws", "aws_saa"), ("az-104", "az_104"), ("az-500", "az_500"),
    ("az-900", "az_900"), ("az-305", "az_305"), ("cpent", "cpent"), ("cka", "cka"),
)  # fmt: skip

_ITEMS = r"(?:questions?|mcqs?|quiz(?:zes)?|tests?|problems?|multiple[- ]choice(?: questions?)?)"
_LEVEL = r"(?:(?:" + "|".join(_DIFFICULTY) + r")\s+)?"
_COUNTW = r"(?:\d{1,3}|" + "|".join(sorted(_NUMBERS, key=len, reverse=True)) + r")"
_KIND = (
    r"(?:(?:practice|exam|mock|mcq|certification|interview|more|new|different|other"
    r"|multiple[- ](?:select|correct|choice)|multi[- ]?select|single[- ](?:correct|choice))\s+)*"
)
_TRACK_WORDS = r"(?:" + "|".join(re.escape(word) for word, _ in _TRACKS) + r")\s+"
_CUES = [
    re.compile(r"\b(?:quiz|test|examine|drill)\s+(?:me|us)\b"),
    re.compile(r"\btest\s+my\s+(?:knowledge|understanding|skills?)\b"),
    re.compile(
        r"\b(?:give|ask|send|show|share|throw|fire|generate|make|create|prepare|set|get|want|need)\s+"
        r"(?:me\s+|us\s+)?(?:a\s+|an\s+|the\s+)?(?:"
        + _COUNTW
        + r"\s+)?(?:more\s+)?"
        + _LEVEL
        + _KIND
        + r"(?:"
        + _TRACK_WORDS
        + r")?"
        + _LEVEL
        + _KIND
        + _ITEMS
        + r"\b"
    ),
    re.compile(
        r"^(?:(?:ok(?:ay)?|now|so|please|pls|then|great|nice|cool|sure|hey|hi)\s+)*(?:"
        + _COUNTW
        + r"\s+)?"
        + _LEVEL
        + _KIND
        + r"(?:"
        + _TRACK_WORDS
        + r")?"
        + _LEVEL
        + _KIND
        + _ITEMS
        + r"\s+(?:on|about|for|from|regarding|in|of|covering|related to)\b"
    ),
    re.compile(
        r"\b(?:start|begin|take|do|run|launch|try)\s+(?:a\s+|an\s+|the\s+)?" + _LEVEL + _KIND + r"(?:quiz|test)\b"
    ),
    re.compile(
        r"\b(?:pe|par|ke|ka|ki)\s+(?:" + _COUNTW + r"\s+)?" + _ITEMS + r"\s+(?:do|dedo|de\s+do|chahiye|pucho|puchho)\b"
    ),
    re.compile(r"\b" + _ITEMS + r"\s+(?:do|dedo|de\s+do|chahiye|pucho|puchho|lo|le\s+lo|karo|karwao|kara\s+do)\b"),
]
_NEXT = re.compile(
    r"^(?:(?:ok(?:ay)?|now|so|please|pls|then|great|nice|cool|sure)\s+)*(?:give\s+me\s+|send\s+me\s+|ask\s+me\s+)?"
    r"(?:(?:the\s+)?next(?:\s+"
    + _COUNTW
    + r")?|"
    + _COUNTW
    + r"\s+more|more|another\s+(?:"
    + _COUNTW
    + r"|set|batch|round)"
    r"|one\s+more\s+(?:set|batch|round))(?:\s+(?:questions?|mcqs?|ones?|please|pls|set|batch))*$"
)
_BLOCKED = re.compile(
    r"\b(?:test\s+(?:lab|bed|environment|plan|cases?|network|topology|setup|aaa|connectivity|the|it|this|my\s+config)"
    r"|(?:pen(?:etration)?|unit|ping|speed|load|stress|a/b|smoke|regression|integration|lab|bandwidth|throughput)\s+tests?"
    r"|practice\s+lab|lab\s+practice|exam\s+(?:date|fee|fees|voucher|registration|pattern|format|center|centre|policy|cost|duration|syllabus|topics|blueprint|tips|strategy)"
    r"|interview\s+(?:questions?|prep)|flash\s*cards?|cheat\s*sheets?|example|best\s+practices?|i\s+have\s+(?:some\s+|a\s+few\s+)?questions?"
    r"|i'?ve\s+got\s+(?:some\s+)?questions?|my\s+questions?|your\s+questions?|questions?\s+(?:i|we)\s+(?:have|had))\b"
)
_ASKING = re.compile(
    r"^(?:what|how|why|when|where|which|who|whom|whose|is|are|was|were|does|do|did|should|would|will|explain|define|"
    r"describe|difference|compare|tell\s+me\s+about|help\s+me\s+(?:understand|with))\b"
)
_STRONG = re.compile(r"\b(?:quiz|test)\s+(?:me|us)\b|\btest\s+my\s+knowledge\b|\b(?:give|ask|send)\s+(?:me|us)\b")
_MULTI = re.compile(
    r"\b(?:multiple[- ](?:correct|select)|multi[- ]?select|multiple[- ]answers?|more\s+than\s+one\s+correct"
    r"|select\s+all|choose\s+(?:two|three|all)|multi[- ]correct)\b"
)
_CHAT = re.compile(
    r"^(?:on\s+|about\s+|from\s+|for\s+)?(?:this|that|it|these|those|the\s+above|above|same|the\s+same|what\s+we\s+"
    r"(?:discussed|covered|talked\s+about)|this\s+(?:topic|lesson|video|chapter|concept)|that\s+(?:topic|lesson|video))?"
    r"(?:\s+(?:topic|lesson|video|chapter|concept))?$"
)
_STOP = {
    "me", "us", "on", "about", "for", "from", "regarding", "in", "of", "covering", "related", "to", "the", "a", "an",
    "some", "please", "pls", "plz", "can", "could", "you", "would", "will", "quiz", "test", "questions", "question",
    "mcqs", "mcq", "practice", "exam", "mock", "give", "ask", "send", "show", "share", "start", "begin", "take", "now",
    "ok", "okay", "so", "then", "my", "knowledge", "understanding", "and", "with", "only", "set", "get", "want", "need",
    "lesson", "video", "topic", "chapter", "certification", "level", "few", "more", "new", "multiple", "choice",
    "correct", "answers", "answer", "select", "all", "pe", "par", "do", "dedo", "de", "chahiye", "pucho",
    "puchho", "ke", "ka", "ki", "upar", "lo", "le", "karo", "karwao", "kara", "skill", "skills",
    "drill", "examine", "generate", "make", "create", "prepare",
    "problems", "problem", "quizzes", "tests", "one", "by", "let", "lets", "let's", "i", "be", "tested",
}  # fmt: skip
_QUOTED = re.compile(r"[\"“”']([^\"“”']{3,120})[\"“”']")
_NAMED = re.compile(r"\b(?:the\s+)?(?:lesson|video|session|class)\s+(?:called\s+|named\s+|titled\s+|on\s+)?(.+)$")


@dataclass(frozen=True)
class Request:
    route: str  # "bank": a new test; "next": the next batch of the latest bank test
    count: int = DEFAULT_COUNT
    difficulty: str | None = None
    multi_only: bool = False
    track: str | None = None
    topic: str = ""
    refers_to_chat: bool = False
    lesson_named: bool = False


def normalise(message: str) -> str:
    text = message.casefold().replace("’", "'").replace("`", "'")
    text = re.sub(r"[?!.,;:]+(?=\s|$)", " ", text)
    words = [_TYPOS.get(word, word) for word in text.split()]
    return " ".join(words).strip()


def _count(text: str) -> int:
    found = re.search(
        r"\b(\d{1,3})\s+(?:more\s+)?" + _LEVEL + _KIND + r"(?:" + _TRACK_WORDS + r")?" + _LEVEL + _KIND + _ITEMS, text
    )
    if found is None:
        found = re.search(r"\b(?:next|another|give\s+me|ask\s+me)\s+(\d{1,3})\b|\b(\d{1,3})\s+more\b", text)
    if found is not None:
        value = int(next(group for group in found.groups() if group))
        return max(1, min(value, MAX_COUNT))
    for word, value in sorted(_NUMBERS.items(), key=lambda item: -len(item[0])):
        if re.search(r"\b" + re.escape(word) + r"\s+(?:more\s+)?" + _LEVEL + _KIND + _ITEMS, text):
            return value
    return DEFAULT_COUNT


def _difficulty(text: str) -> str | None:
    for word, level in _DIFFICULTY.items():
        if re.search(r"\b" + word + r"\b", text):
            return level
    return None


def _track(text: str) -> tuple[str | None, str]:
    for word, code in _TRACKS:
        pattern = r"(?<![\w+])" + re.escape(word) + r"(?![\w+])"
        if re.search(pattern, text):
            return code, re.sub(pattern, " ", text)
    return None, text


def _topic(text: str) -> tuple[str, bool]:
    quoted = _QUOTED.search(text)
    if quoted:
        return quoted.group(1).strip(), True
    named = _NAMED.search(text)
    if named and not _CHAT.match(named.group(1).strip()):
        return _clean(named.group(1)), True
    return _clean(text), False


def _clean(text: str) -> str:
    text = re.sub(r"\b\d{1,3}\b", " ", text)
    for word in _DIFFICULTY:
        text = re.sub(r"\b" + word + r"\b", " ", text)
    text = _MULTI.sub(" ", text)
    words = [word.strip("'\"()[]") for word in text.split()]
    kept = [word for word in words if word and word not in _STOP and word not in _NUMBERS]
    return " ".join(kept)


def parse(message: str) -> Request | None:
    """The quiz request in a message, or None (an ordinary message: the normal turn answers it)."""
    text = normalise(message or "")
    if not text or len(text) > 300:
        return None
    if _NEXT.match(text):
        return Request("next", count=_count(text) if re.search(r"\d|" + "|".join(_NUMBERS), text) else DEFAULT_COUNT)
    if _BLOCKED.search(text):
        return None
    if not any(cue.search(text) for cue in _CUES):
        return None
    if _ASKING.match(text) and not _STRONG.search(text):
        return None
    track, rest = _track(text)
    topic, lesson = _topic(rest)
    refers = not topic or bool(_CHAT.match(topic))
    if refers and topic in {"", "it"} and not re.search(r"\b(?:this|that|it|above|same|discussed|covered)\b", rest):
        refers = False  # "quiz me" / "quiz me on CCNA": no topic, nothing in the chat named
    return Request(
        "bank",
        count=_count(text),
        difficulty=_difficulty(_QUOTED.sub(" ", text)),
        multi_only=bool(_MULTI.search(text)),
        track=track,
        topic="" if refers else topic,
        refers_to_chat=refers,
        lesson_named=lesson,
    )
