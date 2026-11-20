"""Whether a typed chat message asks for an NHprep lesson (video, chapters, notes, cheat sheet, flashcards, interview
questions), and what it names: pure rules, no I/O, deterministic (plan rev 4 §8.3-§8.4).

It runs after the bank's strict parse (app/qbank/request.py) and never claims quiz, test, practice or exam wording:
"questions from this video" and "quiz me on OSPF" are the bank's. Ordinary chat ("explain OSPF areas", "my BGP session
is stuck", "QoS for voice and video") is left to the model: a request needs an explicit cue, and a weak cue word
(class, session, recording) needs a request frame ("show me", "is there", "where can I watch").
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.nhprep.normalize import TRAINER_ALIASES
from app.nhprep.types import NhprepQuery, View
from app.qbank.request import parse as bank_parse

MAX_LENGTH = 300
MAX_TOPIC_WORDS = 12

_TYPOS = {
    "vedio": "video", "vidoe": "video", "viedo": "video", "vdo": "video", "vid": "video", "vids": "videos",
    "vedios": "videos", "vidoes": "videos", "lecure": "lecture", "lectur": "lecture", "leson": "lesson",
    "lession": "lesson", "lessn": "lesson", "flashcard": "flashcards", "flashcrds": "flashcards",
    "flascards": "flashcards", "cheatsheet": "cheat sheet", "cheatsheets": "cheat sheet", "cheat-sheet": "cheat sheet",
    "intervew": "interview", "interveiw": "interview", "intreview": "interview", "qustions": "questions",
    "questins": "questions", "quetions": "questions", "pls": "please", "plz": "please", "u": "you", "wat": "what",
}  # fmt: skip

# Quiz, test, practice and exam wording is the bank's (plan §8.4); so is "questions" unless they are interview ones.
_BANK_WORDS = re.compile(
    r"\b(?:quiz\w*|mcqs?|tests?|testing|tested|exams?|mock|practi[cs]e\w*|assess\w*)\b"
    r"|(?<!interview )(?<!interview prep )\bquestions?\b"
)
# Phrases where a cue word means something else; removed before the cues are read.
_NOT_CUES = re.compile(
    r"\b(?:voice\s+and\s+video|video\s+(?:traffic|streams?|streaming|calls?|calling|conferenc\w*|codecs?|qos|packets?"
    r"|surveillance|quality|bandwidth|frames?|over\s+ip|cameras?|ports?|cards?|drivers?|editing|files?|games?)"
    r"|(?:ip|live|hd|4k|realtime|real-time)\s+video|release\s+notes?|sticky\s+notes?|lessons?\s+learn[et]d?"
    r"|(?:summari[sz]e|write|make|create|prepare|draft|generate|rewrite|convert|turn|take|taking|jot|keep)\b"
    r"(?:\s+\S+){0,4}?\s+notes?"
    r"|class[- ]?(?:maps?|of\s+service|based|ful|less|[a-e])"
    r"|(?:traffic|address|ip|network|service|object)\s+class(?:es)?"
    r"|classful|classless|first[- ]class|(?:world|high|top)[- ]class"
    r"|(?:bgp|ssh|telnet|tcp|udp|tls|ssl|ipsec|ike|vpn|sip|rdp|bfd|ldp|pim|msdp|http|https|console|vty|user|login|web"
    r"|remote|terminal|netconf|nat|stateful|idle|active|established|peer|neighbou?r|span|rspan|monitor|capture|debug"
    r"|ftp|smtp|radius|tacacs|ldap|kerberos|ospf|eigrp|isis|is-is|rsvp|mpls|gre)\s+sessions?"
    r"|sessions?\s+(?:layer|timeouts?|hijack\w*|keys?|tokens?|ids?|cookies?|table|states?|persistence|management|border"
    r"|initiation|limits?|resumption|tickets?|affinity|establishment|establish\w*|count|flaps?|resets?|drops?|is|are|was"
    r"|keeps?|went|goes|stays?|gets?)"
    r"|(?:section|chapter)\s+\d+(?:\.\d+)*\s+(?:of|in)\s+(?:the|my|a|this)\s+\w+"
    r"|(?:call|packet|screen|session)\s+recordings?|recording\s+(?:rules?|devices?|policy|policies))\b"
)
_STRONG = (  # a study aid NHprep serves, or NHprep by name: enough on its own
    (re.compile(r"\bflash\s?cards?\b"), "flashcards"),
    (re.compile(r"\b(?:cheat\s?sheets?|command\s+sheets?)\b"), "cheat_sheet"),
    (re.compile(r"\binterview\s+(?:questions?|q\s?&\s?as?|qna|prep(?:aration)?)\b"), "interview"),
    (re.compile(r"\bnhprep\b"), "video"),
)
_MEDIUM = (  # a lesson word: needs a request frame, or a topic after it in a message that isn't asking something
    (
        re.compile(r"\b(?:videos?|lectures?|tutorials?|lessons?|recorded\s+(?:class|session|lecture)(?:es|s)?)\b"),
        "video",
    ),
    (re.compile(r"\bnotes?\b"), "notes"),
    (re.compile(r"\b(?:chapters?|sections?|time\s?stamps?|outline)\b"), "chapters"),
    # "the part where he explains NAT", "the bit on LSAs": a section asked for ("what part of the header" is not)
    (re.compile(r"\bthe\s+(?:part|bit|portion|segment)(?=\s+(?:on|about|where|covering|explaining)\b)"), "chapters"),
)
_WEAK = ((re.compile(r"\b(?:class(?:es)?|sessions?|recordings?)\b"), "video"),)  # needs a frame or another cue
_FRAME = re.compile(
    r"\b(?:show|give|send|share|find|get|play|open|watch|load|pull\s+up|bring\s+up|link|recommend|suggest|need|want"
    r"|wanna|looking\s+for|look\s+for|search(?:\s+for)?|fetch|where\s+(?:is|are|can\s+i|do\s+i|'?s)|is\s+there"
    r"|are\s+there|do\s+you\s+have|have\s+you\s+got|got\s+any|take\s+me\s+to|jump\s+to|go\s+to"
    r"|start|resume|continue|rewatch|re-watch|replay)\b"
)
_FRAME_REACH = 8  # words between the frame and the cue word
_NEAR_FRAME = re.compile(r"\b(?:which|what|any)\b")  # "which video covers X": the cue within two words
_NEAR_REACH = 1
_TOPIC_AFTER = re.compile(
    r"\s+(?:on|about|for|of|covering|explaining|regarding|related\s+to|from|by|where|that|which|in|with|please|and|or"
    r"|plus|&)(?=\s|$)|\s*$"
)
_ASKING = re.compile(
    r"^(?:what|why|how|when|where(?!\s+(?:is|are|can\s+i|do\s+i|'?s)\b)|who|whom|whose|is(?!\s+there)|are(?!\s+there)"
    r"|was|were|does|did|do(?!\s+you\s+have)|should|would|will|can(?!\s+(?:you|i)\s+(?:show|give|send|share|find|get"
    r"|play|open|watch|have|see|link))|could(?!\s+you\s+(?:show|give|send|share|find|get|play|open|link))|explain"
    r"|define|describe|compare|tell\s+me\s+about|difference|summari[sz]e|help\s+me\s+(?:understand|with)"
    r"|configure|set\s+up|setup|write|create|build|troubleshoot|fix|debug|design|implement|calculate|convert)\b"
)
_DAY = re.compile(r"\bday[\s-]?(\d{1,3})\b")
# Distinctive course words (certifications, products): a course hint, not a topic, when other topic words remain.
_COURSES = (
    ("ccna", "ccna"), ("ccnp", "ccnp"), ("ccie", "ccie"), ("devnet", "devnet"), ("forti\\s?gate", "fortigate"),
    ("palo\\s?alto", "palo alto"), ("check\\s?point", "checkpoint"), ("linux", "linux"), ("aws", "aws"),
    ("azure", "azure"), ("cisco\\s+ise|ise", "ise"), ("asa", "asa"), ("ftd", "ftd"), ("sd[\\s-]?wan", "sd wan"),
    ("sd[\\s-]?access", "sd access"), ("ceh", "ceh"), ("splunk", "splunk"), ("f5", "f5"), ("wsa", "wsa"),
    ("esa", "esa"), ("kubernetes|k8s", "kubernetes"), ("ansible", "ansible"), ("bug\\s+bounty", "bug bounty"),
)  # fmt: skip
_COURSE = re.compile(r"(?<![\w-])(?:" + "|".join(pattern for pattern, _ in _COURSES) + r")(?![\w-])")
_HONORIFIC = re.compile(r"\b(?:mr|trainer|instructor|faculty)\s+([a-z]{3,20})\b")
_REFERENCES = {"this", "that", "it", "same", "above", "these", "those", "one", "topic", "concept", "thing", "lesson"}
_STOP = frozenset(
    """
    & a about again all also an and any are as at available be both bring by can card cards chapter chapters cheat
    class classes command complete continue could course courses covered covering covers day do does explained
    explaining explains faculty fetch find flash flashcards for from full get give go got has have he hello her hey
    hi him his i i'd in instructor interview is it's its jump just kindly learn learning lecture lectures lesson
    lessons let let's lets like link load look looking me mr my need nhprep note notes now of ok okay on open or
    outline part play please pls plus prep preparation pull q&a q&as qna question questions re-watch recommend
    recorded recording recordings regarding related replay resume revise revision rewatch search section sections
    see send session sessions share she sheet sheets show sir so some stamps start study studying suggest take
    taught teaches thank thanks that the then there there's time timestamp timestamps to trainer tutorial tutorials
    up us video videos wanna want was watch watching well were what where which who whole will with would you your
    explain describe define tell how why does work works part bit portion segment
    """.split()
)


def normalise(message: str) -> str:
    text = message.casefold().replace("’", "'").replace("`", "'")
    text = re.sub(r"[?!.,;:()\[\]\"]+(?=\s|$)|^[\"(\[]+|(?<=\s)[\"(\[]+", " ", text)
    text = re.sub(r"\b(\w+)'s\b", r"\1", text)  # "ajay's class" -> "ajay class"
    words = [_TYPOS.get(word, word) for word in text.split()]
    return " ".join(words).strip()


def _views(text: str, named: bool) -> list[tuple[int, View]]:
    """Every cue word's position and view. With a trainer named, a weak cue counts as a
    lesson word ("the NAT class by Mr Sushil")."""
    found: list[tuple[int, View]] = []
    for strength, cues in (("strong", _STRONG), ("medium", _MEDIUM), ("weak", _WEAK)):
        for pattern, view in cues:
            for match in pattern.finditer(text):
                if strength == "medium" and not _accepted_medium(text, match):
                    continue
                if strength == "weak" and not (
                    _accepted_medium(text, match) if named else _framed(text, match.start())
                ):
                    continue
                found.append((match.start(), view))  # type: ignore[arg-type]
    found.sort()
    return found


def _framed(text: str, start: int) -> bool:
    return any(
        frame.end() <= start and len(text[frame.end() : start].split()) <= reach
        for pattern, reach in ((_FRAME, _FRAME_REACH), (_NEAR_FRAME, _NEAR_REACH))
        for frame in pattern.finditer(text)
    )


def _accepted_medium(text: str, match: re.Match[str]) -> bool:
    if _framed(text, match.start()):
        return True
    return bool(_TOPIC_AFTER.match(text, match.end())) and not _ASKING.match(text)


def _trainer(text: str) -> tuple[str | None, str]:
    """A trainer named in the alias table (bare, "mr X", "X sir"), or any name after an honorific; the rest."""
    words = text.split()
    for i, word in enumerate(words):
        letters = re.sub("[^a-z]", "", word)
        joined = letters + re.sub("[^a-z]", "", words[i + 1]) if i + 1 < len(words) else ""
        if letters in ("mr", "trainer") and joined in TRAINER_ALIASES:
            return " ".join(words[i : i + 2]), " ".join(words[:i] + words[i + 2 :])
        if letters in TRAINER_ALIASES:
            return word, " ".join(words[:i] + words[i + 1 :])
    named = _HONORIFIC.search(text)
    if named:  # an unknown name: kept as typed, so the finder answers "none" rather than guess (precision 1.0)
        return named.group(0), text[: named.start()] + " " + text[named.end() :]
    return None, text


def _course(text: str) -> str | None:
    found = _COURSE.search(text)
    if found is None:
        return None
    return next(name for pattern, name in _COURSES if re.fullmatch(pattern, found.group(0)))


def _topic(text: str) -> list[str]:
    words = [word.strip("'\"-/") for word in text.split()]
    return [word for word in words if word and word not in _STOP and not word.isdigit()]


def _refers(words: list[str]) -> bool:
    return not words or all(word in _REFERENCES for word in words)


def parse(message: str, context: Sequence[str] = ()) -> NhprepQuery | None:
    """The NHprep request in a typed message, or None (not one: the bank or the normal turn answers it).

    `context`: the student's earlier messages, newest first; read only when the topic is "this" / "it".
    """
    text = normalise(message or "")
    if not text or len(text) > MAX_LENGTH or _BANK_WORDS.search(text) or bank_parse(message) is not None:
        return None
    masked = _NOT_CUES.sub(lambda m: " " * len(m.group(0)), text)  # cue words only: the topic keeps "classful"
    trainer, rest = _trainer(text)
    found = _views(masked, trainer is not None)
    day_match = _DAY.search(text)
    course = _course(rest)
    named = trainer is not None and (course is not None or bool(found))
    if not found and not named and not (day_match and (course or trainer)):
        return None
    if not found and _ASKING.match(masked) and not named:
        return None
    day = int(day_match.group(1)) if day_match else None
    if day_match:
        rest = _DAY.sub(" ", rest)
    words = _topic(rest)
    without_course = _topic(_COURSE.sub(" ", rest))
    if course and (without_course or day is not None):  # "ajay ccna day 8": the day names the lesson
        words = without_course
    if words and _refers(words):
        words = next((_topic(normalise(earlier)) for earlier in context if not _refers(_topic(normalise(earlier)))), [])
    if not words and day is None:
        return None
    views: list[View] = []
    for _, view in found:
        if view not in views:
            views.append(view)
    return NhprepQuery(
        text=" ".join(words[:MAX_TOPIC_WORDS]),
        views=tuple(views) or ("video",),
        course_hint=course,
        trainer_hint=trainer,
        day=day,
    )
