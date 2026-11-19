"""The question kinds of a schemaVersion 2 practice test (app/assessments/kinds.py, rules.py): each is parsed
whole or refused, graded by the server, keeps its key hidden until final, and reaches the assistant only in the
form a client sees before answering. Pure: no request, no model."""

import json
import time
from itertools import combinations
from os.path import commonprefix

import pytest

from app.assessments import rules, state
from app.qbank import chat_tests

AT = "2026-11-18T10:00:00+00:00"
DIAGRAM = {
    "nodes": [
        {"id": "r1", "label": "R1", "kind": "router"},
        {"id": "fw1", "label": "FW1", "kind": "firewall", "detail": "Edge firewall"},
        {"id": "sw1", "label": "SW1", "kind": "switch"},
    ],
    "links": [{"id": "l1", "from": "r1", "to": "fw1", "label": "Gi0/0"}, {"id": "l2", "from": "fw1", "to": "sw1"}],
}
# Every kind: the author's question, a right value and a wrong one (None: computed from the shown order).
KINDS = {
    "mcq": ({"question": "Which port does SSH use?", "options": ["22", "23", "443"], "answer": "A"}, 0, 1),
    "multi": (
        {
            "type": "multi",
            "question": "Which ranges are private?",
            "options": ["10.0.0.0/8", "8.8.8.0/24", "192.168.0.0/16"],
            "answer": [0, "C"],
        },
        [2, 0],
        [0],
    ),
    "fill_blank": ({"type": "fill", "text": "SSH uses TCP [[22]], Telnet [[23]]."}, ["22", "23"], ["22", "24"]),
    "numeric": (
        {"type": "numeric", "question": "Usable hosts in a /26?", "answer": 62, "unit": "hosts"},
        "62",
        "64",
    ),
    "short": (
        {
            "type": "short",
            "question": "Why use VLANs?",
            "keyPoints": [["broadcast domain", "broadcast domains"], "segmentation"],
            "sample": "They split broadcast domains: segmentation.",
        },
        "Smaller broadcast domains and segmentation.",
        "They are faster.",
    ),
    "long": (
        {
            "type": "long",
            "question": "Explain the TCP three-way handshake.",
            "keyPoints": ["SYN", "SYN-ACK", ["ACK", "acknowledgement"]],
            "sample": "SYN, SYN-ACK, ACK.",
        },
        "The client sends SYN, the server SYN-ACK, then the client an ACK.",
        "The client sends SYN.",
    ),
    "match": (
        {"type": "match", "question": "Match the ports.", "pairs": [["SSH", "22"], ["HTTPS", "443"]], "extra": ["80"]},
        None,
        [None, None],
    ),
    "order": ({"type": "order", "question": "Order the handshake.", "items": ["SYN", "SYN-ACK", "ACK"]}, None, None),
    "cli": (
        {
            "type": "cli",
            "question": "Show a brief interface status.",
            "prompt": "R1#",
            "answers": ["show ip interface brief"],
            "output": "Gi0/0 10.0.0.1 YES manual up up",
        },
        "sh ip int br",
        "show ip route",
    ),
    "lines": (
        {
            "question": "Which line shows the failure?",
            "listing": {"title": "R1 log", "language": "text", "content": "BGP up\r\nBGP down\nOSPF up\n"},
            "select": "lines",
            "answer": [2],
        },
        [1],
        [0],
    ),
    "diagram": (
        {"type": "diagram", "question": "Pick the firewall.", "topology": DIAGRAM, "select": "node", "answer": ["fw1"]},
        ["fw1"],
        ["r1"],
    ),
}
EXPLANATION = "Because the standard says so."


def quiz(*questions: dict, **head) -> str:
    return (
        "Here is your test.\n\n```quiz\n"
        + json.dumps({"title": "Net basics", **head, "questions": list(questions)})
        + "\n```"
    )


def built(*questions: dict, **head) -> dict:
    records, error = state.build(quiz(*questions, **head), None)
    assert error is None and records, error
    return records[0]


def refused(*questions: dict, **head) -> str | None:
    records, error = state.build(quiz(*questions, **head), None)
    assert records is None
    return error


