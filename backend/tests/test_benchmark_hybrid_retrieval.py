"""Pure safety, statistics, and report-contract tests for the retrieval benchmark."""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "benchmark_hybrid_retrieval.py"
SPEC = importlib.util.spec_from_file_location("benchmark_hybrid_retrieval", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql+asyncpg://bench@127.0.0.1:5432/retrieval_test",
        "postgresql+asyncpg://bench@localhost:5432/retrieval_test",
        "postgresql+asyncpg://bench@[::1]:5432/retrieval_test",
    ],
)
def test_url_guard_accepts_explicit_loopback_test_database(url):
    assert benchmark.validate_test_database_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "postgresql+asyncpg://bench@db.internal:5432/retrieval_test",
        "postgresql+asyncpg://bench@127.0.0.1:5432/retrieval",
        "postgresql://bench@127.0.0.1:5432/retrieval_test",
        "postgresql+asyncpg:///retrieval_test",
        "postgresql+asyncpg://bench@127.0.0.1:5432/production?ssl=require#fragment",
    ],
)
def test_url_guard_rejects_missing_remote_or_non_test_database(url):
    with pytest.raises(ValueError):
        benchmark.validate_test_database_url(url)


def test_percentile_uses_linear_interpolation():
    values = [1.0, 2.0, 3.0, 4.0]
    assert benchmark.percentile(values, 50) == pytest.approx(2.5)
    assert benchmark.percentile(values, 95) == pytest.approx(3.85)
    assert benchmark.percentile(values, 99) == pytest.approx(3.97)
    assert benchmark.percentile([7.5], 95) == 7.5


@pytest.mark.parametrize("values, percent", [([], 50), ([1.0], -1), ([1.0], 101), ([float("nan")], 50)])
def test_percentile_rejects_invalid_input(values, percent):
    with pytest.raises(ValueError):
        benchmark.percentile(values, percent)


def test_latency_summary_records_failures_and_percentiles():
    summary = benchmark.latency_summary([10.0, 20.0, 30.0, 40.0], Counter({"TimeoutError": 2}), 6)
    assert summary == {
        "samples": 4,
        "expected_samples": 6,
        "failures": 2,
        "failure_types": {"TimeoutError": 2},
        "p50_ms": 25.0,
        "p95_ms": 38.5,
        "p99_ms": 39.7,
    }


def test_report_schema_and_rendering_are_bounded_and_secret_free():
    metric = benchmark.latency_summary([10.0, 11.0], Counter(), 2)
    report = {
        "schema_version": 1,
        "benchmark": "hybrid_retrieval_local_db",
        "corpus": {"chunks": 100},
        "methodology": {"embedding_latency_included": False},
        "results": {
            "dense": {"1": metric, "4": metric, "8": metric},
            "hybrid": {"1": metric, "4": metric, "8": metric},
        },
        "plans": {"hnsw": {"nodes": []}, "gin": {"nodes": []}},
        "assertions": [{"name": "exact_identifier_retrieved", "passed": True}],
        "acceptance": {"passed": True},
        "cleanup": {"attempted": True, "completed": True},
    }
    assert benchmark.report_schema_valid(report)
    rendered = benchmark.render_report(report)
    assert len(rendered.encode("utf-8")) <= benchmark.MAX_REPORT_BYTES
    assert "database_url" not in rendered.casefold()
    assert "password" not in rendered.casefold()

    report["results"]["hybrid"].pop("8")
    assert not benchmark.report_schema_valid(report)


def test_argument_defaults_represent_about_sixty_thousand_chunks():
    args = benchmark.parse_args([])
    assert args.generic_chunks == 60_000
    with pytest.raises(ValueError):
        benchmark.parse_args(["--documents", "1", "--chunks-per-document", "1"])
