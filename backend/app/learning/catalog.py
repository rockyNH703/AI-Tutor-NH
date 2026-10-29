"""The catalogs: the values a profile may use. The academic catalog (GET /v1/catalog/academic) is
for the learner profile; the engineering catalog (GET /v1/catalog/engineering) for the Engineering
Profile, which is what the assistant is now told about a user.

Deliberately small, and made to grow:

- **Codes are permanent.** A code is never deleted, reused or given another meaning. A value
  that should no longer be offered is marked `deprecated`: it is still listed (so a profile
  that has it can show it), still accepted while a profile keeps it unchanged, and no longer
  accepted as a new choice.
- **Every change to this data bumps its version** (CATALOG_VERSION, ENGINEERING_VERSION). Clients
  cache each catalog by version (ETag); tests/test_learning.py fails when the content changes and
  the version does not.
- Names are English display names, at most 60 characters, with no comma or semicolon (the
  assistant's instructions list them separated by "; "). The prompt uses them; clients may show
  their own translations, keyed by code.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass

CATALOG_VERSION = 1


@dataclass(frozen=True)
class Entry:
    code: str
    name: str
    deprecated: bool = False


def _entries(*pairs: tuple[str, str]) -> tuple[Entry, ...]:
    return tuple(Entry(code, name) for code, name in pairs)


GRADES = _entries(
    *((f"class_{n}", f"Class {n}") for n in range(6, 13)),
    ("undergraduate", "Undergraduate"),
    ("other", "Other"),
)

BOARDS = _entries(
    ("cbse", "CBSE"),
    ("icse", "ICSE / ISC"),
    ("state", "State board"),
    ("nios", "NIOS"),
    ("ib", "IB"),
    ("cambridge", "Cambridge (IGCSE / A Level)"),
    ("other", "Other"),
)

# Only meaningful with board = "state".
BOARD_STATES = _entries(
    ("andhra_pradesh", "Andhra Pradesh"),
    ("arunachal_pradesh", "Arunachal Pradesh"),
    ("assam", "Assam"),
    ("bihar", "Bihar"),
    ("chhattisgarh", "Chhattisgarh"),
    ("goa", "Goa"),
    ("gujarat", "Gujarat"),
    ("haryana", "Haryana"),
    ("himachal_pradesh", "Himachal Pradesh"),
    ("jharkhand", "Jharkhand"),
    ("karnataka", "Karnataka"),
    ("kerala", "Kerala"),
    ("madhya_pradesh", "Madhya Pradesh"),
    ("maharashtra", "Maharashtra"),
    ("manipur", "Manipur"),
    ("meghalaya", "Meghalaya"),
    ("mizoram", "Mizoram"),
    ("nagaland", "Nagaland"),
    ("odisha", "Odisha"),
    ("punjab", "Punjab"),
    ("rajasthan", "Rajasthan"),
    ("sikkim", "Sikkim"),
    ("tamil_nadu", "Tamil Nadu"),
    ("telangana", "Telangana"),
    ("tripura", "Tripura"),
    ("uttar_pradesh", "Uttar Pradesh"),
    ("uttarakhand", "Uttarakhand"),
    ("west_bengal", "West Bengal"),
    ("andaman_nicobar", "Andaman and Nicobar Islands"),
    ("chandigarh", "Chandigarh"),
    ("dadra_nagar_haveli_daman_diu", "Dadra and Nagar Haveli and Daman and Diu"),
    ("delhi", "Delhi"),
    ("jammu_kashmir", "Jammu and Kashmir"),
    ("ladakh", "Ladakh"),
    ("lakshadweep", "Lakshadweep"),
    ("puducherry", "Puducherry"),
)

# The language the student is taught in at school.
MEDIUMS = _entries(
    ("english", "English"),
    ("hindi", "Hindi"),
    ("assamese", "Assamese"),
    ("bengali", "Bengali"),
    ("gujarati", "Gujarati"),
    ("kannada", "Kannada"),
    ("malayalam", "Malayalam"),
    ("marathi", "Marathi"),
    ("odia", "Odia"),
    ("punjabi", "Punjabi"),
    ("tamil", "Tamil"),
    ("telugu", "Telugu"),
    ("urdu", "Urdu"),
    ("other", "Other"),
)

ACADEMIC_YEARS = _entries(
    ("2025_26", "2025–26"),
    ("2026_27", "2026–27"),
    ("2027_28", "2027–28"),
)

SUBJECTS = _entries(
    ("mathematics", "Mathematics"),
    ("science", "Science"),
    ("physics", "Physics"),
    ("chemistry", "Chemistry"),
    ("biology", "Biology"),
    ("english", "English"),
    ("hindi", "Hindi"),
    ("sanskrit", "Sanskrit"),
    ("social_science", "Social Science"),
    ("history", "History"),
    ("geography", "Geography"),
    ("political_science", "Political Science"),
    ("economics", "Economics"),
    ("accountancy", "Accountancy"),
    ("business_studies", "Business Studies"),
    ("computer_science", "Computer Science"),
    ("environmental_studies", "Environmental Studies"),
    ("psychology", "Psychology"),
)

# Catalog list per learner-profile field (and "subjects" for the subject list).
LISTS: dict[str, tuple[Entry, ...]] = {
    "grades": GRADES,
    "boards": BOARDS,
    "boardStates": BOARD_STATES,
    "mediums": MEDIUMS,
    "academicYears": ACADEMIC_YEARS,
    "subjects": SUBJECTS,
}
FIELD_LISTS = {
    "grade": GRADES,
    "board": BOARDS,
    "board_state": BOARD_STATES,
    "medium": MEDIUMS,
    "academic_year": ACADEMIC_YEARS,
}

_BY_CODE = {id(entries): {entry.code: entry for entry in entries} for entries in LISTS.values()}


def lookup(entries: tuple[Entry, ...], code: str | None) -> Entry | None:
    return _BY_CODE[id(entries)].get(code) if code else None


def as_json() -> dict:
    return {"version": CATALOG_VERSION, **{key: [asdict(e) for e in entries] for key, entries in LISTS.items()}}


def content_hash() -> str:
    """SHA-256 of the catalog's data (not its version): what the version must follow."""
    data = {key: [asdict(e) for e in entries] for key, entries in LISTS.items()}
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


