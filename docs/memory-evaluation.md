# Memory evaluation

`backend/scripts/memory_eval.py` is a deterministic, database-free release gate for the proposed memory system. It scores recorded candidate outputs; it does not import backend application code, contact providers, or mutate data.

## Corpora and integrity

The versioned fixtures are under `backend/tests/fixtures/memory_eval/`:

- `development.json`: open tuning set.
- `holdout.json`: sealed synthetic acceptance smoke set.
- `*.manifest.json`: corpus identity, split, case counts, sealed/provider-backed status, and exact SHA256 of the corpus bytes.
- `candidates/`: known-good examples and an intentionally bad candidate used by tests.

Every run loads both corpora, verifies their manifests and SHA256 values, rejects duplicate case IDs or identical normalized inputs across development and holdout, and requires the holdout to remain sealed. A changed corpus therefore requires an intentional manifest update and versioned review.

The committed corpora are a practical initial representative set covering create/correct/delete extraction, source attribution, preferences, temporal/conflict handling, sensitive and authoritative-field rejection, semantic/episodic retrieval, abstention, lifecycle filtering, prompt injection, and user isolation.

**This set is not release-sized. A provider-backed sealed holdout with at least 100 eligible extraction facts and 100 retrieval questions remains mandatory before memory extraction or retrieval can be enabled.** It must also include at least 25 no-memory cases, 20 temporal/conflict cases, and 20 security/isolation cases. The runner reports these release requirements and exits nonzero while they are unmet.

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

## Conversation summaries

The existing bounded conversation-history path remains authoritative. Versioned summaries were **not implemented** because this initial 12-case, non-provider-backed corpus cannot prove a material continuity improvement or deletion-safe regeneration. Summary work remains blocked until a separate long-history development/holdout set demonstrates a measurable gain within the same factual-error, latency, context, and zero-resurrection gates.

## Commands

From `backend/`:

```powershell
.venv\Scripts\python scripts\memory_eval.py --split development --candidate tests\fixtures\memory_eval\candidates\reference-development.json
```

The committed holdout is expected to return exit code `1` because it is deliberately below release scale and is not provider-backed:

```powershell
.venv\Scripts\python scripts\memory_eval.py --split holdout --candidate tests\fixtures\memory_eval\candidates\reference-holdout.json
```

For final acceptance, provide the frozen baseline result:

```powershell
.venv\Scripts\python scripts\memory_eval.py --split holdout --candidate candidate.json --baseline-result baseline-result.json --output candidate-result.json
```

Exit codes are objective: `0` means every applicable gate passed, `1` means valid evaluation data failed one or more gates, and `2` means the corpus, manifest, separation, candidate coverage, or JSON was invalid. Do not tune against holdout results. A holdout defect requires a new corpus version, a written reason, and preservation of the old result.
