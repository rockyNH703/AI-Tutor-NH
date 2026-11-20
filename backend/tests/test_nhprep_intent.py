"""NHprep's cue parse (app/nhprep/intent.py, plan rev 4 §8.4): explicit cues only, never the bank's quiz wording,
never ordinary chat. Pure: no database, no provider."""

from __future__ import annotations

import pytest

from app.nhprep.intent import parse
from app.nhprep.types import NhprepQuery


@pytest.mark.parametrize(
    ("message", "text", "views"),
    [
        ("interview questions on BGP", "bgp", ("interview",)),
        ("flashcards on OSPF", "ospf", ("flashcards",)),
        ("show me the ospf dr election video", "ospf dr election", ("video",)),
        ("is there a video on BGP route reflectors?", "bgp route reflectors", ("video",)),
        ("ospf video", "ospf", ("video",)),
        ("which lesson covers vlan trunking", "vlan trunking", ("video",)),
        ("notes and flashcards for dhcp snooping", "dhcp snooping", ("notes", "flashcards")),
        ("show me the chapters of the eigrp video", "eigrp", ("chapters", "video")),
        ("cheat sheet for fortigate ha", "ha", ("cheat_sheet",)),
        ("vedio on hsrp plz", "hsrp", ("video",)),
        ("can you find the class where he explains nat", "nat", ("video",)),
        ("show me the part on ospf lsa types", "ospf lsa types", ("chapters",)),
    ],
)
def test_cued_requests_are_parsed(message: str, text: str, views: tuple[str, ...]) -> None:
    query = parse(message)
    assert query is not None
    assert (query.text, query.views) == (text, views)


@pytest.mark.parametrize(
    "message",
    [
        "quiz me on OSPF",  # the bank's
        "questions from this video",  # the bank's: questions about a video are a quiz
        "give me 5 practice questions on subnetting",
        "test my knowledge of BGP with a mock exam",
        "explain OSPF areas",  # ordinary chat: no cue
        "my bgp session keeps flapping",
        "how do I prioritise voice and video traffic on a WAN link?",
        "what is classful addressing",
        "configure a class-map for video",
        "what does the session layer do",
        "summarize this into notes",
        "what part of the ip header holds the ttl",
        "lessons learned from the outage",
        "what are the release notes for IOS XE 17.9",
        "show me the video for this",  # a reference and no earlier message: nothing to find
        "",
    ],
)
def test_bank_wording_and_ordinary_chat_are_left_alone(message: str) -> None:
    assert parse(message) is None


def test_trainer_course_and_day_are_read() -> None:
    assert parse("ajay ccna day 8") == NhprepQuery(
        text="", views=("video",), course_hint="ccna", trainer_hint="ajay", day=8
    )
    query = parse("day 5 of mr sushil palo alto class")
    assert query is not None
    assert (query.trainer_hint, query.course_hint, query.day) == ("mr sushil", "palo alto", 5)
    query = parse("sushil sir palo alto nat video")
    assert query is not None and (query.text, query.trainer_hint) == ("nat", "sushil")


def test_an_unknown_trainer_after_an_honorific_is_kept_as_typed() -> None:
    query = parse("video by mr bharath on static routing")
    assert query is not None and (query.text, query.trainer_hint) == ("static routing", "mr bharath")


def test_a_reference_reads_the_latest_earlier_message_with_a_topic() -> None:
    query = parse("show me the video for this", ["thanks", "explain OSPF LSA types"])
    assert query is not None and query.text == "ospf lsa types"


def test_a_very_long_message_is_not_parsed() -> None:
    assert parse("show me the ospf video " + "x " * 200) is None
