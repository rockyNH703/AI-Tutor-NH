# Question bank

The question bank holds CBSE-pattern multiple-choice questions for **Classes 6–12**. They are written
and reviewed in advance, then stored in PostgreSQL. Practice sets and tests serve them using SQL filters
and fixed rules.

**No model is involved** in any of this: not in retrieving or picking questions, not in assembling tests,
not in checking answers, and not in explaining them. `tests/test_architecture_guards.py` fails if
`app/qbank` or its scripts import `app.llm`, `app.rag`, `app.chat` or an HTTP client.

The tutor's own practice blocks in chat (`app/assessments`) are separate and unchanged.

- Code: `backend/app/qbank/`
- Schema: `backend/alembic/versions/0015_question_bank.py` (and 0016, 0017)
- Content: `qbank/` (see [qbank/README.md](../qbank/README.md))
- Reference SQL: [question_bank_queries.sql](question_bank_queries.sql)

## The NetSec certification bank (2026-11-19)

The same tables hold NetSec's certification bank (migration 0026, curriculum `netsec`). The CBSE description
below still applies to the CBSE shape, which nothing serves in NetSec (its content was deleted, 7a69002).

**How the tables map:**

| Table | Holds |
|---|---|
| `qb_books` | A certification track (a `catalog.CERTIFICATIONS` code: `ccna`, `ccnp_enterprise`, …), with `class_level` as its tier (1 associate, 2 professional, 3 expert) and `subject_code` as its family |
| `qb_chapters` | An exam domain (CCNA 200-301 v1.1 has 6) |
| `qb_topics` | A canonical topic that NHprep lessons map to, with `aliases`, `lessons`, and `search_title` / `search_doc`, written by the importer, plus a weighted `search_tsv` and trigram indexes |
| `qb_questions` | Single-answer (`mcq`) or multiple-answer (`multi_select`) questions: `options` (2–8) and `correct_keys` (letters). The database checks the shape (`qb_options_valid`). `lesson_ref` names the NHprep lesson; `key_origin` says whether the key came from NHprep or was written |
| `qb_set_items.chosen_keys` | An answer. The trigger grades it as a set: right means exactly the key's letters |

**Who:**
- NHprep-derived questions (`source_kind` `nhprep_*`) are served only to NetworkersHome students (verified emails at
  `NHPREP_ALLOWED_EMAIL_DOMAINS`, `app/access/students.py`) and, since 2026-11-24 (owner), to accounts an NH invoice
  enrols, on their programs' mapped tracks only (`bank_scope`, `app/programs/access.py`).
- Every pick checks this (`selection.Pool.entitled`).
- The model can't open the netsec bank: a `question-bank` block never names a netsec domain id.

**The bank turn** (`QUESTION_BANK_TURN` off|qa|on, `app/chat/bank_turn.py`): **no model, embedding or search
call**, decided before memory recall or any classifier runs.
- **Parse** (`app/qbank/request.py`):
  - a strict, verb-led request grammar: "quiz me on X", "give me 10 hard MCQs on X", "practice questions on X",
    "CCNA questions on X", Hinglish, "next 5";
  - questions *about* something, labs, exam logistics, flashcards and interview questions are left to the normal
    turn.
- **Find** (`app/qbank/finder.py`): one SQL round trip over the netsec topics.
  - The candidates come from exact titles and aliases, lesson titles, full text (any word, with coverage) and
    trigram similarity.
  - Typos are corrected against the bank's own words: one edit, a transposition first.
  - The result is confident, broad (several topics in one track, or an exam domain), ambiguous or none.
  - "this / that" means the chat's last messages, then the last bank test.
- **Pick** (`app/qbank/bank_tests.py`): a fixed batch, default 5, at most 30.
  - Mix: the asked difficulty, else roughly 35% easy, 45% medium and 20% hard. It leans easier after a weak batch
    and harder after a strong one.
  - Unseen questions and the weakest concepts come first, and the named lesson's questions before others.