def act(record: dict, question: int, action: str, value=None, revision=None) -> tuple[dict, bool]:
    return state.respond(
        record,
        status="complete",
        client_hash=record["hash"],
        question=question,
        action=action,
        value=value,
        at=AT,
        revision=revision,
    )


def right_value(record: dict, index: int, kind: str):
    value = KINDS[kind][1]
    return record["questions"][index]["key"] if value is None else value


def wrong_value(record: dict, index: int, kind: str):
    value = KINDS[kind][2]
    return list(reversed(record["questions"][index]["key"])) if value is None else value


# ------------------------------------------------------------------ the rules
def test_no_two_keywords_or_interface_types_share_an_accepted_prefix():
    for table in (rules.KEYWORDS, rules.INTERFACE_TYPES):
        assert all(1 <= least <= len(word) for word, least in table.items())
        shared = [(a, b) for a, b in combinations(table, 2) if len(commonprefix([a, b])) >= max(table[a], table[b])]
        assert shared == []


@pytest.mark.parametrize(
    ("typed", "accepted", "matches"),
    [
        ("sh int", "show interfaces", True),
        ("sh ip int br", "show ip interface brief", True),
        ("conf t", "configure terminal", True),
        ("show IPv6 int br", "show ipv6 interface brief", True),
        ("int gi 0/1", "interface GigabitEthernet0/1", True),
        ("interface g0/1", "interface GigabitEthernet0/1", True),
        ("s ip int br", "show ip interface brief", False),  # "s" is shorter than the table's minimum
        ("ip add 10.0.0.2 255.255.255.0", "ip address 10.0.0.1 255.255.255.0", False),  # a wrong argument
        ("hostname r1", "hostname R1", False),  # arguments match exactly
        ("hostname e1", "hostname E1", False),  # the author's 'E1' is a name, not Ethernet1
        ("hostname ethernet1", "hostname E1", False),
        ("hostname E1", "hostname E1", True),
        ("int e1", "interface Ethernet1", True),  # where the author's word is an interface, any abbreviation
        ("ip route 0.0.0.0 0.0.0.0 se0/0/0", "ip route 0.0.0.0 0.0.0.0 Serial0/0/0", True),
        ("int gi0/2", "interface GigabitEthernet0/1", False),
        ("", "show", False),
    ],
)
def test_the_cli_rule(typed, accepted, matches):
    assert rules.command_matches(typed, [accepted]) is matches


@pytest.mark.parametrize(
    ("answer", "point", "found"),
    [
        ("ship it", "IP", False),  # a whole word, not inside another
        ("IPS", "IP", False),
        ("tag frames with 802.1q", "802.1Q", True),
        ("the TCP/IP stack", "TCP/IP", True),
        ("route 10.0.0.0/24", "/24", True),  # starts with a non-word character: no left boundary
        ("a three way handshake", "three-way", True),
        ("ＩＰ addressing", "IP", True),  # fullwidth, read as NFKC
    ],
)
def test_the_written_rule(answer, point, found):
    assert rules.key_points_found(answer, [[point]]) == [found]


HANDSHAKE = [["SYN"], ["SYN-ACK"], ["ACK", "acknowledgement"]]


@pytest.mark.parametrize(
    ("answer", "points", "found"),
    [
        ("The client sends SYN-ACK", HANDSHAKE, [False, True, False]),
        ("SYN then SYN-ACK then ACK", HANDSHAKE, [True, True, True]),
        ("TCP/IP", [["TCP/IP"], ["IP"]], [True, False]),
    ],
)
def test_one_phrase_counts_for_one_key_point(answer, points, found):
    assert rules.key_points_found(answer, points) == found


