# Certification coverage matrix

> NetSec AI's shared Knowledge Library against the official exam objectives, checked on 2026-11-16. The
> objective lists are the official Cisco exam-topic PDFs, imported and published in the library themselves
> (CCNA 200-301 v2.0, ENCOR 350-401 v1.2, ENARSI 300-410 v1.1, ENAUTO 300-435 v2.0, ENSDWI 300-415 v1.2).
> Coverage is judged per objective: which library sources support it, and whether a retrieval test found
> them. **A topic is covered only when a source passage supports it; an exam-objective line alone is not
> study coverage.**

## Eight libraries in production (2026-11-18)

Read on production (`f7727c5`, read-only): 66 published revisions, 7 retired, none failed or waiting for review;
**24,335 live passages**. Retrieval (real embeddings, production retriever): **8×8 course isolation with no leak**
(64 scoped searches), and **27/27 per-objective questions** answered from their own course (p95 563 ms including
the embedding call). Exam versions from the official study guides: AZ-900 skills as of 2026-07-20, AZ-305 as of
2026-04-17.

| Course | Live passages | Sources | Status |
|---|---|---|---|
| ccna | 229 | CCNA 200-301 shared library (samples) | PARTIAL by source (unchanged, below) |
| ccnp | 3,190 | CCNP Enterprise shared library | as below (unchanged) |
| aws | 2,467 | AWS Cloud shared library (2017 SAA Official Study Guide, SAA and Security slide decks, exam sheets) 2,435; AWS official SAA-C03 exam guide 32 | **PARTIAL**: SAA-C03 objectives answer from the 2017 guide; no current-edition study book (SOURCE GAP until the owner provides one) |
| azure | 650 | Azure Cloud shared library (AZ-104/305/500/900 syllabi) 23; Azure official study content (Microsoft Learn, 174 units) 627 | **PARTIAL, rights pending**: all 7 AZ-900/AZ-305 skill areas answer from the Learn content, but Learn's Terms of Use restrict its training content to personal, non-commercial use; the recorded "CC-BY" basis is not confirmed (owner decision) |
| ceh | 7,043 | CEH shared library (v12 modules, 20 published) | processed incl. the formerly oversized modules |
| cpent | 1,799 | CPENT shared library (14 modules) | processed incl. module 06 |
| fortigate | 6,563 | Fortigate shared library (FortiOS 7.6 Administration Guide, 4,169 pages, + training-institute syllabus) 6,559; "Fortinet official NSE program reference" 4 | **PARTIAL**: technical content complete for FortiOS 7.6; Fortinet's official NSE 4 blueprint is login-only (BLOCKED until the owner downloads it); the 4-passage "program reference" was written by an agent, not by Fortinet (owner decision: delete) |
| paloalto | 2,395 | Palo Alto NGFW shared library (PAN-OS administration 2023 + PCNSE datasheet) | PARTIAL per objective (2023 content) |

## Library (10 published books, 2026-11-16)

| Course | Source | Book | Pages | Passages |
|---|---|---|---|---|
| ccna | CCNA 200-301 shared library | CCNA 200-301 Official Cert Guide Vol 1, 2nd ed (sample) | 123 | 116 |
| ccna | CCNA 200-301 shared library | CCNA 200-301 Official Cert Guide Vol 2, 2nd ed (sample) | 101 | 110 |
| ccna | CCNA 200-301 shared library | 200-301 exam topics v2.0 | 3 | 8 |
| ccnp | CCNP Enterprise shared library | CCNP/CCIE ENCOR 350-401 Official Cert Guide, 2nd ed (full) | 1,149 | 2,090 |
| ccnp | CCNP Enterprise shared library | 350-401 ENCOR exam topics v1.2 | 3 | 9 |
| ccnp | CCNP Enterprise shared library | 300-410 ENARSI exam topics v1.1 | 2 | 6 |
| ccnp | CCNP Enterprise shared library | 300-435 ENAUTO exam topics v2.0 | 2 | 7 |
| ccnp | CCNP Enterprise shared library | 300-415 ENSDWI exam topics v1.2 | 3 | 9 |
| ccnp | CCNP Enterprise shared library | Cisco Software-Defined WAN (full book) | 608 | 980 |
| ccnp | CCNP Enterprise shared library | Cisco Catalyst SD-WAN Design (excerpt) | 72 | 91 |

