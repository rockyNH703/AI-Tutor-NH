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
    ("aws saa", "aws_saa"), ("saa", "aws_saa"), ("solutions architect", "aws_saa"),
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
    r"(?:(?:practice|exam|mock|mcq|certification|interview|more|new|different|other|quick|short|mixed|small|mini"
    r"|full|random|multiple[- ](?:select|correct|choice)|multi[- ]?select|single[- ](?:correct|choice)"
    r"|select[- ]all(?:[- ]that[- ]apply)?)\s+)*"
)
_TRACK_ALT = "|".join(re.escape(word) for word, _ in _TRACKS)
_TRACK_WORDS = r"(?:" + _TRACK_ALT + r")\s+"
# What a request asks for. A bare "test" is not one: "create a test VLAN", "run a test from R1" (session 1's red team,
# 2026-11-19). A practice, mock or exam test is.
_ASKED = (
    r"(?:questions?|mcqs?|quiz(?:zes)?|problems?|multiple[- ]choice(?: questions?)?"
    r"|(?<=practice )tests?|(?<=mock )tests?|(?<=exam )tests?|(?<=mock )exams?|(?<=practice )exams?)"
)
# ...and what follows it: a topic ("on/about/for/from ...") or nothing. "give me the questions an interviewer asks",
# "I need questions answered about NAT" and "send me the test script" are not requests.
_THEN = r"(?=\s+(?:on|about|for|from|regarding|covering|related\s+to|in)\b|\s+at\s+(?:me|us)\b|\s*(?:please|pls)?\s*$)"
_CUES = [
    re.compile(r"\b(?:quiz|test|examine|drill)\s+(?:me|us)\b"),
    re.compile(r"\btest\s+my\s+(?:knowledge|understanding|skills?)\b"),
    re.compile(
        r"\b(?:give|ask|send|throw|fire|generate|make|create|prepare|(?:can|could|may)\s+(?:i|we)\s+(?:get|have|try|take|do))\s+"
        r"(?:me\s+|us\s+)?(?:a\s+|an\s+|the\s+|some\s+)?(?:"
        + _COUNTW
        + r"\s+)?(?:more\s+)?"
        + _LEVEL
        + _KIND
        + r"(?:"
        + _TRACK_WORDS
        + r")?"
        + _LEVEL
        + _KIND
        + _ASKED
        + _THEN
    ),
    re.compile(  # "I want 10 questions on X", "I'd like a practice quiz covering X"
        r"\b(?:want|need|(?:i'?d|i\s+would)\s+like)\s+(?:(?:"
        + _COUNTW
        + r"|a|an|some)\s+)?"
        + _LEVEL
        + _KIND
        + r"(?:"
        + _TRACK_WORDS
        + r")?"
        + _LEVEL
        + _KIND
        + _ASKED
        + _THEN
    ),
    re.compile(  # "subnetting questions for my ccna"
        r"\b" + _ASKED + r"\s+for\s+(?:my|the)\s+(?:" + _TRACK_ALT + r")(?![\w+])"
    ),
    re.compile(  # "CCNA practice questions please", "Security+ practice questions": a named track, nothing else
        r"^(?:(?:ok(?:ay)?|now|so|please|pls|hey|hi)\s+)*(?:(?:a|an|some)\s+)?(?:"
        + _COUNTW
        + r"\s+)?"
        + _LEVEL
        + _TRACK_WORDS
        + _LEVEL
        + _KIND
        + _ASKED
        + r"\s*(?:please|pls)?\s*$"
    ),
    re.compile(
        r"^(?:(?:ok(?:ay)?|now|so|please|pls|then|great|nice|cool|sure|hey|hi)\s+)*(?:(?:a|an|some)\s+)?(?:"
        + _COUNTW
        + r"\s+)?"
        + _LEVEL
        + _KIND
        + r"(?:"
        + _TRACK_WORDS
        + r")?"
        + _LEVEL
        + _KIND
        + _ASKED
        + r"\s+(?:on|about|for|from|regarding|in|covering|related to)\b"
    ),
    re.compile(  # "start a quiz / mock test (on X)": a bare test is "run a test from R1 to R2"
        r"\b(?:start|begin|take|do|launch|try)\s+(?:a\s+|an\s+|the\s+)?"
        + _LEVEL
        + r"(?:quiz|(?:mock|practice)\s+(?:test|exam))"
        + _THEN
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
    r"|i'?ve\s+got\s+(?:some\s+)?questions?|my\s+questions?|your\s+questions?|questions?\s+(?:i|we)\s+(?:have|had)"
    r"|(?:quiz|test)\s+(?:me|us)\s+(?:later|tomorrow|afterwards|after|next\s+time)|interviewers?)\b"
)
_ASKING = re.compile(
    r"^(?:what|how|why|when|where|which|who|whom|whose|is|are|was|were|does|do|did|should|would|will|explain|define|"
    r"describe|difference|compare|tell\s+me\s+about|help\s+me\s+(?:understand|with))\b"
)
# A weak cue ("practice questions on X", "quiz on X") in a message that is really asking or reporting something
# ("questions on my mind: why is OSPF stuck in EXSTART?") is left to the model.
_QUESTIONING = re.compile(
    r"\b(?:why|how|what|explain|can'?t|cannot|isn'?t|doesn'?t|won'?t|stuck|confused|useless|help)\b"
)
_STRONG = re.compile(r"\b(?:quiz|test)\s+(?:me|us)\b|\btest\s+my\s+knowledge\b|\b(?:give|ask|send)\s+(?:me|us)\b")
_MULTI = re.compile(
    r"\b(?:multiple[- ](?:correct|select)|multi[- ]?select|multiple[- ]answers?|more\s+than\s+one\s+correct"
    r"|select[- ]all(?:[- ]that[- ]apply)?"
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
    "puchho", "ke", "ka", "ki", "i'd", "like", "have", "try", "quick", "short", "mixed", "small",
    "mini", "full", "random", "throw", "fire", "at", "upar", "lo", "le", "karo", "karwao", "kara", "skill", "skills",
    "drill", "examine", "generate", "make", "create", "prepare",
    "problems", "problem", "quizzes", "tests", "one", "by", "let", "lets", "let's", "i", "be", "tested",
}  # fmt: skip
_WATCHED = re.compile(
    r"\b(?:the|this|that)\s+(?:video|lesson|class|session)\s+(?:i|we)\s+(?:just\s+)?"
    r"(?:watched|saw|finished|completed|did|attended)\b"
)
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
    # "quiz me", "test me", "give/ask me ...": the student asked for a quiz in so many words. A weaker cue is answered
    # by the bank only when it finds a topic (app/chat/bank_turn.py); otherwise the model answers.
    strong: bool = True


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
    # The count goes ("5 hard questions"); a number that is part of the topic stays ("layer 2", "rfc 1918").
    text = re.sub(r"\b\d{1,3}\b(?=\s+(?:more\s+)?(?:[a-z+-]+\s+){0,3}?" + _ITEMS + r"\b)", " ", text)
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
    strong = bool(_STRONG.search(text))
    if not strong and (_ASKING.match(text) or _QUESTIONING.search(_QUOTED.sub(" ", text))):
        return None
    track, rest = _track(text)
    topic, lesson = _topic(rest)
    refers = not topic or bool(_CHAT.match(topic)) or bool(_WATCHED.search(text))
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
        strong=strong,
    )
