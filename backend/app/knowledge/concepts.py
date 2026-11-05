"""The concept graph's deterministic layer: which networking concepts a passage mentions and what it states.

Local and model-free (no text leaves): a versioned vocabulary with aliases, acronyms the books define themselves
("Open Shortest Path First (OSPF)"), and high-precision patterns for what a sentence states: a value (a port, an IP
protocol number, an administrative distance, a timer, a multicast address) or a relation between two concepts
(is a, uses, operates at, replaces, extends, compared with). Every relation keeps the verbatim span that states it
(evidence_start/evidence_end into the passage text), so it can be shown and checked. Precision over recall: a
sentence the patterns do not understand states nothing here. A model-assisted layer is separate and gated on the
owner's extraction-egress approval.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    KnowledgeConcept,
    KnowledgeConceptAlias,
    KnowledgePassage,
    KnowledgePassageConcept,
    KnowledgePassageOccurrence,
    KnowledgeRelation,
)

EXTRACTOR_VERSION = "concepts-v1"

# key, name, kind, aliases. Short all-capital aliases (AD, DR, AS) match case-sensitively, everything else not.
VOCABULARY: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    # routing protocols and their parts
    ("ospf", "Open Shortest Path First", "protocol", ("OSPF", "OSPFv2", "OSPFv3")),
    ("eigrp", "Enhanced Interior Gateway Routing Protocol", "protocol", ("EIGRP",)),
    ("rip", "Routing Information Protocol", "protocol", ("RIP", "RIPv2", "RIPng")),
    ("bgp", "Border Gateway Protocol", "protocol", ("BGP", "eBGP", "iBGP", "BGP-4", "MP-BGP")),
    ("isis", "Intermediate System to Intermediate System", "protocol", ("IS-IS", "ISIS")),
    ("igrp", "Interior Gateway Routing Protocol", "protocol", ("IGRP",)),
    ("static_route", "Static route", "concept", ("static route", "static routes", "static routing")),
    ("default_route", "Default route", "concept", ("default route", "gateway of last resort")),
    ("floating_static", "Floating static route", "concept", ("floating static route",)),
    ("dual", "Diffusing Update Algorithm", "algorithm", ("DUAL",)),
    ("spf", "Shortest Path First algorithm", "algorithm", ("SPF", "Dijkstra")),
    ("bellman_ford", "Bellman-Ford algorithm", "algorithm", ("Bellman-Ford",)),
    ("lsa", "Link-state advertisement", "concept", ("LSA", "LSAs", "link-state advertisement")),
    ("dr", "Designated router", "concept", ("DR", "designated router")),
    ("bdr", "Backup designated router", "concept", ("BDR", "backup designated router")),
    ("abr", "Area border router", "concept", ("ABR", "area border router")),
    ("asbr", "Autonomous system boundary router", "concept", ("ASBR",)),
    ("backbone_area", "OSPF backbone area", "concept", ("backbone area", "area 0")),
    ("feasible_successor", "Feasible successor", "concept", ("feasible successor",)),
    ("successor", "Successor route", "concept", ("successor",)),
    ("administrative_distance", "Administrative distance", "attribute", ("administrative distance", "AD")),
    ("metric", "Routing metric", "attribute", ("metric", "cost")),
    ("autonomous_system", "Autonomous system", "concept", ("autonomous system", "AS")),
    ("route_redistribution", "Route redistribution", "concept", ("redistribution", "route redistribution")),
    ("route_summarization", "Route summarization", "concept", ("route summarization", "summarization")),
    ("policy_based_routing", "Policy-based routing", "concept", ("PBR", "policy-based routing")),
    ("vrf", "Virtual routing and forwarding", "technology", ("VRF", "VRF-Lite")),
    ("mpls", "Multiprotocol Label Switching", "technology", ("MPLS",)),
    ("ldp", "Label Distribution Protocol", "protocol", ("LDP",)),
    ("bfd", "Bidirectional Forwarding Detection", "protocol", ("BFD",)),
    # protocol classes
    ("routing_protocol", "Routing protocol", "class", ("routing protocol",)),
    ("igp", "Interior gateway protocol", "class", ("IGP", "interior gateway protocol")),
    ("egp", "Exterior gateway protocol", "class", ("EGP", "exterior gateway protocol")),
    ("link_state_protocol", "Link-state routing protocol", "class",
     ("link-state protocol", "link-state routing protocol")),
    ("distance_vector_protocol", "Distance-vector routing protocol", "class",
     ("distance-vector protocol", "distance-vector routing protocol")),
    ("path_vector_protocol", "Path-vector routing protocol", "class",
     ("path-vector protocol", "path-vector routing protocol")),
    ("advanced_distance_vector", "Advanced distance-vector protocol", "class",
     ("advanced distance-vector protocol", "hybrid routing protocol")),
    ("fhrp", "First hop redundancy protocol", "class", ("FHRP", "first hop redundancy protocol")),
    ("tunneling_protocol", "Tunneling protocol", "class", ("tunneling protocol",)),
    # first hop redundancy, management, services
    ("hsrp", "Hot Standby Router Protocol", "protocol", ("HSRP", "HSRPv2")),
    ("vrrp", "Virtual Router Redundancy Protocol", "protocol", ("VRRP", "VRRPv3")),
    ("glbp", "Gateway Load Balancing Protocol", "protocol", ("GLBP",)),
    ("dhcp", "Dynamic Host Configuration Protocol", "protocol", ("DHCP", "DHCPv4", "DHCPv6")),
    ("dns", "Domain Name System", "protocol", ("DNS",)),
    ("ntp", "Network Time Protocol", "protocol", ("NTP",)),
    ("snmp", "Simple Network Management Protocol", "protocol", ("SNMP", "SNMPv2c", "SNMPv3")),
    ("syslog", "Syslog", "protocol", ("syslog",)),
    ("ssh", "Secure Shell", "protocol", ("SSH", "SSHv2")),
    ("telnet", "Telnet", "protocol", ("Telnet",)),
    ("tftp", "Trivial File Transfer Protocol", "protocol", ("TFTP",)),
    ("ftp", "File Transfer Protocol", "protocol", ("FTP",)),
    ("http", "Hypertext Transfer Protocol", "protocol", ("HTTP",)),
    ("https", "HTTP over TLS", "protocol", ("HTTPS",)),
    ("netflow", "NetFlow", "technology", ("NetFlow", "IPFIX")),
    ("cdp", "Cisco Discovery Protocol", "protocol", ("CDP",)),
    ("lldp", "Link Layer Discovery Protocol", "protocol", ("LLDP",)),
    ("nat", "Network Address Translation", "technology", ("NAT", "PAT", "NAT overload", "port address translation")),
    ("qos", "Quality of service", "technology", ("QoS", "quality of service")),
    ("dscp", "Differentiated Services Code Point", "attribute", ("DSCP",)),
    ("netconf", "NETCONF", "protocol", ("NETCONF",)),
    ("restconf", "RESTCONF", "protocol", ("RESTCONF",)),
    ("yang", "YANG", "technology", ("YANG",)),
    ("radius", "RADIUS", "protocol", ("RADIUS",)),
    ("tacacs", "TACACS+", "protocol", ("TACACS+", "TACACS")),
    ("aaa", "Authentication, authorization and accounting", "concept", ("AAA",)),
    # switching
    ("vlan", "Virtual LAN", "technology", ("VLAN", "VLANs")),
    ("native_vlan", "Native VLAN", "concept", ("native VLAN",)),
    ("trunk", "Trunk link", "concept", ("trunk", "trunk link", "trunk port")),
    ("access_port", "Access port", "concept", ("access port",)),
    ("dot1q", "IEEE 802.1Q", "protocol", ("802.1Q", "dot1q")),
    ("isl", "Inter-Switch Link", "protocol", ("ISL",)),
    ("dtp", "Dynamic Trunking Protocol", "protocol", ("DTP",)),
    ("vtp", "VLAN Trunking Protocol", "protocol", ("VTP",)),
    ("stp", "Spanning Tree Protocol", "protocol", ("STP", "802.1D", "spanning tree")),
    ("rstp", "Rapid Spanning Tree Protocol", "protocol", ("RSTP", "802.1w", "Rapid PVST+")),
    ("mst", "Multiple Spanning Tree", "protocol", ("MST", "MSTP", "802.1s")),
    ("pvst", "Per-VLAN Spanning Tree Plus", "protocol", ("PVST+",)),
    ("root_bridge", "Root bridge", "concept", ("root bridge",)),
    ("bpdu", "Bridge protocol data unit", "concept", ("BPDU", "BPDUs")),
    ("portfast", "PortFast", "technology", ("PortFast",)),
    ("bpdu_guard", "BPDU Guard", "technology", ("BPDU Guard",)),
    ("etherchannel", "EtherChannel", "technology", ("EtherChannel", "port channel", "port-channel")),
    ("lacp", "Link Aggregation Control Protocol", "protocol", ("LACP", "802.3ad")),
    ("pagp", "Port Aggregation Protocol", "protocol", ("PAgP",)),
    ("mac_address", "MAC address", "concept", ("MAC address", "MAC addresses")),
    ("mac_table", "MAC address table", "concept", ("MAC address table", "CAM table")),
    ("port_security", "Port security", "technology", ("port security",)),
    ("dhcp_snooping", "DHCP snooping", "technology", ("DHCP snooping",)),
    ("dai", "Dynamic ARP Inspection", "technology", ("DAI", "Dynamic ARP Inspection")),
    ("arp", "Address Resolution Protocol", "protocol", ("ARP",)),
    ("ethernet", "Ethernet", "technology", ("Ethernet",)),
    ("vxlan", "Virtual Extensible LAN", "technology", ("VXLAN",)),
    # IP and transport
    ("ipv4", "IPv4", "protocol", ("IPv4",)),
    ("ipv6", "IPv6", "protocol", ("IPv6",)),
    ("icmp", "Internet Control Message Protocol", "protocol", ("ICMP", "ICMPv6")),
    ("ndp", "Neighbor Discovery Protocol", "protocol", ("NDP", "neighbor discovery")),
    ("slaac", "Stateless address autoconfiguration", "technology", ("SLAAC",)),
    ("tcp", "Transmission Control Protocol", "protocol", ("TCP",)),
    ("udp", "User Datagram Protocol", "protocol", ("UDP",)),
    ("subnet_mask", "Subnet mask", "concept", ("subnet mask",)),
    ("cidr", "Classless inter-domain routing", "concept", ("CIDR",)),
    ("vlsm", "Variable-length subnet masking", "concept", ("VLSM",)),
    ("ttl", "Time to live", "attribute", ("TTL", "time to live")),
    ("mtu", "Maximum transmission unit", "attribute", ("MTU",)),
    ("multicast", "Multicast", "concept", ("multicast",)),
    ("igmp", "Internet Group Management Protocol", "protocol", ("IGMP",)),
    ("pim", "Protocol Independent Multicast", "protocol", ("PIM", "PIM-SM")),
    # security and VPN
    ("acl", "Access control list", "technology", ("ACL", "ACLs", "access control list", "access list")),
    ("standard_acl", "Standard ACL", "technology", ("standard ACL", "standard access list")),
    ("extended_acl", "Extended ACL", "technology", ("extended ACL", "extended access list")),
    ("firewall", "Firewall", "device", ("firewall", "firewalls", "NGFW")),
    ("ipsec", "IPsec", "protocol", ("IPsec", "IPSec")),
    ("ike", "Internet Key Exchange", "protocol", ("IKE", "IKEv1", "IKEv2", "ISAKMP")),
    ("esp", "Encapsulating Security Payload", "protocol", ("ESP",)),
    ("ah", "Authentication Header", "protocol", ("AH",)),
    ("gre", "Generic Routing Encapsulation", "protocol", ("GRE",)),
    ("dmvpn", "Dynamic Multipoint VPN", "technology", ("DMVPN",)),
    ("vpn", "Virtual private network", "technology", ("VPN", "VPNs")),
    ("ssl_vpn", "SSL VPN", "technology", ("SSL VPN", "AnyConnect")),
    ("tls", "Transport Layer Security", "protocol", ("TLS", "SSL")),
    ("dot1x", "IEEE 802.1X", "protocol", ("802.1X", "dot1x")),
    ("eap", "Extensible Authentication Protocol", "protocol", ("EAP",)),
    ("wpa", "Wi-Fi Protected Access", "protocol", ("WPA2", "WPA3")),
    ("zone_based_firewall", "Zone-based policy firewall", "technology", ("zone-based firewall", "ZBFW")),
    ("ips", "Intrusion prevention system", "technology", ("IPS", "IDS")),
    # wireless, WAN, fabric, automation
    ("wlan", "Wireless LAN", "technology", ("WLAN", "wireless LAN", "Wi-Fi")),
    ("wlc", "Wireless LAN controller", "device", ("WLC", "wireless LAN controller")),
    ("capwap", "CAPWAP", "protocol", ("CAPWAP",)),
    ("ssid", "Service set identifier", "concept", ("SSID",)),
    ("sd_wan", "SD-WAN", "technology", ("SD-WAN", "SDWAN", "Viptela")),
    ("vmanage", "vManage", "device", ("vManage",)),
    ("vsmart", "vSmart controller", "device", ("vSmart",)),
    ("vbond", "vBond orchestrator", "device", ("vBond",)),
    ("vedge", "vEdge router", "device", ("vEdge", "cEdge", "WAN Edge")),
    ("omp", "Overlay Management Protocol", "protocol", ("OMP",)),
    ("sd_access", "SD-Access", "technology", ("SD-Access", "SDA")),
    ("lisp", "Locator/ID Separation Protocol", "protocol", ("LISP",)),
    ("dna_center", "Catalyst Center", "device", ("DNA Center", "Catalyst Center")),
    ("ansible", "Ansible", "technology", ("Ansible",)),
    ("rest_api", "REST API", "technology", ("REST API", "REST APIs")),
    ("json", "JSON", "technology", ("JSON",)),
    ("ppp", "Point-to-Point Protocol", "protocol", ("PPP", "PPPoE")),
    # devices and layers
    ("router", "Router", "device", ("router", "routers")),
    ("switch", "Switch", "device", ("switch", "switches", "multilayer switch")),
    ("layer1", "OSI layer 1 (physical)", "layer", ("layer 1", "physical layer")),
    ("layer2", "OSI layer 2 (data link)", "layer", ("layer 2", "data link layer")),
    ("layer3", "OSI layer 3 (network)", "layer", ("layer 3", "network layer")),
    ("layer4", "OSI layer 4 (transport)", "layer", ("layer 4", "transport layer")),
    ("layer7", "OSI layer 7 (application)", "layer", ("layer 7", "application layer")),
)  # fmt: skip

_CASE_SENSITIVE = re.compile(r"^[A-Z0-9]{1,3}$")
_SENTENCE = re.compile(r"(?:[^.!?\n]|[.!?](?=\S))+[.!?]?")  # a dot before a non-space (802.1D, 224.0.0.5) stays in
_STOPWORDS = {"of", "and", "the", "to", "for", "a", "an", "in", "on", "with"}


def _alias_pattern(aliases: Iterable[str], flags: int) -> re.Pattern[str] | None:
    ordered = sorted(set(aliases), key=len, reverse=True)
    if not ordered:
        return None
    return re.compile(r"(?<![\w+-])(" + "|".join(re.escape(alias) for alias in ordered) + r")(?![\w+-])", flags)


def _key(alias: str) -> str:
    return alias if _CASE_SENSITIVE.match(alias) else alias.casefold()


ALIASES: dict[str, str] = {_key(alias): key for key, _, _, aliases in VOCABULARY for alias in aliases}
ALIASES.update({name.casefold(): key for key, name, _, _ in VOCABULARY})
_STRICT = _alias_pattern((alias for alias in ALIASES if _CASE_SENSITIVE.match(alias)), 0)
_LOOSE = _alias_pattern((alias for alias in ALIASES if not _CASE_SENSITIVE.match(alias)), re.IGNORECASE)
KIND = {key: kind for key, _, kind, _ in VOCABULARY}


@dataclass(frozen=True)
class Mention:
    concept: str
    start: int
    end: int


@dataclass(frozen=True)
class Fact:
    """subject + predicate + (object concept | attribute=value), with the verbatim span that states it."""

    subject: str
    predicate: str
    start: int
    end: int
    object: str | None = None
    attribute: str | None = None
    value: str | None = None
    confidence: int = 90


def mentions(text: str) -> list[Mention]:
    """Concept mentions in text order; overlapping matches keep the longest ("backup designated router" over DR)."""
    found: list[Mention] = []
    for pattern in (_STRICT, _LOOSE):
        for match in pattern.finditer(text) if pattern else ():
            concept = ALIASES.get(_key(match.group(1))) or ALIASES.get(match.group(1).casefold())
            if concept:
                found.append(Mention(concept, match.start(1), match.end(1)))
    found.sort(key=lambda m: (m.start, -(m.end - m.start)))
    kept: list[Mention] = []
    for mention in found:
        if not kept or mention.start >= kept[-1].end:
            kept.append(mention)
    return kept


def concept_counts(text: str) -> Counter[str]:
    return Counter(mention.concept for mention in mentions(text))


# Too general to say what a question is about (every networking passage has them).
_GENERIC = frozenset({"router", "switch", "metric", "tcp", "udp", "multicast", "ipv4", "ethernet"})


def query_concepts(text: str) -> tuple[str, ...]:
    """The concepts a question is about, for the concept leg of retrieval (in-memory, no database)."""
    return tuple(sorted({mention.concept for mention in mentions(text)} - _GENERIC))


# ----------------------------------------------------------------------------- learned acronyms
_DEFINITION = re.compile(
    r"\b([A-Z][a-z]+(?:[ -](?:[A-Za-z][a-z]*|of|and|to|the)){1,7})\s+\(([A-Z][A-Za-z0-9+-]{1,9})\)"
)


def _initials_match(long_form: str, acronym: str) -> bool:
    words = [word for word in re.split(r"[ -]", long_form) if word and word.lower() not in _STOPWORDS]
    letters = [c for c in acronym.upper() if c.isalpha()]
    initials = "".join(word[0].upper() for word in words)
    position = 0
    for letter in letters:  # every letter of the acronym, in order, among the initials (OSPF, EIGRP, DMVPN)
        position = initials.find(letter, position)
        if position < 0:
            return False
        position += 1
    return len(letters) >= 2


def definitions(text: str) -> list[tuple[str, str, int, int]]:
    """Acronyms a passage defines: (acronym, long form, start, end) where the letters fit the long form."""
    found = []
    for match in _DEFINITION.finditer(text):
        words, acronym = match.group(1).strip().split(" "), match.group(2)
        # the shortest tail whose initials still fit ("Routers run Open Shortest Path First (OSPF)")
        for start in reversed(range(len(words))):
            tail = " ".join(words[start:])
            if tail[0].isupper() and _initials_match(tail, acronym):
                offset = match.start(1) + len(" ".join(words[:start])) + (1 if start else 0)
                found.append((acronym, tail, offset, match.end(2) + 1))
                break
    return found


# ----------------------------------------------------------------------------- facts
_NUMBER = r"(\d{1,5})"
_FILLER = r"(?:value\s+)?(?:of\s+|is\s+|defaults\s+to\s+|=\s*)?"
_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "tcp_port",
        re.compile(r"\bTCP(?:/|\s+ports?\s+(?:number\s+)?)" + _NUMBER + r"\b|\bport\s+" + _NUMBER + r"/TCP\b"),
    ),
    (
        "udp_port",
        re.compile(r"\bUDP(?:/|\s+ports?\s+(?:number\s+)?)" + _NUMBER + r"\b|\bport\s+" + _NUMBER + r"/UDP\b"),
    ),
    ("ip_protocol", re.compile(r"\bIP\s+protocol\s+(?:number\s+)?" + _NUMBER + r"\b", re.IGNORECASE)),
    ("administrative_distance", re.compile(r"\b(?:[Aa]dministrative\s+distance|AD)\s+" + _FILLER + _NUMBER + r"\b")),
    (
        "timer_s",
        re.compile(
            r"\b(hello|dead|hold|keepalive|update|invalid|flush)(?:\s+(?:timer|interval|time))?\s+"
            + _FILLER
            + _NUMBER
            + r"\s*(?:seconds|secs?|s)\b",
            re.IGNORECASE,
        ),
    ),
    ("multicast_address", re.compile(r"\b(224\.0\.0\.\d{1,3}|[Ff][Ff]02::[0-9A-Fa-f:]{1,20})(?![\d.:A-Fa-f])")),
)
_RELATION_VERBS: tuple[tuple[str, re.Pattern[str], int], ...] = (
    # "X is a/an/the [up to 3 words] Y" where Y ends the phrase (not "the Y controller", not "the ... of Y")
    ("is_a", re.compile(r"^\s*(?:is|was)\s+(?:an?|the)\s+(?:[\w-]+\s+){0,3}?$", re.IGNORECASE), 85),
    ("uses", re.compile(r"^\s*(?:uses|relies\s+on)\s+(?:the\s+|an?\s+)?$", re.IGNORECASE), 80),
    (
        "replaces",
        re.compile(r"^\s*(?:replaces|replaced|supersedes)\s+(?:the\s+)?(?:[\w-]+\s+){0,2}?$", re.IGNORECASE),
        80,
    ),
    ("extends", re.compile(r"^\s*extends\s+(?:the\s+)?(?:[\w-]+\s+){0,2}?$", re.IGNORECASE), 80),
    ("compared_with", re.compile(r"^\s*(?:vs\.?|versus|compared\s+(?:with|to))\s+(?:the\s+)?$", re.IGNORECASE), 70),
    ("operates_at", re.compile(r"^\s*(?:operates|works|runs)\s+at\s+(?:the\s+)?(?:OSI\s+)?$", re.IGNORECASE), 85),
)
# Concepts that are values or contexts rather than a statement's subject.
_NOT_SUBJECTS = {"tcp", "udp", "administrative_distance", "metric", "multicast", "router", "switch"}
_ENDS_PHRASE = re.compile(
    r"^(?:\s*[,.;:)!?]|\s*$|\s+(?:that|which|and|or|with|for|in|on|used|from|to|but|while|whereas|because|by|when|where)\b)",
    re.IGNORECASE,
)
_OBJECT_KINDS = {
    "is_a": ("class", "protocol", "technology", "device"),
    "replaces": ("protocol", "technology"),
    "extends": ("protocol", "technology"),
}


def _subject_kind_ok(concept: str) -> bool:
    return concept not in _NOT_SUBJECTS and KIND.get(concept) != "layer"


def facts(text: str) -> list[Fact]:
    """What the passage's sentences state, each with its evidence span (offsets into `text`)."""
    found: list[Fact] = []
    for sentence in _SENTENCE.finditer(text):
        base, body = sentence.start(), sentence.group(0)
        spotted = mentions(body)
        subjects = [m for m in spotted if _subject_kind_ok(m.concept)]
        for attribute, pattern in _VALUE_PATTERNS:
            for match in pattern.finditer(body):
                groups = [group for group in match.groups() if group]
                name = f"{groups[0].lower()}_timer_s" if attribute == "timer_s" else attribute
                before = [
                    m for m in subjects if m.end <= match.start() and KIND.get(m.concept) not in ("device", "class")
                ]
                if before:
                    subject = before[-1]
                    found.append(
                        Fact(
                            subject.concept,
                            "has_value",
                            base + subject.start,
                            base + match.end(),
                            None,
                            name,
                            groups[-1],
                        )
                    )
        for left, right in zip(spotted, spotted[1:], strict=False):
            if left.concept == right.concept or not _subject_kind_ok(left.concept):
                continue
            between = body[left.end : right.start].removeprefix(")")  # "Generic Routing Encapsulation (GRE) is a"
            for predicate, pattern, confidence in _RELATION_VERBS:
                if not pattern.match(between):
                    continue
                if (predicate == "operates_at") != (KIND.get(right.concept) == "layer"):
                    continue
                if predicate in _OBJECT_KINDS and KIND.get(right.concept) not in _OBJECT_KINDS[predicate]:
                    continue
                if predicate == "is_a" and (
                    re.search(r"\bof\s+$", between) or not _ENDS_PHRASE.match(body[right.end :])
                ):
                    continue
                found.append(
                    Fact(
                        left.concept,
                        predicate,
                        base + left.start,
                        base + right.end,
                        right.concept,
                        confidence=confidence,
                    )
                )
                break
    return found


