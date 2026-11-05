"""Admission of book work on the shared host, and where its extractors run (pure; no database)."""

from __future__ import annotations

from app.knowledge import admission, processing
from app.knowledge.admission import decide


def test_a_book_starts_only_when_memory_cpu_and_live_traffic_leave_room():
    assert decide(cores=4, mem_available_mb=1000, load1=0.5, live_streams=0).reason == "memory"
    assert decide(cores=4, mem_available_mb=2600, load1=3.5, live_streams=0).reason == "cpu_busy"
    assert decide(cores=4, mem_available_mb=2600, load1=0.5, live_streams=8).reason == "live_traffic"
    ok = decide(cores=4, mem_available_mb=2600, load1=0.5, live_streams=1)
    assert ok.admitted and ok.ocr_workers == 2  # never all four cores on a shared 4-CPU host


def test_ocr_parallelism_follows_free_memory_and_cores():
    assert decide(cores=16, mem_available_mb=1200, load1=1.0, live_streams=0).ocr_workers == 1
    assert decide(cores=16, mem_available_mb=1500, load1=1.0, live_streams=0).ocr_workers == 2
    assert decide(cores=16, mem_available_mb=64_000, load1=1.0, live_streams=0).ocr_workers == 4  # the extractor cap
    assert decide(cores=2, mem_available_mb=64_000, load1=0.1, live_streams=0).ocr_workers == 1
    unknown = decide(cores=8, mem_available_mb=None, load1=None, live_streams=0)  # not Linux
    assert unknown.admitted and unknown.ocr_workers == 1


def test_mem_available_is_read_from_proc_meminfo(tmp_path):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:  4026532 kB\nMemAvailable:  2662400 kB\n", encoding="ascii")
    assert admission.mem_available_mb(meminfo) == 2600
    assert admission.mem_available_mb(tmp_path / "missing") is None


def test_extractors_run_in_the_knowledge_slice_with_the_admitted_ocr_processes():
    unit = "netsecai-knowledge-extract-x-1.service"
    argv, _ = processing._extractor_command("systemd", unit, 2, processing.KNOWLEDGE_SLICE)
    assert "--slice=netsecai-knowledge.slice" in argv
    assert "--setenv=KNOWLEDGE_OCR_WORKERS=2" in argv
    assert "CPUQuota=200%" in argv and f"MemoryMax={384 + 256 * 2}M" in argv
    local, local_env = processing._extractor_command("subprocess", "unit", 2, processing.KNOWLEDGE_SLICE)
    assert not any(arg.startswith("--slice") for arg in local) and local_env["KNOWLEDGE_OCR_WORKERS"] == "2"
