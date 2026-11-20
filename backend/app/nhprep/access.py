"""Who NHprep is for (owner decision Q2): the one rule for every /v1/nhprep route, the chat's NHprep turn and every
reader of a reply's NHprep card, decided per request from the account as it is now."""

from __future__ import annotations

from typing import Any

from app.access.students import is_nh_student
from app.settings import Settings


def nhprep_allows(user: Any, settings: Settings) -> bool:
    """NHPREP_MODE for this account: off nobody; otherwise a NetworkersHome student (a verified address at
    NHPREP_ALLOWED_EMAIL_DOMAINS, app/access/students.py); qa narrows that to NHPREP_QA_EMAILS, never widens it."""
    if settings.nhprep_mode == "off" or not is_nh_student(user, settings):
        return False
    # is_nh_student checks only the domain is ASCII: require the whole address to be, so lowercasing can't fold a
    # lookalike ("Kumar@…", KELVIN SIGN) into a listed address.
    if settings.nhprep_mode == "on":
        return True
    email = user.email.strip()
    return email.isascii() and email.lower() in settings.nhprep_qa_list
