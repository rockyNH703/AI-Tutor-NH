"""Learning: the catalogs, the Engineering Profile, the learner profile, subjects, preferences and
the preview."""

import asyncio
import re

from app.learning import catalog
from app.qbank.netsec import FAMILIES
from app.ratelimit import SlidingWindowLimiter
from tests.conftest import register

# Each catalog version's content, by hash. Changing the catalog's data without bumping
# CATALOG_VERSION fails here: clients cache the catalog by version and would never see it.
CATALOG_FINGERPRINTS = {
    1: "b23e2375a9ca24fb2004c9a9432e648a72e112bf2adf2ecb69481b6c15e6c08c",
    2: "199839b8e057b786cd5d7f514f4b6990639851c7be13d5906c1e06691ba8196a",
}
# The same for the engineering catalog and ENGINEERING_VERSION.
ENGINEERING_FINGERPRINTS = {
    1: "4306366cdf44cfc29e881b5e31e5f1c42297020e3e0fb16232f698eef249d5c9",
    2: "c86589e5cd350d9b640f7e95b71e0c88606aa4375f45fffcdb6e4afc36812f9d",
}


# ----------------------------------------------------------------------- catalog
def test_the_catalog_version_follows_its_content():
    assert CATALOG_FINGERPRINTS.get(catalog.CATALOG_VERSION) == catalog.content_hash(), (
        "the catalog changed: bump CATALOG_VERSION and record its new hash here"
    )


def test_the_engineering_catalog_version_follows_its_content():
    assert ENGINEERING_FINGERPRINTS.get(catalog.ENGINEERING_VERSION) == catalog.engineering_hash(), (
        "the engineering catalog changed: bump ENGINEERING_VERSION and record its new hash here"
    )


def test_catalog_codes_are_well_formed_and_unique():
    for key, entries in [*catalog.LISTS.items(), *catalog.ENGINEERING_LISTS.items()]:
        codes = [entry.code for entry in entries]
        assert len(codes) == len(set(codes)), key
        for entry in entries:
            assert re.fullmatch(r"[a-z0-9_]{1,40}", entry.code), (key, entry.code)
            assert 0 < len(entry.name) <= 60, (key, entry.code)


def test_engineering_names_can_be_listed_with_semicolons():
    for key, entries in catalog.ENGINEERING_LISTS.items():
        for entry in entries:
            assert not re.search(r"[,;\n]", entry.name), (key, entry.code)
    fields = {**catalog.ENGINEERING_FIELDS, **catalog.ENGINEERING_LIST_FIELDS}
    assert {id(entries) for entries in fields.values()} == {id(e) for e in catalog.ENGINEERING_LISTS.values()}


async def test_the_catalog_is_public_and_cacheable_by_version(client):
    response = await client.get("/v1/catalog/academic")
    assert response.status_code == 200
    body = response.json()
    assert body["version"] == catalog.CATALOG_VERSION
    assert {"code": "class_10", "name": "Class 10", "deprecated": False} in body["grades"]
    assert set(body) == {
        "version",
        "grades",
        "boards",
        "boardStates",
        "mediums",
        "academicYears",
        "subjects",
        "examTracks",
    }
    # NetSec's exam tracks name every family of the certification bank (a lab session's "Exam track").
    assert [entry["code"] for entry in body["examTracks"]] == list(FAMILIES)
    assert {"code": "routing_switching", "name": "Routing and Switching", "deprecated": False} in body["examTracks"]
    assert response.headers["etag"] == catalog.ETAG
    assert response.headers["cache-control"] == "public, max-age=3600, must-revalidate"

    same = await client.get("/v1/catalog/academic", headers={"If-None-Match": catalog.ETAG})
    assert same.status_code == 304 and same.content == b""
    assert same.headers["etag"] == catalog.ETAG
    older = await client.get("/v1/catalog/academic", headers={"If-None-Match": '"academic-0-old"'})
    assert older.status_code == 200


