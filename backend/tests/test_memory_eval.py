from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from langchain_core.embeddings import Embeddings

from app.llm.base import GenerationOptions, LLMStream, ProviderInfo, StreamPart, Usage

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "memory_eval"
V2_FIXTURES = FIXTURES / "v2"
SCRIPT = ROOT / "scripts" / "memory_eval.py"


class ScriptedProvider:
    name = "scripted"

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, messages, options: GenerationOptions) -> LLMStream:
        self.calls += 1
        prompt = messages[-1].content
        fact_id = source_id = None
        for line in prompt.splitlines():
            if line.startswith("fact_ids="):
                fact_ids = json.loads(line.removeprefix("fact_ids="))
                fact_id = fact_ids[0] if fact_ids else None
            elif line.startswith("source_id="):
                source_id = line.removeprefix("source_id=")
        proposals = []
        if fact_id and source_id and "password" not in prompt.casefold() and "class 10" not in prompt.casefold():
            operation = (
                "delete"
                if "forget" in prompt.casefold()
                else "correct"
                if "correction:" in prompt.casefold()
                else "create"
            )
            proposals = [{"fact_id": fact_id, "operation": operation, "source_id": source_id, "content": "omitted"}]
        payload = json.dumps({"proposals": proposals})

        async def parts() -> AsyncIterator[StreamPart]:
            yield StreamPart(text=payload)
            yield StreamPart(usage=Usage(input_tokens=80, output_tokens=12), finish_reason="stop")

        return LLMStream(
            info=ProviderInfo(self.name, options.model or "scripted-model"),
            parts=parts(),
            extra={"cost_usd": 0.0003},
        )


