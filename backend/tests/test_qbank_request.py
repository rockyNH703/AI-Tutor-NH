"""The bank turn's request grammar (app/qbank/request.py) against ordinary engineering messages: session 1's red-team
probe (2026-11-19, finding 1). A false trigger replaces a real answer with a quiz, so none of these may parse as a
quiz request. Pure: no database, no model."""

import pytest

from app.qbank.finder import Topic, _decide, corrected
from app.qbank.request import parse


def _topic(book: str, topic_id: str, *, covered: int, named: int, exact: bool = False, similar: float = 0.3) -> Topic:
    return Topic(
        topic_id, f"title {topic_id}", topic_id.rsplit("-", 1)[0], "Domain", book, book.upper(), 1, "general",
        "english", exact, False, covered, named, 0.0, 0.1, similar, 5,
    )  # fmt: skip


def test_the_students_goal_book_wins_when_it_has_a_credible_topic():
    # 54 books (2026-11-20): "wifi channels" is an exact alias in the Wireless book, but a CCNA student's CCNA
    # topic covers both words: the goal book answers.
    words = ["wifi", "channels"]
    other = _topic("wireless_networking", "WIRELESS-D1-T03", covered=2, named=4, exact=True)
    mine = _topic("ccna", "CCNA-D1-T09", covered=2, named=4)
    found = _decide([other, mine], words, None, "ccna", {})
    assert found.status == "confident" and found.topics[0].topic_id == "CCNA-D1-T09"
    # no goal, or a named track: the exact match stands
    assert _decide([other, mine], words, None, None, {}).topics[0].topic_id == "WIRELESS-D1-T03"
    assert _decide([other, mine], words, "wireless_networking", "ccna", {}).topics[0].topic_id == "WIRELESS-D1-T03"


def test_a_broad_term_whose_best_topic_cannot_fill_a_batch_widens_to_the_book_that_covers_it():
    # session 1's Quiz me check (2026-11-20): "quiz me on bgp" served one 3-question topic of Routing and Switching
    # Essentials (an exact alias) while BGP in the Real World has 13 BGP topics.
    words = ["bgp"]
    small = _topic("routing_switching", "RS-D2-T01", covered=1, named=2, exact=True)
    small = Topic(**{**small.__dict__, "available": 3})
    deep = [_topic("bgp_real_world", f"BGP-D1-T{n:02d}", covered=1, named=2) for n in range(1, 14)]
    found = _decide([small, *deep], words, None, None, {})
    assert found.status == "broad" and {t.book_id for t in found.topics} == {"bgp_real_world"}
    # a confident topic that can fill a batch stays confident
    big = Topic(**{**small.__dict__, "available": 12})
    assert _decide([big, *deep], words, None, None, {}).status == "confident"


def test_with_no_goal_the_strongest_topic_decides_the_book_not_the_book_with_most_matches():
    # holdout5 (2026-11-20): CCNA's broad aliases matched more topics, so "pat" went to CCNA's NAT topic although the
    # NAT book has a topic named for it.
    words = ["pat"]
    ccna = [_topic("ccna", f"CCNA-D4-T0{n}", covered=1, named=2) for n in (1, 2)]
    special = _topic("nat_deep_dive", "NAT-D2-T03", covered=1, named=3, similar=0.9)
    found = _decide([*ccna, special], words, None, None, {})
    assert found.topics[0].topic_id == "NAT-D2-T03"


def test_a_request_the_bank_only_half_knows_does_not_get_a_confident_wrong_topic():
    # "jinja2 templates": the bank has no "jinja2", so only "templates" was left and Panorama templates were served
    weak = _topic("palo_alto", "PAN-D2-T04", covered=1, named=1)
    assert _decide([weak], ["templates"], None, None, {}, partial=True).status == "none"
    # an alias the student named is still enough
    named = _topic("ansible_networks", "ANS-D2-T01", covered=1, named=2)
    assert _decide([named], ["templates"], None, None, {}, partial=True).status != "none"


