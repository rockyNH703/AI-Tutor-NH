"""NHprep video playback (Revision 5): POST /v1/nhprep/lessons/{id}/playback asks the VideoSigner for a fresh link
and sends it only if it is exactly https://videos.nhprep.com/{videoId}.mp4?…; anything else is an honest
`unavailable` reason. The link is never cached, logged or stored. No real signer exists yet (the company chooses
how NetSec gets a signature), so production refuses NHPREP_PLAYBACK other than off; every test uses a fake signer."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.nhprep.playback import SignedVideo, build_signer, checked_src
from app.nhprep.schemas import NhprepPlaybackOut
from tests.conftest import make_settings
from tests.test_nhprep_api import ACL, CARDS, OSPF, STATIC, nhprep_app, seed, student
from tests.test_platform import PRODUCTION_LIKE

VIDEO = uuid.UUID("aaaaaaaa-0000-4000-8000-000000000001")  # the sample's nhprep-mp4 lesson (STATIC)
GOOD = f"https://videos.nhprep.com/{VIDEO}.mp4?exp=1763650000&sig=c3ludGhldGljLXNpZ25hdHVyZS1mb3ItdGVzdHM"


class FakeSigner:
    def __init__(self, src: str | None = GOOD, *, ttl_s: float = 6 * 3600, fail: Exception | None = None, naive=False):
        self.src, self.ttl_s, self.fail, self.naive = src, ttl_s, fail, naive
        self.asked: list[uuid.UUID] = []

    async def sign(self, video_id: uuid.UUID) -> SignedVideo:
        self.asked.append(video_id)
        if self.fail:
            raise self.fail
        expires = datetime.now(UTC) + timedelta(seconds=self.ttl_s)
        return SignedVideo(self.src, expires.replace(tzinfo=None) if self.naive else expires)


async def play(client, headers, lesson: uuid.UUID):
    return await client.post(f"/v1/nhprep/lessons/{lesson}/playback", headers=headers)


async def test_a_ready_link_is_sent_uncached_and_never_logged(caplog):
    signer = FakeSigner()
    async with nhprep_app(signer) as (app, client):
        logging.getLogger().addHandler(caplog.handler)  # create_app's configure_logging replaced the root handlers
        caplog.set_level(logging.DEBUG)
        lessons = await seed(app)
        headers = await student(client, app)
        response = await play(client, headers, lessons[STATIC])
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == set(CARDS["playback"]["ready"]) and body["status"] == "ready" and body["src"] == GOOD
        assert 6 * 3600 - 60 < body["expiresIn"] <= 6 * 3600
        assert response.headers["cache-control"] == "no-store"
        assert signer.asked == [VIDEO]
        detail = (await client.get(f"/v1/nhprep/lessons/{lessons[STATIC]}", headers=headers)).json()
        assert detail["video"] == "ready" and GOOD not in str(detail)
    logged = "\n".join(f"{record.getMessage()} {record.__dict__}" for record in caplog.records)
    assert "nhprep.playback" in logged and "sig=" not in logged and "videos.nhprep.com" not in logged


@pytest.mark.parametrize(
    "src",
    [
        f"http://videos.nhprep.com/{VIDEO}.mp4?sig=x",
        f"https://videos.nhprep.com.evil.tld/{VIDEO}.mp4?sig=x",
        f"https://videos.nhprep.com@evil.tld/{VIDEO}.mp4?sig=x",
        f"https://videos.nhprep.com:8443/{VIDEO}.mp4?sig=x",
        f"https://videos.nhprep.com/{uuid.uuid4()}.mp4?sig=x",  # another video
        f"https://videos.nhprep.com/{VIDEO}.mp4",  # unsigned
        f"https://videos.nhprep.com/{VIDEO}.mp4?sig=x#t=1",
        f"https://videos.nhprep.com/{VIDEO}.mp4?sig=x y",
        f"https://videos.nhprep.com/{VIDEO}.mp4?sig=x\n",
        f"https://videos.nhprep.com/{VIDEO}.mp4?sig=" + "x" * 2100,
        f"https://player.mediadelivery.net/embed/1/{VIDEO}?token=x",
        "javascript:alert(1)",
        None,
    ],
)
def test_only_a_link_to_this_video_on_nhpreps_video_host_passes(src):
    assert checked_src(GOOD, VIDEO)
    assert not checked_src(src, VIDEO)


@pytest.mark.parametrize(
    "signer",
    [
        FakeSigner("https://evil.tld/a.mp4?sig=x"),
        FakeSigner(fail=RuntimeError("signing endpoint down")),
        FakeSigner(ttl_s=-5),  # already expired
        FakeSigner(naive=True),  # an expiry without a time zone is a signer bug, not a crash
    ],
    ids=["wrong_link", "signer_error", "expired", "naive_expiry"],
)
async def test_a_bad_or_failed_signature_is_signing_failed_never_a_500(signer):
    async with nhprep_app(signer) as (app, client):
        lessons = await seed(app)
        response = await play(client, await student(client, app), lessons[STATIC])
        assert response.status_code == 200, response.text
        assert response.json() == {"status": "unavailable", "reason": "signing_failed"}


async def test_without_a_signer_or_a_video_the_reason_is_honest():
    async with nhprep_app() as (app, client):  # NHPREP_PLAYBACK=off: no signer
        lessons = await seed(app)
        headers = await student(client, app)
        assert (await play(client, headers, lessons[STATIC])).json() == CARDS["playback"]["unavailable"]
    signer = FakeSigner()
    async with nhprep_app(signer) as (app, client):
        lessons = await seed(app)
        headers = await student(client, app)
        assert (await play(client, headers, lessons[ACL])).json() == {"status": "unavailable", "reason": "no_video"}
        bunny = (await play(client, headers, lessons[OSPF])).json()
        assert bunny == {"status": "unavailable", "reason": "not_configured"}  # only NHprep's own MP4s play here
        assert (await play(client, headers, uuid.uuid4())).status_code == 404
    assert signer.asked == []


async def test_playbacks_have_their_own_lower_limit_per_user():
    async with nhprep_app(FakeSigner(), nhprep_playbacks_per_minute=2) as (app, client):
        lessons = await seed(app)
        headers = await student(client, app)
        codes = [(await play(client, headers, lessons[STATIC])).status_code for _ in range(3)]
        assert codes == [200, 200, 429]
        assert (await client.get(f"/v1/nhprep/lessons/{lessons[STATIC]}", headers=headers)).status_code == 200


@pytest.mark.parametrize("mode", ["secret", "endpoint"])
def test_no_signer_can_be_switched_on_before_it_exists(mode):
    settings = make_settings(**{**PRODUCTION_LIKE, "nhprep_playback": mode})
    with pytest.raises(RuntimeError, match="NHPREP_PLAYBACK must be off"):
        settings.validate_for_env()
    with pytest.raises(RuntimeError, match="has no signer yet"):
        build_signer(make_settings(nhprep_playback=mode))
    assert build_signer(make_settings()) is None
    make_settings(**PRODUCTION_LIKE).validate_for_env()  # off (the default) is valid


def test_qa_mode_needs_its_list_in_production():
    with pytest.raises(RuntimeError, match="NHPREP_MODE=qa needs NHPREP_QA_EMAILS"):
        make_settings(**{**PRODUCTION_LIKE, "nhprep_mode": "qa"}).validate_for_env()
    make_settings(
        **{**PRODUCTION_LIKE, "nhprep_mode": "qa", "nhprep_qa_emails": "a@networkershome.com"}
    ).validate_for_env()


def test_the_pinned_playback_shapes_are_what_the_model_sends():
    for sample in CARDS["playback"].values():
        assert (
            NhprepPlaybackOut.model_validate(sample).model_dump(mode="json", by_alias=True, exclude_none=True) == sample
        )
