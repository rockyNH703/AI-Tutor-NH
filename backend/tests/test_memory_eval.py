from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "memory_eval"
SCRIPT = ROOT / "scripts" / "memory_eval.py"


def _module():
    spec = importlib.util.spec_from_file_location("memory_eval", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_manifests_sha_counts_and_train_holdout_separation():
    memory_eval = _module()
    development = memory_eval.load_corpus(FIXTURES / "development.json", FIXTURES / "development.manifest.json")
    holdout = memory_eval.load_corpus(FIXTURES / "holdout.json", FIXTURES / "holdout.manifest.json")

    memory_eval.verify_separation(development, holdout)
    for corpus in (development, holdout):
        assert corpus.sha256 == hashlib.sha256(corpus.path.read_bytes()).hexdigest()
        assert corpus.manifest["counts"] == memory_eval._case_counts(corpus.data["cases"])
        assert corpus.manifest["release_note"].startswith("Synthetic initial corpus only")
    assert holdout.manifest["sealed"] is True
    assert holdout.manifest["provider_backed"] is False
    assert holdout.manifest["counts"]["eligible_extraction_facts"] < 100
    assert holdout.manifest["counts"]["retrieval_questions"] < 100


def test_reference_development_candidate_passes_quality_gates():
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--split",
            "development",
            "--candidate",
            str(FIXTURES / "candidates" / "reference-development.json"),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert completed.returncode == 0, completed.stderr
    assert result["passed"] is True
    assert result["quality_passed"] is True
    assert result["release_ready"] is False
    assert all(value == 0 for value in result["safety_counters"].values())


def test_small_holdout_is_objectively_blocked_from_release():
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--split",
            "holdout",
            "--candidate",
            str(FIXTURES / "candidates" / "reference-holdout.json"),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert result["quality_passed"] is True
    assert result["release_ready"] is False
    assert result["release_requirements"]["eligible_extraction_facts"]["passed"] is False
    assert result["release_requirements"]["retrieval_questions"]["passed"] is False
    assert result["release_requirements"]["provider_backed"]["passed"] is False
    assert result["release_requirements"]["baseline_supplied"]["passed"] is False


def test_deliberately_bad_candidate_fails_metrics_safety_and_budgets():
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--split",
            "development",
            "--candidate",
            str(FIXTURES / "candidates" / "deliberately-bad-development.json"),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    result = json.loads(completed.stdout)
    assert completed.returncode == 1
    assert result["passed"] is False
    assert result["metrics"]["extraction_recall"] == 0
    assert result["metrics"]["retrieval_recall_at_5"] == 0
    assert result["safety_counters"]["sensitive_false_acceptance"] > 0
    assert result["safety_counters"]["cross_user_leakage"] > 0
    assert result["gates"]["recall_latency_p95_ms"]["passed"] is False
    assert result["gates"]["max_provider_calls"]["passed"] is False
