"""Which certification-bank topics a quiz request is about: one SQL round trip and fixed rules, no model.

Steps:
1. Correct the topic words. A word the bank doesn't know but is one edit away from a term it does ("vlna",
   "opsf", "topolgy") is replaced by that term. The bank's own words are read once and cached.
2. Find candidates in one query over the netsec topics:
   - an exact title or alias;
   - a lesson title;
   - full text, any word (the rank and how many of the words a topic covers come back);
   - trigram word similarity on the title and aliases.
3. Decide with features, not with tuned scores:
   - an exact or lesson match is confident;
   - topics covering every word (or very similar) are good;
   - one good topic is confident; several in one track are broad (a mixed test over them);
   - nothing good is none.
   The track is the one named, else the student's goal, else the one with the most matches.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.qbank.netsec import normal

BROAD_MAX = 8  # topics in one mixed test
GOOD_SIMILARITY = 0.6
DOMAIN_SIMILARITY = 0.8  # the words name an exam domain ("ip connectivity"): a test over the domain
DOMAIN_MAX = 12
VOCABULARY_TTL_S = 300.0
_TOKEN = re.compile(r"[a-z0-9]+(?:[.+/-][a-z0-9]+)*\+?")
_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "on", "in", "for", "to", "with", "about", "from", "by", "at", "is", "are",
    "be", "it", "this", "that", "what", "how", "my", "me", "i", "you", "your", "its", "as", "into", "via", "vs",
    "basics", "basic", "overview", "introduction", "intro", "explained",
}  # fmt: skip

FIND = text(
    """
    WITH terms AS (SELECT unnest(CAST(:words AS text[])) AS word)
    SELECT t.topic_id, t.title, t.chapter_id, c.title AS chapter_title, b.book_id, b.title AS book_title,
           b.class_level, b.subject_code, b.language,
           (lower(t.title) = :phrase OR t.aliases @> ARRAY[CAST(:phrase AS text)]) AS exact,
           EXISTS (SELECT 1 FROM jsonb_array_elements(t.lessons) l WHERE lower(l->>'title') = :phrase) AS lesson,
           (SELECT count(*) FROM terms
             WHERE t.search_tsv @@ plainto_tsquery('english', terms.word)) AS covered,
           (SELECT 2 * count(*) FILTER (WHERE t.aliases @> ARRAY[terms.word])
                   + count(*) FILTER (WHERE position(' ' || terms.word || ' ' IN ' ' || lower(t.title) || ' ') > 0)
              FROM terms) AS named,
           CASE WHEN length(:phrase) >= 5 THEN word_similarity(:phrase, lower(c.title)) ELSE 0 END AS domain,
           ts_rank_cd(t.search_tsv, to_tsquery('english', :any_word), 32) AS rank,
           greatest(word_similarity(:phrase, t.search_title), similarity(:phrase, lower(t.title))) AS similar,
           (SELECT count(*) FROM qb_questions q
             WHERE q.topic_id = t.topic_id AND q.review_status IN ('approved', 'needs_review') AND q.is_active
               AND (CAST(:entitled AS boolean) OR q.source_kind NOT LIKE 'nhprep%')) AS available
    FROM qb_topics t
    JOIN qb_chapters c ON c.chapter_id = t.chapter_id
    JOIN qb_books b ON b.book_id = c.book_id
    WHERE b.curriculum = 'netsec'
      AND (CAST(:track AS text) IS NULL OR b.book_id = :track)
      AND (lower(t.title) = :phrase
           OR t.aliases @> ARRAY[CAST(:phrase AS text)]
           OR EXISTS (SELECT 1 FROM jsonb_array_elements(t.lessons) l WHERE lower(l->>'title') = :phrase)
           OR (:any_word <> '' AND t.search_tsv @@ to_tsquery('english', :any_word))
           OR word_similarity(:phrase, t.search_title) >= 0.45
           OR (length(:phrase) >= 5 AND word_similarity(:phrase, lower(c.title)) >= 0.8))
    ORDER BY 10 DESC, 11 DESC, 13 DESC, 12 DESC, 15 DESC
    LIMIT 60
    """
)
TRACK = text(
    """
    SELECT t.topic_id, t.title, t.chapter_id, c.title AS chapter_title, b.book_id, b.title AS book_title,
           b.class_level, b.subject_code, b.language, false AS exact, false AS lesson, 0 AS covered, 0 AS named,
           0.0 AS domain, 0.0 AS rank, 0.0 AS similar,
           (SELECT count(*) FROM qb_questions q
             WHERE q.topic_id = t.topic_id AND q.review_status IN ('approved', 'needs_review') AND q.is_active
               AND (CAST(:entitled AS boolean) OR q.source_kind NOT LIKE 'nhprep%')) AS available
    FROM qb_topics t
    JOIN qb_chapters c ON c.chapter_id = t.chapter_id
    JOIN qb_books b ON b.book_id = c.book_id
    WHERE b.curriculum = 'netsec' AND (CAST(:track AS text) IS NULL OR b.book_id = :track)
      AND (CAST(:topic_ids AS text[]) IS NULL OR t.topic_id = ANY(CAST(:topic_ids AS text[])))
    """
)
WORDS = text(
    """
    SELECT DISTINCT word FROM (
        SELECT unnest(t.aliases) AS word FROM qb_topics t
        UNION ALL SELECT regexp_split_to_table(lower(t.title), '[^a-z0-9.+/-]+') FROM qb_topics t
        UNION ALL SELECT regexp_split_to_table(lower(c.title), '[^a-z0-9.+/-]+') FROM qb_chapters c
    ) w WHERE length(word) BETWEEN 2 AND 40
    """
)


@dataclass(frozen=True)
class Topic:
    topic_id: str
    title: str
    chapter_id: str
    chapter_title: str
    book_id: str
    book_title: str
    class_level: int
    subject_code: str
    language: str
    exact: bool
    lesson: bool
    covered: int
    named: int  # 2 per word that is one of its aliases, 1 per word of its title
    domain: float  # how similar the words are to its exam domain's name
    rank: float
    similar: float
    available: int


@dataclass(frozen=True)
class Found:
    status: str  # confident | broad | ambiguous | none
    topics: tuple[Topic, ...] = ()
    closest: tuple[str, ...] = ()  # titles to offer when nothing matched well
    words: tuple[str, ...] = ()
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def book_id(self) -> str | None:
        return self.topics[0].book_id if self.topics else None


_vocabulary: dict[str, Any] = {"at": 0.0, "words": frozenset()}


async def bank_words(session: AsyncSession) -> frozenset[str]:
    """Every word the bank knows (aliases and title words), cached for VOCABULARY_TTL_S: an import shows up
    within minutes, and a turn never waits for more than one read."""
    if time.monotonic() - _vocabulary["at"] > VOCABULARY_TTL_S:
        rows = await session.scalars(WORDS)
        words = set()
        for row in rows:
            words.update(_TOKEN.findall(row.lower()))
        _vocabulary.update(at=time.monotonic(), words=frozenset(words))
    return _vocabulary["words"]


def _edits(a: str, b: str, limit: int) -> int:
    """Optimal string alignment distance (a transposition is one edit), stopping early past `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous2: list[int] = []
    previous = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        current = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = a[i - 1] != b[j - 1]
            current[j] = min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                current[j] = min(current[j], previous2[j - 2] + 1)
        if min(current) > limit:
            return limit + 1
        previous2, previous = previous, current
    return previous[-1]


def corrected(words: list[str], known: frozenset[str]) -> list[str]:
    """The words as the bank knows them:
    - two words that are one known term ("ip sec" -> "ipsec") are joined;
    - an unknown word becomes the one known word within one edit (two for words of 7+ letters);
    - when several are that close, the one with the same letters wins ("nta" -> "nat", not "ntp");
    - numbers and known words are kept."""
    joined: list[str] = []
    for word in words:
        if joined and joined[-1] + word in known and (joined[-1] not in known or word not in known):
            joined[-1] += word
        else:
            joined.append(word)
    out = []
    for word in joined:
        if word in known or word.isdigit() or len(word) < 3:
            out.append(word)
            continue
        limit = 2 if len(word) >= 7 else 1
        close = {
            other for other in known if abs(len(other) - len(word)) <= limit and _edits(word, other, limit) <= limit
        }
        if len(close) > 1:  # the same letters, then the fewest edits
            close = {other for other in close if sorted(other) == sorted(word)} or close
        if len(close) > 1:
            fewest = min(_edits(word, other, limit) for other in close)
            close = {other for other in close if _edits(word, other, limit) == fewest}
        out.append(next(iter(close)) if len(close) == 1 else word)
    return out


_CHATTER = {
    "don't", "dont", "do", "does", "did", "understand", "explain", "explained", "tell", "work", "works", "working",
    "why", "when", "where", "which", "who", "can", "could", "would", "should", "please", "help", "know", "learn",
    "learned", "learning", "just", "watched", "read", "studied", "studying", "doubt", "confused", "difference",
    "between", "use", "used", "need", "want", "get", "got", "still", "really", "much", "some", "more", "lesson",
    "video", "topic", "chapter", "session", "class", "we", "discussed", "talked", "covered", "now", "so", "ok",
}  # fmt: skip


def words_of(topic: str, *, sentence: bool = False) -> list[str]:
    """The search words of a topic name, or (sentence=True) of a chat sentence, conversational words left out."""
    skip = _STOPWORDS | _CHATTER if sentence else _STOPWORDS
    return [word for word in _TOKEN.findall(normal(topic)) if word not in skip and not word.startswith("'")]


def _row(row: Any) -> Topic:
    return Topic(**{name: row._mapping[name] for name in Topic.__dataclass_fields__})


def _choose_track(topics: list[Topic], named: str | None, goal: str | None) -> str | None:
    if named:
        return named
    books = {topic.book_id for topic in topics}
    if goal in books:
        return goal
    if not books:
        return None
    counts: dict[str, tuple[int, int]] = {}
    for topic in topics:
        good, questions = counts.get(topic.book_id, (0, 0))
        counts[topic.book_id] = (good + 1, questions + topic.available)
    return max(sorted(counts), key=lambda book: counts[book])


async def find(
    session: AsyncSession,
    topic: str,
    *,
    track: str | None = None,
    goal: str | None = None,
    entitled: bool = False,
    need_questions: bool = True,
    loose: bool = False,
) -> Found:
    """The topics a request names. `topic`: the request's topic words ("" for a whole track); `track`: a named
    certification; `goal`: the student's certification goal, a tie-breaker; `need_questions`: only topics with
    servable questions (False: topics alone, for the retrieval eval); `loose`: words from a sentence or a lesson
    title rather than a topic name, so the topics covering the most of them count (at least half)."""
    started = time.perf_counter()
    known = await bank_words(session)
    words = corrected(words_of(topic, sentence=loose), known) if topic else []
    # Words the bank has never heard of ("kuch", "alternatives", "lite") can't be covered by any topic: the
    # request is judged on the ones it knows. None known: nothing to search, the closest titles are offered.
    words = [word for word in words if word in known or word.isdigit()] or words
    timings = {"vocabularyMs": round((time.perf_counter() - started) * 1000, 2)}
    if not words:
        rows = await session.execute(TRACK, {"track": track or goal, "entitled": entitled, "topic_ids": None})
        topics = [_row(row) for row in rows if row.available or not need_questions]
        if not topics and goal and not track:  # the goal track has nothing yet: every track
            rows = await session.execute(TRACK, {"track": None, "entitled": entitled, "topic_ids": None})
            topics = [_row(row) for row in rows if row.available or not need_questions]
        chosen = _choose_track(topics, track, goal)
        topics = sorted((t for t in topics if t.book_id == chosen), key=lambda t: (t.chapter_id, t.topic_id))
        timings["sqlMs"] = round((time.perf_counter() - started) * 1000, 2)
        return Found("broad" if topics else "none", tuple(topics), words=(), timings=timings)
    phrase = " ".join(words)
    params = {
        "words": words,
        "phrase": phrase,
        "any_word": " | ".join(re.sub(r"[^a-z0-9]", "", word) for word in words if re.sub(r"[^a-z0-9]", "", word)),
        "track": track,
        "entitled": entitled,
    }
    rows = await session.execute(FIND, params)
    timings["sqlMs"] = round((time.perf_counter() - started) * 1000, 2)
    candidates = [_row(row) for row in rows if row.available or not need_questions]
    return _decide(candidates, words, track, goal, timings, loose)


def _decide(
    candidates: list[Topic], words: list[str], track: str | None, goal: str | None, timings: dict, loose: bool = False
) -> Found:
    if not candidates:
        return Found("none", words=tuple(words), timings=timings)
    exact = [t for t in candidates if t.exact or t.lesson]
    if exact:
        chosen = _choose_track(exact, track, goal)
        best = [t for t in exact if t.book_id == chosen]
        status = "confident" if len(best) == 1 else "broad"
        return Found(status, tuple(best[:BROAD_MAX]), words=tuple(words), timings=timings)
    domain = max(t.domain for t in candidates)
    named = max(t.named for t in candidates)
    domains = {t.chapter_id for t in candidates if t.domain == domain}
    exact_domain = any(normal(t.chapter_title) == " ".join(words) for t in candidates)  # "network fundamentals"
    if domain >= DOMAIN_SIMILARITY and len(domains) == 1 and (named <= 1 or exact_domain):  # "automation"
        chapters = [t for t in candidates if t.domain == domain]
        chosen = _choose_track(chapters, track, goal)
        best = sorted((t for t in chapters if t.book_id == chosen), key=lambda t: t.topic_id)
        return Found("broad", tuple(best[:DOMAIN_MAX]), words=tuple(words), timings=timings)
    whole = len(words)
    if loose:
        most = max(t.covered for t in candidates)
        whole = most if most * 2 >= len(words) else whole
    # Every word covered first; else a topic one of the words names as an alias ("vrfs lite", "HSRP configuration"
    # is the FHRP topic, not configuration management); only then fuzzy similarity.
    good = [t for t in candidates if t.covered >= whole]
    most = max(t.named for t in candidates)
    if not good and most >= 2:
        good = [t for t in candidates if t.named == most]
    if not good:
        good = [t for t in candidates if t.similar >= GOOD_SIMILARITY]
    if not good:
        closest = tuple(t.title for t in sorted(candidates, key=_strength, reverse=True)[:3])
        return Found("none", closest=closest, words=tuple(words), timings=timings)
    chosen = _choose_track(good, track, goal)
    best = sorted((t for t in good if t.book_id == chosen), key=_strength, reverse=True)
    if len(best) == 1 or _strength(best[0])[:2] > _strength(best[1])[:2]:
        return Found("confident", (best[0],), words=tuple(words), timings=timings)
    if len(best) > BROAD_MAX and len({t.chapter_id for t in best}) > 2:
        return Found("ambiguous", tuple(best[:3]), words=tuple(words), timings=timings)
    return Found("broad", tuple(best[:BROAD_MAX]), words=tuple(words), timings=timings)


def _strength(topic: Topic) -> tuple[int, int, float, float]:
    """A topic named by the words (its aliases or title words) beats one whose text only mentions them; then one
    whose title is very similar; then similarity and rank."""
    return (topic.named, int(topic.similar >= 0.8), topic.similar + min(topic.rank, 1.0), topic.covered)
