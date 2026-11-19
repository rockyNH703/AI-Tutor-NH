"""NetworkersHome students: the accounts that may see NHprep-derived content.

That covers the certification bank's NHprep questions (app/qbank) and NHprep's own catalog. One rule, shared
by both features:
- the account's email is verified;
- the part after its last "@", case-folded, is exactly one of NHPREP_ALLOWED_EMAIL_DOMAINS;
- no list means nobody: it fails closed.
Each feature adds its own rollout setting on top (QUESTION_BANK_TURN, NHPREP_MODE).
"""

from __future__ import annotations

from typing import Any

from app.settings import Settings


def allowed_domains(settings: Settings) -> frozenset[str]:
    return frozenset(
        part.strip().casefold() for part in settings.nhprep_allowed_email_domains.split(",") if part.strip()
    )


def is_nh_student(user: Any, settings: Settings) -> bool:
    """Whether this account is a NetworkersHome student (`user`: anything with `email` and
    `email_verified`)."""
    email = getattr(user, "email", None)
    if not getattr(user, "email_verified", False) or not isinstance(email, str) or "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1].strip()
    # ASCII before folding: U+212A KELVIN SIGN folds to "k", so a lookalike domain would otherwise pass.
    return bool(domain) and domain.isascii() and domain.casefold() in allowed_domains(settings)