ETAG = f'"academic-{CATALOG_VERSION}-{content_hash()[:16]}"'


# ------------------------------------------------------------------ engineering
ENGINEERING_VERSION = 1

ROLES = _entries(
    ("network_engineer", "Network Engineer"),
    ("network_architect", "Network Architect"),
    ("security_engineer", "Security Engineer"),
    ("soc_analyst", "SOC Analyst"),
    ("penetration_tester", "Penetration Tester"),
    ("cloud_engineer", "Cloud Engineer"),
    ("devops_engineer", "DevOps Engineer"),
    ("systems_administrator", "Systems Administrator"),
    ("automation_engineer", "Network Automation Engineer"),
    ("support_engineer", "Support Engineer (NOC / TAC)"),
    ("it_manager", "IT Manager"),
    ("certification_candidate", "Certification Candidate"),
    ("other", "Other"),
)

EXPERIENCE_LEVELS = _entries(
    ("junior", "Junior (0–2 years)"),
    ("mid", "Mid-level (3–5 years)"),
    ("senior", "Senior (6–10 years)"),
    ("principal", "Principal (10+ years)"),
)

FOCUS_AREAS = _entries(
    ("routing_switching", "Routing and Switching"),
    ("wireless", "Wireless LAN"),
    ("wan_sdwan", "WAN and SD-WAN"),
    ("datacenter", "Data Center Networking"),
    ("firewalls", "Firewalls"),
    ("vpn", "VPN (IPsec / SSL)"),
    ("network_security", "Network Security"),
    ("pentesting", "Penetration Testing"),
    ("soc_siem", "Security Operations (SOC / SIEM)"),
    ("cloud", "Cloud Infrastructure"),
    ("linux", "Linux"),
    ("windows_ad", "Windows Server and Active Directory"),
    ("identity", "Identity and Access Management"),
    ("devsecops", "DevSecOps"),
    ("automation", "Network Automation"),
    ("iac", "Infrastructure as Code"),
    ("containers", "Containers and Kubernetes"),
    ("ai_security", "AI Security"),
    ("load_balancing", "Load Balancing"),
    ("dns_dhcp_ipam", "DNS / DHCP / IPAM"),
    ("observability", "Monitoring and Observability"),
    ("grc", "Governance / Risk / Compliance (GRC)"),
)

