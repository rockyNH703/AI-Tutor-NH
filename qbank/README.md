# qbank: question bank content (text)

These files are the source of PadhAI's question bank. `backend/scripts/import_questions.py` checks them
and imports them into PostgreSQL. Rules and contract: [docs/question_bank.md](../docs/question_bank.md).

Content is written offline. Nothing here is generated at runtime, and the app serves only questions that
a reviewer has approved.

```
manifest/<syllabus version>/<book_id>.json       one book: its syllabus version, chapters, topics, concepts
content/<book_id>/<chapter_id>/cases.jsonl       shared passages (case- and source-based questions)
content/<book_id>/<chapter_id>/questions.jsonl   one question per line
```

## IDs
- **Book:** `<title>-c<class>-<en|hi|sa>`, e.g. `science-c10-en`. A Hindi or Sanskrit book has its own ID.
  The manifest also records NCERT's book code (`ncert_code`, e.g. `jesc1`).
- **Chapter:** `<NCERT code>-CH<nn>`, e.g. `JESC1-CH09`. It is unique across every class, subject, book
  and language, and matches NCERT's chapter PDF (`jesc109.pdf`, the manifest's `source_pdf`).
- **Topic:** `<chapter>-T<n>`.
- **Case:** `<topic>-CS<n>`.
- **Question:** `<topic>-<nnn>`.

IDs are permanent: never reuse one for different content.

**The inventory** (`manifest/cbse-2026-27/`) lists every NCERT book of Classes 6–12 in the app's subject
catalog, in English, Hindi and Sanskrit, with all their chapters. It was taken from NCERT's own textbook
index (books, codes, chapter counts).
- A chapter title read from the book's contents page has `"title_verified": true`.
- Any other title reads `Chapter <n> [title unverified]` until someone checks it against `source_pdf`.
  Titles are never guessed.

**Topics** are added to a chapter's manifest when its questions are written.

## A question (one JSON object per line)

```json
{"question_id": "JESC1-CH09-T7-001", "chapter_id": "JESC1-CH09", "topic_id": "JESC1-CH09-T7",
 "concept_tag": "power-of-lens", "difficulty": "easy", "format": "mcq", "competency": "application",
 "case_id": null, "stem": "…", "options": ["…", "…", "…", "…"], "answer": "B", "explanation": "Correct option: (B). …",
 "marks": 1, "source": {"kind": "original", "ref": "…", "url": null},
 "review": {"status": "needs_review", "note": "…"}, "active": true}
```

**Allowed values:**