def test_a_goal_book_that_only_brushes_the_words_does_not_take_over():
    words = ["kubernetes", "networking"]
    other = _topic("kubernetes_networking", "K8S-D1-T01", covered=2, named=2)
    mine = _topic("ccna", "CCNA-D6-T02", covered=1, named=1)  # "networking" in a title only
    assert _decide([other, mine], words, None, "ccna", {}).topics[0].topic_id == "K8S-D1-T01"


def test_a_short_word_is_corrected_only_when_its_letters_are_swapped():
    # "cool" is not "pool" (a NAT pool): holdout 2 served NAT for "cool, now quiz me on it". "opsf" is ospf.
    known = frozenset({"pool", "ospf", "bgp", "nat", "vlan"})
    assert corrected(["cool", "opsf", "bpg", "vlna", "nta"], known) == ["cool", "ospf", "bgp", "vlan", "nat"]
    assert corrected(["spannning"], frozenset({"spanning"})) == ["spanning"]
    # one letter missing from a short term, same first letter: "jsn" is json (holdout 3)
    assert corrected(["jsn", "cool"], frozenset({"json", "pool", "col"})) == ["json", "cool"]


ORDINARY = [
    "create a test VLAN for the lab",
    "create a test user in AD",
    "create a test policy on the FortiGate",
    "generate test traffic with iperf",
    "generate a test certificate with openssl",
    "run a test on the link",
    "run a test from R1 to R2",
    "run the test again",
    "start a test of the backup link",
    "start a test call on the SIP trunk",
    "do a test failover of the HSRP pair",
    "take a test backup of the config",
    "make a test route-map for the lab",
    "make a test ACL that denies telnet",
    "set a test static route to 10.1.1.0/24",
    "I want a test environment for BGP",
    "I need a test plan for the cutover",
    "need a test topology for EIGRP",
    "get a test license for the Palo Alto",
    "show me the test commands for IP SLA",
    "send me the test script",
    "share the test results with me",
    "give me a test subnet for 50 hosts",
    "give me a test IP for the DMZ",
    "want to test my config, here it is",
    "I need questions answered about NAT",
    "I need some questions answered about VPN tunnels",
    "I have a few questions on IPsec phase 1",
    "some questions about BGP: why is the session idle?",
    "questions on my mind: why is OSPF stuck in EXSTART?",
    "questions about my lab: R2 can't ping R3",
    "practice questions on ospf are useless, explain DR election instead",
    "the exam questions on STP confused me, can you explain root bridge election",
    "quiz results showed I'm weak at ACLs, explain wildcard masks",
    "my practice test score was low on subnetting, how do I get better?",
    "interview questions on BGP",
    "give me the questions an interviewer asks about BGP",
    "what questions should I ask the ISP about the circuit",
    "which questions come in CCNA about OSPF",
    "how many questions are in the CCNA exam",
    "is the CCNA exam multiple choice?",
    "test failed on the IPsec tunnel, here's the debug",
    "the ping test failed between sites",
    "test ospf adjacency on R1",
    "test connectivity from the jump host",
    "tested the ACL and it still blocks ssh",
    "test case: R1 cannot reach 8.8.8.8",
    "show me the questions from yesterday's exam",
    "quiz me later, first explain OSPF areas",
    # Guards for the request frames (2026-11-19, holdout 2): a frame plus a question word is not always a quiz.
    "let me ask you some questions about BGP",
    "can you review the questions I wrote for my team",
    "set me up a lab for OSPF",
    "can you explain the questions on the exam",
    "how about we look at the OSPF config first",
    "what about NAT overload, how does it work",
    "I need my questions about VPNs cleared up",
    "could you answer a few questions on IPsec for me",
    "I want to know which questions are on the CCNA exam",
    "best me at this",
    "a quick question on syslog levels: is it stateful?",
    "can you check my answers to these vlan questions: 1 is B, 2 is D",
    # Guards for a quiz noun standing alone (holdout 3, 2026-11-19), in this file's own words.
    "could you suggest questions to ask the vendor about their firewall?",
    "test my understanding by explaining where my ACL logic breaks",
    "walk me through the OSPF commands one by one, no quiz please",
    "I'd like my quiz score explained",
    "my practice test results say I'm weak on NAT, why?",
    "the mock exam had a weird question about STP, can you explain it",
    "is there a quiz feature in this app?",
    "clear my quiz history",
    "the quiz portal on our LAN uses port 9443, allow it in the ACL",
    "those practice questions claim EIGRP is a link-state protocol, correct?",
    "build quiz software in Flask",
    "honestly I need a rest from mcqs today",
    "could you tell me if this is a good question format for my team",
    # Holdout6 (2026-11-20): build requests and the student's own questions, in this file's own words.
    "write a script that turns a CSV into quiz questions for students",
    "have the model generate MCQs on OSPF from our wiki",
    "I'd like to ask a few questions about Meraki licensing",
    "can I ask some questions on SD-WAN pricing",
    "vlan questions came up in my interview yesterday",
    "I have so many bgp questions",
    "any idea why these questions about NAT confuse students",
    "design a database for quiz questions and answers",
    # Holdout7 (2026-11-20): negation, recommendations, answer keys, build requests, a question about the exam.
    "don't give me a quiz, just explain eigrp",
    "i do not want questions now, what is vrrp?",
    "can you recommend websites with ccna practice questions",
    "send me the answer key for the mcq test",
    "could you write a bash script that generates practice questions from a file",
    "questions about the exam lab: do we get console access?",
]

