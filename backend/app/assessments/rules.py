"""The grading rules no library has (docs/api-contract.md, "Practice tests"): written answers by key points,
CLI commands by the author's accepted forms, numbers within a tolerance, and the order items are shown in.
Pure functions, no I/O; a user's value never makes one raise."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from decimal import Decimal, DecimalException, localcontext

# ------------------------------------------------------------------ shown order


def shown_order(locator: str, question: int, count: int, fixed: int) -> list[int]:
    """Indices 0..count-1 in the order they are shown: sorted by sha256(f"{locator}:{question}:{i}"), rotated by
    one when the first `fixed` would sit where the answer puts them (shown in the right order). No randomness:
    the same block always shows the same order."""
    order = sorted(range(count), key=lambda i: hashlib.sha256(f"{locator}:{question}:{i}".encode()).hexdigest())
    return order[1:] + order[:1] if order[:fixed] == list(range(fixed)) else order


# ------------------------------------------------------------------ written answers


def normalise(text: str) -> str:
    """Both sides of a written answer before matching: NFKC, casefold, `-`/`_` as spaces, spacing collapsed."""
    return " ".join(re.sub(r"[-_]", " ", unicodedata.normalize("NFKC", text).casefold()).split())


def _phrase(alternative: str) -> re.Pattern[str]:
    """An alternative as a phrase: its own characters, bounded by non-word characters only where it starts or
    ends with a word character ('IP' is not in 'ship'; '/24' is in '10.0.0.0/24')."""
    norm = normalise(alternative)
    start = r"(?<!\w)" if re.match(r"\w", norm) else ""
    end = r"(?!\w)" if re.search(r"\w$", norm) else ""
    return re.compile(start + re.escape(norm) + end)


def key_points_found(answer: str, points: list[list[str]]) -> list[bool]:
    """Which key points the answer names (any of a point's alternatives; each point counts once). No stemming:
    authors list every form, e.g. ["ACL", "ACLs"]. Longest forms first, and the words a match used are spent, so
    one phrase never counts for two points ("SYN-ACK" is not also "SYN" and "ACK"; "TCP/IP" is not also "IP")."""
    text, found = normalise(answer), [False] * len(points)
    ranked = sorted(
        ((normalise(form), point) for point, forms in enumerate(points) for form in forms), key=lambda p: -len(p[0])
    )
    for form, point in ranked:
        if not found[point] and (match := _phrase(form).search(text)):
            found[point] = True
            # Spaces keep the boundaries; a run of 2+ spaces is in no normalised form (forms are 2+ characters).
            text = text[: match.start()] + " " * len(match[0]) + text[match.end() :]
    return found


# ------------------------------------------------------------------ numbers

NUMBER = re.compile(r"-?[0-9]{1,30}(?:\.[0-9]{1,9})?")  # what a user may type (ASCII digits only)
_PRECISION = 100  # more than any number the grammar allows (39 digits), so nothing is rounded


def fixed_point(value: object) -> str | None:
    """An author's number (JSON number or text) as a fixed-point string within the user grammar's range, or None.
    Checked before formatting, so '1e999999999' is refused without writing a billion digits."""
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        value = repr(value)  # the shortest text that reads back as this float: 0.1 stays 0.1
    elif isinstance(value, int):
        value = str(value)
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        with localcontext() as context:
            context.prec = _PRECISION
            number = Decimal(value.strip())
            if not number.is_finite() or number.adjusted() > 29:
                return None
            number = number.normalize()
            if number.as_tuple().exponent < -9:  # type: ignore[operator]  # finite: an int
                return None
            text = format(number, "f")
    except (DecimalException, ValueError, TypeError):
        return None
    return text if NUMBER.fullmatch(text) else None


def tolerance_of(value: object, answer: str) -> str | None:
    """An author's tolerance: absent (exact), an absolute amount, or "n%" with 0 < n <= 100 of a non-zero answer."""
    if value is None:
        return "0"
    if isinstance(value, str) and value.strip().endswith("%"):
        share = fixed_point(value.strip()[:-1])
        if share is None or not 0 < Decimal(share) <= 100 or Decimal(answer) == 0:
            return None
        return f"{share}%"
    amount = fixed_point(value)
    return amount if amount is not None and not amount.startswith("-") else None


def within(given: str, answer: str, tolerance: str) -> bool:
    """Whether a typed number is the answer within its tolerance. Any decimal error is a wrong answer, never a
    raise: stored values are bounded, but a record changed by later code must still grade."""
    try:
        with localcontext() as context:
            context.prec = _PRECISION
            target = Decimal(answer)
            if tolerance.endswith("%"):
                limit = abs(target) * Decimal(tolerance[:-1]) / 100
            else:
                limit = Decimal(tolerance)
            return abs(Decimal(given) - target) <= limit
    except (ArithmeticError, ValueError, TypeError):  # DecimalException is an ArithmeticError
        return False


# ------------------------------------------------------------------ CLI commands

# A closed table of command keywords and the shortest prefix accepted for each (Cisco-style abbreviation). A
# keyword is listed in its longest form ("interfaces": "interface" and "int" are its prefixes). No two keywords
# share an accepted prefix (tests/test_assessment_kinds.py checks it), so a token names at most one keyword.
KEYWORDS: dict[str, int] = {
    "access": 3, "access-group": 8, "access-list": 8, "address": 3, "address-table": 8, "all": 2, "allowed": 4,
    "any": 2, "area": 3, "arp": 2, "bgp": 2, "brief": 2, "cdp": 2, "channel-group": 2, "clear": 3, "clock": 3,
    "community": 3, "configure": 4, "connected": 4, "console": 4, "copy": 3, "counters": 3, "crypto": 3,
    "debug": 3, "default": 3, "deny": 3, "description": 4, "detail": 3, "dhcp": 2, "domain-name": 3, "dot1q": 4,
    "eigrp": 2, "enable": 2, "encapsulation": 3, "end": 3, "eq": 2, "etherchannel": 2, "excluded-address": 3,
    "exit": 3, "generate": 3, "host": 2, "hostname": 5, "input": 3, "interfaces": 3, "ip": 2, "ipv6": 3,
    "key": 3, "line": 2, "lldp": 2, "logging": 4, "login": 4, "mac": 2, "maximum": 3, "memory": 3, "mode": 2,
    "nat": 2, "native": 4, "neighbors": 3, "network": 3, "no": 2, "ntp": 2, "ospf": 2, "passive-interface": 5,
    "password": 5, "permit": 2, "ping": 2, "pool": 2, "port-security": 5, "privilege": 4, "protocols": 3,
    "redistribute": 3, "reload": 3, "route": 2, "router": 6, "rsa": 2, "running-config": 2, "secret": 3,
    "server": 3, "show": 2, "shutdown": 4, "snmp-server": 2, "spanning-tree": 2, "ssh": 2, "startup-config": 3,
    "static": 4, "sticky": 2, "summary": 2, "switchport": 2, "terminal": 1, "traceroute": 3, "translations": 4,
    "trunk": 2, "undebug": 3, "unicast-routing": 3, "username": 2, "version": 3, "violation": 2, "vlan": 2,
    "vrf": 2, "vty": 2, "write": 2,
}  # fmt: skip
# Interface types, anchored the same way: "gi0/1" and "gi 0/1" are GigabitEthernet0/1.
INTERFACE_TYPES: dict[str, int] = {
    "ethernet": 1, "fastethernet": 1, "gigabitethernet": 1, "loopback": 1, "port-channel": 1, "serial": 2,
    "tengigabitethernet": 2, "tunnel": 2, "vlan": 1,
}  # fmt: skip
_INTERFACE = re.compile(r"([a-z][a-z-]*?)(\d+(?:[/.:]\d+)*)", re.I)
_NUMBERING = re.compile(r"\d+(?:[/.:]\d+)*")


def _resolve(token: str, table: dict[str, int]) -> str | None:
    """The one entry of `table` the token abbreviates (at least its minimum prefix), or None."""
    word = token.lower()
    found = [entry for entry, least in table.items() if entry.startswith(word) and len(word) >= least]
    return found[0] if len(found) == 1 else None


def _words(command: str) -> list[str]:
    """A command's words, an interface type joined to its number ("gi 0/1" -> "gi0/1")."""
    words = command.split()
    joined: list[str] = []
    index = 0
    while index < len(words):
        word = words[index]
        follows = words[index + 1] if index + 1 < len(words) else ""
        if _NUMBERING.fullmatch(follows) and word.isascii() and _resolve(word, INTERFACE_TYPES):
            word, index = word + follows, index + 1
        joined.append(word)
        index += 1
    return joined


def _interface(word: str, *, author: bool) -> str | None:
    """The interface a word names (its type resolved), or None. An author's word is one only when it can't be a
    name: a type of 2+ letters or a slot in its number (Gi0/1, g0/1, Lo0, Ethernet1; 'E1' is a hostname)."""
    found = _INTERFACE.fullmatch(word)
    kind = _resolve(found[1], INTERFACE_TYPES) if found else None
    if found is None or kind is None or (author and len(found[1]) < 2 and found[2].isdigit()):
        return None
    return kind + found[2]


def _token(word: str) -> tuple[str, str]:
    """An author's word as (kind, value): an interface or a keyword resolved, anything else (an argument) as is."""
    if interface := _interface(word, author=True):
        return "interface", interface
    if keyword := _resolve(word, KEYWORDS):
        return "keyword", keyword
    return "argument", word


def _typed_as(word: str, token: tuple[str, str]) -> bool:
    """Whether a typed word is the author's token, read as the author's word is read."""
    kind, value = token
    if kind == "interface":
        return _interface(word, author=False) == value
    return (_resolve(word, KEYWORDS) if kind == "keyword" else word) == value


def command_matches(given: str, accepted: list[str]) -> bool:
    """Whether a typed command is one of the author's accepted forms, word by word, each typed word read the way
    the author's word at that place is: a keyword any of its accepted prefixes in any case, an interface any
    abbreviation of its type, an argument exactly itself ('hostname e1' is not 'hostname E1')."""
    typed = _words(given)
    return bool(typed) and any(
        len(tokens := [_token(word) for word in _words(form)]) == len(typed) and all(map(_typed_as, typed, tokens))
        for form in accepted
    )
