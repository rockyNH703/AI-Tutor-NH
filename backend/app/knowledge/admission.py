"""How much of the shared host the Knowledge Library may use right now.

The production VM (4 CPUs, 3.9 GB) also runs the API, the web app, PostgreSQL and PadhAI's production. A worker
tick claims a book only when that leaves them room: enough memory for the extractor and at least one OCR process
on top of a fixed reserve, the CPUs not already busy, and few replies streaming to users. The book's OCR then uses
as many processes as fit (at most extract.MAX_OCR_WORKERS, never all the cores). The kernel enforces the rest:
everything runs in netsecai-knowledge.slice (low CPU and IO weight, a hard memory ceiling).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select

from app.db.models import Message
from app.db.session import Database
from app.knowledge.extract import MAX_INPUT_BYTES, MAX_OCR_WORKERS

MEMORY_RESERVE_MB = 512  # always left to the live services
EXTRACTOR_BASE_MB, OCR_WORKER_MB = 384, 256  # what processing.sandbox_overrides grants the extractor
BOOK_CAP_MB = MAX_INPUT_BYTES // (1024 * 1024)  # the extractor holds the whole book while it reads and OCRs it
LOAD_SHARE = 0.75  # load1 above this share of the cores: the host is busy, books wait
MAX_LIVE_STREAMS = 8  # replies streaming right now: past this, live users come first


@dataclass(frozen=True)
class Admission:
    admitted: bool
    reason: str
    ocr_workers: int
    mem_available_mb: int | None
    load1: float | None
    cores: int
    live_streams: int


def decide(*, cores: int, mem_available_mb: int | None, load1: float | None, live_streams: int) -> Admission:
    """Pure: whether a book may start now, and with how many OCR processes. Unknown readings (not Linux) admit one."""

    def verdict(admitted: bool, reason: str, workers: int = 0) -> Admission:
        return Admission(admitted, reason, workers, mem_available_mb, load1, cores, live_streams)

    if mem_available_mb is not None and mem_available_mb < (
        MEMORY_RESERVE_MB + EXTRACTOR_BASE_MB + BOOK_CAP_MB + OCR_WORKER_MB
    ):
        return verdict(False, "memory")
    if load1 is not None and load1 > LOAD_SHARE * cores:
        return verdict(False, "cpu_busy")
    if live_streams >= MAX_LIVE_STREAMS:
        return verdict(False, "live_traffic")
    by_memory = (
        (mem_available_mb - MEMORY_RESERVE_MB - EXTRACTOR_BASE_MB - BOOK_CAP_MB) // OCR_WORKER_MB
        if mem_available_mb
        else 1
    )
    return verdict(True, "ok", max(1, min(MAX_OCR_WORKERS, cores - 2, by_memory)))


def mem_available_mb(meminfo: Path = Path("/proc/meminfo")) -> int | None:
    try:
        for line in meminfo.read_text(encoding="ascii").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


async def admit(db: Database) -> Admission:
    try:
        load1: float | None = os.getloadavg()[0]
    except (AttributeError, OSError):  # not on this platform
        load1 = None
    async with db.sessionmaker() as session:
        streams = int(
            await session.scalar(select(func.count()).select_from(Message).where(Message.status == "streaming")) or 0
        )
    return decide(cores=os.cpu_count() or 1, mem_available_mb=mem_available_mb(), load1=load1, live_streams=streams)