PLATFORMS = _entries(
    ("cisco_ios", "Cisco IOS / IOS XE"),
    ("cisco_nxos", "Cisco NX-OS"),
    ("cisco_iosxr", "Cisco IOS XR"),
    ("cisco_asa_ftd", "Cisco ASA / Firepower Threat Defense"),
    ("cisco_meraki", "Cisco Meraki"),
    ("juniper", "Juniper Junos"),
    ("arista", "Arista EOS"),
    ("palo_alto", "Palo Alto Networks PAN-OS"),
    ("fortinet", "Fortinet FortiGate (FortiOS)"),
    ("check_point", "Check Point"),
    ("f5", "F5 BIG-IP"),
    ("aruba", "HPE Aruba Networking"),
    ("mikrotik", "MikroTik RouterOS"),
    ("aws", "Amazon Web Services (AWS)"),
    ("azure", "Microsoft Azure"),
    ("gcp", "Google Cloud"),
    ("linux", "Linux"),
    ("windows_server", "Windows Server"),
    ("vmware", "VMware vSphere / NSX"),
    ("kubernetes", "Kubernetes"),
    ("splunk", "Splunk"),
    ("elastic", "Elastic Stack (ELK)"),
    ("sentinel", "Microsoft Sentinel"),
    ("other", "Other"),
)

CERTIFICATIONS = _entries(
    ("ccna", "CCNA"),
    ("ccnp_enterprise", "CCNP Enterprise"),
    ("ccnp_security", "CCNP Security"),
    ("ccnp_data_center", "CCNP Data Center"),
    ("ccie_enterprise", "CCIE Enterprise Infrastructure"),
    ("ccie_security", "CCIE Security"),
    ("jncia", "Juniper JNCIA-Junos"),
    ("jncis", "Juniper JNCIS"),
    ("pcnse", "Palo Alto Networks PCNSE"),
    ("fortinet_fcp", "Fortinet Certified Professional (FCP)"),
    ("comptia_network", "CompTIA Network+"),
    ("comptia_security", "CompTIA Security+"),
    ("comptia_cysa", "CompTIA CySA+"),
    ("comptia_pentest", "CompTIA PenTest+"),
    ("ceh", "EC-Council CEH"),
    ("oscp", "OffSec OSCP"),
    ("cissp", "ISC2 CISSP"),
    ("aws_saa", "AWS Solutions Architect – Associate"),
    ("aws_security", "AWS Security – Specialty"),
    ("az_104", "Microsoft Azure Administrator (AZ-104)"),
    ("az_500", "Microsoft Azure Security Engineer (AZ-500)"),
    ("cka", "Certified Kubernetes Administrator (CKA)"),
    ("cks", "Certified Kubernetes Security Specialist (CKS)"),
    ("rhcsa", "Red Hat RHCSA"),
    ("other", "Other"),
)

# Catalog list per key of GET /v1/catalog/engineering.
ENGINEERING_LISTS: dict[str, tuple[Entry, ...]] = {
    "roles": ROLES,
    "experienceLevels": EXPERIENCE_LEVELS,
    "focusAreas": FOCUS_AREAS,
    "platforms": PLATFORMS,
    "certifications": CERTIFICATIONS,
}
# Engineering Profile column -> its list: one code each, or a list of codes.
ENGINEERING_FIELDS = {"role": ROLES, "experience_level": EXPERIENCE_LEVELS, "certification_goal": CERTIFICATIONS}
ENGINEERING_LIST_FIELDS = {"focus_areas": FOCUS_AREAS, "platforms": PLATFORMS}

_BY_CODE.update({id(entries): {entry.code: entry for entry in entries} for entries in ENGINEERING_LISTS.values()})


def _data(lists: dict[str, tuple[Entry, ...]]) -> dict[str, list[dict]]:
    return {key: [asdict(e) for e in entries] for key, entries in lists.items()}


def engineering_json() -> dict:
    return {"version": ENGINEERING_VERSION, **_data(ENGINEERING_LISTS)}


def engineering_hash() -> str:
    """SHA-256 of the engineering catalog's data (not its version): what ENGINEERING_VERSION must follow."""
    return hashlib.sha256(json.dumps(_data(ENGINEERING_LISTS), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


ENGINEERING_ETAG = f'"engineering-{ENGINEERING_VERSION}-{engineering_hash()[:16]}"'