QUIZ_REQUESTS = [
    "quiz me on ospf",
    "test me on vlans",
    "give me 5 questions on spanning tree",
    "give me 10 CCNA practice questions",
    "5 hard mcqs on etherchannel",
    "practice questions on network security",
    "start a quiz on JSON encoded data",
    "start a mock test on ccna",
    "can you quiz me on rest apis?",
    "test my knowledge of eui 64",
    "ospf pe 5 mcq do",
    "mcqs from this lesson",
    "next 5",
    # Phrasings the held-out set showed the grammar missed (session 1, 2026-11-19), in this file's own words.
    "can I get some questions on static routes?",
    "could I have a few mcqs on nat",
    "throw a few mcqs at me on vlans",
    "fire some questions at me about ospf",
    "I'd like a practice quiz covering acls",
    "i would like a quick quiz on stp",
    "a mixed quiz on network access",
    "an easy quiz on ipv6",
    "subnetting questions for my ccna",
    "a CCNA quiz on hsrp",
    "CCNA practice questions please",
    "Security+ practice questions",
    "select-all-that-apply questions on acls",
    "test me on the video I just watched",
    # Request frames (session 1's holdout 2, 2026-11-19), in this file's own words: a quiz word plus a request frame.
    "what about a few mcqs on dhcp",
    "check how well I know ospf with a quiz",
    "I'd like to be tested on spanning tree",
    "time for some questions on acls",
    "could you set me a quiz about nat",
    "fire away, questions on ipv6 please",
    "let me practise vlans with a couple of questions",
    "I need an etherchannel quiz",
    "I'll take 10 questions on wireless",
    "set me 4 tricky mcqs on hsrp",
    "exam-style questions on qos",
    "I want CCNA mock questions",
    "let's do some questions on what you just explained",
    "tset me on vlans",
    "qiz me on ospf pls",
    # A quiz noun phrase on its own, and frames holdout 3 showed missing (2026-11-19), in this file's own words.
    "quiz time! subnetting",
    "assess me on ipv6 addressing",
    "kick me off with some questions on nat",
    "run a short quiz on stp",
    "practice set on wireless security",
    "would really love some mcqs about acls",
    "a complete network access quiz",
    "practice questions across ip connectivity",
    "a 6-question hard quiz on bgp",
    "do 3 questions on vlans with me",
    "I'm after 8 mcqs on etherchannel",
    "dhcp snooping: ccna practice questions",
    "begin a CISSP practice test",
    "test what I learned just now",
    "a quiz on that would help now",
    "can I get a quiz on how the root bridge is chosen?",
    "carry on, next 3",
    "next batch please",
    "3 more like these",
    "keep them coming",
    # Holdout6 (2026-11-20): a topic, then the quiz noun, then only the request's settings; more frames and "more".
    "hsrp quiz please, advanced level",
    "eigrp metric mcqs, tricky ones",
    "nat overload 6 questions",
    "route summarization questions, 12 hard ones",
    "wildcard mask practice questions, lots",
    "dns record types quiz (a, aaaa, mx)",
    "vxlan mcqs but only multi-select",
    "ipv6 questions, ccna level",
    "Python for Network Engineers course questions, 6",
    "pop quiz on ipsec",
    "revision quiz: acl placement",
    "prep questions on syslog please",
    "grill me on ospf areas",
    "can I be quizzed on etherchannel?",
    "shoot me some ntp questions",
    "I'd appreciate some questions about snmp",
    "5 easy questions to check my subnetting",
    "give me 7 advanced test questions on bgp",
    "give me multiple-answer questions on qos",
    "only choose-two questions on stp",
    "aur 5 do",
    "aur mcqs chahiye",
    "another round, 8 this time",
    "more like these, 4",
    "give me more of those",
    "same topic, next few",
    "iska quiz lo",
    # Holdout7 (2026-11-20): "u", Hinglish next, "nxt", "bhejo", trailing "give me N", "question set".
    "can u quiz me on stp",
    "agla batch do",
    "ek aur set",
    "nxt 5",
    "eigrp mcqs bhejo",
    "bgp questions, give me 6",
    "ospf question set",
]