class CatalogEmbeddings(Embeddings):
    model = "catalog-test"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float("diagram" in text.casefold()), float("routing" in text.casefold()), 1.0] for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.embed_documents(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return self.embed_query(text)


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


def test_release_sized_v2_manifests_coverage_and_separation():
    memory_eval = _module()
    development = memory_eval.load_corpus(V2_FIXTURES / "development.json", V2_FIXTURES / "development.manifest.json")
    holdout = memory_eval.load_corpus(V2_FIXTURES / "holdout.json", V2_FIXTURES / "holdout.manifest.json")

    memory_eval.verify_separation(development, holdout)
    for corpus in (development, holdout):
        assert corpus.data["schema_version"] == 2
        assert corpus.sha256 == hashlib.sha256(corpus.path.read_bytes()).hexdigest()
        assert corpus.manifest["counts"] == memory_eval._case_counts(corpus.data["cases"])
        assert corpus.manifest["provider_backed"] is True
        assert corpus.manifest["coverage"] == {
            "synthetic": True,
            "privacy_safe": True,
            "distinct_split_templates": True,
            "source_ownership_lifecycle_prompt_injection": True,
        }
        for name, minimum in memory_eval.RELEASE_MINIMUMS.items():
            assert corpus.manifest["counts"][name] >= minimum
        coverage = {item for case in corpus.data["cases"] for item in case["coverage"]}
        assert {"source_attribution", "ownership", "lifecycle", "prompt_injection"} <= coverage
    assert holdout.manifest["sealed"] is True


def test_v2_structural_validation_rejects_missing_scenario(tmp_path):
    memory_eval = _module()
    corpus = json.loads((V2_FIXTURES / "development.json").read_text(encoding="utf-8"))
    del corpus["cases"][0]["scenario"]
    corpus_path = tmp_path / "development.json"
    corpus_path.write_text(json.dumps(corpus), encoding="utf-8")
    manifest = json.loads((V2_FIXTURES / "development.manifest.json").read_text(encoding="utf-8"))
    manifest["sha256"] = hashlib.sha256(corpus_path.read_bytes()).hexdigest()
    manifest_path = tmp_path / "development.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(memory_eval.EvaluationError, match="scenario"):
        memory_eval.load_corpus(corpus_path, manifest_path)


def test_v2_holdout_release_counts_and_provider_proof_pass():
    memory_eval = _module()
    holdout = memory_eval.load_corpus(V2_FIXTURES / "holdout.json", V2_FIXTURES / "holdout.manifest.json")
    candidate = {
        "schema_version": 1,
        "candidate_id": "structural-reference-v2",
        "corpus_id": holdout.data["corpus_id"],
        "predictions": {},
    }
    for case in holdout.data["cases"]:
        telemetry = {
            "latency_ms": 8 if case["task"] == "extraction" else 12,
            "db_query_ms": 4 if case["task"] == "retrieval" else None,
            "provider_calls": 0 if not case.get("expected") and not case.get("relevant_ids") else 1,
            "input_chars": 200,
            "input_tokens": 50,
            "output_tokens": 40 if case["task"] == "extraction" else 0,
            "proposal_count": len(case.get("expected", [])),
            "context_chars": 600 if case.get("relevant_ids") else 0,
            "cost_usd": 0.0004,
        }
        if case["task"] == "extraction":
            prediction = {"proposals": copy.deepcopy(case["expected"]), "telemetry": telemetry}
        else:
            prediction = {"retrieved_ids": copy.deepcopy(case["relevant_ids"]), "telemetry": telemetry}
        candidate["predictions"][case["id"]] = prediction

    baseline = {
        "metrics": {
            "retrieval_recall_at_5": 1.0,
            "retrieval_precision_at_5": 0.8,
            "retrieval_mrr": 1.0,
        }
    }
    result = memory_eval.score(holdout, candidate, baseline)

    assert result["quality_passed"] is True
    assert all(result["release_requirements"][name]["passed"] for name in memory_eval.RELEASE_MINIMUMS)
    assert result["release_requirements"]["provider_backed"]["passed"] is True
    assert result["release_ready"] is True


def test_telemetry_treats_nullable_numeric_fields_as_zero():
    memory_eval = _module()
    cases = [{"id": "nullable-telemetry", "task": "retrieval", "relevant_ids": []}]
    predictions = {
        "nullable-telemetry": {
            "retrieved_ids": [],
            "telemetry": {
                "latency_ms": 1,
                "db_query_ms": None,
                "provider_calls": 1,
                "input_chars": 10,
                "input_tokens": None,
                "output_tokens": None,
                "proposal_count": 0,
                "context_chars": None,
                "cost_usd": 0.0001,
            },
        }
    }

    telemetry = memory_eval._telemetry(predictions, cases)

    assert telemetry["max_input_tokens"] == 0
    assert telemetry["max_output_tokens"] == 0
    assert telemetry["max_context_chars"] == 0


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


@pytest.mark.asyncio
async def test_provider_generation_is_bounded_repeatable_and_text_free():
    memory_eval = _module()
    corpus = memory_eval.load_corpus(FIXTURES / "development.json", FIXTURES / "development.manifest.json")
    provider = ScriptedProvider()
    settings = memory_eval.Settings(
        llm_provider="fake",
        embedding_provider="fake",
        memory_worker_max_input_chars=4_000,
        memory_worker_timeout_s=2,
    )
    candidate = await memory_eval.generate_candidate(
        corpus,
        provider,
        CatalogEmbeddings(),
        settings,
        memory_eval.GenerationConfig(
            candidate_id="baseline-2026-10-28",
            run_role="baseline",
            repeats=2,
            concurrency=2,
            timeout_s=2,
            price_input_per_million=1,
            price_output_per_million=2,
            price_embedding_per_million=0.1,
        ),
    )

    assert provider.calls == 12
    assert len(candidate["predictions"]) == 12
    assert candidate["protocol"]["run_role"] == "baseline"
    assert candidate["protocol"]["repeats"] == 2
    assert all(prediction["telemetry"]["repeats"] == 2 for prediction in candidate["predictions"].values())
    assert candidate["predictions"]["dev-ext-create-preference"]["telemetry"]["cost_usd"] == 0.0003
    rendered = json.dumps(candidate)
    for case in corpus.data["cases"]:
        for message in case.get("input", {}).get("messages", []):
            assert message["content"] not in rendered
        query = case.get("input", {}).get("query")
        if query:
            assert query not in rendered


@pytest.mark.asyncio
async def test_generation_rejects_non_synthetic_corpus():
    memory_eval = _module()
    corpus = memory_eval.load_corpus(FIXTURES / "development.json", FIXTURES / "development.manifest.json")
    corpus.data["description"] = "Production conversations"
    with pytest.raises(memory_eval.EvaluationError, match="synthetic"):
        await memory_eval.generate_candidate(
            corpus,
            ScriptedProvider(),
            CatalogEmbeddings(),
            memory_eval.Settings(llm_provider="fake", embedding_provider="fake"),
            memory_eval.GenerationConfig(
                candidate_id="candidate",
                run_role="candidate",
                repeats=1,
                concurrency=1,
                timeout_s=1,
            ),
        )