async def test_the_engineering_catalog_needs_a_session_and_is_cacheable_by_version(client, app):
    assert (await client.get("/v1/catalog/engineering")).status_code == 401
    headers = (await register(client, app))["headers"]
    response = await client.get("/v1/catalog/engineering", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body == catalog.engineering_json()
    assert set(body) == {"version", "roles", "experienceLevels", "focusAreas", "platforms", "certifications"}
    assert {"code": "cisco_ios", "name": "Cisco IOS / IOS XE", "deprecated": False} in body["platforms"]
    assert response.headers["etag"] == catalog.ENGINEERING_ETAG != catalog.ETAG
    assert response.headers["cache-control"] == "private, max-age=3600, must-revalidate"

    same = await client.get("/v1/catalog/engineering", headers={**headers, "If-None-Match": catalog.ENGINEERING_ETAG})
    assert same.status_code == 304 and same.content == b""
    assert same.headers["etag"] == catalog.ENGINEERING_ETAG
    older = await client.get("/v1/catalog/engineering", headers={**headers, "If-None-Match": '"engineering-0-old"'})
    assert older.status_code == 200
    no_session = await client.get("/v1/catalog/engineering", headers={"If-None-Match": catalog.ENGINEERING_ETAG})
    assert no_session.status_code == 401


# ----------------------------------------------------------- engineering profile
FULL_ENGINEERING = {
    "role": "network_engineer",
    "experienceLevel": "senior",
    "focusAreas": ["routing_switching", "network_security"],
    "platforms": ["cisco_ios", "palo_alto", "aws"],
    "certificationGoal": "ccnp_enterprise",
}


async def test_the_engineering_profile_starts_empty(client, app):
    headers = (await register(client, app))["headers"]
    profile = (await client.get("/v1/engineering/profile", headers=headers)).json()
    assert profile == {
        "version": 0,
        "role": None,
        "experienceLevel": None,
        "focusAreas": [],
        "platforms": [],
        "certificationGoal": None,
        "updatedAt": None,
    }


async def test_an_engineering_profile_edit_sets_and_resets(client, app):
    headers = (await register(client, app))["headers"]
    first = await client.patch("/v1/engineering/profile", json={"version": 0, "set": FULL_ENGINEERING}, headers=headers)
    assert first.status_code == 200, first.text
    profile = first.json()
    assert profile["version"] == 1 and profile["updatedAt"] is not None
    assert {key: profile[key] for key in FULL_ENGINEERING} == FULL_ENGINEERING  # lists keep their order

    reset = await client.patch(
        "/v1/engineering/profile",
        json={"version": 1, "reset": ["focusAreas", "certificationGoal"], "set": {"platforms": ["juniper"]}},
        headers=headers,
    )
    assert reset.status_code == 200, reset.text
    after = reset.json()
    assert (after["focusAreas"], after["certificationGoal"], after["platforms"]) == ([], None, ["juniper"])
    assert (after["version"], after["role"], after["experienceLevel"]) == (2, "network_engineer", "senior")
    assert (await client.get("/v1/engineering/profile", headers=headers)).json() == after

    emptied = await client.patch(
        "/v1/engineering/profile", json={"version": 2, "set": {"platforms": []}}, headers=headers
    )
    assert emptied.status_code == 200 and emptied.json()["platforms"] == []


async def test_a_stale_engineering_edit_gets_the_current_profile(client, app):
    headers = (await register(client, app))["headers"]
    await client.patch("/v1/engineering/profile", json={"version": 0, "set": {"role": "soc_analyst"}}, headers=headers)
    stale = await client.patch(
        "/v1/engineering/profile", json={"version": 0, "set": {"role": "cloud_engineer"}}, headers=headers
    )
    assert stale.status_code == 409
    error = stale.json()["error"]
    assert error["code"] == "version_conflict"
    assert error["current"] == (await client.get("/v1/engineering/profile", headers=headers)).json()
    assert (error["current"]["role"], error["current"]["version"]) == ("soc_analyst", 1)


async def test_two_engineering_edits_from_one_version_cannot_both_win(client, app):
    headers = (await register(client, app))["headers"]
    responses = await asyncio.gather(
        *(
            client.patch("/v1/engineering/profile", json={"version": 0, "set": {"role": role}}, headers=headers)
            for role in ("security_engineer", "devops_engineer")
        )
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert (await client.get("/v1/engineering/profile", headers=headers)).json()["version"] == 1


async def test_an_empty_engineering_edit_changes_nothing(client, app):
    headers = (await register(client, app))["headers"]
    response = await client.patch("/v1/engineering/profile", json={"version": 0}, headers=headers)
    assert response.status_code == 200 and response.json()["version"] == 0


async def test_engineering_codes_must_come_from_the_catalog(client, app):
    headers = (await register(client, app))["headers"]
    response = await client.patch(
        "/v1/engineering/profile",
        json={
            "version": 0,
            "set": {
                "role": "wizard",
                "experienceLevel": "senior",
                "focusAreas": ["firewalls", "basket_weaving"],
                "platforms": ["juniper", "cisco_ios"],
                "certificationGoal": "ccna_voice",
            },
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["fields"] == [
        {"field": "set.role", "reason": "unknown_code"},
        {"field": "set.certificationGoal", "reason": "unknown_code"},
        {"field": "set.focusAreas.1", "reason": "unknown_code"},
    ]
    # A code of one list is not a code of another.
    crossed = await client.patch(
        "/v1/engineering/profile", json={"version": 0, "set": {"platforms": ["firewalls"]}}, headers=headers
    )
    assert crossed.json()["error"]["fields"] == [{"field": "set.platforms.0", "reason": "unknown_code"}]
    assert (await client.get("/v1/engineering/profile", headers=headers)).json()["version"] == 0


async def test_a_deprecated_engineering_code_may_be_kept_but_not_chosen_anew(client, app, monkeypatch):
    headers = (await register(client, app))["headers"]
    first = await client.patch(
        "/v1/engineering/profile",
        json={"version": 0, "set": {"role": "it_manager", "platforms": ["mikrotik", "juniper"]}},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    original = catalog.lookup

    def deprecating(entries, code):
        entry = original(entries, code)
        if entry and entry.code in ("it_manager", "mikrotik"):
            return catalog.Entry(entry.code, entry.name, deprecated=True)
        return entry

    monkeypatch.setattr(catalog, "lookup", deprecating)

    kept = await client.patch(
        "/v1/engineering/profile",
        json={"version": 1, "set": {"role": "it_manager", "platforms": ["juniper", "mikrotik", "arista"]}},
        headers=headers,
    )
    assert kept.status_code == 200, kept.text
    moved = await client.patch(
        "/v1/engineering/profile",
        json={"version": 2, "set": {"role": "network_architect", "platforms": ["arista"]}},
        headers=headers,
    )
    assert moved.status_code == 200
    back = await client.patch(
        "/v1/engineering/profile",
        json={"version": 3, "set": {"role": "it_manager", "platforms": ["arista", "mikrotik"]}},
        headers=headers,
    )
    assert back.status_code == 422
    assert back.json()["error"]["fields"] == [
        {"field": "set.role", "reason": "deprecated"},
        {"field": "set.platforms.1", "reason": "deprecated"},
    ]
    assert (await client.get("/v1/engineering/profile", headers=headers)).json()["version"] == 3


async def test_malformed_engineering_edits_are_refused(client, app):
    headers = (await register(client, app))["headers"]
    focus = [entry.code for entry in catalog.FOCUS_AREAS]
    platforms = [entry.code for entry in catalog.PLATFORMS]
    for patch in (
        {"focusAreas": focus[:9]},
        {"platforms": platforms[:13]},
        {"focusAreas": ["firewalls", "firewalls"]},
        {"platforms": ["aws", "azure", "aws"]},
        {"role": None},
        {"focusAreas": None},
        {"role": "Network Engineer"},
        {"role": "x" * 41},
        {"platforms": "aws"},
        {"platforms": [1]},
        {"employer": "Acme"},
    ):
        response = await client.patch("/v1/engineering/profile", json={"version": 0, "set": patch}, headers=headers)
        assert response.status_code == 422, patch
    for body in (
        {"set": {}},
        {"version": -1},
        {"version": 0, "reset": ["role", "role"]},
        {"version": 0, "reset": ["grade"]},
        {"version": 0, "set": {"role": "soc_analyst"}, "reset": ["role"]},
        {"version": 0, "userId": "someone-else"},
    ):
        assert (await client.patch("/v1/engineering/profile", json=body, headers=headers)).status_code == 422, body
    assert (await client.get("/v1/engineering/profile", headers=headers)).json()["version"] == 0
    # The largest lists allowed are accepted.
    full = await client.patch(
        "/v1/engineering/profile",
        json={"version": 0, "set": {"focusAreas": focus[:8], "platforms": platforms[:12]}},
        headers=headers,
    )
    assert full.status_code == 200, full.text
    assert (len(full.json()["focusAreas"]), len(full.json()["platforms"])) == (8, 12)


async def test_the_engineering_profile_is_separate_from_the_learner_profile(client, app):
    headers = (await register(client, app))["headers"]
    await client.patch("/v1/learning/profile", json={"version": 0, "set": {"grade": "class_11"}}, headers=headers)
    engineering = await client.patch(
        "/v1/engineering/profile", json={"version": 0, "set": {"role": "soc_analyst"}}, headers=headers
    )
    assert engineering.status_code == 200 and engineering.json()["version"] == 1
    assert (await client.get("/v1/learning/profile", headers=headers)).json()["grade"] == "class_11"


# ----------------------------------------------------------------------- profile
async def test_the_profile_starts_empty(client, app):
    headers = (await register(client, app))["headers"]
    profile = (await client.get("/v1/learning/profile", headers=headers)).json()
    assert profile["version"] == 0 and profile["weakTopics"] == [] and profile["updatedAt"] is None
    assert all(value is None for key, value in profile.items() if key not in ("version", "weakTopics"))


async def test_a_profile_edit_sets_normalises_and_resets(client, app):
    headers = (await register(client, app))["headers"]
    first = await client.patch(
        "/v1/learning/profile",
        json={
            "version": 0,
            "set": {
                "grade": "class_10",
                "board": "state",
                "boardState": "maharashtra",
                "medium": "marathi",
                "academicYear": "2026_27",
                "mainGoal": "board_exams",
                "targetExam": "  SSC\n\tboards‮ ",
                "targetScorePercent": 90,
                "weakTopics": ["Trigonometry", " Chemical   reactions "],
                "dailyActiveMinutesGoal": 45,
                "weeklyActiveMinutesGoal": 300,
                "preferredStudyTime": "07:30",
                "shortTermGoal": "Finish the syllabus",
                "longTermGoal": "Become an engineer",
            },
        },
        headers=headers,
    )
    assert first.status_code == 200, first.text
    profile = first.json()
    assert profile["version"] == 1
    assert profile["targetExam"] == "SSC boards"
    assert profile["weakTopics"] == ["Trigonometry", "Chemical reactions"]
    assert profile["preferredStudyTime"] == "07:30" and profile["boardState"] == "maharashtra"

    reset = await client.patch(
        "/v1/learning/profile",
        json={"version": 1, "reset": ["weakTopics", "boardState", "longTermGoal"], "set": {"board": "cbse"}},
        headers=headers,
    )
    assert reset.status_code == 200, reset.text
    after = reset.json()
    assert (after["weakTopics"], after["boardState"], after["longTermGoal"], after["board"]) == ([], None, None, "cbse")
    assert after["version"] == 2 and after["grade"] == "class_10"
    assert (await client.get("/v1/learning/profile", headers=headers)).json() == after


async def test_a_stale_profile_edit_gets_the_current_profile(client, app):
    headers = (await register(client, app))["headers"]
    await client.patch("/v1/learning/profile", json={"version": 0, "set": {"grade": "class_9"}}, headers=headers)
    stale = await client.patch(
        "/v1/learning/profile", json={"version": 0, "set": {"grade": "class_8"}}, headers=headers
    )
    assert stale.status_code == 409
    error = stale.json()["error"]
    assert error["code"] == "version_conflict"
    assert error["current"]["grade"] == "class_9" and error["current"]["version"] == 1


async def test_two_profile_edits_from_one_version_cannot_both_win(client, app):
    headers = (await register(client, app))["headers"]
    responses = await asyncio.gather(
        *(
            client.patch("/v1/learning/profile", json={"version": 0, "set": {"grade": grade}}, headers=headers)
            for grade in ("class_8", "class_9")
        )
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    assert (await client.get("/v1/learning/profile", headers=headers)).json()["version"] == 1


async def test_an_empty_profile_edit_changes_nothing(client, app):
    headers = (await register(client, app))["headers"]
    response = await client.patch("/v1/learning/profile", json={"version": 0}, headers=headers)
    assert response.status_code == 200 and response.json()["version"] == 0


async def test_profile_codes_must_come_from_the_catalog(client, app):
    headers = (await register(client, app))["headers"]
    response = await client.patch(
        "/v1/learning/profile", json={"version": 0, "set": {"grade": "class_99", "medium": "klingon"}}, headers=headers
    )
    assert response.status_code == 422
    assert response.json()["error"]["fields"] == [
        {"field": "set.grade", "reason": "unknown_code"},
        {"field": "set.medium", "reason": "unknown_code"},
    ]
    assert (await client.get("/v1/learning/profile", headers=headers)).json()["version"] == 0


async def test_a_board_state_needs_a_state_board(client, app):
    headers = (await register(client, app))["headers"]
    alone = await client.patch(
        "/v1/learning/profile", json={"version": 0, "set": {"boardState": "kerala"}}, headers=headers
    )
    assert alone.status_code == 422
    assert alone.json()["error"]["fields"] == [{"field": "set.boardState", "reason": "requires_state_board"}]
    both = await client.patch(
        "/v1/learning/profile", json={"version": 0, "set": {"board": "state", "boardState": "kerala"}}, headers=headers
    )
    assert both.status_code == 200
    away = await client.patch("/v1/learning/profile", json={"version": 1, "set": {"board": "cbse"}}, headers=headers)
    assert away.status_code == 422  # the state would be left dangling: reset it in the same edit


async def test_malformed_profile_edits_are_refused(client, app):
    headers = (await register(client, app))["headers"]
    for patch in (
        {"targetScorePercent": 101},
        {"dailyActiveMinutesGoal": 721},
        {"weeklyActiveMinutesGoal": -1},
        {"preferredStudyTime": "24:00"},
        {"preferredStudyTime": "7:30"},
        {"targetExam": "x" * 81},
        {"targetExam": "   "},
        {"shortTermGoal": "x" * 281},
        {"weakTopics": ["a"] * 2},
        {"weakTopics": [f"topic {n}" for n in range(11)]},
        {"weakTopics": ["x" * 61]},
        {"mainGoal": "fame"},
        {"grade": None},
        {"grade": "Class 10"},
        {"dateOfBirth": "2010-01-01"},
    ):
        response = await client.patch("/v1/learning/profile", json={"version": 0, "set": patch}, headers=headers)
        assert response.status_code == 422, patch
    for body in ({"set": {}}, {"version": 0, "reset": ["grade", "grade"]}, {"version": 0, "reset": ["email"]}):
        assert (await client.patch("/v1/learning/profile", json=body, headers=headers)).status_code == 422, body
    assert (await client.get("/v1/learning/profile", headers=headers)).json()["version"] == 0


async def test_a_deprecated_code_may_be_kept_but_not_chosen_anew(client, app, monkeypatch):
    headers = (await register(client, app))["headers"]
    await client.patch("/v1/learning/profile", json={"version": 0, "set": {"grade": "class_9"}}, headers=headers)
    await client.put("/v1/learning/subjects", json={"version": 0, "items": [{"code": "sanskrit"}]}, headers=headers)
    original = catalog.lookup

    def deprecating(entries, code):
        entry = original(entries, code)
        if entry and entry.code in ("class_9", "sanskrit"):
            return catalog.Entry(entry.code, entry.name, deprecated=True)
        return entry

    monkeypatch.setattr(catalog, "lookup", deprecating)

    kept = await client.patch(
        "/v1/learning/profile", json={"version": 1, "set": {"grade": "class_9", "medium": "hindi"}}, headers=headers
    )
    assert kept.status_code == 200, kept.text
    moved = await client.patch(
        "/v1/learning/profile", json={"version": 2, "set": {"grade": "class_10"}}, headers=headers
    )
    assert moved.status_code == 200
    back = await client.patch("/v1/learning/profile", json={"version": 3, "set": {"grade": "class_9"}}, headers=headers)
    assert back.status_code == 422
    assert back.json()["error"]["fields"] == [{"field": "set.grade", "reason": "deprecated"}]

    still = await client.put(
        "/v1/learning/subjects",
        json={"version": 1, "items": [{"code": "sanskrit"}, {"code": "hindi"}]},
        headers=headers,
    )
    assert still.status_code == 200, still.text
    await client.put("/v1/learning/subjects", json={"version": 2, "items": []}, headers=headers)
    again = await client.put(
        "/v1/learning/subjects", json={"version": 3, "items": [{"code": "sanskrit"}]}, headers=headers
    )
    assert again.status_code == 422
    assert again.json()["error"]["fields"] == [{"field": "items.0.code", "reason": "deprecated"}]


# ---------------------------------------------------------------------- subjects
async def test_subjects_are_replaced_whole_and_keep_their_order(client, app):
    headers = (await register(client, app))["headers"]
    assert (await client.get("/v1/learning/subjects", headers=headers)).json() == {"version": 0, "items": []}
    items = [
        {"code": "physics", "isPriority": True, "needsImprovement": False},
        {"code": "mathematics", "isPriority": False, "needsImprovement": True},
        {"code": "english", "isPriority": False, "needsImprovement": False},
    ]
    first = await client.put("/v1/learning/subjects", json={"version": 0, "items": items}, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json() == {"version": 1, "items": items}

    reordered = [items[2], items[0]]
    second = await client.put("/v1/learning/subjects", json={"version": 1, "items": reordered}, headers=headers)
    assert second.json() == {"version": 2, "items": reordered}
    assert (await client.get("/v1/learning/subjects", headers=headers)).json() == second.json()

    stale = await client.put("/v1/learning/subjects", json={"version": 1, "items": []}, headers=headers)
    assert stale.status_code == 409 and stale.json()["error"]["current"] == second.json()
    cleared = await client.put("/v1/learning/subjects", json={"version": 2, "items": []}, headers=headers)
    assert cleared.json() == {"version": 3, "items": []}


async def test_subjects_and_the_profile_are_versioned_separately(client, app):
    headers = (await register(client, app))["headers"]
    await client.put("/v1/learning/subjects", json={"version": 0, "items": [{"code": "biology"}]}, headers=headers)
    profile = await client.patch(
        "/v1/learning/profile", json={"version": 0, "set": {"grade": "class_11"}}, headers=headers
    )
    assert profile.status_code == 200 and profile.json()["version"] == 1
    subjects = await client.put("/v1/learning/subjects", json={"version": 1, "items": []}, headers=headers)
    assert subjects.status_code == 200


async def test_malformed_subject_lists_are_refused(client, app):
    headers = (await register(client, app))["headers"]
    unknown = await client.put(
        "/v1/learning/subjects",
        json={"version": 0, "items": [{"code": "physics"}, {"code": "alchemy"}]},
        headers=headers,
    )
    assert unknown.status_code == 422
    assert unknown.json()["error"]["fields"] == [{"field": "items.1.code", "reason": "unknown_code"}]
    codes = [entry.code for entry in catalog.SUBJECTS]
    for items in (
        [{"code": "physics"}, {"code": "physics"}],
        [{"code": code} for code in codes[:16]],
        [{"code": "physics", "userId": "x"}],
    ):
        response = await client.put("/v1/learning/subjects", json={"version": 0, "items": items}, headers=headers)
        assert response.status_code == 422, items
    assert (await client.get("/v1/learning/subjects", headers=headers)).json() == {"version": 0, "items": []}


# ------------------------------------------------------------------- preferences
async def test_preferences_start_on_auto_and_can_be_set_and_reset(client, app):
    headers = (await register(client, app))["headers"]
    default = (await client.get("/v1/learning/preferences", headers=headers)).json()
    assert default["version"] == 0 and default["updatedAt"] is None
    assert {value for key, value in default.items() if key not in ("version", "updatedAt")} == {"auto"}

    first = await client.patch(
        "/v1/learning/preferences",
        json={"version": 0, "set": {"language": "hindi", "answerMode": "socratic", "stepByStep": "on"}},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    assert (first.json()["language"], first.json()["answerMode"], first.json()["version"]) == ("hindi", "socratic", 1)

    second = await client.patch("/v1/learning/preferences", json={"version": 1, "reset": ["language"]}, headers=headers)
    assert second.json()["language"] == "auto" and second.json()["answerMode"] == "socratic"

    stale = await client.patch(
        "/v1/learning/preferences", json={"version": 1, "set": {"pace": "fast"}}, headers=headers
    )
    assert stale.status_code == 409 and stale.json()["error"]["current"]["version"] == 2
    for bad in ({"language": "french"}, {"pace": None}, {"tone": "fun"}):
        response = await client.patch("/v1/learning/preferences", json={"version": 2, "set": bad}, headers=headers)
        assert response.status_code == 422, bad


# ------------------------------------------------------------------ tutor preview
async def test_the_preview_shows_a_draft_without_saving_it(client, app):
    headers = (await register(client, app))["headers"]
    empty = await client.post("/v1/learning/tutor-preview", json={}, headers=headers)
    assert empty.status_code == 200 and empty.json()["instructions"] == ""
    assert empty.json()["maxChars"] == 3000

    await client.patch(
        "/v1/engineering/profile", json={"version": 0, "set": {"role": "network_engineer"}}, headers=headers
    )
    draft = await client.post(
        "/v1/learning/tutor-preview",
        json={"engineering": {"platforms": ["juniper"]}, "preferences": {"answerMode": "direct"}},
        headers=headers,
    )
    assert draft.status_code == 200, draft.text
    text = draft.json()["instructions"]
    assert "- Role: Network Engineer" in text  # stored
    assert "- Platforms: Juniper Junos" in text  # draft
    assert "Lead with the commands or configuration" in text
    assert (await client.get("/v1/engineering/profile", headers=headers)).json()["platforms"] == []
    assert (await client.get("/v1/learning/preferences", headers=headers)).json()["answerMode"] == "auto"

    cleared = await client.post("/v1/learning/tutor-preview", json={"engineering": {"role": None}}, headers=headers)
    assert cleared.json()["instructions"] == ""

    # The retired school settings are still accepted in a draft, and add nothing.
    retired = await client.post(
        "/v1/learning/tutor-preview",
        json={
            "engineering": {"role": None},
            "profile": {"grade": "class_7", "medium": "hindi"},
            "subjects": [{"code": "science"}],
            "preferences": {"pace": "slow", "personality": "friendly"},
        },
        headers=headers,
    )
    assert retired.status_code == 200 and retired.json()["instructions"] == ""


async def test_the_preview_refuses_unknown_codes(client, app):
    headers = (await register(client, app))["headers"]
    response = await client.post(
        "/v1/learning/tutor-preview",
        json={
            "profile": {"board": "hogwarts"},
            "subjects": [{"code": "potions"}],
            "engineering": {"role": "wizard", "platforms": ["juniper", "skynet"]},
        },
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["error"]["fields"] == [
        {"field": "profile.board", "reason": "unknown_code"},
        {"field": "subjects.0.code", "reason": "unknown_code"},
        {"field": "engineering.role", "reason": "unknown_code"},
        {"field": "engineering.platforms.1", "reason": "unknown_code"},
    ]


async def test_the_preview_is_rate_limited(client, app):
    headers = (await register(client, app))["headers"]
    app.state.tutor_preview_limiter = SlidingWindowLimiter(limit=2, window_s=60)
    for _ in range(2):
        assert (await client.post("/v1/learning/tutor-preview", json={}, headers=headers)).status_code == 200
    refused = await client.post("/v1/learning/tutor-preview", json={}, headers=headers)
    assert refused.status_code == 429 and refused.json()["error"]["code"] == "rate_limited"


def test_the_rate_limiter_forgets_old_hits():
    now = [0.0]
    limiter = SlidingWindowLimiter(limit=2, window_s=10, clock=lambda: now[0])
    assert limiter.allow("a") and limiter.allow("a") and not limiter.allow("a")
    assert limiter.allow("b")  # per key
    now[0] = 10.5
    assert limiter.allow("a")