The CCNA volumes are the owner-provided **samples** (front matter, one chapter, index): the owner confirmed
(2026-11-16) that `F:\CCNA` is the complete available CCNA scope. `ccna_book.pdf` is a byte-duplicate of
`ccna_vol_2.pdf` and was never imported.

## CCNA 200-301 v2.0 — coverage by domain

| Domain | Objectives | Supported by | Coverage | Retrieval |
|---|---|---|---|---|
| 1.0 Network Infrastructure and Connectivity (25%) | 1.1–1.7 | Vol 1/Vol 2 samples (cabling, IPv4/IPv6 addressing, wireless, DHCP) | PARTIAL: the samples cover the fundamentals they contain; deep troubleshooting detail for every subobjective is not in the samples | OK (native VLAN, MAC learning, routed port questions all hit with pages) |
| 2.0 Switching and Network Access (25%) | 2.1–2.5 | Vol 1/Vol 2 samples (VLANs, trunks, STP, port security) | PARTIAL: same-source limit; Rapid PVST+ detail beyond the sample chapter is absent | OK (trunk/802.1Q questions hit) |
| 3.0 IP Routing (20%) | 3.1–3.4 | Vol 1/Vol 2 samples (routing table, static routes, OSPF, FHRP) | PARTIAL | OK (OSPF DR/BDR question hit with pages) |
| 4.0 Network Services and Security (20%) | 4.1–4.7 | Vol 1/Vol 2 samples (AAA, NAT, ACLs, L2 security) | PARTIAL | OK |
| 5.0 AI and Network Operations (10%) | 5.1–5.6 | Vol 2 sample (SNMP, syslog, Ansible basics) | PARTIAL: agentic-AI objectives (5.1, 5.2) are new in v2.0 and not in the 2024/2025 samples | SOURCE GAP for 5.1–5.2 |

**CCNA status: PARTIALLY COMPLETE by design of the available sources.** Every objective the samples support
retrieves correctly; the objectives outside the sample chapters are SOURCE GAPs, recorded, not fabricated.

## CCNP ENCOR 350-401 — coverage by domain

| Domain | Objectives | Supported by | Coverage | Retrieval |
|---|---|---|---|---|
| 1.0 Architecture (15%) | 1.1–1.4 | ENCOR book (full), SD-WAN books | FULL (SD-Access and QoS chapters included in the full book) | OK |
| 2.0 Virtualization (10%) | 2.1–2.3 | ENCOR book (VRF, GRE/IPsec, LISP, VXLAN) | FULL | OK (VRF question hit) |
| 3.0 Infrastructure (30%) | 3.1–3.3 | ENCOR book (L2, L3, IP services) | FULL | OK (EIGRP, OSPF LSA questions hit) |
| 4.0 Network Assurance (10%) | 4.1–4.6 | ENCOR book (NetFlow, SPAN, IP SLA, NETCONF/RESTCONF) | FULL | OK |
| 5.0 Security (20%) | 5.1–5.4 | ENCOR book (AAA, ACLs, CoPP, REST API security, TrustSec/MACsec) | FULL | OK (CoPP question hit) |
| 6.0 Automation and AI (15%) | 6.1–6.7 | ENCOR book (Python, JSON, YANG, EEM) | FULL | OK |

**ENCOR status: VERIFIED COMPLETE** — the full Official Cert Guide (1,149 pages) covers every domain; the
exam-topic PDF reconciles with it (kept separate: different works, no duplicate review raised).

## CCNP ENARSI 300-410 — coverage by domain

| Domain | Objectives | Supported by | Coverage | Retrieval |
|---|---|---|---|---|
| 1.0 Layer 3 Technologies (35%) | 1.1–1.11 | ENCOR book (EIGRP, OSPF, BGP fundamentals) + exam topics | PARTIAL: ENCOR covers the core; ENARSI-specific depth (route maps, redistribution, VRF-lite, BGP route reflectors) is thinner without the ENARSI guide | OK (EXSTART troubleshooting hit) |
| 2.0 VPN Technologies (20%) | 2.1–2.3 | ENCOR book (GRE/IPsec basics); MPLS/DMVPN depth | SOURCE GAP: no MPLS or DMVPN study material in the library | GAP |
| 3.0 Infrastructure Security (20%) | 3.1–3.4 | ENCOR book (AAA, ACLs, CoPP, IPv6 first-hop security) | PARTIAL (ENCOR covers the concepts) | OK |
| 4.0 Infrastructure Services (25%) | 4.1–4.7 | ENCOR book (SNMP, syslog, DHCP, IP SLA, NetFlow) | PARTIAL | OK |

