"""The bank turn's request grammar (app/qbank/request.py) against ordinary engineering messages: session 1's red-team
probe (2026-11-19, finding 1). A false trigger replaces a real answer with a quiz, so none of these may parse as a
quiz request. Pure: no database, no model."""

import pytest

from app.qbank.request import parse

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
]


@pytest.mark.parametrize("message", ORDINARY)
def test_an_ordinary_engineering_message_is_not_a_quiz_request(message):
    assert parse(message) is None


@pytest.mark.parametrize("message", QUIZ_REQUESTS)
def test_a_quiz_request_still_parses(message):
    assert parse(message) is not None
