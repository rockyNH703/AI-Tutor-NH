"""NHprep videos in the chat (Revision 5): progressive MP4s on videos.nhprep.com that play only with NHprep's
short-lived signature. NetSec never stores a link: each Play asks a VideoSigner for a fresh one.

How NetSec gets a signature is the company's choice, not made yet: (A) NHprep's signing secret in api.env, or (B) a
signing endpoint on NHprep. Until then no signer exists, NHPREP_PLAYBACK stays off (validate_for_env) and every
video is `not_configured`. Tests pass a fake signer to create_app, as they pass a fake model.

A link is checked before it leaves (`https://videos.nhprep.com/{video_id}.mp4?…`), and is never logged or stored."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.logging import get_logger
from app.nhprep.schemas import NhprepPlaybackOut
from app.settings import Settings

log = get_logger("app.nhprep")

PROVIDER = "nhprep-mp4"  # the only provider that plays here (a dormant `bunny` row is not_configured)
VIDEO_ORIGIN = "https://videos.nhprep.com"
SIGN_TIMEOUT_S = 5.0  # a signer that hangs must not hold the request
MAX_SRC = 2048


@dataclass(frozen=True)
class SignedVideo:
    src: str
    expires_at: datetime


class VideoSigner(Protocol):
    async def sign(self, video_id: uuid.UUID) -> SignedVideo: ...


def build_signer(settings: Settings) -> VideoSigner | None:
    """The signer NHPREP_PLAYBACK names; None when off (every video is then not_configured)."""
    if settings.nhprep_playback == "off":
        return None
    # shortcut: neither signer is written yet (Revision 5, D9); write (A) or (B) here, ~20 lines plus a pinned vector,
    # when the company answers, and lift the refusal in Settings.validate_for_env.
    raise RuntimeError(f"NHPREP_PLAYBACK={settings.nhprep_playback} has no signer yet; set NHPREP_PLAYBACK=off")


def checked_src(src: object, video_id: uuid.UUID) -> bool:
    """Exactly NHprep's video host and this video's path, with a query (the signature): no userinfo, port, other
    path, fragment, whitespace or control character can pass the prefix and character checks."""
    return (
        isinstance(src, str)
        and len(src) <= MAX_SRC
        and src.startswith(f"{VIDEO_ORIGIN}/{video_id}.mp4?")
        and src.isascii()
        and src.isprintable()
        and not any(ch in src for ch in ' #\\"<>')
    )


def unavailable(reason: str) -> NhprepPlaybackOut:
    return NhprepPlaybackOut(status="unavailable", reason=reason)


async def play(
    signer: VideoSigner | None, lesson_id: uuid.UUID, provider: str | None, video_id: uuid.UUID | None
) -> NhprepPlaybackOut:
    """A fresh link for one lesson's video, or why it can't play here. Logs the reason code only."""
    started = time.monotonic()
    out = await _play(signer, provider, video_id)
    log.info(
        "nhprep.playback",
        extra={
            "lesson_id": str(lesson_id),
            "reason": out.reason or "ready",
            "expires_in": out.expires_in,
            "latency_ms": int((time.monotonic() - started) * 1000),
        },
    )
    return out


async def _play(signer: VideoSigner | None, provider: str | None, video_id: uuid.UUID | None) -> NhprepPlaybackOut:
    if provider is None or video_id is None:
        return unavailable("no_video")
    if signer is None or provider != PROVIDER:
        return unavailable("not_configured")
    try:
        async with asyncio.timeout(SIGN_TIMEOUT_S):
            signed = await signer.sign(video_id)
        expires_in = int((signed.expires_at - datetime.now(UTC)).total_seconds())
    except Exception as error:  # noqa: BLE001 - a signer failure is an honest "can't play", never a 500
        log.warning("nhprep.playback signer failed", extra={"error_type": type(error).__name__})
        return unavailable("signing_failed")
    if not checked_src(signed.src, video_id) or expires_in <= 0:
        return unavailable("signing_failed")
    return NhprepPlaybackOut(status="ready", src=signed.src, expires_in=expires_in)