# ------------------------------------------------------------------ reading fences
@pytest.mark.parametrize(
    ("reply", "block", "body"),
    [
        ("```quiz\n{}\n```", ("quiz", "{}", True, True, ""), "{}"),
        ("  ```mcq x\n  {}\n  ```", ("mcq", "{}", True, True, "  "), "{}"),
        ("- ```quiz\n  {}\n  ```", ("quiz", "  {}", True, False, "  "), "{}"),
        ("1. ~~~quiz\n   {}\n   ~~~", ("quiz", "   {}", True, False, "   "), "{}"),
        ("> ```quiz\n> {}\n> ```", ("quiz", "> {}", True, False, "> "), "{}"),
        ("````quiz\n```\n````", ("quiz", "```", True, True, ""), "```"),
        ("> 2) \t```fill-blank\n> x\n> ```", ("fill-blank", "> x", True, False, ">    \t"), "x"),
    ],
)
def test_fences_are_found_at_the_top_in_list_items_and_in_quotes(reply, block, body):
    found = state._blocks(reply)[0][0]
    assert (found.language, found.body, found.closed, found.top_level, found.indent) == block
    assert chat_tests._segments(reply) == [(block[0], body, reply)]  # the question-bank repair reads the same


# Lines that took seconds to forever: a run of spaces after a list marker could split many ways, and a long
# info string ending in a backtick was re-read from every position.
SLOW_LINES = {
    f"{name}-{count}": shape * count + "x"
    for name, shape in (("list", "-  "), ("numbered-quote", "1.  > "), ("tab-quote", "* \t> "))
    for count in (22, 5000)
} | {"long-info": "```" + "a" * 20000 + "`"}


@pytest.mark.parametrize("line", SLOW_LINES.values(), ids=SLOW_LINES)
def test_reading_fences_takes_linear_time_on_any_line(line):
    reply = f"A quiz:\n{line}\n```quiz\n{{}}\n```"
    for read in (state._blocks, state.for_model, chat_tests._segments):
        started = time.perf_counter()
        read(reply)
        assert time.perf_counter() - started < 0.5, read.__name__


def test_numbers_are_fixed_point_within_the_user_grammar_and_never_raise():
    assert (rules.fixed_point(0.1), rules.fixed_point(1e20), rules.fixed_point("-67")) == (
        "0.1",
        "100000000000000000000",
        "-67",
    )
    assert rules.fixed_point("1e999999999") is None and rules.fixed_point(True) is None
    assert rules.fixed_point(float("nan")) is None and rules.fixed_point("1e-12") is None
    assert rules.tolerance_of("1e999999999%", "5") is None and rules.tolerance_of("5%", "0") is None
    assert rules.tolerance_of("150%", "5") is None and rules.tolerance_of("-1", "5") is None
    assert rules.within("0.1", "0.1", "0") and rules.within("99", "100", "1%") and not rules.within("98", "100", "1%")
    assert rules.within("1", "1E+999999999", "100%") is False  # Overflow: wrong, not a 500


def test_the_shown_order_is_fixed_and_never_the_answer():
    assert rules.shown_order("abc", 0, 5, 5) == rules.shown_order("abc", 0, 5, 5)
    for index in range(40):
        assert rules.shown_order(f"block{index}", 0, 3, 3) != [0, 1, 2]
        assert rules.shown_order(f"block{index}", 1, 4, 2)[:2] != [0, 1]


# ------------------------------------------------------------------ each kind
def test_an_unknown_type_keeps_the_v1_record_and_hash():
    plain = KINDS["mcq"][0]
    v1 = built(plain)
    assert v1["schemaVersion"] == 1 and "mode" not in v1 and "drafts" not in v1
    typed = built({**plain, "type": "mcq"})
    assert typed["hash"] == v1["hash"]  # the same questions; only the block's text (its locator) differs
    assert {**typed, "locator": None} == {**v1, "locator": None}


@pytest.mark.parametrize("kind", sorted(KINDS))
def test_each_kind_is_graded_and_its_key_stays_hidden_until_final(kind):
    raw = {**KINDS[kind][0], "explanation": EXPLANATION}
    record = built(raw, mode="practice")
    assert record["schemaVersion"] == 2 and record["questions"][0]["kind"] == kind
    before = state.public(record, "m", "complete")["questions"][0]
    secret = ("correct", "answers", "explanation", "correctList", "accepted", "tolerance", "keyPoints", "sample")
    assert all(before[key] is None for key in (*secret, "output"))
    model = json.loads(state.for_model(quiz(raw, mode="practice")).split("```quiz\n")[1].split("\n```")[0])
    assert EXPLANATION not in json.dumps(model)
    assert set(model["questions"][0]) <= {key for key, value in before.items() if value is not None}

    right, _ = act(record, 0, "answer", right_value(record, 0, kind))
    answered = state.public(right, "m", "complete")
    assert answered["earned"] == answered["possible"] > 0
    assert answered["questions"][0]["explanation"] == EXPLANATION
    revealed = [key for key in secret if answered["questions"][0][key] is not None]
    assert revealed, "a final question shows its key"
    # Wrong (numeric and CLI questions take three tries: Show answer ends them at once).
    wrong, _ = (
        act(record, 0, "skip") if kind in ("numeric", "cli") else act(record, 0, "answer", wrong_value(record, 0, kind))
    )
    assert wrong["responses"][0]["earned"] < wrong["responses"][0]["possible"]
    summary = state.tutor_summary([wrong])
    assert "- Q1 " in summary and len(summary) <= state.SUMMARY_MAX


