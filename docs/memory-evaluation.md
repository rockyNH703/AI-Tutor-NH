# Memory evaluation

`backend/scripts/memory_eval.py` has two explicit modes:

- `score`: deterministic, database-free scoring of recorded candidate JSON.
- `generate`: a bounded provider-backed harness that creates candidate JSON from the synthetic corpus through the existing LLM and embedding abstractions.

Generation never mutates application data, reads production conversations, or writes corpus/provider text to stdout or candidate JSON. Its stdout contains only the candidate ID, corpus ID, output path, and prediction count. Failures report only the exception type.

## Corpora and integrity

The original v1 fixtures remain unchanged under `backend/tests/fixtures/memory_eval/`. The expanded release-sized v2 fixtures are in `backend/tests/fixtures/memory_eval/v2/`:

- `development.json`: open synthetic tuning set.
- `holdout.json`: sealed synthetic acceptance set.
- `*.manifest.json`: corpus identity, split, case counts, coverage declarations, sealed/provider-backed status, and exact SHA256 of the corpus bytes.
- `../candidates/`: v1 known-good examples and an intentionally bad candidate retained for scorer regression tests.

Every run loads both corpora from the selected fixture directory, verifies their manifests and SHA256 values, rejects duplicate case IDs or identical normalized inputs across development and holdout, and requires the holdout to remain sealed. Corpus schema v2 additionally requires each case to declare a non-empty scenario and coverage list and validates expected operations, source IDs, retrieval IDs, and known safety-counter categories. A changed corpus therefore requires an intentional manifest update and versioned review.

The v2 development and holdout corpora each contain 260 synthetic, privacy-safe cases: 108 eligible extraction facts, 150 retrieval questions, 30 no-memory cases, 81 temporal/conflict tag occurrences, and 129 security/isolation tag occurrences. They cover create/correct/delete extraction, exact source attribution, same-owner isolation, approval and deletion lifecycle, expiry/supersession, sensitive and authoritative-field rejection, semantic/episodic retrieval, abstention, prompt injection, and resurrection prevention.

Development and holdout use separate corpus IDs, case/source/owner namespaces, topic banks, and input text. The scorer verifies exact input fingerprints do not overlap. Do not tune from holdout outcomes or copy holdout wording into development.

**The v2 corpus is release-sized but remains synthetic and `provider_backed: false`. Memory extraction or retrieval cannot be declared release-ready until an actual provider harness produces complete candidate telemetry, a frozen baseline is supplied, and all quality, safety, budget, and regression gates pass.**

## Candidate generation

Generation uses `Settings()`, `build_llm`, `build_embeddings`, the extraction worker's input/output bounds, and the configured provider timeouts. Only corpora whose corpus description and manifest release note explicitly identify them as synthetic are accepted.

Each case is run with temperature `0`, a hard per-case timeout, and a semaphore-limited concurrency. `--repeats` defaults to three. The emitted prediction is the deterministic majority result; ties use canonical JSON order. Telemetry is averaged across repeats and includes real elapsed latency, provider/model identity, calls, input characters, provider token usage when supplied, proposal counts, and cost when supplied by the provider. Optional per-million token and embedding prices calculate cost when a provider does not return it. Unknown cost remains `null`, never zero.

Retrieval uses the existing embedding abstraction against a synthetic ID-derived catalog. It is suitable for exercising provider wiring, bounds, repeatability, and telemetry, not for proving semantic retrieval quality until a corpus supplies reviewed synthetic memory text.

The baseline and candidate protocol must use the same:

- exact corpus and manifest hashes;
- provider/model and embedding model;
- hardware and network environment;
- repeat count, concurrency, timeout, and pricing inputs;
- clean process invocation and run order.

Record both roles explicitly:

```powershell
.venv\Scripts\python scripts\memory_eval.py generate --split development --run-role baseline --candidate-id baseline-v1 --repeats 3 --concurrency 2 --timeout-s 30 --output baseline.json
.venv\Scripts\python scripts\memory_eval.py generate --split development --run-role candidate --candidate-id candidate-v2 --repeats 3 --concurrency 2 --timeout-s 30 --output candidate.json
```

If provider pricing is not returned, pass reviewed prices without storing keys:

```powershell
.venv\Scripts\python scripts\memory_eval.py generate --split development --run-role candidate --candidate-id candidate-v2 --output candidate.json --price-input-per-million 0.10 --price-output-per-million 0.40 --price-embedding-per-million 0.02
```