# ----------------------------------------------------------------------------- storage
async def _concept_ids(session: AsyncSession, keys: set[str]) -> dict[str, uuid.UUID]:
    """Vocabulary concepts by key, created on first use (one row per key, whoever creates it first)."""
    names = {key: (name, kind) for key, name, kind, _ in VOCABULARY}
    missing = sorted(key for key in keys if key in names)
    if missing:
        await session.execute(
            pg_insert(KnowledgeConcept).on_conflict_do_nothing(index_elements=["key"]),
            [{"key": key, "name": names[key][0], "kind": names[key][1], "origin": "vocabulary"} for key in missing],
        )
    rows = await session.execute(
        select(KnowledgeConcept.key, KnowledgeConcept.id).where(KnowledgeConcept.key.in_(keys))
    )
    return dict(rows.tuples().all())


async def index_revision(session: AsyncSession, revision_id: uuid.UUID) -> int:
    """The concept rows of every passage a revision carries (shared passages are re-read: deterministic, cheap)."""
    carried = select(KnowledgePassageOccurrence.passage_id).where(KnowledgePassageOccurrence.revision_id == revision_id)
    rows = await session.execute(
        select(KnowledgePassage.id, KnowledgePassage.content)
        .where(KnowledgePassage.id.in_(carried))
        .order_by(KnowledgePassage.id)
    )
    return await index_passages(session, rows.tuples().all())