def test_partial_marks_for_written_answers_and_pairs():
    written = built(KINDS["long"][0])
    answered, _ = act(written, 0, "answer", "It starts with a SYN and ends with an acknowledgement.")
    response = answered["responses"][0]
    assert (response["earned"], response["possible"], response["marks"]) == (2, 3, [True, False, True])
    summary = state.tutor_summary([answered])
    assert "partly right (2/3)" in summary and 'missed key points "SYN-ACK"' in summary
    match = built(KINDS["match"][0])
    key = match["questions"][0]["key"]
    pairs, _ = act(match, 0, "answer", [key[0], None])
    assert (pairs["responses"][0]["earned"], pairs["responses"][0]["marks"]) == (1, [True, False])


@pytest.mark.parametrize(
    ("raw", "why"),
    [
        ({**KINDS["multi"][0], "options": ["A", "a", "B"]}, "duplicate options"),
        ({**KINDS["multi"][0], "answer": [0, 0]}, "a duplicate answer"),
        ({**KINDS["order"][0], "items": ["SYN", "syn", "ACK"]}, "duplicate items"),
        ({**KINDS["match"][0], "pairs": [["SSH", "22"], ["ssh", "443"]]}, "duplicate left items"),
        ({**KINDS["match"][0], "extra": ["22"]}, "an extra equal to a right item"),
        ({**KINDS["diagram"][0], "topology": {**DIAGRAM, "links": [{"id": "R1", "from": "r1", "to": "sw1"}]}}, "ids"),
        (
            {**KINDS["diagram"][0], "topology": {**DIAGRAM, "links": [{"id": "l9", "from": "r1", "to": "x"}]}},
            "dangling",
        ),
        ({**KINDS["diagram"][0], "select": "link"}, "an answer that can't be selected"),
        ({**KINDS["numeric"][0], "tolerance": "1e999999999%"}, "a tolerance out of range"),
        ({**KINDS["numeric"][0], "answer": "12.0000000001"}, "more decimals than a user can type"),
        ({**KINDS["numeric"][0], "answer": 0, "tolerance": "5%"}, "a share of zero"),
        ({**KINDS["short"][0], "keyPoints": ["a"]}, "a one-character key point"),
        ({**KINDS["short"][0], "keyPoints": ["VLAN", ["vlan", "VLANs"]]}, "one form in two points"),
        ({**KINDS["short"][0], "keyPoints": [[f"point {n}"] for n in range(7)]}, "more than 6 key points"),
        ({**KINDS["short"][0], "sample": "x" * 1001}, "a sample past the kind's length"),
        ({**KINDS["cli"][0], "prompt": "R" * 61}, "a long prompt"),
        ({**KINDS["lines"][0], "answer": [4]}, "a line the listing doesn't have"),
        ({**KINDS["lines"][0], "listing": {"content": "x\n" * 201}}, "a listing past 200 lines"),
        ({**KINDS["mcq"][0], "listing": {"content": "x", "language": "a b"}}, "a language that isn't a name"),
        ({**KINDS["numeric"][0], "explanation": "x" * 2001}, "a long explanation"),
    ],
)
def test_a_malformed_question_refuses_the_whole_test(raw, why):
    assert refused(KINDS["mcq"][0], raw) == "invalid_question", why


