# API contract (v1)

Machine-readable: [openapi.json](openapi.json) (generated from the code; a test fails if it drifts).
Base URL: the API listens on `127.0.0.1:4000` in development and `127.0.0.1:4100` in production, and is called by the web app's server, not by browsers.

## Conventions

- JSON bodies, **camelCase** fields. Unknown request fields are rejected (422).
- Auth: `Authorization: Bearer <session token>` on everything except `/health`, `/ready` and the
  sign-up/login/verify/reset endpoints.
- Optional `X-Request-ID` (`[A-Za-z0-9._-]{1,64}`); echoed back, otherwise generated.
- Request bodies are limited to 64 KB (413 `payload_too_large`), except the profile photo upload
  (`PUT /v1/me/avatar`: a raw image of at most `AVATAR_MAX_BYTES`, 5 MB by default) and a file
  upload (`POST /v1/files`: raw bytes of at most 25 MB).
- Resources owned by another user return **404**, exactly like missing ones.
- Errors: `{"error": {"code": "...", "message": "...", "requestId": "..."}}`, plus `fields` on 422
  and `current` on 409 `version_conflict`.

| HTTP | code | meaning |
|---|---|---|
| 401 | `unauthorized` | missing/invalid/expired session |
| 401 | `invalid_credentials` | wrong email or password |
| 403 | `email_not_verified` | log in after verifying the email code |
| 400 | `invalid_code` | wrong, expired or over-used email code |
| 404 | `not_found` | missing, or not yours |
| 409 | `conflict` | a reply is already generating in this conversation; nothing to regenerate |
| 409 | `version_conflict` | a versioned edit named an old `version`; `current` holds the resource as it is now |
| 409 | `current_session` | revoking the session in use (sign out instead) |
| 409 | `email_unavailable` | the new email address was taken before the change was confirmed |
| 413 | `payload_too_large` | body over 64 KB (the photo upload: over its own limit) |
| 415 | `unsupported_media_type` | a photo that is not JPEG, PNG or WebP |
| 400 | `invalid_token` | an unsubscribe link that this server did not sign |
| 429 | `too_many_open_requests` | 30 support requests are still open |
| 422 | `validation_error` | invalid body/query (`fields: [{field, reason}]`, values never echoed) |
| 429 | `rate_limited` | auth throttling or the per-user generation quota |
| 503 | `service_unavailable` | database or email temporarily unavailable |
| 500 | `internal_error` | unexpected; report the `requestId` |

## Auth and account

**Signing in is not in `/v1` any more.** Since 2026-10-01 accounts are created and signed in by the
web app's Better Auth (`/api/auth/*` on the web origin; `frontend/apps/app/src/lib/auth`): email
codes, password sign-in, Google, password reset and change. The API checks every request against
the same sessions (`Authorization: Bearer <session token>`, found by its SHA-256 in
`auth_sessions`), refuses an unverified address (403 `email_not_verified`) and keeps everything
below. `POST /v1/auth/{signup, verify-email, login, resend-code, password/forgot, password/reset,
password/change}` were removed in the same release that moved the web app (no other client existed;
the one deliberate exception to "additive" below). A future native client signs in at `/api/auth`
(docs/architecture.md, "Clients").