async def index_passages(session: AsyncSession, passages: Sequence[tuple[uuid.UUID, str]]) -> int:
    """Mentions and facts of these passages, replacing what the pattern layer said about them before."""
    if not passages:
        return 0
    ids = [pid for pid, _ in passages]
    await session.execute(delete(KnowledgePassageConcept).where(KnowledgePassageConcept.passage_id.in_(ids)))
    await session.execute(
        delete(KnowledgeRelation).where(KnowledgeRelation.passage_id.in_(ids), KnowledgeRelation.method == "pattern")
    )
    analysed = [(pid, concept_counts(content), facts(content), definitions(content)) for pid, content in passages]
    keys = {key for _, counts, _, _ in analysed for key in counts}
    keys |= {key for _, _, found, _ in analysed for fact in found for key in (fact.subject, fact.object) if key}
    keys |= {
        ALIASES[long.casefold()]
        for _, _, _, defined in analysed
        for _, long, _, _ in defined
        if long.casefold() in ALIASES
    }
    concept = await _concept_ids(session, keys)
    mention_rows = [
        {
            "passage_id": pid,
            "concept_id": concept[key],
            "mentions": min(count, 32_767),
            "extractor_version": EXTRACTOR_VERSION,
        }
        for pid, counts, _, _ in analysed
        for key, count in counts.items()
        if key in concept
    ]
    if mention_rows:
        await session.execute(pg_insert(KnowledgePassageConcept).on_conflict_do_nothing(), mention_rows)
    relation_rows = [
        {
            "subject_concept_id": concept[fact.subject],
            "predicate": fact.predicate,
            "object_concept_id": concept[fact.object] if fact.object else None,
            "attribute": fact.attribute,
            "value": fact.value,
            "passage_id": pid,
            "evidence_start": fact.start,
            "evidence_end": fact.end,
            "method": "pattern",
            "extractor_version": EXTRACTOR_VERSION,
            "confidence": fact.confidence,
        }
        for pid, _, found, _ in analysed
        for fact in found
        if fact.subject in concept and (fact.object is None or fact.object in concept)
    ]
    if relation_rows:
        await session.execute(pg_insert(KnowledgeRelation), relation_rows)
    learned = [
        {"alias": acronym.casefold(), "concept_id": concept_id, "origin": "learned", "evidence_passage_id": pid}
        for pid, _, _, defined in analysed
        for acronym, long_form, _, _ in defined
        if acronym.casefold() not in ALIASES and (concept_id := concept.get(ALIASES.get(long_form.casefold(), "")))
    ]
    if learned:
        await session.execute(pg_insert(KnowledgeConceptAlias).on_conflict_do_nothing(), learned)
    return len(relation_rows)
