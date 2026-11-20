"""Who NHprep is for (app/nhprep/access.py): NHPREP_MODE, then a NetworkersHome student (app/access/students.py:
a verified ASCII address whose domain is exactly one of NHPREP_ALLOWED_EMAIL_DOMAINS), and for qa also listed in
NHPREP_QA_EMAILS. One row per rule."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.nhprep.access import nhprep_allows
from tests.conftest import make_settings

QA = "qa@networkershome.com, Lead@NetworkersHome.com, kumar@networkershome.com"


@pytest.mark.parametrize(
    ("email", "verified", "mode", "allowed"),
    [
        ("a@networkershome.com", True, "on", True),
        ("A@NetworkersHome.COM", True, "on", True),  # case-insensitive
        ("a+tag@networkershome.com", True, "on", True),  # the local part is not the domain's business
        ("a@mail.networkershome.com", True, "on", False),  # exact domain only
        ("a@networkershome.com.evil.tld", True, "on", False),
        ("a@evilnetworkershome.com", True, "on", False),
        ("a@networkershome.com.", True, "on", False),  # no trailing-dot folding
        ("a@networKershome.com", True, "on", False),  # KELVIN SIGN folds to "k": ASCII is checked first
        ("a@example.com", True, "on", False),
        ("networkershome.com", True, "on", False),
        ("a@networkershome.com", False, "on", False),  # unverified
        ("a@networkershome.com", True, "off", False),  # the kill switch
        ("qa@networkershome.com", True, "qa", True),
        ("lead@networkershome.com", True, "qa", True),  # the list is case-insensitive too
        ("a@networkershome.com", True, "qa", False),  # not listed
        ("qa@example.com", True, "qa", False),  # qa narrows, never widens
        ("kumar@networkershome.com", True, "qa", True),
        ("Kumar@networkershome.com", True, "qa", False),  # KELVIN SIGN would lowercase into the listed address
        ("Kumar@networkershome.com", True, "on", True),  # on: the domain decides; the local part is theirs
    ],
)
def test_nhprep_allows(email, verified, mode, allowed):
    settings = make_settings(nhprep_mode=mode, nhprep_qa_emails=QA + ", qa@example.com")
    user = SimpleNamespace(email=email, email_verified=verified)
    assert nhprep_allows(user, settings) is allowed


def test_no_domain_list_means_nobody():
    user = SimpleNamespace(email="a@networkershome.com", email_verified=True)
    assert not nhprep_allows(user, make_settings(nhprep_mode="on", nhprep_allowed_email_domains=""))
