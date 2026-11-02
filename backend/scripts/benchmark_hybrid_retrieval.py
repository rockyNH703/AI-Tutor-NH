"""Deterministic local benchmark for PostgreSQL dense and hybrid retrieval.

The benchmark is intentionally separate from application configuration. It reads only an
explicit ``TEST_DATABASE_URL`` from the process environment, accepts only a loopback
PostgreSQL database whose name ends in ``_test``, uses deterministic vectors (no provider
call), creates uniquely named synthetic rows, and removes those rows on exit.

Typical WSL invocation after migrations are already applied::

    TEST_DATABASE_URL='postgresql+asyncpg://user@127.0.0.1:5432/retrieval_bench_test' \
      .venv/bin/python scripts/benchmark_hybrid_retrieval.py --output retrieval-benchmark.json
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import json
import math
import os
import sys
import time
import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_DOCUMENTS = 3_000
DEFAULT_CHUNKS_PER_DOCUMENT = 20
DEFAULT_WARMUPS = 3
DEFAULT_REPETITIONS = 20
CONCURRENCY_LEVELS = (1, 4, 8)
EMBEDDING_DIMENSIONS = 1_536
TOPIC_COUNT = 12
BENCHMARK_MODEL = "benchmark:deterministic-v1"
OTHER_MODEL = "benchmark:other-model-v1"
HYBRID_P95_LIMIT_MS = 300.0
MAX_REPORT_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class BenchmarkArgs:
    documents: int
    chunks_per_document: int
    warmups: int
    repetitions: int
    output: Path | None

    @property
    def generic_chunks(self) -> int:
        return self.documents * self.chunks_per_document


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    name: str
    lexical_text: str
    vector: list[float]


def positive_int(value: str) -> int:
    """Argparse type for positive, non-boolean decimal integers."""
    try:
        parsed = int(value, 10)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError("must be a positive integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--documents", type=positive_int, default=DEFAULT_DOCUMENTS)
    parser.add_argument("--chunks-per-document", type=positive_int, default=DEFAULT_CHUNKS_PER_DOCUMENT)
    parser.add_argument("--warmups", type=positive_int, default=DEFAULT_WARMUPS)
    parser.add_argument("--repetitions", type=positive_int, default=DEFAULT_REPETITIONS)
    parser.add_argument("--output", type=Path, help="write bounded JSON here instead of stdout")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> BenchmarkArgs:
    parsed = build_parser().parse_args(argv)
    total = parsed.documents * parsed.chunks_per_document
    if total < 100:
        raise ValueError("the synthetic corpus must contain at least 100 chunks")
    if total > 250_000:
        raise ValueError("the synthetic corpus is capped at 250000 chunks")
    return BenchmarkArgs(
        documents=parsed.documents,
        chunks_per_document=parsed.chunks_per_document,
        warmups=parsed.warmups,
        repetitions=parsed.repetitions,
        output=parsed.output,
    )


def validate_test_database_url(value: str | None) -> str:
    """Return an explicit safe benchmark URL or fail closed.

    No default is supplied and no settings or dotenv loader is consulted. ``localhost``,
    IPv4 loopback, and IPv6 loopback are accepted; every other host is rejected.
    """
    if value is None or not value.strip():
        raise ValueError("TEST_DATABASE_URL is required; no default is used")
    if any(character.isspace() for character in value):
        raise ValueError("TEST_DATABASE_URL must not contain whitespace")

    parsed = urlsplit(value)
    if parsed.scheme != "postgresql+asyncpg":
        raise ValueError("TEST_DATABASE_URL must use postgresql+asyncpg")
    if parsed.fragment:
        raise ValueError("TEST_DATABASE_URL must not contain a fragment")
    host = parsed.hostname
    if host is None:
        raise ValueError("TEST_DATABASE_URL must include a host")
    if host.casefold() != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError("TEST_DATABASE_URL host must be loopback")
        except ValueError as error:
            if str(error) == "TEST_DATABASE_URL host must be loopback":
                raise
            raise ValueError("TEST_DATABASE_URL host must be loopback") from error

    database_name = unquote(parsed.path.removeprefix("/"))
    if "/" in database_name or not database_name.casefold().endswith("_test"):
        raise ValueError("TEST_DATABASE_URL database name must end in _test")
    if not database_name:
        raise ValueError("TEST_DATABASE_URL database name must end in _test")
    return value


def percentile(values: Sequence[float], percent: float) -> float:
    """Linear-interpolated percentile (R-7), defined for one or more finite values."""
    if not values:
        raise ValueError("percentile requires at least one value")
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")
    ordered = sorted(float(value) for value in values)
    if not all(math.isfinite(value) for value in ordered):
        raise ValueError("percentile values must be finite")
    position = (len(ordered) - 1) * percent / 100
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def latency_summary(latencies_ms: Sequence[float], failures: Counter[str], expected_samples: int) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "samples": len(latencies_ms),
        "expected_samples": expected_samples,
        "failures": sum(failures.values()),
        "failure_types": dict(sorted(failures.items())),
    }
    for label, percent in (("p50_ms", 50), ("p95_ms", 95), ("p99_ms", 99)):
        summary[label] = round(percentile(latencies_ms, percent), 3) if latencies_ms else None
    return summary


def report_schema_valid(report: Mapping[str, Any]) -> bool:
    """Validate the stable, intentionally small top-level report contract."""
    required = {
        "schema_version",
        "benchmark",
        "corpus",
        "methodology",
        "results",
        "plans",
        "assertions",
        "acceptance",
        "cleanup",
    }
    if set(report) != required or report.get("schema_version") != 1:
        return False
    results = report.get("results")
    if not isinstance(results, Mapping) or set(results) != {"dense", "hybrid"}:
        return False
    for mode in ("dense", "hybrid"):
        if not isinstance(results[mode], Mapping) or set(results[mode]) != {"1", "4", "8"}:
            return False
    assertions = report.get("assertions")
    acceptance = report.get("acceptance")
    return isinstance(assertions, list) and isinstance(acceptance, Mapping) and "passed" in acceptance


def deterministic_vector(primary: int, secondary: int | None = None) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSIONS
    vector[primary] = 1.0
    if secondary is not None:
        vector[secondary] = 0.25
    magnitude = math.sqrt(sum(value * value for value in vector))
    return [value / magnitude for value in vector]


def _vector_sql() -> str:
    leading = ", ".join(f"CASE WHEN topic = {index} THEN 1.0::real ELSE 0.0::real END" for index in range(TOPIC_COUNT))
    return f"(ARRAY[{leading}] || array_fill(0.0::real, ARRAY[{EMBEDDING_DIMENSIONS - TOPIC_COUNT}]))::vector"


async def _check_schema(session: Any) -> None:
    from sqlalchemy import text

    row = (
        await session.execute(
            text(
                "SELECT to_regclass('public.ix_document_chunks_embedding_hnsw'), "
                "to_regclass('public.ix_document_chunks_content_tsv_gin'), "
                "EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'document_chunks' AND column_name = 'content_tsv')"
            )
        )
    ).one()
    if row[0] is None or row[1] is None or not row[2]:
        raise RuntimeError("migrations are not at the hybrid-retrieval schema")


async def _seed_corpus(session: Any, run_tag: str, args: BenchmarkArgs) -> dict[str, Any]:
    from sqlalchemy import text

    from app.db.models import Document, DocumentChunk, KnowledgeSource, User

    owner_a = User(email=f"benchmark-a-{run_tag}@example.invalid", email_verified=True)
    owner_b = User(email=f"benchmark-b-{run_tag}@example.invalid", email_verified=True)
    session.add_all([owner_a, owner_b])
    await session.flush()

    source_specs = (
        (f"benchmark-global-{run_tag}", None),
        (f"benchmark-owner-a-{run_tag}", owner_a.id),
        (f"benchmark-owner-b-{run_tag}", owner_b.id),
        (f"benchmark-other-model-{run_tag}", None),
    )
    sources: dict[str, KnowledgeSource] = {}
    for name, owner_id in source_specs:
        source = KnowledgeSource(
            name=name,
            owner_user_id=owner_id,
            description="synthetic local retrieval benchmark",
            metadata_={"benchmark_run": run_tag},
        )
        session.add(source)
        sources[name] = source
    await session.flush()
    global_source = sources[f"benchmark-global-{run_tag}"]

    await session.execute(
        text(
            "INSERT INTO documents (source_id, uri, title, content_hash, ingest_signature, metadata) "
            "SELECT CAST(:source_id AS uuid), CAST(:uri_prefix AS text) || doc_no::text, "
            "'Synthetic benchmark document ' || doc_no::text, "
            "md5(CAST(:run_tag AS text) || ':content:' || doc_no::text), "
            "md5(CAST(:run_tag AS text) || ':signature:' || doc_no::text), "
            "jsonb_build_object('benchmark_run', CAST(:run_tag AS text), 'document_index', doc_no) "
            "FROM generate_series(1, :documents) AS doc_no"
        ),
        {
            "source_id": global_source.id,
            "uri_prefix": f"benchmark://{run_tag}/generic/",
            "run_tag": run_tag,
            "documents": args.documents,
        },
    )

    vector_sql = _vector_sql()
    await session.execute(
        text(
            "WITH synthetic AS ("
            " SELECT d.id AS document_id, d.source_id, chunk_no - 1 AS ordinal,"
            " ((d.metadata->>'document_index')::int + chunk_no) % :topic_count AS topic"
            " FROM documents d CROSS JOIN generate_series(1, :chunks_per_document) AS chunk_no"
            " WHERE d.source_id = :source_id"
            ") "
            "INSERT INTO document_chunks "
            "(document_id, source_id, ordinal, content, metadata, embedding, embedding_model) "
            "SELECT document_id, source_id, ordinal, "
            "CASE (topic % 6) "
            " WHEN 0 THEN 'bgp neighbor route policy convergence telemetry common overlap token' "
            " WHEN 1 THEN 'ospf adjacency exstart mtu interface common overlap token' "
            " WHEN 2 THEN 'firewall vpn ike phase security policy common overlap token' "
            " WHEN 3 THEN 'linux systemd service journal socket common overlap token' "
            " WHEN 4 THEN 'cloud kubernetes ingress workload network common overlap token' "
            " ELSE 'switch vlan spanning tree ethernet common overlap token' END "
            "|| ' topic-' || topic::text || ' ordinal-' || ordinal::text, "
            "jsonb_build_object('benchmark_run', CAST(:run_tag AS text), 'topic', topic), "
            f"{vector_sql}, CAST(:embedding_model AS text) FROM synthetic"
        ),
        {
            "source_id": global_source.id,
            "chunks_per_document": args.chunks_per_document,
            "topic_count": TOPIC_COUNT,
            "run_tag": run_tag,
            "embedding_model": BENCHMARK_MODEL,
        },
    )

    async def sentinel(
        source: KnowledgeSource,
        uri_suffix: str,
        content: str,
        vector: list[float],
        model: str = BENCHMARK_MODEL,
    ) -> None:
        document = Document(
            source_id=source.id,
            uri=f"benchmark://{run_tag}/{uri_suffix}",
            title=f"Synthetic {uri_suffix}",
            content_hash=uuid.uuid5(uuid.NAMESPACE_URL, f"{run_tag}:{uri_suffix}:content").hex,
            ingest_signature=uuid.uuid5(uuid.NAMESPACE_URL, f"{run_tag}:{uri_suffix}:signature").hex,
            metadata_={"benchmark_run": run_tag, "sentinel": uri_suffix},
        )
        session.add(document)
        await session.flush()
        session.add(
            DocumentChunk(
                document_id=document.id,
                source_id=source.id,
                ordinal=0,
                content=content,
                metadata_={"benchmark_run": run_tag, "sentinel": uri_suffix},
                embedding=vector,
                embedding_model=model,
            )
        )

    await sentinel(
        global_source,
        "exact-identifier",
        "interface TenGigabitEthernet1/0/24 crc input errors diagnostic exact identifier",
        deterministic_vector(EMBEDDING_DIMENSIONS - 1),
    )
    await sentinel(
        sources[f"benchmark-owner-a-{run_tag}"],
        "owner-a-private",
        "private-a-edge-7d91 bgp incident owner isolation sentinel",
        deterministic_vector(13, 0),
    )
    await sentinel(
        sources[f"benchmark-owner-b-{run_tag}"],
        "owner-b-private",
        "private-b-edge-2f44 firewall incident owner isolation sentinel",
        deterministic_vector(14, 2),
    )
    await sentinel(
        sources[f"benchmark-other-model-{run_tag}"],
        "other-model",
        "wrong-model-only-6a21 model isolation sentinel",
        deterministic_vector(15, 3),
        OTHER_MODEL,
    )
    await session.flush()
    await session.execute(text("ANALYZE document_chunks"))
    return {
        "owner_a": owner_a.id,
        "owner_b": owner_b.id,
        "source_names": list(sources),
        "global_source": global_source.name,
        "owner_a_source": f"benchmark-owner-a-{run_tag}",
        "other_model_source": f"benchmark-other-model-{run_tag}",
        "sentinel_chunks": 4,
    }


async def _cleanup(sessionmaker: Any, run_tag: str) -> bool:
    from sqlalchemy import delete

    from app.db.models import KnowledgeSource, User

    async with sessionmaker() as session, session.begin():
        await session.execute(delete(KnowledgeSource).where(KnowledgeSource.name.like(f"benchmark-%-{run_tag}")))
        await session.execute(User.__table__.delete().where(User.email.like(f"benchmark-%-{run_tag}@example.invalid")))
    return True


async def _quality_assertions(store: Any, seeded: Mapping[str, Any], run_tag: str) -> list[dict[str, Any]]:
    from app.rag.lexical import build_lexical_query
    from app.rag.types import AccessScope

    async def hybrid(scope: AccessScope, text_value: str, vector: list[float], source_names: list[str] | None = None):
        return await store.hybrid_search(
            scope,
            vector,
            build_lexical_query(text_value),
            embedding_model=BENCHMARK_MODEL,
            k=5,
            dense_candidates=40,
            lexical_candidates=40,
            rrf_k=60,
            source_names=source_names,
        )

    exact = await hybrid(
        AccessScope(None),
        "TenGigabitEthernet1/0/24",
        deterministic_vector(EMBEDDING_DIMENSIONS - 1),
        [str(seeded["global_source"])],
    )
    exact_uri = next(
        (hit.uri for hit in exact.hits if hit.metadata.get("sentinel") == "exact-identifier"),
        None,
    )
    exact_ok = exact_uri is not None and exact_uri.endswith("/exact-identifier")

    owner_a = await hybrid(
        AccessScope(seeded["owner_a"]),
        "private-a-edge-7d91",
        deterministic_vector(13, 0),
        [str(seeded["owner_a_source"])],
    )
    owner_b = await hybrid(
        AccessScope(seeded["owner_b"]),
        "private-a-edge-7d91",
        deterministic_vector(13, 0),
        [str(seeded["owner_a_source"])],
    )
    global_scope = await hybrid(
        AccessScope(None),
        "private-a-edge-7d91",
        deterministic_vector(13, 0),
        [str(seeded["owner_a_source"])],
    )
    owner_ok = (
        bool(owner_a.hits)
        and owner_a.hits[0].uri.endswith("/owner-a-private")
        and not owner_b.hits
        and not global_scope.hits
    )

    wrong_model = await hybrid(
        AccessScope(None),
        "wrong-model-only-6a21",
        deterministic_vector(15, 3),
        [str(seeded["other_model_source"])],
    )
    model_ok = not wrong_model.hits
    return [
        {"name": "exact_identifier_retrieved", "passed": exact_ok},
        {"name": "cross_user_source_isolation", "passed": owner_ok},
        {"name": "embedding_model_isolation", "passed": model_ok},
    ]


def _cases() -> tuple[BenchmarkCase, ...]:
    return (
        BenchmarkCase("bgp", "bgp neighbor route policy", deterministic_vector(0)),
        BenchmarkCase("ospf", "ospf adjacency exstart mtu", deterministic_vector(1)),
        BenchmarkCase("vpn", "firewall vpn ike phase", deterministic_vector(2)),
        BenchmarkCase("linux", "linux systemd journal service", deterministic_vector(3)),
        BenchmarkCase("cloud", "kubernetes ingress workload", deterministic_vector(4)),
        BenchmarkCase(
            "identifier",
            "TenGigabitEthernet1/0/24 crc",
            deterministic_vector(EMBEDDING_DIMENSIONS - 1),
        ),
    )


async def _one_search(store: Any, mode: str, case: BenchmarkCase, global_source: str) -> None:
    from app.rag.lexical import build_lexical_query
    from app.rag.types import AccessScope

    if mode == "dense":
        await store.search(
            AccessScope(None),
            case.vector,
            embedding_model=BENCHMARK_MODEL,
            k=5,
            source_names=[global_source],
        )
        return
    await store.hybrid_search(
        AccessScope(None),
        case.vector,
        build_lexical_query(case.lexical_text),
        embedding_model=BENCHMARK_MODEL,
        k=5,
        dense_candidates=40,
        lexical_candidates=40,
        rrf_k=60,
        source_names=[global_source],
    )


async def _measure(store: Any, mode: str, concurrency: int, args: BenchmarkArgs, global_source: str) -> dict[str, Any]:
    cases = _cases()

    async def batch(batch_index: int, record: bool) -> tuple[list[float], Counter[str]]:
        async def operation(slot: int) -> tuple[float | None, str | None]:
            case = cases[(batch_index * concurrency + slot) % len(cases)]
            started = time.perf_counter()
            try:
                await _one_search(store, mode, case, global_source)
                return (time.perf_counter() - started) * 1000, None
            except Exception as error:  # noqa: BLE001 - benchmark records bounded exception classes
                return None, type(error).__name__

        outcomes = await asyncio.gather(*(operation(slot) for slot in range(concurrency)))
        if not record:
            return [], Counter()
        latencies = [latency for latency, error in outcomes if error is None and latency is not None]
        failures = Counter(error for _, error in outcomes if error is not None)
        return latencies, failures

    for warmup in range(args.warmups):
        await batch(warmup, False)
    latencies: list[float] = []
    failures: Counter[str] = Counter()
    for repetition in range(args.repetitions):
        measured, failed = await batch(args.warmups + repetition, True)
        latencies.extend(measured)
        failures.update(failed)
    return latency_summary(latencies, failures, concurrency * args.repetitions)


def _plan_evidence(plan: Mapping[str, Any]) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []

    def visit(node: Mapping[str, Any], depth: int = 0) -> None:
        if len(nodes) >= 50 or depth > 12:
            return
        selected: dict[str, Any] = {}
        for source_key, target_key in (
            ("Node Type", "node_type"),
            ("Relation Name", "relation"),
            ("Index Name", "index"),
            ("Actual Rows", "actual_rows"),
            ("Actual Total Time", "actual_total_ms"),
            ("Actual Loops", "loops"),
            ("Shared Hit Blocks", "shared_hit_blocks"),
            ("Shared Read Blocks", "shared_read_blocks"),
            ("Shared Dirtied Blocks", "shared_dirtied_blocks"),
            ("Shared Written Blocks", "shared_written_blocks"),
        ):
            if source_key in node:
                selected[target_key] = node[source_key]
        nodes.append(selected)
        for child in node.get("Plans", ()):  # type: ignore[union-attr]
            if isinstance(child, Mapping):
                visit(child, depth + 1)

    root = plan.get("Plan")
    if isinstance(root, Mapping):
        visit(root)
    return {
        "planning_ms": plan.get("Planning Time"),
        "execution_ms": plan.get("Execution Time"),
        "nodes": nodes,
    }


async def _explain_evidence(db: Any, store: Any, global_source: str) -> dict[str, Any]:
    from sqlalchemy import func, literal_column, select, text

    from app.db.models import DocumentChunk
    from app.rag.lexical import build_lexical_query
    from app.rag.types import AccessScope

    async with db.sessionmaker() as session, session.begin():
        allowed = await store.allowed_source_ids(session, AccessScope(None), [global_source])
        await store.apply_search_settings(session)
        # nosemgrep: netsec-sql-text-built-string -- fixed local setting allowlist
        for statement in (
            "SET LOCAL enable_seqscan = off",
            "SET LOCAL enable_sort = off",
            "SET LOCAL enable_bitmapscan = off",
        ):
            await session.execute(text(statement))
        dense_statement = store.search_statement(deterministic_vector(0), BENCHMARK_MODEL, allowed, 5)
        dense_sql = dense_statement.compile(
            dialect=session.bind.dialect,
            compile_kwargs={"literal_binds": True, "render_postcompile": True},
        )
        dense_raw = (
            await session.execute(
                # nosemgrep: netsec-sql-text-built-string -- SQLAlchemy literal-bound statement
                text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + str(dense_sql))
            )
        ).scalar_one()

    lexical_query = build_lexical_query("ospf adjacency exstart mtu")
    if lexical_query is None:
        raise AssertionError("benchmark lexical query unexpectedly empty")
    async with db.sessionmaker() as session, session.begin():
        await session.execute(text("SET LOCAL enable_seqscan = off"))
        lexical_statement = select(DocumentChunk.id).where(
            DocumentChunk.content_tsv.op("@@")(
                func.websearch_to_tsquery(
                    literal_column("'simple'::regconfig"),
                    lexical_query.text,
                )
            )
        )
        lexical_sql = lexical_statement.compile(
            dialect=session.bind.dialect,
            compile_kwargs={"literal_binds": True, "render_postcompile": True},
        )
        lexical_raw = (
            await session.execute(
                # nosemgrep: netsec-sql-text-built-string -- SQLAlchemy literal-bound statement
                text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + str(lexical_sql))
            )
        ).scalar_one()

    dense_plan = dense_raw[0] if isinstance(dense_raw, list) else dense_raw
    lexical_plan = lexical_raw[0] if isinstance(lexical_raw, list) else lexical_raw
    return {"hnsw": _plan_evidence(dense_plan), "gin": _plan_evidence(lexical_plan)}


def _plan_uses_index(evidence: Mapping[str, Any], index_name: str) -> bool:
    return any(node.get("index") == index_name for node in evidence.get("nodes", ()))


async def run_benchmark(database_url: str, args: BenchmarkArgs) -> tuple[dict[str, Any], int]:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.rag.store import KnowledgeStore

    engine = create_async_engine(
        database_url,
        pool_size=max(CONCURRENCY_LEVELS) + 2,
        max_overflow=0,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={"timeout": 10.0},
    )

    class BenchmarkDatabase:
        def __init__(self) -> None:
            self.engine = engine
            self.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    db = BenchmarkDatabase()
    store = KnowledgeStore(db, hnsw_ef_search=100)
    run_tag = uuid.uuid4().hex[:16]
    cleanup_ok = False
    report: dict[str, Any] | None = None
    try:
        async with db.sessionmaker() as session, session.begin():
            await _check_schema(session)
            seeded = await _seed_corpus(session, run_tag, args)

        assertions = await _quality_assertions(store, seeded, run_tag)
        results: dict[str, dict[str, Any]] = {"dense": {}, "hybrid": {}}
        for mode in ("dense", "hybrid"):
            for concurrency in CONCURRENCY_LEVELS:
                results[mode][str(concurrency)] = await _measure(
                    store,
                    mode,
                    concurrency,
                    args,
                    str(seeded["global_source"]),
                )
        plans = await _explain_evidence(db, store, str(seeded["global_source"]))
        assertions.extend(
            (
                {
                    "name": "hnsw_plan_uses_expected_index",
                    "passed": _plan_uses_index(plans["hnsw"], "ix_document_chunks_embedding_hnsw"),
                },
                {
                    "name": "gin_plan_uses_expected_index",
                    "passed": _plan_uses_index(plans["gin"], "ix_document_chunks_content_tsv_gin"),
                },
            )
        )
        hybrid_acceptance = {
            concurrency: result["failures"] == 0
            and result["p95_ms"] is not None
            and result["p95_ms"] < HYBRID_P95_LIMIT_MS
            for concurrency, result in results["hybrid"].items()
        }
        passed = all(item["passed"] for item in assertions) and all(hybrid_acceptance.values())
        report = {
            "schema_version": 1,
            "benchmark": "hybrid_retrieval_local_db",
            "corpus": {
                "documents": args.documents + 4,
                "generic_documents": args.documents,
                "chunks": args.generic_chunks + int(seeded["sentinel_chunks"]),
                "generic_chunks": args.generic_chunks,
                "sentinel_chunks": seeded["sentinel_chunks"],
                "topic_groups": TOPIC_COUNT,
                "owners": 2,
                "embedding_models": 2,
                "distribution": "round_robin_topics_with_dense_overlap_and_exact_identifier_sentinels",
            },
            "methodology": {
                "database_only": True,
                "embedding_latency_included": False,
                "vectors": "deterministic_local_1536d",
                "warmup_batches": args.warmups,
                "repetitions": args.repetitions,
                "concurrency": list(CONCURRENCY_LEVELS),
                "dense_candidates": 40,
                "lexical_candidates": 40,
                "top_k": 5,
            },
            "results": results,
            "plans": plans,
            "assertions": assertions,
            "acceptance": {
                "hybrid_p95_limit_ms_exclusive": HYBRID_P95_LIMIT_MS,
                "hybrid_by_concurrency": hybrid_acceptance,
                "passed": passed,
            },
            "cleanup": {"attempted": True, "completed": False},
        }
    finally:
        try:
            cleanup_ok = await _cleanup(db.sessionmaker, run_tag)
        finally:
            await engine.dispose()

    if report is None:
        raise RuntimeError("benchmark did not produce a report")
    report["cleanup"]["completed"] = cleanup_ok
    report["acceptance"]["passed"] = bool(report["acceptance"]["passed"] and cleanup_ok)
    if not report_schema_valid(report):
        raise RuntimeError("internal report schema validation failed")
    return report, 0 if report["acceptance"]["passed"] else 1


def render_report(report: Mapping[str, Any]) -> str:
    rendered = json.dumps(report, indent=2, sort_keys=True, separators=(",", ": ")) + "\n"
    if len(rendered.encode("utf-8")) > MAX_REPORT_BYTES:
        raise RuntimeError("benchmark report exceeded its bounded size")
    lowered = rendered.casefold()
    for forbidden in ('"database_url"', '"password"', '"content"', '"vector"', '"query"'):
        if forbidden in lowered:
            raise RuntimeError(f"benchmark report contains forbidden field {forbidden}")
    return rendered


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    try:
        args = parse_args(argv)
        database_url = validate_test_database_url((environ or os.environ).get("TEST_DATABASE_URL"))
        report, exit_code = asyncio.run(run_benchmark(database_url, args))
        rendered = render_report(report)
        if args.output is None:
            sys.stdout.write(rendered)
        else:
            args.output.write_text(rendered, encoding="utf-8")
        return exit_code
    except (ValueError, RuntimeError) as error:
        sys.stderr.write(f"benchmark refused: {error}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
