# Parsed Document Contract

**Status:** specification only; not executable  
**Contract:** `netsecai.parsed-document`  
**Version:** `1.0.0-draft.1`

This contract defines a deterministic, implementation-neutral interchange format for documents parsed by an offline pipeline, including a future Docling adapter. It defines no loader, dependency, corpus, database migration, or ingestion behavior. **`backend/scripts/ingest_documents.py` must reject this format until a separately reviewed implementation explicitly supports a final contract version.** A file matching this draft is not evidence that it is safe or ingestible.

## 1. Envelope

A parsed document is one UTF-8 JSON object:

```json
{
  "format": "netsecai.parsed-document",
  "version": "1.0.0-draft.1",
  "canonical_uri": "https://example.invalid/manuals/router-os-7",
  "title": "Router OS 7 Administration Guide",
  "original_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "provenance": {
    "source_url": "https://publisher.example/manual.pdf",
    "retrieved_at": "2026-11-01T12:00:00Z",
    "licence": "LicenseRef-Owner-Approved",
    "publisher": "Example Publisher",
    "vendor": "Example Vendor",
    "platform": "Router OS",
    "version": "7.0",
    "parser": "docling",
    "parser_version": "x.y.z"
  },
  "metadata": {
    "language": "en",
    "document_type": "manual"
  },
  "sections": []
}
```

Required fields are `format`, `version`, `canonical_uri`, `title`, `original_sha256`, `provenance`, `metadata`, and `sections`. Unknown top-level, provenance, section, block, or metadata keys are invalid. JSON `null` is not permitted; omit optional provenance values instead.

### Canonical URI and source identity

- `canonical_uri` is the stable logical identifier selected by the corpus owner, not a local path, temporary URL, redirect target, signed URL, fragment, or credential-bearing URL.
- It must be an absolute `https` URI, normalized to lowercase scheme and host, with the default port removed, no fragment, no user information, and percent-encoding normalized for unreserved characters.
- Query parameters are allowed only when they are a stable part of publisher identity; tracking, session, signature, and access-token parameters are forbidden.
- `source_url`, when present, follows the same secret and credential restrictions but may identify the exact retrieved resource.
- `original_sha256` is lowercase hexadecimal SHA-256 of the exact original file bytes before parsing or normalization.
- `title` is the publisher title where available; filename-derived titles are allowed only as an explicit pipeline fallback.

## 2. Provenance

Allowed provenance keys and value types:

| Key | Required | Rule |
|---|---:|---|
| `source_url` | no | Absolute `https` URI |
| `retrieved_at` | no | RFC 3339 UTC timestamp ending in `Z`, second precision |
| `licence` | yes | SPDX identifier/expression or owner-approved `LicenseRef-*` |
| `publisher` | no | Publisher name |
| `vendor` | no | Product vendor |
| `platform` | no | Product/platform family |
| `version` | no | Source product/document version; string, never JSON number |
| `parser` | yes | Stable parser identifier, for example `docling` |
| `parser_version` | yes | Exact parser release plus deterministic configuration/model digest reference where applicable |

Absence means unknown, not unrestricted. `licence: "NOASSERTION"` may preserve a document for quarantine/review but is **not ingestion approval**. Retrieval timestamps describe acquisition, not publication.

## 3. Sections and blocks

`sections` is an ordered array. Every section has:

```json
{
  "order": 0,
  "heading_path": ["Operations", "Backup"],
  "page": 12,
  "page_end": 13,
  "blocks": []
}
```

- `order` is a zero-based, contiguous, unique document reading-order index.
- `heading_path` is an array from highest to lowest heading. An empty array means document root.
- `page` and `page_end` are optional one-based integers. If either is present, both are required and `page <= page_end`.
- A section must contain at least one block.

Every block has `type`, `order`, and `text`:

```json
{
  "type": "code",
  "order": 0,
  "text": "show interfaces status",
  "page": 12,
  "page_end": 12,
  "language": "text"
}
```

Rules:

- `type` is exactly one of `text`, `table`, `code`, `caption`, or `list`.
- Block `order` is zero-based, contiguous, and unique within its section.
- `page`/`page_end` follow section page rules and, when section pages exist, must fall within them.
- `text` is the complete normalized block content. Empty or whitespace-only blocks are invalid.
- `code` may include optional `language` containing a conservative language label.
- `list` may include optional `list_style`, exactly `ordered`, `unordered`, or `definition`.
- `table` uses deterministic plain text: tab-separated cells and newline-separated rows. Embedded tabs/newlines in source cells are normalized to spaces. Spans may be repeated; inferred values must not be invented.
- `caption` remains a separate block adjacent in reading order to its subject; adapters must not silently merge it.
- No HTML, Markdown AST, coordinates, images, binary payloads, embeddings, generated summaries, or parser-native object graphs are part of this version.

