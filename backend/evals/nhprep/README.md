# NHprep finder: development cases

`dev.jsonl` is the **development** split for `scripts/eval_nhprep.py` (plan rev 4 §10 G4): tune `app/nhprep/intent.py`
and `app/nhprep/tuning.json` on it. The held-out test split is frozen separately by lane L6; never tune on it.

One JSON object per line:
- `kind: "retrieval"`: `category`, `message` (as a student types it), `relevant` (graded: lesson `external_id` → 2 for
  the lesson or a same-title equivalent, 1 for another lesson whose title holds every topic word), optional `views`,
  `trainer` (trainer precision). Full NHprep titles are never copied: `{title}`, `{chapter}`, `{note}` are filled from
  the database by `title_of`, `chapter_of` and `note_of` (ids); other messages hold at most a four-word fragment.
- `kind: "intent"`: `message`, `expect` (`nhprep`, `bank` or `chat`, the bank's parse first) and optional `views`.

Built from export v1 (2026-11-19T08-17-18-846Z) with a fixed seed. Run (lexical and title legs; the dense leg needs
real vectors to mean anything):

```sh
DATABASE_URL=postgresql+asyncpg://aichat:aichat@127.0.0.1:5432/netsec_nhprep_eval_test python scripts/eval_nhprep.py
```