| Method | Path | Body | Success |
|---|---|---|---|
| POST | `/v1/auth/logout` | – | 204 (this session) |
| POST | `/v1/auth/sessions/revoke-others` | – | 204 |
| GET | `/v1/auth/sessions` | – | `{items: [{id, current, device, ipMasked, createdAt, lastSeenAt, expiresAt}]}`, most recently used first |
| DELETE | `/v1/auth/sessions/{id}` | – | 204 — signs that device out; 409 `current_session` for your own, 404 if not yours |
| GET | `/v1/me` | – | the user (below) |
| PATCH | `/v1/me/profile` | `{version, set: {displayName?, timezone?}, reset: [...]}` ([versioned edits](#versioned-edits)) | the user |
| PATCH | `/v1/me` | `{displayName}` | the user. **Deprecated**: use `/v1/me/profile`. It moves `profileVersion` on too |
| PUT | `/v1/me/avatar` | the raw image; `Content-Type: image/jpeg\|png\|webp` | the user, with a new `avatar.url` |
| DELETE | `/v1/me/avatar` | – | the user, `avatar: null` (idempotent) |
| GET | `/v1/me/avatar?v=<hash>` | – | `image/webp`, cacheable forever; 404 (never cached) unless `v` is the current photo |
| POST | `/v1/me/email/change` | `{newEmail, password?}` | 202 (always, [below](#changing-the-email-address)) |
| POST | `/v1/me/email/confirm` | `{newEmail, code}` | the user, with the new email; other sessions revoked |
| DELETE | `/v1/me` | `{password?}` | 204 — deletes the account and all its data, then emails the address that it was |
| GET / PATCH | `/v1/me/appearance` | PATCH [versioned](#versioned-edits): `theme` (system\|light\|dark), `fontSize` (small\|medium\|large\|x_large), `highContrast`, `reduceMotion`, `readingComfort`, `showStreak` | the appearance, the same on every device |
| GET / PATCH | `/v1/me/settings` | PATCH `{preferences: {...}}` (merged; `null` removes a key; ≤ 4 KB) | `{preferences, updatedAt}`. Unused by the web app; appearance is `/v1/me/appearance` |
| GET | `/v1/greetings` | – | `{weekOf, items}`: this week's study greetings, the same pool for every user (`items` empty when none is live; the web app then shows its own fallback). The web app shows a random one per new chat. The first request of a week whose pool is older than that week starts a background refresh by the LLM (10-15 checked lines replace the pool; any failure keeps it) and never waits for it. Weeks start Monday in `DEFAULT_TIMEZONE`; greetings are retired, never deleted |
| GET | `/v1/greetings/current` | – | `{text, weekOf}`: ONE greeting for the whole week (kept for older clients; 404 when none is live) |

The user: `{id, email, displayName, emailVerified, createdAt, profileVersion, timezone, avatar: {url} | null,
passwordChangedAt}`. `timezone` is an IANA name (`Asia/Kolkata`) or null. `passwordChangedAt` is null
until the password is first changed or reset.

**Re-authentication** (email change, account deletion): the account's password, or, for an account
without one (it signs in with Google or email codes), a sign-in within the last 15 minutes; otherwise
409 `reauth_required` (sign in again). An account with two-step sign-in needs both: the password and
a sign-in (both steps) within the last 15 minutes. Password checks share one per-account limit with Better Auth's
sign-in: 10 per 15 minutes, counted atomically, then 429 even for the right password.

**Signed-in devices** show a label parsed from the user agent ("Chrome on Windows", or "Unknown device")
and the network only (`203.0.113.x`, or an IPv6 /48). The raw IP address and user agent never leave the
server.

**Profile photo.** The server checks that the bytes are the declared type, refuses images over 40
megapixels before decoding them, turns the photo upright, crops it square and stores a new 256 px
WebP (at most 64 KB). Nothing of the original file is kept, including its EXIF data and GPS position.
`avatar.url` contains the photo's hash, so a browser may cache it forever; a replaced or removed
photo gets a new URL, and its old one answers 404. 10 uploads per 15 minutes; a changed photo does
not change `profileVersion`.

### Changing the email address

1. `POST /v1/me/email/change {newEmail, password?}` needs [re-authentication](#auth-and-account)
   (a wrong password counts as a failed sign-in). It always answers 202: if the address is free, a code goes to it; if another
   account has it, that address gets a notice instead, so the caller cannot tell which. A new request
   cancels the previous one. 3 requests per 15 minutes; the same address as now is 422.
2. `POST /v1/me/email/confirm {newEmail, code}` with a code sent for **this** account (15 minutes, 5
   attempts). The email changes and counts as verified, every other session is signed out, every
   pending code for the old and the new address is deleted (the API's and Better Auth's), and the old
   address is told (with the new one masked). If someone registered the address in the meantime: 409 `email_unavailable`.

### Versioned edits

A resource that can be edited from two places at once (two tabs, two devices) has an integer
version, and every edit sends the version it read. One shape, for every such resource:

```json
{"version": 3, "set": {"displayName": "Asha"}, "reset": ["timezone"]}
```

- `set` replaces the listed fields (a list replaces the whole list). A field not listed is unchanged.
- `reset` returns the listed fields to their defaults (empty, or `auto` for a learning preference).
- 422 for an unknown field, `null` inside `set` (use `reset`), a field in both, or a field twice.
- An edit that changes nothing (`set` and `reset` empty) answers 200 and keeps the version.
- Otherwise the version must be current: the edit applies and the version goes up by one, or it is
  refused whole with 409 `version_conflict`, whose `current` is the resource as it is now. The client
  applies its own pending change to `current` and sends it again, or asks the person.
- The answer is always the whole resource.

## Learning: what the tutor knows about the student

| Method | Path | Body | Success |
|---|---|---|---|
| GET | `/v1/catalog/academic` | – | the catalog (below). **No session needed**; `ETag`, `If-None-Match` → 304 |
| GET / PATCH | `/v1/learning/profile` | PATCH [versioned](#versioned-edits) | the learner profile |
| GET / PUT | `/v1/learning/subjects` | PUT `{version, items: [{code, isPriority, needsImprovement}]}` (≤ 15, replaces the list; order = display order) | `{version, items}` |
| GET / PATCH | `/v1/learning/preferences` | PATCH [versioned](#versioned-edits) | the preferences |
| POST | `/v1/learning/tutor-preview` | `{profile?, subjects?, preferences?}`: unsaved edits over what is stored | `{instructions, maxChars}`; nothing is saved; 60 per minute |

- **Learner profile:** `{version, grade, board, boardState, medium, academicYear, mainGoal, targetExam (≤ 80),
  targetScorePercent (0–100), weakTopics (≤ 10 × 60), dailyActiveMinutesGoal (0–720),
  weeklyActiveMinutesGoal (0–5040), preferredStudyTime ("HH:MM"), shortTermGoal (≤ 280), longTermGoal (≤ 280),
  examDate ("YYYY-MM-DD", from yesterday to 730 days ahead; additive, 2026-10-22), updatedAt}`. Nothing else
  about the student (no date of birth, gender, phone, school or country) is asked for. `examDate` sets the
  practice plan's exam window ([The practice record](#the-practice-record)).
  - `grade`, `board`, `boardState`, `medium` and `academicYear` are catalog codes: an unknown code is 422
    `unknown_code`. `boardState` needs `board: "state"` (422 `requires_state_board`).
  - Free text is stored on one line: runs of whitespace become one space; control characters are removed.
- **Preferences:** `language`, `explanationLevel`, `pace`, `difficulty`, `realWorldExamples`, `visualExplanations`,
  `personality`, `responseLength`, `answerMode`, `stepByStep`, `askBeforeAnswer`, `practiceQuestions`,
  `mistakeCorrection`, `highlightKeyConcepts`. Each is an enum whose default, `auto`, tells the tutor nothing.
- **The catalog** `{version, grades, boards, boardStates, mediums, academicYears, subjects}` lists
  `{code, name, deprecated}`. Codes are permanent. A `deprecated` code is still listed and still accepted while
  a profile keeps it, but can't be chosen anew (422 `deprecated`). Every content change bumps `version` and the
  ETag; clients refetch when the version they hold is older.
- **What the tutor is told** (`app/learning/tutor.py`): the frozen system prompt, plus a block that describes the
  student (catalog names; their own text only in quotes, as information, never as instructions) and lists one fixed
  sentence per preference that isn't `auto`. A student with nothing set, or everything on `auto`, gets exactly the
  tutor everyone got before. The block is capped at `maxChars` (6,000 characters, far above the worst case);
  nothing that identifies the student (name, email, photo) is ever included. The preview returns this block,
  computed by the same function the chat uses.

## Progress: active time in PadhAI

| Method | Path | Body | Success |
|---|---|---|---|
| POST | `/v1/progress/activity` | – | `{credited, date}`: one active minute; 6 reports per minute at most |
| GET | `/v1/progress?days=14` | – (`days` 7–90) | `{timezone, timezoneIsDefault, today, thisWeek, streak, goals, days}` |
| GET | `/v1/progress/streak` | – | `{currentStreak, longestStreak, todayActive, todayDate, timezone, dayEnd, previousStreak, lastLearningDate}`: the learning streak (India days with a meaningful, fully answered question, or 5 answers to question-bank tests; app/progress/learning.py). `previousStreak` is the streak that ended, > 0 only once `currentStreak` is 0 after a whole missed day (never while today is pending); `lastLearningDate` the latest learning day, null before the first |

- **What is measured:** time active in PadhAI, not study time. A client reports a minute while PadhAI is open and
  visible and the student interacted with it (or a reply was streaming) within the last two minutes. The server
  credits at most one minute per UTC minute per user: other tabs and devices reporting the same minute get
  `credited: false`. A student can inflate their own number; it is never compared with anyone else's.
- **Questions** are counted when the chat accepts a message; regenerating a reply is not a new question.
- **Days** are the student's: their `timezone` (`PATCH /v1/me/profile`), or the server's `DEFAULT_TIMEZONE`
  (`Asia/Kolkata`) until they choose one (`timezoneIsDefault: true`). A change of time zone doesn't rewrite
  past days. Weeks start on Monday.
- **Streak:** a day is active with at least one minute or one question. `currentDays` counts active days up to
  today, or up to yesterday while today has none yet; `longestDays` is the best run so far. Streaks start at
  the release that introduced them (no backfill).
- **Goals** are the daily and weekly active-minute goals from the learner profile; `dailyMet`/`weeklyMet` are
  null without a goal.
- `today`/`days[]`: `{date, activeMinutes, questions}`; `days` lists the last `days` days, oldest first,
  including days without activity.

## The practice record

What the student's answers to question-bank tests in the chat say (`app/progress/practice.py`; additive,
2026-10-22). Every endpoint is the signed-in student's own, read-only, and takes no ids. Dates are India
learning days (`Asia/Kolkata`). Every number is an estimate from practice, never a mark.

| Method | Path | Success |
|---|---|---|
| GET | `/v1/progress/plan` | `{date, timezone, budgetMinutes, budgetSource: goal\|default, insufficientHistory, needsProfile, examDate, daysToExam, answeredToday, answersForLearningDay, summary: {answered, practisedConcepts, masteredConcepts, weakConcepts, dueRevisions, overdueRevisions, recurringMistakes}, tasks: [...]}` |
| GET | `/v1/progress/mastery` | `{model: {method: "bkt", prior, learn, guess, slip, masteredAt, minAnswers, minQuestions}, answered, revisedAnswers, subjects: [{subjectCode, classLevel, language, estimate, coverage, chapters: [{chapterId, number, title, bookTitle, estimate, coverage, topicCount, topics: [{topicId, number, title, estimate, state, coverage, conceptCount, concepts: [{conceptTag, objective, estimate, state, answered, correct, distinctQuestions, lastAnsweredAt}]}]}]}]}` |
| GET | `/v1/progress/revisions` | `{date, ladderDays: [1, 3, 7, 14, 30, 60], items: [{topicId, topicTitle, topicNumber, chapterId, chapterTitle, chapterNumber, bookTitle, subjectCode, classLevel, state: overdue\|due\|done\|upcoming, dueDate, overdueDays, intervalDays, recall, lastReviewed, lastAccuracy, reviews, estimate, prompt}]}` |
| GET | `/v1/progress/mistakes?limit=50` (1–200) | `{recurringRule: {wrong: 2, days: 14}, concepts: [{topicId, topicTitle, chapterId, chapterTitle, chapterNumber, bookTitle, subjectCode, classLevel, conceptTag, objective, wrong, wrong14d, recurring, state, prompt, items: [{questionId, answeredAt, stem, options, chosen, correct, explanation, conversationId}]}]}` |
| GET | `/v1/progress/practice-tests?limit=20` (1–100) | `{items: [{setId, messageId, conversationId, title, chapterIds, startedAt, total, served, answered, correct, open}]}`, newest first |
| GET | `/v1/progress/practice-topics` | `{available, reason: no_class\|no_questions\|null, classLevel, language: english\|hindi, subjects: [{subjectCode, books: [{bookId, title, language, chapters: [{chapterId, number, title, questions, topics: [{topicId, number, title, questions}]}]}]}]}`: the bank's chapters with servable questions for the student's class, in their medium, plus the English, Hindi and Sanskrit books; their own subjects first |

- **Evidence:** an answer to a question of the student's practice sets, graded by the database: only the
  first answer to a question on a day counts, and an answer to a question edited since it was served is set
  aside (`revisedAnswers`). Skips, flashcards and the tutor's own fill-in-the-blanks exercises never count.
  Deleting a chat keeps its answers (`conversationId` and `messageId` become null); deleting the account
  deletes them.
- **Mastery** per concept is Bayesian knowledge tracing (the `model` values): `mastered` at 0.95 with 3
  answers to at least 2 of its questions (or all it has), `weak` below 0.5 after a wrong answer,
  `not_enough_evidence` under 3 answers, else `learning`. A topic is mastered when all its concepts are.
  Topics, chapters and subjects show the mean over what was practised, and `coverage`: how much of it was.
  Only practised ones are listed.
- **Revision** per topic: a day with answers is a review, recalled with 75% right. The next review is 1, 3,
  7, 14, 30 or 60 days after the last, one step further for each recalled day in a row, back to 1 day after a
  lapse, and at most 3 days while the topic is weak. States are scheduled from the days before today, so
  they hold all day (`done`: reviewed today while due or overdue). Ordered overdue, due (each the lowest
  `recall` first, then the weaker topic), done, then upcoming by `dueDate`.
- **The plan:** at most 6 tasks (`revise`, `strengthen` or `learn`; `reason`: `overdue`, `due`,
  `weak_concept`, `next_topic`, `start_here`) of 5 questions (about 8 minutes) each, within the daily goal
  (`budgetMinutes`; 30 without one): revisions due, then weak topics (the weakest first), then the next new
  topic of each of the student's subjects (in a chapter already begun first, then in book order). Within 30
  days of `examDate` at most one new topic, and none in the last 7. Chosen from the answers before today, so
  it holds all day; today's answers fill in `answeredToday` and `done`. `needsProfile`: no class 6–12, or
  none of the student's subjects is in the bank (then `learn` tasks are `start_here`, over the bank's subjects).
  A task's `prompt` (also a revision's and a mistake's) is the request a client puts in a new chat for the
  student to send, e.g. `Test me on Class 10 Science (Science) Chapter 2 "Acids, Bases and Salts", topic "pH
  scale": 5 questions.`

## Study time: estimated, in the sessions a student starts

| Method | Path | Body | Success |
|---|---|---|---|
| GET | `/v1/study/session` | – | `{session, lastEnded, idle, serverTime}`: the open session (active or paused) and the latest that ended in the last 24 h, as of now. Only reads |
| POST | `/v1/study/session` | `{clientId, subject?, chapterId?, activity}` | the same: starts a session, or joins the one already open (as it is) |
| PATCH | `/v1/study/session/{id}` | `{clientId, expectedSegmentId, subject?, chapterId?, activity}` | the same: a new subject, chapter or activity |
| POST | `/v1/study/session/{id}/pause` · `/resume` · `/end` | `{clientId, expectedSegmentId}` | the same |
| POST | `/v1/study/beat` | `{beatId, clientId, wasActive, nowActive, studyView, visible}` | the same |
| GET | `/v1/study/summary?days=7` | – (`days` 1–92) | `{timezone, from, to, firstRecordedDate, totals, days, subjects, chapters, activities, sessions}` |
| GET | `/v1/study/chapters?subject=` | – | `{available, reason, chapters}`: NCERT chapters for the student's class (6–12) in that subject |

- **A client** is one page load (a tab or a device): a new `clientId` each time, never stored. It beats every 30 s
  and at once whenever it starts or stops studying. `wasActive`: it was studying the whole time since the server last
  heard from it (visible, on a learning view, session running, not idle); `nowActive`: it is now.
- **Credit** (server time only) for a client's `[previous observation, now)` needs both observations active and at
  most 75 s apart; it is clipped to the session's studying time and added only where no other client already
  covered it, so tabs and devices never add up. Hidden, idle, paused, offline and unobserved time is never credited;
  two or more lost beats in a row lose that span. A repeated `beatId` changes nothing (retry with the same one).
- **Transitions** are idempotent by target (pausing a paused session is a 200); `expectedSegmentId` older than the
  session's current segment is `409 study_state_changed` (another tab or device changed it: reload); acting on an
  ended session is `409 study_session_ended`; another student's session is 404. Start, pause, resume, end and
  context changes count 30 a minute per student, beats 30 a minute.
- **Ends on its own** at the last study activity after 15 minutes without one (an hour after a pause), and after
  8 hours (`endReason`: `stale`, `limit`; `ended` when the student ends it). Reads show this at once; the next write
  stores it.
- **Totals:** `activeSeconds + idleSeconds + pausedSeconds = durationSeconds`, exactly (idle is studying time not
  credited). Days are each session's own time zone; `days[]` also has `appSeconds` (PadhAI open, visible or not;
  a gap over 3 minutes starts a new app session). Days before `firstRecordedDate` are not listed: no data, not zero.
- **Idle limits** (`idle`): reading and revision `STUDY_IDLE_READING_S` (300 s), practice and tests
  `STUDY_IDLE_SOLVING_S` (120 s), the same for every student. Goals stay on active time (above).

## Notifications

| Method | Path | Body | Success |
|---|---|---|---|
| GET / PATCH | `/v1/notifications/preferences` | PATCH [versioned](#versioned-edits): `dailyReminder`, `streakWarning`, `weeklySummary`, `securityAlerts` | `{version, dailyReminder, streakWarning, weeklySummary, securityAlerts, remindersNeedTimezone, delivery, updatedAt}`; `delivery` is `paused` while this server sends no notification email to the user (its rollout), `active` otherwise |
| POST | `/v1/notifications/unsubscribe` | `{token}` from an email's link. **No session needed** | `{kind, unsubscribed: true}`; using it again changes nothing |
| GET | `/v1/notifications?limit=30` | – | The bell: `{items: [{id, kind, title, body, link, createdAt, readAt}], unreadCount}`, newest first, at most 30, never a cleared one. `unreadCount` counts every unread one, also beyond the 30 listed |
| POST | `/v1/notifications/read` | `{ids?}` (≤ 30) | 204. The listed ones read, or, with no `ids`, every one of the student's ("Read all", also beyond the 30 listed). Someone else's ids match nothing. Idempotent |
| POST | `/v1/notifications/clear` | – | 204. "Clear all": every one of the student's leaves the list and counts as read; new ones still arrive. Idempotent, and safe alongside Read all (additive, 2026-10-01) |

- **The bell:** opening it reads nothing; the web app marks a notification read when it is chosen. A cleared
  notification keeps its row (and its dedup key) until retention deletes read ones after 90 days, so the same
  reminder or alert is never made again because it was cleared.
- **Emails:** all are plain text, and none contains an IP address.
  - Reminders are off until chosen: a daily reminder at the learner profile's `preferredStudyTime` (or 18:00), a
    streak warning at 20:00–22:00, and a weekly summary on Monday mornings. All three need a confirmed `timezone`,
    and each is sent at most once per period.
  - Sign-in alerts (on by default) are sent for a sign-in from a device the account has no live session on.
  - A password change or reset is always reported, and has no unsubscribe link.
- **Delivery:** emails are sent by the jobs worker, only as its rollout allows (`NOTIFICATIONS_MODE`,
  [deployment.md](deployment.md#jobs-worker)), at most once, and never later than a day for alerts.
- **Unsubscribe tokens** name one user and one kind of email, signed with the server's key. They don't expire.

## Support

| Method | Path | Body | Success |
|---|---|---|---|
| POST | `/v1/support/requests` | `{requestId (uuid, chosen by the client), kind: question\|bug\|feedback\|account, subject (≤ 120), message (10–4000), page?}` | 201 `{id, requestId, kind, subject, status, createdAt}`; the same `requestId` again → 200, the same request |

- **Limits:** 5 per hour and 20 per day, and at most 30 open (`too_many_open_requests`).
- **Text:** the subject loses line breaks and control characters; the message keeps line breaks. At most 5 links.
- **Forwarding:** requests are forwarded to the team's address through the outbox, and kept until the account is
  deleted.

## Conversations

| Method | Path | Notes |
|---|---|---|
| GET | `/v1/conversations?limit=30&cursor=` | newest activity first → `{items:[{id,title,createdAt,updatedAt}], nextCursor}` |
| POST | `/v1/conversations` | `{title?}` → 201 (title defaults to "New chat"; see naming below) |
| GET | `/v1/conversations/{id}` | |
| PATCH | `/v1/conversations/{id}` | `{title}` (1-120 chars) — rename |

Naming: a conversation starts as "New chat" and is named once, in at most six words, when a reply to a
meaningful question completes (the `done` event carries the new title; small talk, a stopped or failed
reply names nothing). The name comes from the question ("Photosynthesis Explained", "Mitosis vs
Meiosis"), else the reply's first heading, else the question's own words, with no second model call.
A title the user set is never changed.
| DELETE | `/v1/conversations/{id}` | 204; messages and feedback are deleted with it |
| GET | `/v1/conversations/{id}/messages?limit=50&before=` | oldest-first page; `nextCursor` loads older messages |

Message: `{id, conversationId, role: user|assistant, content, status, createdAt, metadata: {model?, errorCode?, finishReason?, citations?, assessments?, assessmentError?, nhprep?}}`.
`nhprep`: an NHprep lesson card ([NHprep video courses](#nhprep-video-courses); additive, 2026-11-20).
`assessments` and `assessmentError`: a reply's practice test (below, [Practice tests](#practice-tests); additive, 2026-10-01).
`citations` (a reply whose prompt numbered sources or attached files; additive, 2026-09-30):
`[{ref: "1" | "F1", kind: "source" | "file", title, fileId?}]`, what the reply's `[1]` and `[F1]`
markers point at. Titles only, never URIs; absent when nothing was numbered. It arrives with the
finished message (`done` and message lists), not while the reply streams.
`status`: `complete`, `streaming`, `cancelled` (stopped, partial text kept), `error` (partial text kept).
`content` is stored exactly as produced (Markdown, code fences, tables, `$math$`).

## Chat

| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/v1/chat/stream` | `{conversationId?, message (1-16000), attachmentIds? (≤ 5 ready files), mode? ("text" \| "voice", default "text"; added 2026-10-23: see Voice)}` | `text/event-stream` (below). No `conversationId` → a new conversation. A file that is not the student's, or not `ready`: 422 `fields: [{field: "attachmentIds", reason: "not_found" \| "not_ready"}]` |
| POST | `/v1/chat` | same | 200 `{conversation, userMessage, message}`; LLM errors → 502/504/422 with the error envelope |
| POST | `/v1/conversations/{id}/regenerate/stream` | – | SSE; the last reply becomes `superseded` and a new one streams |
| POST | `/v1/messages/{id}/cancel` | – | `{status}` — stops a `streaming` reply (idempotent) |
| PUT | `/v1/messages/{id}/feedback` | `{rating: up\|down, comment?}` | `{messageId, rating, comment}` (assistant messages only; upsert) |
| GET / DELETE | `/v1/messages/{id}/feedback` | – | read / remove your feedback |
| POST | `/v1/messages/{id}/assessment-responses` | `{position, hash, question, action: answer\|skip\|save\|clear\|submit, value? (answer and save only: an option index, one text per blank, or a newer kind's value), revision? (submit only)}` | The test as recorded (below), and `learning` (additive, 2026-10-22): `{event: started\|extended\|milestone, streak}` when this answer made its India day a learning day, else null. 409 `assessment_answered` / `assessment_out_of_order` / `assessment_changed` / `assessment_closed`, and for schemaVersion 2 (2026-11-18) `assessment_not_exam` / `assessment_exam` / `assessment_submitted` / `assessment_stale`; 422 no such question or option (v2: `fields: [{field: "value", reason, question}]`); 429 `rate_limited` (120 a minute per user); 404 not the student's reply or no such test |

### Practice tests

A reply may hold one practice block: a test (one ```` ```quiz ```` block, or one ```` ```mcq ```` for a
single question) or an exercise (one ```` ```fill-blank ```` with `[[answer|alternative]]` blanks). The
tutor writes them when `TUTOR_INTERACTIVE_BLOCKS` is on. With `QUESTION_BANK_TESTS` on (the default since
2026-10-16), a test's questions come only from the question bank: the tutor names a chapter in a
```` ```question-bank ```` block, the server replaces it with a ```` ```quiz ```` of stored questions
before the reply is saved, and a test block the tutor writes itself becomes a note; a fill-in-the-blanks
exercise it writes is kept (2026-10-22), put in its fence if it wrote one without (2026-10-23)
(docs/question_bank.md, "Tests in the chat"). The server owns them
(`app/assessments/state.py`):

- **Built with the reply, whole or not at all.** When a reply completes, its block is checked and
  saved by the same write as the reply, in `metadata.assessments`. A test needs 1-30 questions, 2-8
  distinct options each, an answer naming exactly one option, and the number of questions the
  student asked for. An exercise needs 1-20 blanks of at most 80 characters, each with accepted answers
  and none of them empty (2026-10-22: `[[a|]]` is refused, not read as `[[a]]`). A reply that wrote
  a block the server can't accept has none, and `metadata.assessmentError: {code}` (`multiple_tests`,
  `nested_test`, `incomplete_test`, `invalid_json`, `invalid_test`, `invalid_question`,
  `too_many_questions`, `count_mismatch`). Regenerate is the retry. A reply saved before its kind of block
  was recorded (tests before 2026-10-01, exercises before the same day's second release) has neither
  stored: its record is built from its text when read (the same each time, so it can be answered;
  the first answer stores it). A block the server can't accept stays read-only.
- **An item of `assessments`:** `{id: "<messageId>:<position>", position, schemaVersion: 1,
  kind: test|exercise, title, hash, locator, state: not_started|active|completed, answerable, total,
  answered, current, earned, possible, questions: [...]}`. Every question has every field, null
  where it doesn't apply: `{kind: mcq|fill_blank, text, options, segments, blanks, response,
  correct, answers, explanation}`. A multiple-choice question has `text` and `options`; an exercise
  (one question) has `segments`, the text around its `blanks`.
  - `response` is `{status: answered|skipped, value, earned, possible, at, marks}` or null. `value`
    is the chosen option's index, or what was typed into each blank; `marks` says which blanks were
    right (exercises only).
  - `correct` (multiple choice), `answers` (the accepted answers of each blank) and `explanation`
    are null until the question has a response.
  - `hash` is the sha256 of the block's canonical form; `locator` is the sha256 of its source,
    trimmed (which block it is). `current` is the waiting question (null once completed).
  - A test question is worth 1; an exercise is worth one per blank. A skip earns 0.
- **Answering:** only the waiting question, only in a `complete` reply (a regenerated one is closed
  with its block), naming the `hash` it was shown. `value` is an option's index for a multiple-choice
  question, or one text (at most 200 characters) per blank for an exercise, graded ignoring case,
  spacing and a trailing `.;,!`. Answers are final. The same response sent again returns the record
  unchanged (a safe retry); any other response to an answered question is 409. The questions and the
  grading never come from the client.
- **A question-bank test grows** (2026-10-22): it starts with its first question (`questions` has one,
  `total` says how many it will have), and each response adds the next one, chosen from that answer, in
  the same transaction; the response returns the grown test with its new `hash`. A response names the hash
  of the questions it was shown, so a retry after a lost reply (with the old hash) is the same success and
  returns the test as it is now; a question not chosen yet is 409 `assessment_out_of_order`. If the bank has
  nothing left, `total` shrinks to the questions it has and the test is complete. Clients draw only what the
  record holds, so nothing else changes for them. Answers to these tests are the student's practice record
  ([The practice record](#the-practice-record)), and every 5th answer of an India day counts as one fully
  answered question, so 5 make a learning day: that response's `learning` says so, as `done.learning` does
  (null on every other response, and on a replay).
  Fill-in-the-blanks exercises the tutor writes are never practice evidence.
- **The tutor** is told the chat's practice record on every turn, the latest first, in words checked
  against the model (`scripts/eval_tutor_record.py`): finished or not, the score, every answer,
  and what to do when asked how they did. Flashcards, graphs and diagrams are not recorded, and the
  tutor is told so.

#### Newer kinds, practice and mock exams (schemaVersion 2, 2026-11-18)

A ```` ```quiz ```` is **schemaVersion 2** when it uses anything beyond the v1 shape: a newer `type`, a `listing`,
a `mode` or a `scenario` (`app/assessments/kinds.py`, `rules.py`, `modes.py`). Anything else, including an absent
or unknown `type`, is read as before, with the same record and `hash`. ```` ```mcq ```` and
```` ```fill-blank ```` are unchanged.

```
{title?, mode?: "practice" | "exam", scenario?: {title?, summary, symptoms[], evidence[{label, title?, language?, content}]},
 questions: [{type?, question, listing?: {title?, language?, content}, explanation?, ...}]}
```

| `type` (kind) | the author writes | the value a client sends | graded | shown once final |
|---|---|---|---|---|
| none (`mcq`) | `options` 2-8, `answer` | option index | exact, 1 pt | `correct` |
| `multi` | `options` 2-8, `answer[]` | indices (sorted; a duplicate is 422) | exact set, 1 pt; `marks` per option | `correctList` |
| `fill` (`fill_blank`) | `text` with `[[a\|alt]]` | one text per blank | per blank (the v1 rule) | `answers` |
| `numeric` | `answer`, `tolerance?` (amount, or `"n%"`, 0 < n ≤ 100), `unit?` | text `-?[0-9]{1,30}(\.[0-9]{1,9})?` | within the tolerance, 1 pt | `accepted`, `tolerance` |
| `short` / `long` | `keyPoints` (1-6 / 1-10, each a form or a list of forms), `sample?` | text ≤ 1,000 / ≤ 6,000 | 1 pt per key point found | `keyPoints`, `sample` |
| `match` | `pairs [[left, right]]` 2-8, `extra?` ≤ 3 | a choice index (or null) per left item | 1 pt per pair | `correctList` |
| `order` | `items` 3-8, in order | a permutation of the shown indices | exact, 1 pt; `marks` per position | `correctList` |
| `cli` | `prompt`, `answers` 1-8 (every accepted form), `output?` | text ≤ 300 | the CLI rule, 1 pt | `accepted`, `output` |
| `lines` (`select: "lines"`) | `listing`, `answer` (1-based line numbers) | 0-based line indices (sorted) | exact set, 1 pt | `correctList` |
| `diagram` | `topology {nodes[{id, label, kind, detail?}], links[{id, from, to, label?, detail?}]}`, `select: node\|link\|any`, `answer[ids]` | ids (sorted) | exact set, 1 pt | `accepted` |

- **Rules (refused whole at build, `invalid_question`/`invalid_test`):** duplicates ignoring case (options, items,
  left items, right items with extras, topology ids); a dangling link; caps (a listing ≤ 200 lines and 8,000
  characters, counted after CR LF → LF and one trailing newline dropped; a scenario summary ≤ 1,000, ≤ 8 symptoms
  of ≤ 200, ≤ 4 evidence listings; ≤ 8 forms per key point, 2-80 characters each once normalised, no form in two
  points; an item ≤ 200; a CLI prompt ≤ 60 and forms ≤ 300; a unit ≤ 20; a diagram ≤ 30 nodes and ≤ 40 links,
  labels ≤ 40, details ≤ 300; an explanation ≤ 2,000; the record's canonical JSON ≤ 64 KB). A numeric answer
  and tolerance must fit the value grammar (stored as fixed-point text); `%` of a zero answer is refused.
- **Shown order:** `match` choices and `order` items are shown sorted by `sha256("<locator>:<question>:<i>")`,
  rotated by one when that would be the answer itself. Values index the shown order.
- **Written answers:** both sides are normalised (NFKC, casefold, `-`/`_` as spaces, spacing collapsed); a form
  matches as a phrase, bounded by non-word characters only where it starts or ends with a word character ('IP'
  is not in 'ship'). No stemming: authors list every form. Forms are tried longest first across all key points,
  and the text a match used is masked, so a phrase inside a longer key point's matched form doesn't count again
  ('SYN-ACK' found for one point is not also 'SYN' or 'ACK'; 'TCP/IP' is not also 'IP'). Each point counts once.
- **CLI:** token by token against each accepted form with as many tokens (an interface type and its number,
  `gi 0/1`, are joined into one token first). The author's token decides how the typed token at that place is
  read: as a keyword from a closed table (any accepted prefix, in any case: `sh ip int br`), as an interface (any
  anchored abbreviation of its type: `gi0/1`), or otherwise as an exact argument. An author's word is an
  interface only when it can't be a name: a type of two or more letters, or a slot in its number (`Gi0/1`,
  `g0/1`, `Lo0`, `Ethernet1`); a one-letter interface type with a plain number, like `E1`, is an exact argument
  (`hostname E1` does not accept `hostname e1`).
- **Record (v2 only, every field present):** `mode`, `submitted` (exam: graded and revealed; practice: null),
  `revision` (bumped by every change, never by a replay), `scenario`, `pending` (practice: the waiting question's
  wrong tries, `{question, tries}`). Each question has `choose, unit, prompt, left, right, items, listing,
  diagram, select, points, maxLength, maxTries, draft` before it is final, and `correctList, accepted,
  tolerance, keyPoints, sample, output` (with `explanation`) only once final. A response adds `answer` (a newer
  kind's value; `value` is null for those) and `tries` only when set. A **schemaVersion 1 record is shown
  exactly as before**: none of these fields.
- **Practice:** `answer` and `skip` ("Show answer", 0 points), in order and final. A `numeric` or `cli` question
  allows 3 tries: a wrong try is 200 with `pending` and reveals nothing; the third, or a right one, is final and
  its earlier tries move into `response.tries`. The same try sent again is a replay.
- **Mock exam:** `save` (`value` checked as `answer` would be: well formed, not necessarily complete) and `clear`
  keep one ungraded `draft` per question, in any order, last write wins; at most 64 KB of drafts per test (past
  it: 422 `drafts_full`). `submit` (question 0, optional `revision`: a stale one is 409 `assessment_stale`)
  grades every draft and records the empty ones as skipped; it is never 422. Nothing (`correct...`,
  `explanation`) is shown before it. `answer`/`skip` are 409 `assessment_exam`; after submit, any change is 409
  `assessment_submitted`. `current` is null; the test is `active` once anything is saved.
- **Order of checks:** a request that would not change the record (the same draft, clearing an empty draft, a
  submit after submit, an exact replay) returns it unchanged, 200, before any rule. **save, clear and submit are
  v2 only:** on a v1 record (bank tests, fill-blank exercises) or a practice test they are 409
  `assessment_not_exam`, and nothing is recorded.
- **422 (v2):** `message` names the question ("Question 3: ..."); `fields: [{field: "value", reason, question}]`
  with `reason` one of `question`, `value`, `number`, `duplicate`, `too_long`, `drafts_full`. Values are never
  echoed or logged.
- **Rate limit:** 120 requests a minute per user (`429 rate_limited`), checked before the reply is locked.
- **The tutor** sees a v2 quiz in the chat history as exactly what a client sees before answering (no keys,
  explanations or outputs). While a mock exam is not submitted, the summary starts with a guard (nothing is
  graded; never reveal or hint at answers). Points and questions are told apart ("earned 7 of 12 points; 3 of
  5 questions fully right, 1 partly").
- **Client obligation:** draw only records whose `schemaVersion` you know (today 1 and 2); otherwise show the
  block's source.

### Server-Sent Events

```
: open
event: meta
data: {"conversationId","conversation":{...},"userMessage":{...}|null,"assistantMessageId","replacesMessageId"|null}

event: citations                         (at most once, before the first token; only when the
data: {"messageId","citations":[...]}      prompt numbered sources or files: the Message
                                           `metadata.citations` shape, which `done` repeats)

event: expression                       (voice turns only, at most once, before the first
data: {"messageId","expression"}           token: neutral|happy|explaining|focused|thinking|
                                           encouraging, the tutor face's manner; see Voice)

event: speech                           (voice turns only, VOICE_SERVER_SPEECH: the reply's
data: {"messageId","state","chars",        first words spoken by the server. `planned` names
       "text","type","audio"}              them (its first `chars` characters, said as `text`);
                                           then `ready` (`audio` base64 of `type`) or `failed`
                                           (the client speaks them); `skipped`: none. Before `done`)

event: token
data: {"text":"partial text"}            (repeated)

: keepalive                              (every 15 s without output)

event: done
data: {"message":{...final assistant message...},"conversation":{...},
       "learning":{"event":"started|extended|milestone","streak":7}}   (learning: see below)
  — or —
event: error
data: {"code":"llm_unavailable|llm_timeout|llm_bad_request|service_unavailable|internal_error",
       "message":"safe, user-facing text","retryable":true|false,"messageId","requestId"}
```

- Concatenated `token.text` equals `done.message.content`.
- `done.learning` (optional, added 2026-10-14) is present only on the answer that made its India day a
  learning day, once per day per student, after it was saved: `started` (day 1 of a new streak),
  `extended`, or `milestone` (every 7th day: 7, 14, 21, …, which takes `extended`'s place). Clients
  celebrate it; it is never repeated, so a client that missed it (a dropped connection) shows nothing
  later. An answer counts for the day its question was asked.
- Stopping: call `POST /v1/messages/{assistantMessageId}/cancel`; the stream then ends with `done`
  whose message has `status: "cancelled"`. Closing the connection also stops the reply (same result).
- Unknown event types may be added later and must be ignored by clients (`citations` was, on
  2026-09-30; `expression`, on 2026-10-23; `speech`, on 2026-10-26).

## Files (the File Library)

Every student has 100 MB (104,857,600 bytes) for files, counted over every file whatever its state.
A file is read in the background: `processing` → `ready` (the tutor can use it) or `failed`
(`errorCode`, and `retryable`); `stored` means it is kept but files cannot be read right now.

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/v1/files?sha256=` | – | `{items: FileOut[] (newest first), usage: {usedBytes, quotaBytes, remainingBytes}}`; with `sha256`, only the file with those bytes (0 or 1): lets a client skip an upload |
| POST | `/v1/files?filename=` | the file's raw bytes (`application/octet-stream`, ≤ 25 MB) | 201 `FileOut` (stored, being read), or 200 with the file already holding these bytes |
| GET | `/v1/files/{id}` | – | `FileOut` (poll while `processing`) |
| GET | `/v1/files/{id}/content` | – | The file's own bytes as its type (PDF, the three Office types, CSV, text, Markdown, PNG, JPEG, WebP), streamed; `Cache-Control: private, max-age=3600`, `ETag` = its SHA-256. The owner only (404 otherwise). The web app shows previews with it |
| POST | `/v1/files/{id}/retry` | – | `FileOut`; 409 `retry_not_allowed` unless it failed for a reason a retry can fix and had fewer than 3 attempts |
| DELETE | `/v1/files/{id}` | – | 204. Frees its storage; messages keep its name with `fileId: null` |

`FileOut`: `{id, filename, kind: pdf|docx|pptx|xlsx|csv|txt|md|image, sizeBytes, status, errorCode, retryable, createdAt, updatedAt}`.
Types by extension (the content must match): PDF, DOCX, PPTX (25 MB), XLSX, PNG, JPEG, WebP (10 MB),
CSV, TXT, MD (2 MB). Upload errors: 415 `unsupported_file_type` / `type_mismatch`, 413
`file_too_large`, 409 `storage_quota_exceeded` / `too_many_files` (500), 429 `too_many_pending_files`
(5 being read) / `rate_limited` (20 per 15 min, 30 a day), 503 `files_unavailable` /
`files_capacity_reached`. `errorCode` of a failed file: `corrupt`, `encrypted`, `no_text`,
`unreadable_text`, `invalid_encoding`, `too_many_pages`, `too_many_scanned_pages`, `too_many_rows`,
`content_too_large`, `timeout`, `processing_interrupted` (final); `embedding_failed`,
`extractor_failed`, `chunk_limit_reached`, `files_capacity_reached`, `model_changed` (retryable).
A message's `attachments`: `[{fileId | null, filename}]` (user messages).

## Voice

Voice mode (`app/voice`, added 2026-10-23): the student speaks, the API transcribes it with OllaLink, the
client sends the words as an ordinary chat message with `mode: "voice"`, and reads the reply aloud one
sentence at a time with OllaLink's speech. **English only** (OllaLink speaks no Hindi), so it is offered to a
student whose tutor language is English or unset, and only as the rollout allows (`VOICE_MODE`
`off` → `qa` → `on`, docs/deployment.md). Every voice route checks both itself: for anyone else it is a 404
and nothing is sent to OllaLink.

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/v1/voice` | – | `{enabled, ack, serverSpeech}`: whether to offer voice mode (for showing a button; decides nothing), whether to say a short pre-recorded word (the web app's own audio) when the student stops (`VOICE_ACK`), and whether the server speaks a voice reply's first words itself (`VOICE_SERVER_SPEECH`: the stream's `speech` events; the client then waits for them before its first chunk). Both rolled out like `VOICE_MODE` |
| POST | `/v1/voice/transcriptions?seconds=` | the raw recording: `audio/webm` (Opus), `audio/mp4` (AAC or Opus) or `audio/wav`, up to 1 MiB (`x-max-bytes`) and 60 s | `{text, seconds}`; `text` "" when nothing was heard. 415 another type, 413 too big, 422 `voice_unreadable` |
| POST | `/v1/voice/speech` | `{messageId (the student's own reply), text (1-600), format?}`: `format` `mp3` (default), `opus` or `aac`, what the client can decode | `audio/mpeg`, `audio/ogg` (Opus) or `audio/aac` (ADTS) as asked, `Cache-Control: no-store`. 404 not the student's reply |

- A voice message is a typed one in every other way (quotas, scope, intents, search, learning day, title).
  Its chat request may say `speechFormat` (`mp3`, `opus`, `aac`): the audio of the server's first words.
  With `VOICE_PARALLEL_ROUTING` the study scope, the intent router and the search start together rather than
  scope first: the same requests on the same words, so the same decisions (measured faster; docs/deployment.md).
  For a student voice mode is not for, `mode: "voice"` is ignored. A voice turn's prompt has one more line
  (spoken style: short sentences, no tables or code blocks, maths in words), and its stream an `expression`
  event: the face's manner from the study scope and the intent router (worry → encouraging, unclear →
  thinking, explain/materials/visual → explaining, solve/tests/feedback/planning → focused, a greeting →
  happy, anything else → neutral). Only that word is sent, never the intent or a confidence.
- Limits per student (counted in `auth_attempts`, so they hold across processes): transcriptions 20 a minute
  and 400 a day, speech 120 a minute and 3000 a day (429 `rate_limited`); at once, counted in the API's one
  process, 1 transcription and 2 speech requests per student and 8 and 16 in all (429 / 503 `voice_busy`);
  8000 characters of speech per reply (429). OllaLink failing is 503 `voice_unavailable`.
- Nothing is stored: not the audio, not the speech. Logs carry sizes and times only. A client that goes away
  cancels its OllaLink call.

## NH programs

The NH program area (`app/programs`, 2026-11-23). Open only while `PROGRAMS_MODE` admits the account (`off`, `qa`
with `PROGRAMS_QA_EMAILS`, `on`), and a program only while the account holds a claim on an invoice for it whose row
in `nh_invoices` is `active` and not past `valid_until`, checked on every request. Anything else is 404 `not_found`.
Every response is `Cache-Control: no-store`.

| Route | Answer |
|---|---|
| `GET /v1/programs` | `{enabled, invoicePrompt, programs: [{slug, title, months: [{month, title, material}]}]}` |
| `POST /v1/programs/claims` `{invoiceNumber}` | 201 claimed, 200 already this account's; 404 `invoice_not_verified` for every failure (unknown, inactive, expired, claimed by someone else); 429 `rate_limited` |
| `POST /v1/programs/invoice-prompt/dismiss` | 204; the first-run question is not asked again |
| `GET /v1/programs/{slug}` | the program and its months |
| `GET /v1/programs/{slug}/months/{month}` | the month: material counts, NHprep lesson ids, bank topics and whether Quiz me has questions |

**Program chats.** The first `POST /v1/chat[/stream]` of a new chat may name `{program, month (1-12 or null for the
program's Q&A), tutorMode: "tutor"|"interview"}`; with a `conversationId` these fields are 422. The server checks
access and binds the conversation; every later turn reads its scope from the stored conversation, never from the
request. A month chat retrieves only that month's mapped material, with no fallback. `ConversationOut.program` is
`{slug, month, mode}` or null; `GET /v1/conversations` leaves program chats out unless asked for with
`?program=<slug>&month=<n>` (no `month`: the Q&A). When access ends, the program's chats and messages are 404.

## NH Friend Zone

Friends and 1v1 quiz battles for enrolled NH students (`app/friendzone`, 2026-11-24, Phase 1 in progress, not
released). Open only while `FRIEND_ZONE_MODE` admits the account (`off`; `qa` with `FRIEND_ZONE_QA_EMAILS`; `on`), it
is eligible as for NH programs (an open invoice claim, checked on every request), and it has joined and is not
suspended. Anything else is the same 404 `not_found`, except `GET /me`. Every response is `Cache-Control: no-store`.

- **Identity:** a member is known only by `playerId` (`fz_profiles.id`). No response, error or notification carries
  an invoice number, an email or an account id.
- **Cohorts:** a QA account (listed at join) is in the `qa` cohort for good. The database refuses any relationship
  between cohorts, and every read is scoped to the caller's cohort, so QA and real members never see each other.
- **Silence:** remove, block and decline look like no answer to the other member. A declined request stays
  `outgoing` to its sender until it expires (30 days).

| Route | Answer |
|---|---|
| `GET /v1/friend-zone/me` | `{enabled, eligible, member, suspended, profile, serverTime}` |
| `POST /v1/friend-zone/me` `{displayName, discoverable?, showProgram?, showStats?}` | 201 the profile (switches default false); 409 `already_member`; 422 `displayName` refused |
| `PATCH /v1/friend-zone/me` `{version, set: {…}}` | the profile; 409 `version_conflict` |
| `POST /v1/friend-zone/search` `{invoiceNumber}` | 200 a player (name and initials); every miss 404 `no_match`, identical; at least 0.4 s every answer; 10 an hour (429 with `Retry-After`), 30 a day per account, 200 a day per address |
| `GET /v1/friend-zone/friends` | `{items: [player]}`: photo for friends; `programs` only when the friend shows them |
| `DELETE /v1/friend-zone/friends/{playerId}` | 204 |
| `GET /v1/friend-zone/requests` | `{incoming, outgoing}`: `[{requestId, player, createdAt, expiresAt}]` |
| `POST /v1/friend-zone/requests` `{playerId}` | 201 the player, `relation` `outgoing` (or `friend` when they had asked you); only a findable member can be asked; 409 `already_friends`, `request_pending`, `request_limit` (50 waiting), `friend_limit` (200); 20 a day |
| `POST /v1/friend-zone/requests/{requestId}/accept` | 200 the player (`friend`); repeating it answers the same |
| `POST /v1/friend-zone/requests/{requestId}/decline` / `cancel` | 204 |
| `GET /v1/friend-zone/blocks`, `POST /v1/friend-zone/blocks` `{playerId}`, `DELETE …/blocks/{playerId}` | the members you blocked; 204; 204 (a friendship a block ended does not return) |
| `POST /v1/friend-zone/reports` `{playerId, reason, details?, battleId?}` | 201 `{reportId}`; reviewed by the owner on the host; 10 a day |
| `GET /v1/friend-zone/players/{playerId}` | the player, for yourself, a friend or a pending-request party; else 404 |
| `GET /v1/friend-zone/players/{playerId}/avatar?v=` | a friend's photo, `private, no-store`, authorised on every request |

A player is `{playerId, displayName, initials, relation: self|friend|incoming|outgoing|blocked|none, avatarUrl,
programs}`. Notifications (`friend_request`, `friend_accepted`) name no one and open
`/programs/{slug}/quizzes?zone=friends`.

**Battles** (1v1, live, between friends who both have the program open). The opponent's accept is their Ready; the
challenger's Ready picks the questions and starts a 3 s countdown. Each round has its own window (20-45 s, by the
question's length), then a 3 s reveal. Every state read moves the battle on by the database clock: poll it, using
`nextChangeAt`. Only competitive questions are used: active, single-answer, approved by a person for their current
content, not NHprep-keyed, at most 105 words; a question withdrawn or changed mid-round voids that round for both.
A correct answer scores 100 + 50 × time left ÷ window; three missed rounds in a row forfeit. Live answers count in the
practice record; QA ones never do.

| Route | Answer |
|---|---|
| `GET /v1/friend-zone/battles?view=open\|history&cursor=` | `{items: [{battleId, status, result, role, opponent, program, month, length, createdAt, endedAt, expiresAt}], nextCursor}` |
| `POST /v1/friend-zone/battles` `{opponentId, program, month, topicId?, difficulty?, length: 5\|10, requestId}` | 201 the battle (`invited`, 10 min); the same `requestId` answers the same battle; 409 `not_enough_questions` (`current: {available, needed}`), `battle_exists` (`current: {battleId}`), `invite_limit` (3 waiting); 30 a day |
| `GET /v1/friend-zone/battles/{battleId}` | the battle (below); at most 180 reads and answers a minute |
| `POST …/battles/{battleId}/accept` | the battle, `waiting` (3 min for the challenger's Ready); 409 `busy_in_battle`, `battle_not_open` |
| `POST …/battles/{battleId}/ready` | the battle, `active` (or `cancelled`, `not_enough_questions`); 409 `busy_in_battle`, `battle_not_open` |
| `POST …/battles/{battleId}/decline` / `cancel` | the battle, `declined` / `cancelled` |
| `POST …/battles/{battleId}/answers` `{round, choice: "A"-"H"}` | the battle; final at once (the same choice again answers the same); 409 `already_answered`, `round_closed`, `invalid_choice` |

A battle is `{battleId, status, endReason, result: points|forfeit|draw|void, program, month, topicId, difficulty,
length, roundsClosed, expiresAt, startsAt, endsAt, nextChangeAt, serverTime, me, opponent, question, rounds,
review}`. `question` is the open round only (`{round, stem, options: [{key, text}], seconds, opensAt, closesAt,
myChoice}`), never its key; `rounds` are the closed ones (`{round, voided, correctKey, myChoice, theirChoice,
myPoints, theirPoints}`); `review` (with explanations) only once `completed`. A block or an unfriend ends the pair's
open battle (`cancelled` before the start, `completed`/`void` after). Notifications: `battle_invite`,
`battle_accepted`, `battle_finished` (not for a void battle), naming no one. A battle's `endReason` is `finished`,
`forfeit`, `absent`, `expired`, `declined`, `cancelled`, `not_enough_questions`, or `ended` (a block, an unfriend, a
suspension or a deleted account, which may not be told apart).

**Phase 2** (shipped with Phase 1; owner, 2026-11-24):

| Route | Answer |
|---|---|
| `POST /v1/friend-zone/battles/{battleId}/rematch` `{requestId}` | 201 a new invitation (`invited`) to the same friend with the same settings, and fresh questions where the pool allows; only after a battle decided by play; the same `requestId` answers the same; 409 `battle_exists`, `invite_limit`, `not_enough_questions`; the friend's bell says "Rematch invitation" |
| `GET /v1/friend-zone/me/xp?cursor=` | `{xp, level, levelFloor, nextLevelAt, items: [{reason, amount, createdAt}], nextCursor}`, your own only |
| `GET /v1/friend-zone/players/{playerId}` | now also `stats`: `{battles, wins, losses, draws, correct, answered, xp, level, levelFloor, nextLevelAt, badges: [{key, title, description}], dailiesCompleted, dailyStreak}` for yourself, and for a friend only when they show their stats; otherwise `null` |

XP is awarded by the server only, once per (reason, event), and for live members only:
- **Battles:** completed, having answered at least half the counted rounds, +20; a win by points (both answered at
  least half) +30; a draw +10. At most 5 rewarded battles a day, and 2 a day with the same friend.
- **Daily challenges:** completed +25; perfect +15; streaks of 3, 7, 14 and 30 days +20, +50, +100, +200.
  Completed means all ten served and at least half of the counted (not withdrawn) questions answered.
- **Forfeit:** the player who forfeits earns nothing.

Level n starts at 50·n·(n−1) XP. The badges are `first_battle`, `quiz_champion` (10 rewarded wins against 3 or more
friends), `daily_challenger` (10 dailies), `consistency_master` (7 in a row) and `perfect_score`. Notifications:
`level_up`, `badge_unlocked`, `xp_milestone`, `daily_available`, `daily_completed`.

Which questions count is set by `FRIEND_ZONE_KEY_POLICY`. Under `human_only`, a question counts only with a person's
approval of its current text. Under `human_or_ai`, an authored question with an AI blind check bound to its exact
`content_hash` in `qb_key_checks` also counts (`scripts/bind_key_checks.py`).

## NHprep video courses

NHprep's video lessons and study aids (`app/nhprep`, [nhprep.md](nhprep.md); additive, 2026-11-20, not released).
For NetworkersHome students only: a verified address at `NHPREP_ALLOWED_EMAIL_DOMAINS`, as `NHPREP_MODE` allows
(`off`, `qa` with `NHPREP_QA_EMAILS`, `on`). For anyone else, and for everyone while `NHPREP_MODE=off`, every route
is 404 `not_found` before any id, query or body is validated. Ids are NetSec's own UUIDs: no response carries an
NHprep id or page URL, and only the playback POST carries a video link.

**The card.** A typed chat message that asks for an NHprep lesson or its study aids (after the question bank's
parse) can be answered by the server itself: the reply's `content` is a fixed line, and `metadata.nhprep` is the
card, every key always present (pinned by `backend/tests/fixtures/nhprep/cards.json`):

`{version: 1, views: ("video"|"chapters"|"notes"|"cheat_sheet"|"flashcards"|"interview")[], status: "confident"|"ambiguous", primaryLessonId, alternativeLessonIds (0-5), matchedItemId | null}`

`metadata.nhprep` is sent only to a reader NHprep is for now (in `done` and in message lists). For a reader who is
not a NetworkersHome student the whole reply is `content: "This reply isn't available."` with empty `metadata`. A
client that sees another `version` shows a reload line. Regenerating a card reply runs the search again.

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/v1/nhprep/lessons?ids=` | – | `{items: [{id, title, course: {id, title}, trainers: [string], position, lessonCount, durationSeconds}]}`: `ids` is 1-10 comma-separated lesson ids in one value; in the order asked, a missing or removed lesson left out |
| GET | `/v1/nhprep/lessons/{lesson_id}` | – | A summary plus `{description, chapters: [{id, title, startSeconds}], availability: {note, flashcard, cheat_sheet, interview_question: "present"\|"absent"\|"unavailable"\|"error"}, previousLessonId, nextLessonId, video: "ready"\|"no_video"\|"not_configured", removed}`. A removed lesson keeps its title, with no chapters and `video` not ready |
| GET | `/v1/nhprep/lessons/{lesson_id}/items?kind=` | – | `{items: [{id, kind, title, bodyMd}]}` for `kind` `note`, `flashcard` (front, back), `cheat_sheet` or `interview_question` (question, then the model answer with its scenario first); Markdown; 404 for a removed lesson |
| POST | `/v1/nhprep/lessons/{lesson_id}/playback` | – | `{status: "ready", src, expiresIn}` (seconds) or `{status: "unavailable", reason: "not_configured"\|"no_video"\|"signing_failed"}`; `Cache-Control: no-store`. `src` is always `https://videos.nhprep.com/{videoId}.mp4?…`, short-lived: ask again on Play, never store or log it. Until a signer is configured every video is `not_configured` |

- Limits per student: 600 requests a minute on these routes, of which 20 playbacks (429 `rate_limited`).
- Clients check `src`'s origin and path again before playing it, and on a playback error (an expired link answers
  410) ask for one new link before showing a reload control.

## Health

`GET /health` → `{"status":"ok"}` · `GET /ready` → `{"status","database","schema"}` (503 when the database is down, or its schema is `behind` this code's migrations; `ahead`, after a rollback, is ready).
Interactive docs at `/docs` exist only when `APP_ENV=development`.

## Compatibility and versioning

Today the only client (the web app) ships in the same release as the API, so it is always current.
Write every change as if it were not: a native client will keep running older versions for months.

- **Additive within v1:** new endpoints, new optional request fields, new response fields, new enum
  values and new SSE event types. Clients ignore unknown fields and events, and treat an unknown enum
  value as "something newer" rather than an error.
- **Breaking:** removing, renaming or retyping a path, field or enum value; making a field required;
  changing a status code, an error code or what a field means. A breaking change gets a new endpoint
  or a `/v2` path, and the old one keeps working while any supported client still uses it. Never
  repurpose an existing field.
- **The contract file** is regenerated from the code (`bun run contract`); CI fails if it is stale,
  and fails if a route or request field the web app proxies disappears. CI does not detect breaking
  changes yet: read the `docs/openapi.json` diff of every PR with the rules above.
- **Resources, not screens:** responses describe conversations, messages, users and settings, not
  what one page shows. A screen composes resources; an endpoint is not added for one layout.

## Client obligations

What every client (the web app today) must do, so no client re-implements a rule differently:

- Send only `{conversationId?, message}`: never history, never a user id (identity comes from the session).
- Never re-send a message after an interrupted stream. Re-read `GET /v1/conversations/{id}/messages`:
  the server either finished the reply or recorded how it ended.
- Stop a reply with `POST /v1/messages/{id}/cancel`. Closing the stream connection also stops it
  (see "Clients" in [architecture.md](architecture.md)).
- Ignore unknown SSE events and unknown fields.
- Decide on the error `code`; show your own text, with `message` only as a safe fallback.
