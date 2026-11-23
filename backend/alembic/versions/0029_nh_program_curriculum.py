"""NH programs' curriculum: the four programs and their months, verbatim from the owner's curriculum (2026-11-23).

Revision ID: 0029_nh_program_curriculum
Revises: 0028_nh_programs
Create Date: 2026-11-23

Reference rows only (the schema is 0028), inserted idempotently. Lesson-level content is never invented here: what
each month may search and quiz is mapped separately (scripts/program_admin.py mapping sync), after the owner's review.
"""

import sqlalchemy as sa
from alembic import op

revision = "0029_nh_program_curriculum"
down_revision = "0028_nh_programs"
branch_labels = None
depends_on = None

PROGRAMS = (
    ("cyber-cloud-security-ai", "Full Stack Cyber & Cloud Security with AI", 1),
    ("network-security-ai", "Full Stack Network Security with AI", 2),
    ("networking-ai", "Full Stack Networking with AI", 3),
    ("ccna-ccnp-enterprise", "CCNA & CCNP Enterprise", 4),
)

MONTHS = {
    "cyber-cloud-security-ai": (
        "Network Fundamentals & Security (CCNA)",
        "Linux Mastery & Ethical Hacking Foundations",
        "AI Penetration Testing - Web + Network + Cloud",
        "Cloud Computing & AWS Operations, Firewall Technologies (PaloAlto & FortiGate)",
        "Cloud Security & DevSecOps (AWS & Azure)",
        "Container & Kubernetes Security",
        "AI SOC Operations & Incident Response",
        "Career Launch + Capstone + Internship",
    ),
    "network-security-ai": (
        "Networking Foundations - CCNA 200-301",
        "CCNP ENCOR-350-401 (Core)",
        "CCNP ENARSI-300-410 (Advanced)",
        "CCNP SDWAN - Viptela Stack",
        "PaloAlto NGFW - Next-Generation Firewall",
        "FortiGate - NSE-4 - Next-Generation Firewall",
        "Cloud Firewalls, NOC & AI SOC, SASE, ZTA",
        "AI in Network Security + Project",
    ),
    "networking-ai": (
        "Networking Fundamentals & CCNA Core",
        "Advanced Routing & Switching (CCNP ENCOR)",
        "Network Security & Firewall Technologies (PaloAlto & FortiGate)",
        "SD-WAN & Next-Gen Networking",
        "Cloud Networking & Virtualization & Zero Trust & ZTNA - SASE",
        "Wireless Technologies & Enterprise Wi-Fi",
        "Network Automation & Programmability",
        "Final Capstone Projects & Placement Preparation",
    ),
    "ccna-ccnp-enterprise": (
        "Networking Foundations - CCNA 200-301",
        "CCNP ENCOR-350-401 (Core)",
        "CCNP ENARSI-300-410 (Advanced)",
        "CCNP SDWAN - Viptela Stack",
        "AI Network Automation",
    ),
}


PROGRAM = sa.text(
    "INSERT INTO programs (slug, title, position) VALUES (:slug, :title, :position) ON CONFLICT (slug) DO NOTHING"
)
MONTH = sa.text(
    "INSERT INTO program_modules (program_id, month, title) SELECT id, :month, :title FROM programs WHERE slug = :slug "
    "ON CONFLICT (program_id, month) DO NOTHING"
)


def upgrade() -> None:
    for slug, title, position in PROGRAMS:
        op.execute(PROGRAM.bindparams(slug=slug, title=title, position=position))
    for slug, months in MONTHS.items():
        for month, title in enumerate(months, start=1):
            op.execute(MONTH.bindparams(slug=slug, month=month, title=title))


def downgrade() -> None:
    # Program chats, invoices or mappings point at these rows: 0028's downgrade check refuses those first, and the
    # foreign keys refuse anything else.
    op.execute("DELETE FROM program_modules")
    op.execute("DELETE FROM programs")
