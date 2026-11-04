from __future__ import annotations

import re
from typing import Literal

from app.db.models import MEMORY_KINDS, MEMORY_MAX_CONTENT_CHARS
from app.errors import ValidationFailed
from app.llm.redact import mask_secrets

MemoryKind = Literal["learning_context", "study_context"]

_SPACE = re.compile(r"\s+")
# Secrets and credentials never become a memory, whoever asks (the user, or the extraction model).
_SECRET_PATTERNS = (
    re.compile(r"\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|private[_ -]?key)\b", re.IGNORECASE),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|pk)_[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    re.compile(r"\b(?:bearer\s+)[A-Za-z0-9._~+/-]{16,}=*\b", re.IGNORECASE),
    # Network credentials: pre-shared keys, SNMP communities, device and routing-protocol secrets.
    re.compile(
        r"\b(?:pre[-_ ]?shared[-_ ]?keys?|snmp[-_ ]?communit(?:y|ies)|community[-_ ]?strings?|client[-_ ]?secrets?"
        r"|enable[-_ ]?secret|secret[-_ ]?keys?|passphrases?|passwd|key-string|authentication-key)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:psk|secret|token)\s*[:=]|\bpsk\s+is\b", re.IGNORECASE),
    re.compile(r"-----BEGIN PGP PRIVATE KEY" + r" BLOCK-----"),  # split: secret scanners read the literal as a key
    re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@", re.IGNORECASE),  # user:password@ in a URL
    # Tokens: GitHub, Slack, Google API keys, JSON Web Tokens.
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
)
# A configuration dump: two or more configuration statements in one item (Cisco IOS/NX-OS, Junos set
# commands, FortiOS). A sentence about a setup ("runs OSPF on Cisco routers") has none.
_CONFIG_STATEMENT = re.compile(
    r"\b(?:interface\s+(?:gi|fa|te|eth|ge-|xe-|et-|vlan|loopback|tunnel|port-channel|mgmt)\S*\d"
    r"|ip\s+address\s+\d{1,3}(?:\.\d{1,3}){3}"
    r"|router\s+(?:bgp|ospf|eigrp|rip|isis)\b"
    r"|neighbor\s+\S+\s+remote-as\b"
    r"|access-list\s+\S+\s+(?:permit|deny)\b"
    r"|switchport\s+(?:mode|access|trunk)\b"
    r"|hostname\s+\S+"
    r"|set\s+(?:interfaces|security|system|protocols|policy-options|firewall)\s+\S+"
    r"|config\s+(?:system|firewall|router|vpn)\s+\S+"
    r"|crypto\s+(?:isakmp|ikev2|ipsec|map)\s+\S+)",
    re.IGNORECASE,
)
# What the user's profile and settings hold (Settings), and the app's own records, asked to be
# remembered as "my ...". Without "my" these words are engineering facts: a QoS class-map,
# carrier-grade NAT, a medium-sized site, a board-level change.
_AUTHORITATIVE_PATTERNS = (
    re.compile(r"\bmy (?:grade|class|board|medium|target exam|target score|exam date)\b", re.IGNORECASE),
    re.compile(
        r"\bmy (?:pace|difficulty|response length|answer mode|learning preference|teaching preference)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bmy (?:weak topic|mastery|practice score|assessment score|streak)\b", re.IGNORECASE),
)

assert set(MemoryKind.__args__) == set(MEMORY_KINDS)


def normalize_content(content: str, *, max_chars: int = MEMORY_MAX_CONTENT_CHARS) -> str:
    normalized = _SPACE.sub(" ", content).strip()
    if not normalized:
        raise ValidationFailed("Memory content must not be blank.")
    if len(normalized) > max_chars:
        raise ValidationFailed("Memory content is too long.")
    return normalized


def validate_memory_policy(content: str) -> None:
    # The provider masking's patterns too (enable secret 5, snmp-server community, crypto isakmp key, ...):
    # what is never sent to a provider is never kept as a memory either.
    if any(pattern.search(content) for pattern in _SECRET_PATTERNS) or mask_secrets(content)[1]:
        raise ValidationFailed("Secrets and credentials cannot be stored in memory.")
    if len(_CONFIG_STATEMENT.findall(content)) >= 2:
        raise ValidationFailed("Configuration excerpts cannot be stored in memory: describe the setup in a sentence.")
    if any(pattern.search(content) for pattern in _AUTHORITATIVE_PATTERNS):
        raise ValidationFailed("Use your profile or your settings for this information.")