| Field | Values |
|---|---|
| `difficulty` | `easy`, `medium`, `hard` |
| `format` | `mcq`, `assertion_reason` (CBSE's four options, verbatim), `case_based` / `source_based` (with a `case_id` of the same topic) |
| `competency` | `knowledge_understanding`, `application`, `analysis_evaluation` (CBSE's question-paper bands) |
| `source.kind` | `original` (written in the CBSE pattern; never call it official), `ncert_exercise`, `cbse_official` (needs `source.url`) |

## Reviewing
A reviewer checks:
- the key and the explanation;
- syllabus alignment, difficulty, competency and language;
- the near-duplicate notes the importer prints.

Then:
1. **To approve**, run `python scripts/import_questions.py ../qbank --hashes` from `backend/` and set:

   ```json
   "review": {"status": "approved", "by": "<name>", "at": "2026-10-20T10:00:00+05:30", "hash": "<that question's hash>"}
   ```

   If the content changes afterwards, the hash no longer matches and the import refuses the approval.
2. **To refuse**, set `"status": "rejected"` with a `note`.

## Importing
Run from `backend/`:

```sh
python scripts/import_questions.py ../qbank            # dry run: the exact report, nothing written
python scripts/import_questions.py ../qbank --apply    # one transaction, all or nothing
python scripts/qbank_coverage.py                       # real counts, statuses and shortfalls
```

## The NetSec certification bank (curriculum `netsec`)

```
manifest/netsec-2026/<track>.json                one track: its exam domains (chapters), canonical topics
                                                 (aliases, paraphrases, NHprep lessons), concepts
content/<track>/<domain>/questions.jsonl         one question per line
vocabulary/netsec.json                           terms that name one thing (acronym and expansions),
                                                 merged into topic aliases at import
```

A question:

```json
{"question_id": "CCNA-D3-T04-001", "chapter_id": "CCNA-D3", "topic_id": "CCNA-D3-T04", "concept_tag": "core",
 "difficulty": "medium", "format": "multi_select", "competency": "application",
 "stem": "Which two ... (Choose two)", "options": ["...", "...", "...", "..."], "correct": ["A", "C"],
 "explanation": "...", "marks": 1, "source": {"kind": "nhprep_lesson", "ref": "<lesson id>"},
 "lesson_ref": "<NHprep lesson external id>", "key_origin": "authored",
 "verification": {"notes": "agree", "closed_book": "agree", "single_defensible": true, "by": "...", "at": "..."},
 "review": {"status": "needs_review"}, "active": true}
```

**Rules** (`backend/app/qbank/netsec.py`):
- 2–8 distinct options, distinct correct letters;
- an `mcq` has one key; a `multi_select` has 2 ≤ keys < options, and its stem says "(Choose N)";
- an `authored` key is served only when the blind check agreed three ways;
- NHprep's own questions (`source.kind: nhprep_quiz`) keep their text and are exempt from the written-style rules
  (no "all/none of the above", explanations of 40+ characters);
- an inactive question says why (`inactive_reason`);
- titles carry no newline, fence or markup.

**NHprep's own quiz questions** come from session 3's export (export v1: `export.jsonl` + `manifest.json`), through
`backend/scripts/nhprep_quiz_to_bank.py` (`app/qbank/nhprep_quiz.py`):

```sh
python scripts/nhprep_quiz_to_bank.py <export run folder> ../qbank            # dry run on a copy
python scripts/nhprep_quiz_to_bank.py <export run folder> ../qbank --write    # rewrite qbank/content
```

- **What NHprep lacks per question** comes from `nhprep/quiz-map/<category>.json`: the topic, the difficulty and
  any missing explanation, plus an optional `competency`, `concept_tag` and `dispute`. That file is written offline.
- **Kept as served:** the text, the options in NHprep's order and the key (`key_origin: nhprep`). The id is
  `NHQ-<CATEGORY>-<ID>`.
- **Gates:**
  - every exported question must be mapped, or the run fails;
  - a format other than one-of-N, or a key that disagrees with itself, is an error;
  - an exact duplicate is written once;
  - a question NHprep drops is retired only when its category's coverage says every question was seen;
  - a review is kept only while the question is unchanged;
  - a question whose options are identical in NHprep's own data is not imported (listed as unsupported).

## What is here (2026-11-20)
- **NHprep's quiz questions** from session 3's export of 2026-11-19 (`2026-11-19T08-17-18-846Z`, sha256
  955d02e6…fe18f5): 1,665 keyed questions in 54 categories.
  - **1,664 imported:** 1,482 active and 182 inactive (disputed). One is not imported: two of its options are
    identical in NHprep's own data.
  - **Owner audience:** NetworkersHome students only (`NHPREP_ALLOWED_EMAIL_DOMAINS`).
- **How they were mapped** (owner decision 2026-11-19: Claude agents offline, with a blind check):
  - **Topic lists:** an agent per category drafted the category's topic list. That gives 53 new manifests (one per
    category, `manifest/netsec-2026/<book>.json`, edition "NHprep quiz bank 2026"), and the ccna category maps onto
    the existing CCNA manifest.
  - **Topic and difficulty:** the same agent chose each question's topic and difficulty, written to
    `nhprep/quiz-map/<category>.json`.
  - **Independent check:** a second agent re-judged every mapping. It disagreed on 3 topics (settled by session 2
    for the verifier) and on 210 difficulties (a two-level gap became medium; the mapper's level was kept
    otherwise).
- **Answer keys:** the verifiers doubted 294 of NHprep's keys, and two more judges (technical, item-writing) re-judged
  each doubt.
  - **Both found a problem:** 182 questions (66 judged wrong, 116 with more than one defensible answer) are imported
    inactive, with the reason in `inactive_reason`.
  - **Split verdicts:** 21 stay active.
  - Everything is in `nhprep/review.json` for NetworkersHome's trainers. NHprep's text and keys are never changed
    here.
- **CCNA manifest** (`manifest/netsec-2026/ccna.json`): the 200-301 v1.1 domains and 44 canonical topics with
  aliases; the ccna category's 40 questions map onto it.
- **Owner (2026-11-20): no trainer review.** The 182 disputed NHprep questions stay inactive for good;
  `nhprep/review.json` is the record of why.
- **Authored questions** (owner decision 2026-11-19: Claude agents offline with a blind check; written 2026-11-20).
  - **Result:** 6,100 questions, ids `NHA-<lesson id>-<n>`, written from the notes of 856 of the export's 858
    lessons that have notes. 5–9 per lesson, by the length of the notes.
  - **Mix:** 1,756 easy, 2,659 medium and 1,685 hard; 1,768 multiple-correct ("Choose two/three").
  - **Explanations** name the lesson and a chapter time. Source: `nhprep_lesson` (the lesson id);
    `key_origin: authored`.
  - **Blind check:** a different model (Claude Sonnet) answered every question without the keys, closed-book and from
    the lesson notes, and judged whether exactly one answer is defensible. A question is kept only when both answers
    equal the key and it is single-answer: 6,100 of 6,128 written (19 disagreed, 3 not single-answer, 6 duplicates).
    In a few batches the checker recorded one answer for both passes.
  - **Security courses** (bug bounty, CEH web attacks) are asked defensively: recognition, impact, detection and
    mitigation, never working payloads.
  - **The whole bank:** 7,582 active questions on 705 topics; 337 topics hold at least a 5-question batch.
- **Before that:** PadhAI's NCERT content (the CBSE 2026-27 inventory and the Chapter 9 questions) was deleted
  on the owner's instruction; it remains in git history (commit e57652c and earlier). NetSec's production
  question-bank tables were already empty. The certification bank (domains and topics) is still to be written
  in the layout above.