def test_a_numbered_quiz_reads_its_count():
    request = parse("a 6-question hard quiz on bgp")
    assert request is not None and request.count == 6 and request.difficulty == "hard" and request.topic == "bgp"


@pytest.mark.parametrize(
    "message, refers, topic",
    [
        ("cool, now quiz me on it", True, ""),
        ("great, test me on this one", True, ""),
        ("let's do some questions on what you just explained", True, ""),
        ("check how well I know ospf with a quiz", False, "ospf"),
        ("I'd like to be tested on spanning tree", False, "spanning tree"),
        ("questions on the last thing you said", True, ""),
        ("test what I learned just now", True, ""),
        ("a quiz on that would help now", True, ""),
        ("practice questions across ip connectivity", False, "ip connectivity"),
        ("quiz time! subnetting", False, "subnetting"),
        ("now quiz me", True, ""),
        ("test me", True, ""),
        ("quiz me on that, tough ones", True, ""),
        ("test me on this stuff", True, ""),
        ("quiz me on the difference", True, ""),
        ("ab iske upar questions do", True, ""),
        ("hsrp quiz please, advanced level", False, "hsrp"),
        ("nat overload 6 questions", False, "nat overload"),
        ("isi pe quiz do", True, ""),
        ("mujhe iske baare me questions do", True, ""),
        ("can u test me on this", True, ""),
    ],
)
def test_a_frame_request_reads_its_topic_or_the_chat(message, refers, topic):
    request = parse(message)
    assert request is not None and request.refers_to_chat is refers and request.topic == topic


@pytest.mark.parametrize("message", ORDINARY)
def test_an_ordinary_engineering_message_is_not_a_quiz_request(message):
    assert parse(message) is None


@pytest.mark.parametrize("message", QUIZ_REQUESTS)
def test_a_quiz_request_still_parses(message):
    assert parse(message) is not None