**ENARSI status: PARTIALLY COMPLETE.** The exam-topic document is published; the ENARSI Official Cert Guide
itself is not in the authorized folders. **MPLS (2.1–2.2) and DMVPN (2.3) are SOURCE GAPs**: no authorized
study material exists in the library, and Cisco's online documentation cannot be ingested without the owner
providing or authorizing those documents (Cisco's site refuses automated retrieval, and unlicensed copying is
not permitted). The existing import paths are ready the moment the owner supplies them.

## CCNP ENAUTO 300-435 — coverage by domain

| Domain | Objectives | Supported by | Coverage | Retrieval |
|---|---|---|---|---|
| 1.0 Network Automation Foundation (10%) | 1.1–1.5 | ENCOR book (YANG, NETCONF, RESTCONF) + exam topics | PARTIAL (OpenConfig and pyang/YANG Suite tooling detail is ENAUTO-specific) | OK (NETCONF/YANG, RESTCONF questions hit) |
| 2.0 Device-Level Automation (25%) | 2.1–2.7 | ENCOR book (Python, EEM); Netmiko/ncclient depth | PARTIAL | OK |
| 3.0 Controller-Based Automation (30%) | 3.1–3.6 | ENCOR book (Catalyst Center, SD-WAN Manager APIs); Jinja2 | PARTIAL | OK |
| 4.0 Operations (20%) | 4.1–4.6 | ENCOR book (telemetry); ThousandEyes/ISE specifics | PARTIAL | OK |
| 5.0 AI in Automation (15%) | 5.1–5.4 | Exam topics only | SOURCE GAP: no AI-in-automation study material (FastMCP, AI-assisted code) | GAP |

**ENAUTO status: PARTIALLY COMPLETE.** The ENAUTO Official Cert Guide is not in the authorized folders;
ENCOR's automation chapters cover the foundations. Domain 5.0 (AI in Automation) is a **SOURCE GAP**: no
authorized study material exists (FastMCP, AI-assisted code development), and Cisco's online documentation
cannot be ingested without the owner providing or authorizing those documents.

## CCNP ENSDWI 300-415 — coverage by domain

| Domain | Objectives | Supported by | Coverage | Retrieval |
|---|---|---|---|---|
| 1.0 Architecture (20%) | 1.1–1.3 | SD-WAN EBOOK (full), Catalyst SD-WAN Design, exam topics | FULL | OK (vSmart/vBond/vManage, OMP/TLOC questions hit) |
| 2.0 Controller Deployment (15%) | 2.1–2.4 | SD-WAN EBOOK | FULL | OK |
| 3.0 Router Deployment (20%) | 3.1–3.7 | SD-WAN EBOOK | FULL | OK |
| 4.0 Policies (20%) | 4.1–4.5 | SD-WAN EBOOK | FULL | OK |
| 5.0 Security and QoS (15%) | 5.1–5.5 | SD-WAN EBOOK | FULL | OK |
| 6.0 Management and Operations (10%) | 6.1–6.4 | SD-WAN EBOOK | FULL | OK |

**ENSDWI status: VERIFIED COMPLETE** — the full SD-WAN book (608 pages) plus the Catalyst SD-WAN Design
excerpt cover every domain; the exam topics reconcile with them.

## Retrieval verification (2026-11-16, live library, read-only)

12/12 representative questions across all five exams returned hits with pages, from the correct course's
sources, no cross-course leak (3/3 boundary tests clean), all three legs live (dense, lexical, concept),
search p95 well under 300 ms. The first question's cold embedding (1,757 ms) was a provider cold start;
subsequent embeddings ~300 ms.

## Source gaps (recorded, never fabricated)

1. **CCNA**: the sample chapters' scope (owner-confirmed final). Agentic-AI objectives 5.1–5.2 are outside
   the 2024/2025 samples — **SOURCE GAP** (no authorized material exists; Cisco's online documentation
   cannot be ingested without the owner providing or authorizing it).
2. **ENARSI**: the ENARSI 300-410 Official Cert Guide; MPLS (2.1–2.2) and DMVPN (2.3) study depth —
   **SOURCE GAP**.
3. **ENAUTO**: the ENAUTO 300-435 Official Cert Guide; AI-in-Automation (5.0) study material —
   **SOURCE GAP**.

Filling these needs owner-provided or licensed official documents; the existing import paths (operator PDF
import, `ingest_documents.py` for authorized text) are ready the moment they arrive.