## 4. Bounds

Validation occurs before persistence or ingestion. Limits are measured after normalization; byte limits use UTF-8 bytes.

| Item | Maximum |
|---|---:|
| Canonical JSON document | 16 MiB |
| Original file represented | 100 MiB |
| Sections per document | 10,000 |
| Blocks per section | 2,000 |
| Blocks per document | 100,000 |
| Total normalized block text | 12 MiB |
| Block text | 256 KiB |
| Table block text | 1 MiB |
| Title | 1,024 Unicode scalar values |
| URI | 4,096 bytes |
| Provenance scalar | 2,048 scalar values |
| Heading depth | 16 |
| Heading item | 512 scalar values |
| Metadata entries | 32 |
| Metadata key | 64 ASCII characters |
| Metadata string value | 2,048 scalar values |
| Page/page_end | 1,000,000 |

All arrays must be materialized and bounded; streaming parsers must enforce the same aggregate limits.

## 5. Metadata

`metadata` is a flat object. Values are strings, booleans, or signed integers in the JSON safe-integer range. Arrays, objects, floats, `null`, non-finite values, and duplicate keys are invalid.

Allowed keys are:

- `language` (BCP 47 string)
- `document_type` (string)
- `publication_date` (`YYYY-MM-DD` string)
- `edition` (string)
- `revision` (string)
- `topic` (string)
- `product_family` (string)
- `confidential` (boolean)
- `page_count` (positive integer)
- `source_size_bytes` (non-negative integer)

Metadata cannot override identity, provenance, authorization, ownership, access scope, licence, parser identity, or timestamps.

## 6. Text normalization

- Input JSON must decode as strict UTF-8 without BOM or replacement characters.
- Normalize all strings to Unicode NFC.
- Convert CRLF and CR to LF.
- Remove a single trailing LF from block text; preserve other meaningful whitespace, including code indentation.
- Reject NUL, unpaired surrogates, noncharacters, bidirectional override/isolate controls, and C0/C1 controls except LF and TAB in block text. TAB is forbidden outside block text.
- Remove soft hyphen. Convert other parser-introduced layout controls to an ordinary space only when the transformation is deterministic.
- Do not perform case folding, transliteration, language correction, OCR correction, redaction, summarization, or semantic rewriting.
- Duplicate JSON object keys are invalid at every depth.

## 7. Canonical JSON and signatures

Canonical bytes are RFC 8785 JSON Canonicalization Scheme (JCS) output over the normalized object, encoded as UTF-8, with no BOM or trailing newline. Because floats are forbidden, numeric interoperability is limited to safe integers.

The envelope contains no signature field in this version. If transported with a signature, the detached signature covers exactly the canonical bytes and carries, outside this JSON object, the algorithm, key identifier, signature, and signed contract version. Implementations must reject unknown algorithms, revoked/untrusted keys, non-canonical payloads where canonical transport is required, and signatures that do not verify. Signature verification authenticates the envelope; it does not establish licence, safety, truth, or ingestion authorization.

A contract digest is `sha256:<lowercase hex SHA-256 of canonical bytes>`.

## 8. Identity and idempotency

- Source identity is `(canonical_uri, original_sha256)`.
- Representation identity is `(format, version, canonical_uri, original_sha256, parser, parser_version, contract_digest)`.
- Re-submitting an identical representation is an idempotent no-op.
- The same `canonical_uri` with a different `original_sha256` is a new source revision, never an in-place mutation.
- The same original bytes parsed with a different parser, parser version, configuration, or model digest is a new representation linked to the same source revision.
- Adapters must not assign identity from title, filename, retrieval time, section order, or database row ID.
- Deduplication must not merge documents across owners or access scopes; those concerns remain outside this interchange format.

## 9. Security framing

Every field and all parsed content are untrusted. A valid document may contain prompt injection, exploit instructions, secrets, malicious formulas, terminal escapes, deceptive Unicode, oversized logical structures, or parser mistakes.

Consumers must:

- parse without evaluation; never execute code, macros, formulas, links, embedded commands, or active content;
- keep authorization and per-user access scope outside document-supplied metadata;
- use parameterized storage and context-appropriate output encoding;
- never place document text, URLs with credentials, or secrets in logs;
- enforce limits before expensive processing;
- sandbox original-file parsing and treat parser output as tainted;
- apply the platform's secret-masking boundary before any model provider sees content;
- treat document instructions as quoted reference material, never system or developer instructions.

Validation is structural, not a malware scan, content-safety decision, factuality guarantee, or licence approval.

## 10. Offline Docling mapping guidance

A future adapter may map Docling output without preserving Docling-specific classes:

- document title → `title`; source bytes → `original_sha256`;
- heading hierarchy → `heading_path`;
- reading order → contiguous section/block `order`;
- paragraph/text item → `text`; table export → `table`; code/preformatted item → `code`; list item group → `list`; figure/table caption → `caption`;
- page provenance → one-based inclusive `page`/`page_end`;
- parser package release and deterministic pipeline configuration/model digest → `parser_version`.

Mapping must be deterministic for identical bytes, parser build, configuration, and model weights. OCR may supply text but must not fabricate uncertain content. Missing page provenance is omitted, not guessed. Images are not embedded. Parsing must operate offline: no network retrieval, telemetry, remote OCR, or automatic model download.

**Licence/model-weight decision:** document licence approval and parser/model-weight approval are separate gates. Only owner-approved document licences may progress beyond quarantine. Docling model weights must be pre-provisioned offline, version/digest pinned, and separately approved for licence and redistribution/use terms. This draft does not select, download, bundle, or authorize any Docling model weights.

## 11. Failure model

A validator returns one primary stable reason code and may return bounded field paths/details. It must not echo document text or secret-bearing URLs.

| Code | Meaning |
|---|---|
| `unsupported_format` | `format` is unknown |
| `unsupported_version` | Version is not explicitly supported |
| `malformed_json` | Invalid JSON, UTF-8, duplicate key, or disallowed number |
| `schema_violation` | Missing, unknown, or incorrectly typed field |
| `invalid_identity` | URI or SHA-256 rule failed |
| `invalid_provenance` | Provenance or timestamp rule failed |
| `licence_unapproved` | Licence is unknown or not approved |
| `normalization_violation` | Unicode/control/string rule failed |
| `ordering_violation` | Section/block order is not contiguous or unique |
| `page_range_invalid` | Page fields are incomplete, reversed, or inconsistent |
| `limit_exceeded` | A size, count, depth, or length bound was exceeded |
| `signature_required` | Policy requires a detached signature |
| `signature_invalid` | Signature/key/algorithm verification failed |
| `parser_unapproved` | Parser build/configuration/model weights are not approved |
| `content_quarantined` | Policy or security scanning requires quarantine |
| `ingestion_not_implemented` | Contract is valid but no approved consumer exists |

Failures are fail-closed and non-retryable unless the cause is explicitly corrected. Resource/transient parser failures belong to the future pipeline, not this data contract.

## 12. Lifecycle, deletion, and model changes

- Draft versions are immutable once fixtures depend on them; changes require a new version.
- Consumers must explicitly allow supported versions. Major changes may alter meaning; minor changes are additive only after schema review; patch changes clarify without changing canonical data.
- Original bytes, parsed representations, signatures, and downstream chunks/embeddings must retain traceable lineage.
- Deleting or revoking a source must delete or tombstone all derived representations, chunks, indexes, caches, and embeddings according to retention policy; re-ingestion must not resurrect revoked data.
- Parser, configuration, OCR, or model-weight changes require a new representation and controlled re-parse. Embedding/model changes require new derived artifacts and index namespaces; they must not silently mutate this parsed representation.
- Licence revocation, source withdrawal, or access-scope reduction must propagate to every derivative before it is queryable.

## 13. Acceptance fixtures

A future implementation must add machine-readable fixtures outside this specification. Minimum cases:

1. **`minimal-valid`** — one root section and one text block; canonical digest is stable across runs.
2. **`all-block-types-valid`** — text, table, code, caption, and list with valid pages and heading paths.
3. **`unicode-normalization-valid`** — decomposed Unicode and CRLF normalize to the expected NFC/LF canonical bytes.
4. **`duplicate-key-invalid`** — rejected as `malformed_json`.
5. **`control-character-invalid`** — NUL/bidi control rejected as `normalization_violation`.
6. **`uri-secret-invalid`** — user information or token query rejected as `invalid_identity`.
7. **`ordering-invalid`** — duplicate/gapped order rejected as `ordering_violation`.
8. **`page-range-invalid`** — missing/reversed/out-of-section range rejected as `page_range_invalid`.
9. **`metadata-nested-invalid`** — nested or unknown metadata rejected as `schema_violation`.
10. **`bounds-invalid`** — each count, depth, string, and byte boundary tested at limit and limit plus one.
11. **`signature-valid-invalid`** — fixed canonical bytes verify; one-byte mutation fails.
12. **`idempotent-replay`** — identical representation is a no-op; changed source hash or parser identity creates a revision.
13. **`licence-quarantine`** — `NOASSERTION` validates structurally but returns `licence_unapproved` for ingestion eligibility.
14. **`prompt-injection-content-valid`** — hostile instructions remain inert text and are never executed or promoted.
15. **`draft-ingestion-rejected`** — `ingest_documents.py` rejects the draft with `ingestion_not_implemented` until explicit support is reviewed and released.

Acceptance requires deterministic canonical bytes/digests on Windows and Linux, complete negative-code assertions, offline operation with network disabled, and proof that no fixture content is logged.