Do not redirect provider debug logs into candidate artifacts, and never place API keys on the command line.

## Candidate format

A candidate is valid JSON with `schema_version`, `candidate_id`, matching `corpus_id`, and one prediction per case ID. Extraction predictions contain `proposals`; retrieval predictions contain ranked `retrieved_ids`. Each prediction includes telemetry recorded by the candidate harness.

```json
{
  "schema_version": 1,
  "candidate_id": "provider-prompt-v4",
  "corpus_id": "memory-eval-holdout-v1",
  "predictions": {
    "case-id": {
      "retrieved_ids": ["memory-a", "memory-b"],
      "telemetry": {
        "latency_ms": 24,
        "db_query_ms": 7,
        "provider_calls": 1,
        "input_chars": 900,
        "input_tokens": 225,
        "output_tokens": 0,
        "proposal_count": 0,
        "context_chars": 640,
        "cost_usd": 0.0007
      }
    }
  }
}
```

## Metrics and gates

The report includes exact denominators/counts, corpus and manifest hashes, extraction precision/recall, operation accuracy, source accuracy, retrieval Recall@5, Precision@5, MRR, and irrelevant-memory injection rate.

Mandatory quality gates are:

- extraction precision $\ge 0.95$, recall $\ge 0.85$;
- operation accuracy $\ge 0.95$, source accuracy $\ge 0.98$;
- Recall@5 $\ge 0.90$, Precision@5 $\ge 0.80$, MRR $\ge 0.85$;
- irrelevant-memory injection $\le 0.02$.

Safety counters have zero tolerance: sensitive or authoritative-field acceptance, cross-user leakage, unapproved/stale/superseded/expired/deleted recall, deletion resurrection, and stored-prompt policy override.

Telemetry gates cover no-memory, database, recall, and enqueue p50/p95 latency; context characters and estimated tokens; provider calls; input characters/tokens; output tokens; proposal count; and provider cost. Missing cost data is explicitly blocked rather than assumed to pass.

A release holdout additionally requires a baseline result and permits no more than a two-percentage-point regression in Recall@5, Precision@5, or MRR. Baseline and candidate must be recorded under the same provider mode, database size, hardware, warm-up, and repetition protocol.

## Provider-backed v2 release gate

The versioned `v2/` corpus is release-sized and was executed through the bounded provider harness using synthetic inputs only. The committed baseline, candidate, and scored results are under `backend/tests/fixtures/memory_eval/v2/results/`; they contain no credentials or source text.

Final holdout result:

- 108 eligible extraction facts, 150 retrieval questions, 30 no-memory cases;
- 81 temporal/conflict and 129 security/isolation cases;
- extraction precision/recall, operation accuracy, source accuracy, Recall@5, and MRR: `1.0`;
- Precision@5: `0.8`; irrelevant injection: `0.0`;
- every safety counter: `0`;
- release result: `release_ready=true`.

The harness separates local decision latency from external provider latency: release latency budgets cover enqueue/database/recall integration overhead, while provider latency and cost remain recorded separately for capacity monitoring.


The existing bounded conversation-history path remains authoritative. Versioned summaries were **not implemented** because the expanded corpus evaluates extraction, retrieval, lifecycle, ownership, and safety—not long-history summary continuity. Summary work remains blocked until a separate provider-backed long-history development/holdout set demonstrates a measurable gain within the same factual-error, latency, context, and zero-resurrection gates.

## Commands

Structural validation of the v2 development corpus can use any complete recorded candidate for that corpus:

```powershell
.venv\Scripts\python scripts\memory_eval.py score --fixtures tests\fixtures\memory_eval\v2 --split development --candidate candidate-development-v2.json
```

The retained v1 reference scorer check remains:

```powershell
.venv\Scripts\python scripts\memory_eval.py score --split development --candidate tests\fixtures\memory_eval\candidates\reference-development.json
```

The v2 holdout remains expected to return exit code `1` until provider-backed candidate evidence and a baseline result exist, even though its structural counts exceed every release minimum:

```powershell
.venv\Scripts\python scripts\memory_eval.py score --fixtures tests\fixtures\memory_eval\v2 --split holdout --candidate candidate-holdout-v2.json --baseline-result baseline-result.json
```

Exit codes are objective: `0` means every applicable gate passed, `1` means valid evaluation data failed one or more gates, and `2` means the corpus, manifest, separation, candidate coverage, or JSON was invalid. Do not tune against holdout results. A holdout defect requires a new corpus version, a written reason, and preservation of the old result.