- **Reply:** one fixed line plus a ```quiz block without keys. The keys are only in the stored record
  (`app/assessments/bank.py`, a schemaVersion 2 practice test), shown per question once it is final.
- **Saving:** `_finalize` opens the practice set in the reply's own transaction.
- **Answers:** `_bank_step` records each answer in the ledger (`chosen_keys`), and the database grades it.
- **"Next 5"** (the card's button, or the typed text) continues on the same topics without repeats.

**Eval** (`scripts/eval_bank_finder.py`): session 1's frozen query set in `backend/evals/qbank_requests/`, with 420
dev and 415 test cases and 360 negatives. On dev with the CCNA manifest (2026-11-19):
- route accuracy 1.000, with 0 false triggers in 186 negatives;
- topic hit 0.983, precision 0.976;
- parse plus find p95 34 ms in WSL.
- the whole bank turn, from request to `done` (warm, in-process, WSL, 30 turns): p50 232 ms, p95 294 ms;
  the bank's own find + pick p95 37 ms. The target is p95 ≤ 500 ms; the host is not measured yet.

The held-out test split ran once (session 1, on a83df21; it is spent):
- route 0.998, 0 false triggers in 184;
- topic hit 0.942 and precision 0.925, under the 0.95 targets.

The weak spots it showed were fixed on dev only:
- track words (AWS SAA);
- exact domain names;
- "layer 2" / "routing" / "switching" aliases;
- an alias outranking fuzzy similarity ("HSRP configuration");
- numbers that belong to the topic ("layer 2");
- ip6 / "ipv 6".

Session 1's red team also found ordinary engineering messages ("create a test VLAN", "questions on my mind: why …")
read as quiz requests. The grammar now needs an asked item followed by a topic or nothing, a bare "test" is never one,
and a weak cue that finds no topic goes to the model. `tests/test_qbank_request.py` keeps the 49 probes and
`tests/test_qbank_redteam.py` the runtime checks. A fresh held-out set is needed before the next measured claim.

Two fresh held-out sets from session 1 followed, each run once and then committed as spent:
- **`holdout.jsonl`** (442 cases, run on 741cddc):
  - route 0.867, with 0 false triggers in 176;
  - hit 0.947, precision 0.935;
  - positive recall about 0.78.
- **`holdout2.jsonl`** (422 cases, sha256 b9a2dcf7…7c95, run on 06b9896):
  - route 0.725, with 0 false triggers in 165;
  - hit 0.887, precision 0.883;
  - positive recall 0.559;
  - p95 39.7 ms.

What holdout 2 showed:
- **Recall:** every new wording family was missed ("how about some MCQs on X", "time for a quiz on X", "I want to be tested on X", "tst me"). Each set had fixed the families of the one before, so a grammar that lists wordings does not converge.
- **A confident wrong topic:** "cool, now quiz me on it" was served NAT questions, because the typo step turned "cool" into "pool".
- **Two missing aliases:** "longest prefix matching" and "wireless encryption".
- **Track-only requests** for tracks the bank does not hold were scored as misses, even when the answer "none yet" was correct.

**The fix (2026-11-19):**
- **Request frames:** the grammar now also accepts a quiz word after a request frame ("I want/need/'d like/'ll take", "can/could you", "how/what about", "time for", "let me/let's", "set me", "check my", "fire away"). It is structural, not a list of wordings.
- **Guards kept:** the precision guards stay. Blocked phrases now also include "questions answered/I wrote", "ask you" and "answer … questions". A weak cue still needs the finder to be sure.
- **Typos:** one-letter slips in the verb are accepted ("tst me").
- **The NAT bug:** leading interjections are dropped, and a word of four letters or fewer is corrected only by swapping its letters.
- **Aliases:** both added.
- **Eval scoring:** the eval now scores "none" as correct for a track the bank has no questions for.

**After the fix** (WSL, the CCNA manifest):

| Set | Route | False triggers | Hit / precision | p95 |
|---|---|---|---|---|
| dev | 1.000 | 0 of 186 | 1.000 / 0.994 | 19.5 ms |
| test | 1.000 | 0 of 184 | 0.996 / 0.989 | 22.1 ms |
| holdout 1 | 1.000 | 0 of 176 | 0.996 / 0.994 | 21.7 ms |
| holdout 2 | 1.000 | 0 of 165 | 0.988 / 0.965 | 25.3 ms |

The first post-fix run surfaced two problems, now fixed:
- **10 false triggers:** "a quick question on X: is it stateful?" had been read as a quiz request since 06b9896, and "can you check my answers to these … questions" by the new frames. Both are blocked now.
- **A stop-word regression:** "network time protocol" lost "time". Only the phrase "time for" is dropped now.

These numbers are measured on sets that are now spent (holdout 2 was also tuned on). The next honest recall figure needs a fourth fresh set.

**`holdout3.jsonl`** (451 cases, sha256 cfa15069…5c4, run once on a5cc30e; now spent):

| Metric | Result |
|---|---|
| Route | 0.716 |
| False triggers | 4 of 197, at the 0.02 limit |
| Hit / precision | 0.918 / 0.902 |
| Positive recall | 0.52 |
| p95 | 43.4 ms |

What it showed:
- **Recall:** again, about half of every wording family it introduced was missed ("quiz time: X", "assess me on X", "practice set on X", "a 6-question quiz on X", "I'm after N mcqs", "keep the questions coming").
- **False triggers:** all four came in request frames ("questions to ask my ISP", "test my understanding by explaining…", "…not a quiz", "the quiz results explained").
- **A confident wrong topic:** "questions on the last thing you said" was served the routing-table topic.

**The fix:**
- **Quiz nouns:** a quiz noun (quiz, MCQs, practice/mock test, set, questions, paper, "N-question quiz") now carries a request alone. That counts only when the noun is the thing asked for: at the start, after "a/an/some/N/another", or closing the message, and never before app, software, progress, results, score and similar words.
- **More request frames:** "kick me off with", "I'm after", "do 3 questions", "assess me", "test what I learned".
- **Next-batch phrasings:** "go on", "next round", "3 more like these", "keep them coming".
- **Guards:** "questions to ask", "by explaining", negations ("no quiz") and "quiz results/score/feature".
- **Chat references:** "the last thing you said" and "what I learned" now refer to the chat.
- **Typos and parsing:**
  - a short word may also gain one letter ("jsn" → json);
  - "2.4 GHz" keeps its numbers;
  - "spanning <domain>" drops "spanning", but "spanning tree" keeps it.
- **Aliases:** 2.4/5 GHz, virtual IP/MAC, MIBs/OIDs, DHCP across subnets and ip helper, trusted ports, agentless.

The first run of this fix found four false triggers from the noun-alone cue in holdout 1 ("reset my quiz progress", "the quiz app on our intranet…", "these practice questions say…", "write quiz software"), which is why the noun now has to be the thing asked for.

**After the fix** (WSL), route 1.000 and 0 false triggers on every set:

| Set | False triggers | Hit / precision | p95 |
|---|---|---|---|
| dev | 0 of 186 | 1.000 / 0.994 | 29.7 ms |
| test | 0 of 184 | 0.996 / 0.989 | 37.7 ms |
| holdout 1 | 0 of 176 | 0.996 / 0.994 | 37.5 ms |
| holdout 2 | 0 of 165 | 0.981 / 0.961 | 41.3 ms |
| holdout 3 | 0 of 197 | 0.996 / 0.994 | 41.3 ms |

**Honest reading:** holdouts 2 and 3 were tuned on. On fresh sets, recall of new wordings has stayed around 0.5–0.8, because every set brings wording families no rule has seen. False triggers have stayed at or under 0.02. A missed request is not an error: the model answers it as an ordinary message. Students who use the app's own practice buttons send fixed wordings that always parse.

**`holdout4.jsonl`** (488 cases, sha256 9b9ed1d8…aca, run once on 90c8687; spent, not tuned on):

| Metric | Result |
|---|---|
| Route | 0.641 |
| False triggers | 2 of 214 (0.009) |
| Hit / precision | 0.885 / 0.855 |
| Positive recall | 0.39 |
| p95 | 39.1 ms |

Each fresh set brought new wording families: "grill me on X", "pop/revision quiz", "can I be quizzed", "shoot me some questions", "prep questions", "X questions, ccna level". It also showed topic gaps: "inside global addresses", "ike phase 1", "esp and ah", "rogue dhcp servers", "two-factor authentication".

Recall on unseen wordings across the four fresh sets was 0.78, 0.56, 0.52 and 0.39, while false triggers stayed at 0.02 or below.

**Decision (owner, 2026-11-20):**
- **The rules stay as they are:** precision first. A missed request goes to the model as an ordinary message.
- **The sure way in is a Quiz me button** in the chat composer (`frontend/apps/app/src/features/chat/quiz-me.ts`):
  - in a chat with messages, an empty box and no files, it sends "Quiz me on this", which quizzes on what was just discussed;
  - otherwise it puts "Quiz me on " before what is in the box for the student to finish.
- **Who sees it:** only where `GET /v1/progress/bank` says `enabled`, which needs `QUESTION_BANK_TESTS` on, `QUESTION_BANK_TURN` allowing the account, and (owner, 2026-11-24) an NH invoice that opens a program with mapped tracks (`enrolled_books` in `app/programs/access.py`): enrolled students only, not a networkershome.com address by itself. Each chat turn decides what it serves itself (`bank_scope`: a networkershome.com address every track, an enrolled student their programs' tracks).
- **Rejected:** a classifier fallback (INTENT_ROUTER). It would be a model call at query time, which the owner's rule excludes.

The grammar was not tuned on holdout 4 at the time. Its wording families were added on 2026-11-20, after holdouts
6 and 7 showed the same families again (below). Its topic gaps are aliases to add when the CCNA content is mapped.

**The real NHprep bank (2026-11-20).** Imported in WSL with `scripts/import_questions.py`:
- 54 books, 202 domains, 713 topics, 1,664 questions (1,482 active);
- details in `qbank/README.md`, "What is here".

The eval sets were written for the CCNA-only bank, so their expected topics are CCNA titles. On the 54-book bank,
route accuracy and false triggers are unchanged (route 1.000 and 0 false triggers on dev, test and holdouts 1–3).
Topic hit/precision, with `--goal ccna` (a CCNA student, as the chat passes the goal):

| Set | Hit / precision | p95 |
|---|---|---|
| dev | 0.979 / 0.972 | 194 ms |
| test | 0.960 / 0.954 | 226 ms |
| holdout 1 | 0.895 / 0.895 | 214 ms |
| holdout 2 | 0.852 / 0.842 | 226 ms |
| holdout 3 | 0.669 / 0.669 | 219 ms |
| holdout 4 | 0.606 / 0.591 | 207 ms |

Without a goal, dev is 0.861 / 0.855.

**What the misses are:**
- **Mostly** another book's topic for the same subject that the CCNA-only labels do not accept ("wifi channels" →
  the Wireless book's Wi-Fi channels topic; "tcp vs udp" → the TCP/IP book's).
- **Some** are wrong picks ("autonomous vs lightweight aps" → REST APIs).
- **Latency:** p95 rose from about 40 ms to about 220 ms with 16 times the topics. It is still under the 500 ms
  target. The typo step's edit distance over the larger vocabulary and the FIND query are the cost.

**Done before QUESTION_BANK_TURN went on (2026-11-20):**
- **Goal first (472fdbc):** the student's goal book decides when it has a credible topic.
- **Latency (472fdbc):** one grouped count of servable questions in FIND; a letter-set prefilter in the typo step
  (its p95 went from 52 to 10 ms).
- **Widening (77b16b9):** a confident topic with fewer than 5 servable questions becomes a broad pick over the topics
  the same words name, in one book. The real bank averages about 2 questions a topic.
- **holdout5 root causes (6ae78e8):**
  - the strongest topic picks the book, not the book with the most matches;
  - a half-known request ("jinja2 templates") gets "none", not a wrong confident pick;
  - a named course is its whole book;
  - "a quiz on how …" routes;
  - two false triggers from holdout4 are blocked.
- **"Quiz me on this" (b86a3ae):** an earlier quiz request in the chat means its topic, or the last test, never its
  words read as a sentence.

**`holdout5.jsonl`**, the first multi-book set (389 cases, sha256 7dedffd3…01d6, run once on 77b16b9 by session 1;
spent, and 6ae78e8 was tuned on it):

| Metric | Result |
|---|---|
| Route | 0.997 |
| False triggers | 0 of 161 |
| Hit / precision | 0.853 / 0.815 |
| Goal-first | 0.893 |
| p95 | 213 ms |

**`holdout6.jsonl`** (400 cases, sha256 59892461…, written by session 2 with a generator; run once on b86a3ae's
code, on the 1,664-question bank; spent):

| Metric | Result |
|---|---|
| Route | 0.805 |
| False triggers | 2 of 150 |
| Hit / precision | 0.865 / 0.824 |
| Goal-first | 0.915 |
| p95 | 164 ms |

Most misses were short requests the verb-led grammar did not read: "fhrp quiz please, intermediate level", "sql
injection questions, 10 hard ones", "aur 5 do", "X course questions". A bare "quiz me" after a talk about a topic
went to the student's goal instead of the chat.

**The grammar, extended (2026-11-20).** Precision first still holds: every addition was checked against every
negative in every eval set (0 of 1,574 parse).
- **A topic, then the quiz noun, then only settings** (weak): "eigrp metric mcqs, tricky ones", "nat overload 6
  questions", "ipv6 questions, ccna level", "dns record types quiz (a, aaaa, mx)". The words before the noun must
  read as a topic: no subject, verb or question word ("I have so many bgp questions", "vlan questions came up in my
  interview" are not requests), and nothing may follow but count, level, track and the like.
- **Wording families from holdouts 4, 6 and 7:** "grill me", "can I be quizzed", "pop / revision quiz", "prep
  questions", "shoot me some X questions", "I'd appreciate some questions", "N questions to check my X", "test
  questions", "multiple-answer / choose-two questions", "u" for "you".
- **"More":** "aur 5 do", "agla set do", "ek aur set", "nxt 5", "another round, 8 this time", "more like these, 4",
  "give me more of those", "same topic, next few", "i want more". "continue", "keep going" and "again" stay with the
  model: they can mean "go on explaining".
- **"This":** a bare "quiz me" / "test me" reads the chat first (`referent()`), then falls back to the student's goal
  track, else every track. The same fallback serves "quiz me on this" in a chat that names nothing (it used to say
  "I don't have stored practice questions on this yet"). Hinglish "iske / iska / isi / is par", "the difference",
  "this stuff", "tough ones" read as the chat.
- **Blocked** (holdout 6 and 7's false triggers, all present in the grammar live at 7d86100): building something
  that makes quizzes ("write a script that generates a random quiz", "have the model generate MCQs"), the student's
  own questions ("I'd like to ask some questions about…", "questions about the labs: do I need…"), negation ("please
  don't quiz me, just explain…"), recommendations ("recommend books with practice questions") and answer keys.

**`holdout7.jsonl`** (400 cases, sha256 2592f57f…b385), written by an independent agent that did not see the grammar
or the earlier sets. It was run once on the extended grammar before the holdout7 fixes, on the full 7,764-question
bank; it is now spent:

| Metric | Result |
|---|---|
| Route | 0.930 |
| False triggers | 6 of 175 (0.034) |
| Hit / precision | 0.854 / 0.799 |
| Goal-first | 0.967 |
| p95 | 162 ms |

- **False triggers:** all 6 also trigger on the grammar live at 7d86100. The extension added none. All 6 are fixed
  after this run (above).
- **Parse recall on this fresh set:** 162 of 225 requests on the 7d86100 grammar, 203 with the extension.
- **What is still weak:** "this" (hit 0.514). Within a subject, the chat's sentence often picks a neighbouring topic,
  for example "Terraform workflow" instead of "terraform import". Of the "next" wordings, "continue", "keep going"
  and "again" are left to the model on purpose.
- The expected topics for multi-book cases are broad keyword lists, so precision on them is generous.

**After the holdout7 fixes** (WSL, on a94a410 plus this change, the full bank):
- 0 false triggers on all nine sets (dev, test, holdouts 1–7);
- route 1.000 on dev, test and holdouts 1–3 and 5, 0.994 on holdout 4, 0.958 on holdout 6 and 0.968 on holdout 7
  (tuned on);
- topic hit/precision unchanged on dev through holdout 5;
- p95 135–160 ms.

A next fresh set is still the only honest measure of new wordings.

**In production since 2026-11-20 (release b86a3ae, Status: MATCH):**
- the bank is imported (54 books, 713 topics, 1,664 NHprep questions, 1,482 servable);
- `QUESTION_BANK_TURN` went qa → on after a read-only live check of `bank_turn.answer()` on the production database:
  - a student's "quiz me on ospf" served 5 stored questions in 423 ms (cold);
  - "5 hard mcqs on bgp" served the 4 hard questions there are, in 74 ms;
  - a non-student got the not-available copy in 57 ms;
  - no model call in any of them.
- **Authored questions (release 7d86100, Status: MATCH):** the host import added 6,100 questions (7,764 in all,
  7,582 active). A read-only live check of `bank_turn.answer()` served authored questions for OSPF, Netmiko,
  Ansible, VLAN trunking, Kubernetes and SQL-injection requests in 74–118 ms warm (378 ms cold).
- **Owner (2026-11-20): no trainer review.** The 182 disputed NHprep questions stay inactive for good; the 21 split
  verdicts stay active. `qbank/nhprep/review.json` is kept as the record of why.

**Content** (`qbank/`, below):
- every NHprep quiz question, kept as written and flagged when disputed;
- 5–10 questions per NHprep video, written by agents from the lesson's notes and served only after a three-way
  blind check (from the notes, closed-book, one defensible answer).

## What it holds

Each level of the hierarchy is a table:

| Level | Table | Notes |
|---|---|---|
| Syllabus version | `qb_syllabus_versions` | e.g. `cbse-2026-27`, session `2026_27`, with the official source URL |
| Book | `qb_books` | The NCERT ID, e.g. `science-c10-en`. It fixes the class (6–12), the subject (an `app/learning/catalog.py` code) and the language of its content (`english`, `hindi` or `sanskrit`) |
| Chapter | `qb_chapters` | Unique by (book, number), so the same number can exist in another book or language. `old_chapter_number` keeps the pre-2023 numbering |
| Topic | `qb_topics` | |
| Concept | `qb_concepts` | A concept tag and its learning objective |
| Question | `qb_questions` | |

A question records:
- its **place**: book, class, subject, language, chapter, topic and concept, kept consistent by composite
  foreign keys;
- **difficulty** (easy, medium or hard) and **format** (`mcq`, `assertion_reason`, `case_based`,
  `source_based`);
- **competency**, using CBSE's question-paper bands: `knowledge_understanding`, `application`,
  `analysis_evaluation`;
- **content**: stem, four options, key, explanation and marks;
- **provenance**:
  - `original` means a practice question written in the CBSE pattern, never called official;
  - `ncert_exercise`;
  - `cbse_official`, which needs a source URL;
- **review**: `draft`, `needs_review`, `approved` or `rejected`, plus `is_active`;
- a **fingerprint** (duplicates) and a **content hash** (re-imports and approvals).

**Servable = approved or awaiting review (`needs_review`), and active** (`selection.SERVABLE`, the
owner's decision of 2026-10-16: students use the questions before anyone reviews them). Draft, rejected
and retired questions are never served. An approval stores the reviewer, the time and the content hash
of exactly what was approved. A question edited after that goes back to `needs_review`, and the database
refuses an approval whose hash doesn't match.

## How content gets in

Content is produced offline, one book at a time:
1. **Manifest:** the book, its chapters (from the CBSE syllabus and the NCERT book), and its topics and
   concepts (from the textbook's sections).
2. **Write:** questions as JSONL.
3. **Check** with `scripts/import_questions.py ../qbank`. It looks at:
   - structure, enums, mapping to the manifest;
   - four distinct options; the key against the explanation; CBSE's assertion–reason options verbatim;
   - the case rule; the language script;
   - duplicates (refused) and near-duplicates (sent to review);
   - the chapter limit.

   Anything that needs subject judgment (is the key right? is it in the syllabus?) is left to the
   reviewer.
4. **Review:** the reviewer records `approved` together with `--hashes`' value, or `rejected`.
5. **Import** with `--apply`:
   - one transaction, all or nothing, under an advisory lock;
   - a rerun changes only what changed;
   - questions are never deleted, only retired (`--retire-missing`).
6. **Report** with `scripts/qbank_coverage.py`. It gives real counts per topic, a status (complete,
   awaiting_review, underfilled, missing, topics_missing), the shortfall to 5 approved questions (the owner's target, 2026-10-15), and
   each chapter's use of its limit.

Production imports are owner-authorised writes. Run them on the host with the API's environment (see
the script's docstring).

## Tests in the chat (`app/qbank/chat_tests.py`)

A practice test in the chat is always the bank's, never the model's (`QUESTION_BANK_TESTS`, on by default):
1. On a turn whose message asks for a test (its words do, or the intent router reads it as a test request:
   `INTENT_ROUTER=on`, `app/intents`), the tutor is given the chapters it may name: the class the message
   names, else the student's, else the last one named earlier in the chat; in the student's medium, plus
   the English, Hindi and Sanskrit books.
2. The tutor plans the test and writes one `question-bank` block: `{"chapter": "<chapter id>", "topics":
   ["...", 4], "count": 10, "difficulty": "easy|medium|hard|mixed"}`, or, for a mock test over up to three
   chapters of one class, subject and language, `{"chapters": ["<id>", "<id>"], "count": 25}` (whole
   chapters, no topics). For "test me on this" it uses what the chat covered; with no number asked, it
   chooses the count from how much the test covers (about 5 per topic, at most 30). It is taught never to
   write questions itself. A request it fences as JSON, writes inline (`::question-bank{...}`) or as a bare
   object is read the same way when it names chapter ids.
3. Before the reply is saved, the server replaces that block with a `quiz` block and opens the student's
   practice set for the test ("The practice ledger", below):
   - the pool is the servable questions of the named topics (each matched by number or clearly by its
     words), else of the whole chapters, of the asked difficulty; questions longer than the quiz card
     allows (187 case questions with long passages) are left out;
   - the test has as many questions as asked, at most 30 and at most what the pool has (fewer: a note says
     so); no question repeats in a test, and the same reply always starts with the same question (ties are
     seeded by the reply's id);
   - **adaptive** (`ADAPTIVE_CHAT_TESTS`, on by default): the block holds the first question, and each
     answer brings the next one, chosen from it (`selection.pick_next`, below). Off: every question is
     chosen when the test is set, by the same rules, and the test is fixed. It is a kill switch: turning it
     off needs no rollback, and those tests are recorded the same way.
4. The quiz is recorded, answered and graded like every chat test (`app/assessments/state.py`): answers
   are checked against the stored key, with no model call. The record also keeps `adaptive` and
   `source: {kind: "question_bank", chapterIds, topicIds, questionIds, setId}`, which clients never see.
5. A quiz or MCQ block the model writes itself is replaced by a note. An unknown chapter, chapters of
   different subjects, or a chapter with no stored questions gets a note instead of a test. A
   fill-in-the-blanks exercise the tutor writes is kept (the owner's decision, 2026-10-22): it is recorded
   and graded as before (`[[answer|alternative]]`, ignoring case, spacing and a trailing `.;,!`), and a
   malformed key (an empty alternative, a blank over 80 characters, more than 20 blanks, invalid JSON)
   refuses it whole, so it is shown read-only. Its key is the model's, so it is never practice evidence:
   no mastery, mistakes, revision or learning day. An exercise the model writes without its fence (as a
   `:::fill-blank` card, the study cards' form; as bare JSON; in a json or misnamed block; or as a card's
   plain text with `[[blanks]]`, often in Hindi) is put in its fill-blank fence first: on 2026-10-23 most
   exercises came that way and showed their answer key as text.

**Each answer** (`POST /v1/messages/{id}/assessment-responses`, `app/chat/router.py`) is one transaction,
under the reply's row lock: the response is checked and recorded on the reply; the answer is written to
the ledger and graded by the database; an adaptive test gets its next question chosen from that answer, or
the set is closed once the test is complete (or the bank has nothing left for it); the answer counts toward
the day's learning (5 answers make a learning day, `app/progress/learning.py`); then it commits. A response
names the hash of the questions it was shown, so a retry after a lost reply, a double tap or a second tab
is a replay of the test as it is now, never a second answer. A skip writes nothing to the ledger.

The model is still called once for the turn: it plans the test (chapter, topics, count, difficulty). It never
writes, picks, checks or scores a question.

### The practice ledger

- One `qb_sets` row per test in the chat (`mode = 'chat'`; `request_id` is the reply's id; `filters` hold
  the chapters, the topic pool, the named topics, the difficulty, the conversation and the title), and one
  `qb_set_items` row per question served, with `served_hash`: the question's `content_hash` when it was
  served (0017, set by the trigger and final).
- A set is closed when its test is complete (ended early when the bank ran out: `requested` becomes what was
  served), when its reply is regenerated or stopped as it is saved, or when its block can't be built.
- Deleting a chat keeps its sets and answers: they have no link to conversations, so the practice record
  outlives the chat (the owner's decision). Deleting the account deletes them. There is no API to delete a
  set.
- **A served question can't move to another topic**: the item's foreign key to (question, topic) has no ON
  UPDATE, so retire it and add it again. An edited question gets a new `content_hash`, so an answer given
  before the edit is set aside from mastery (`served_hash` <> `content_hash`); items served before 0017 have
  no `served_hash` and count as current.
- What the record says (`app/progress/practice.py`: mastery, mistakes, revision, the day's plan): see
  [api-contract.md](api-contract.md), "The practice record".

## Selection contract (`app/qbank/selection.py`)

**Filters.** Required: `language`, `class_level`, `subject_code`, `syllabus_version`. Optional:
`book_id`, `chapter_id`, `topic_id`, `question_format`, `unseen_only`.
- Every filter matches exactly and is never relaxed.
- A named book, chapter or topic must lie inside the requested class, subject and language, or the
  request is refused (422).

**Test** (`create_test`):
- It takes a count per difficulty.
- If any count is short of eligible questions, the result is a shortage report with nothing created:
  `{"status": "shortage", "buckets": {difficulty: {requested, available}}}`. A partial test is made only
  with `allow_partial`.
- The order is seeded by the set ID, and questions the student has seen come last.
- The key and the explanations come with the results, once the test is closed or fully answered.

**Adaptive practice** (`create_adaptive`, `next_item`; and tests in the chat): one question at a time
(`pick_next`), from a pool of topics (adaptive practice: one). Each choice depends on the student's previous
answer: in that topic, for adaptive practice; in the test so far, for a test in the chat.

| Previous answer | Next question |
|---|---|
| none | medium, the weakest (untried) concept |
| wrong | one level easier, same concept |
| right with a hint | same level, same concept |
| right, no hint | one level harder, the weakest other concept |
| skipped (a test in the chat) | same level, another concept: a skip is not an answer, so no step down |

A concept is a topic's (topic, concept tag). "Weakest" is smoothed accuracy, (right + 1) / (tries + 2): a
practice signal, not a validated measure of mastery. Questions the student has not answered come first, and
none is repeated within a set. "Next" runs under the set's row lock, so a retry returns the open question.

**Answers** (`answer`):
- An answer is accepted only for a question served in the student's own set; otherwise it is 404.
- The database grades it from the key and keeps it (trigger `qb_set_items_answer`).
- The same answer again returns the original result; a different answer, or one for a closed set, gets
  409.
- Creating a set is idempotent per (student, `request_id`).

## Invariants the database enforces

| Invariant | How |
|---|---|
| At most `max_questions` (≤ 1,000) questions per chapter | A counter on the chapter row, kept by triggers on insert, delete and move, and checked by `ck_qb_chapters_question_count`. Concurrent writers serialise on that row; lowering the limit below the count is refused; nothing else can write the counter. |
| Consistent identity | Composite foreign keys: a question cannot claim a language, class, subject or chapter other than its book's, cite another topic's case, or use another topic's concept. |
| Answers | Served unanswered, answered once, graded by the database, final. A served item keeps the question's content hash of that moment (`served_hash`, 0017). |
| Clean account deletion | A student's sets and answers are deleted with their account (not with a chat). |

## Competitive eligibility (NH Friend Zone, 2026-11-24)

Battles and daily challenges (`app/friendzone/eligibility.py`) use a stricter rule than practice. A question
qualifies only when it is:
- active;
- `mcq` with one key;
- at most 105 words of stem and options;
- not NHprep-keyed;
- verified for its **current** text.

Under `FRIEND_ZONE_KEY_POLICY=human_only`, verified means a person's approval (`reviewed_hash = content_hash`).
Under `human_or_ai` (owner U1), an authored question with a row in `qb_key_checks` for its exact `content_hash` also
counts.

`scripts/bind_key_checks.py` writes those rows, and only where all of these hold:
- the content file's last commit is the verification commit;
- the working tree leaves the file unchanged;
- the imported text's hash equals the file's.

Any edit changes `content_hash`, so the question drops out until it is checked again. Practice keeps its own looser
rule (`QB_SERVABLE`).

`qb_sets.mode` gains:
- `battle` and `daily`: live members, which count in the practice record;
- `fz_qa`: the QA cohort, which never counts in practice, mastery or the streak.

## Status (2026-10-16; 2026-10-22: adaptive tests in the chat, the practice ledger and record)
- **Released:** schema (0015, and 0016's servable indexes), importer, selection, coverage, and tests in the
  chat. 2026-10-22 (0017): tests in the chat are adaptive and recorded in the ledger, mock tests span up to
  three chapters, and the practice record (`GET /v1/progress/plan`, `/mastery`, `/revisions`, `/mistakes`,
  `/practice-tests`, `/practice-topics`) reads it. Tests in the chat set before then have no ledger rows:
  the record starts with this release.
- **Inventory (`qbank/manifest/cbse-2026-27/`):** every NCERT book of Classes 6–12 in the app's subject
  catalog, from NCERT's own textbook index: 167 books (80 English, 76 Hindi, 11 Sanskrit), 1,442 chapters.
  - Not in the bank: subjects outside the catalog (e.g. Sociology, Informatics Practices, Home Science,
    Arts, Physical Education, vocational subjects), other mediums (Urdu and regional editions), and the
    superseded Class 8 books (NCF-2005 series).
- **Content:** 1,418 chapters, 9,225 topics, 46,237 questions (5 per topic: 2 easy, 2 medium, 1 hard;
  21,767 English, 20,065 Hindi, 4,405 Sanskrit), written from each chapter's NCERT text.
  - **0 are approved**: all are `needs_review`, and all are servable by the owner's decision.
  - 24 chapters have no topics: NCERT has no text for them or they are not chapters (the list is in
    [question_bank_coverage.md](question_bank_coverage.md), generated by `qbank_coverage.py --markdown`).
- **Not built:** an API for practice outside the chat (`create_test`, `create_adaptive` and the `qb_sets`
  tables are ready for one; My Learning's practice picker starts a test in the chat), a way to delete a set,
  or to report a question.