def test_the_test_level_rules():
    assert refused(KINDS["mcq"][0], mode="quiz") == "invalid_test"
    assert refused(KINDS["mcq"][0], scenario={"summary": "x" * 1001}) == "invalid_test"
    assert refused(KINDS["mcq"][0], scenario={"summary": "Outage", "symptoms": ["s"] * 9}) == "invalid_test"
    huge = [{**KINDS["long"][0], "question": f"Q{n} " + "x" * 990, "sample": "y" * 5990} for n in range(12)]
    assert refused(*huge) == "invalid_test"  # the record past 64 KB
    scenario = {
        "title": "Branch outage",
        "summary": "Users at the branch can't reach the file server.",
        "symptoms": ["Pings time out"],
        "evidence": [{"label": "R1 log", "content": "%LINK-3-UPDOWN: Gi0/1 down"}],
    }
    record = built(KINDS["mcq"][0], scenario=scenario)
    assert record["schemaVersion"] == 2 and record["mode"] == "practice"
    shown = state.public(record, "m", "complete")["scenario"]
    assert shown["evidence"] == [
        {"label": "R1 log", "title": None, "language": None, "content": "%LINK-3-UPDOWN: Gi0/1 down"}
    ]
    other = built(KINDS["mcq"][0], scenario={**scenario, "summary": "Another outage."})
    assert other["hash"] != record["hash"]  # the scenario is part of what a response names


@pytest.mark.parametrize(
    ("kind", "value", "reason"),
    [
        ("numeric", "²", "number"),
        ("numeric", "12.", "number"),
        ("numeric", "1e3", "number"),
        ("numeric", "0,5", "number"),
        ("numeric", 62, "number"),
        ("multi", [0, 0], "duplicate"),
        ("multi", [True], "value"),
        ("match", [0], "value"),
        ("order", [0, 0, 1], "value"),
        ("lines", [3], "value"),
        ("diagram", ["l1"], "value"),  # links aren't selectable here
        ("short", "x" * 1001, "too_long"),
        ("cli", "x" * 301, "too_long"),
        ("mcq", [0], "value"),
    ],
)
def test_a_value_that_doesnt_fit_is_422_with_a_reason_and_changes_nothing(kind, value, reason):
    record = built(KINDS[kind][0])
    with pytest.raises(state.Invalid) as error:
        act(record, 0, "answer", value)
    assert str(error.value) == reason


def test_values_are_canonical_so_a_reordered_replay_is_the_same_answer():
    record = built(KINDS["multi"][0])
    first, changed = act(record, 0, "answer", [2, 0])
    assert changed and first["responses"][0]["answer"] == [0, 2]
    assert act(first, 0, "answer", [0, 2]) == (first, False)
    other, _ = act(record, 0, "answer", [0, 2])
    assert act(other, 0, "answer", [2, 0]) == (other, False)
    huge = built({**KINDS["numeric"][0], "answer": 1e20})  # stored as fixed point: the typed digits match
    assert act(huge, 0, "answer", "100000000000000000000")[0]["responses"][0]["earned"] == 1
    negative = built({**KINDS["numeric"][0], "answer": -67, "tolerance": 0.5})
    assert act(negative, 0, "answer", "-67.4")[0]["responses"][0]["earned"] == 1


def test_the_model_form_keeps_the_shown_order_and_reads_an_indented_fence():
    raw = KINDS["order"][0]
    body = json.dumps({"title": "Handshake", "questions": [raw]})
    content = "Steps:\n\n  ```quiz\n  " + body + "\n  ```\nDone."
    shown = state.for_model(content)
    assert shown.startswith("Steps:\n\n  ```quiz\n  {") and shown.endswith("\n  ```\nDone.")
    form = json.loads(shown.split("\n")[3])
    records, _ = state.build(content, None)
    items = state.public(records[0], "m", "complete")["questions"][0]["items"]
    assert form["questions"][0]["items"] == items != raw["items"]  # the shown order, never the answer's
    assert '"key"' not in shown and form["questions"][0]["kind"] == "order"
    # Left as written: a v1 quiz, a block that doesn't parse, an unclosed fence.
    for untouched in (quiz(KINDS["mcq"][0]), "```quiz\n{not json\n```", "```quiz\n" + body):
        assert state.for_model(untouched) == untouched
