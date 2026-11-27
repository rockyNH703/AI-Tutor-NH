"use client";

import { ArrowLeft, ChevronRight, ListChecks, Search } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useEffect, useId, useMemo, useRef, useState } from "react";

import type { BankTopic } from "@/lib/api/programs";
import { getMastery } from "@/lib/api/progress";
import type { Mastery, MasteryState } from "@/lib/api/types";
import { ARROW, CARD, CARD_GRID, FOOT, NUMBER, twoDigits } from "./card-styles";
import {
  type QuizTopic,
  STATE_LABEL,
  groupQuizzes,
  masteryByTopic,
  plural,
  searchQuizzes,
  topicProgress,
} from "./quiz-topics";

/**
 * Quizzes & Games, one month (owner, 2026-11-27): the month's quiz topics (bank domains) as numbered cards, a topic's
 * own view with its quizzes, and a search over every quiz of the month. The open topic is in the address
 * (`?month=2&topic=…`), so Back and a reload keep it. A quiz card asks for that quiz in the month chat (`onQuiz`), as
 * before. Progress comes only from the practice record (GET /v1/progress/mastery); without one, none is shown.
 */
export function QuizTopicsView({
  month,
  quizzes,
  onQuiz,
}: {
  month: number;
  quizzes: BankTopic[];
  onQuiz: (title: string) => void;
}) {
  const params = useSearchParams();
  const tracks = useMemo(() => groupQuizzes(quizzes), [quizzes]);
  const states = useMasteryStates();
  const [query, setQuery] = useState("");
  const openKey = params.get("topic");
  const open = openKey ? (tracks.flatMap((track) => track.topics).find((topic) => topic.key === openKey) ?? null) : null;
  // The history API (Next follows it: useSearchParams updates): a topic is a step Back can undo.
  const show = (key: string | null) =>
    window.history.pushState(null, "", key ? `?month=${month}&topic=${encodeURIComponent(key)}` : `?month=${month}`);

  if (open) return <TopicQuizzes month={month} topic={open} states={states} onBack={() => show(null)} onQuiz={onQuiz} />;

  const hits = searchQuizzes(tracks, query);
  return (
    <div className="flex max-w-4xl flex-col gap-6">
      <QuizSearch query={query} onQuery={setQuery} found={query.trim() ? hits.length : null} />
      {query.trim() ? (
        hits.length ? (
          <ul className={CARD_GRID} aria-label="Matching quizzes">
            {hits.map(({ quiz, topic }) => (
              <QuizCard key={quiz.topicId} quiz={quiz} topic={topic.title} state={states.get(quiz.topicId)} onQuiz={onQuiz} />
            ))}
          </ul>
        ) : (
          <p className="text-sm text-text-secondary">No quizzes match “{query.trim()}” in Month {month}.</p>
        )
      ) : (
        tracks.map((track) => (
          <section key={track.title} aria-label={track.title} className="flex flex-col gap-3">
            {tracks.length > 1 && <h4 className="text-sm font-semibold text-foreground">{track.title}</h4>}
            <ol className="flex flex-col gap-3">
              {track.topics.map((topic, index) => (
                <TopicCard key={topic.key} number={index + 1} topic={topic} states={states} onOpen={() => show(topic.key)} />
              ))}
            </ol>
          </section>
        ))
      )}
    </div>
  );
}

/** The practice record's state per bank topic; empty until it arrives, and if it cannot be read (progress is extra). */
function useMasteryStates(): Map<string, MasteryState> {
  const [mastery, setMastery] = useState<Mastery | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    getMastery(controller.signal).then(setMastery, () => setMastery(null));
    return () => controller.abort();
  }, []);
  return useMemo(() => masteryByTopic(mastery), [mastery]);
}

function progressText(topic: QuizTopic, states: Map<string, MasteryState>): string {
  const { practised, mastered } = topicProgress(topic, states);
  if (!practised) return "";
  return ` · ${practised} of ${topic.quizzes.length} practised${mastered ? ` · ${mastered} mastered` : ""}`;
}

function TopicCard({
  number,
  topic,
  states,
  onOpen,
}: {
  number: number;
  topic: QuizTopic;
  states: Map<string, MasteryState>;
  onOpen: () => void;
}) {
  return (
    <li className="flex">
      <button type="button" onClick={onOpen} className={`${CARD} flex w-full items-center gap-4 p-4 text-left sm:p-5`}>
        <span aria-hidden className={NUMBER}>
          {twoDigits(number)}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-pretty text-base font-semibold leading-snug text-foreground">{topic.title}</span>
          <span className="mt-1 block text-xs text-text-secondary">
            {plural(topic.quizzes.length, "quiz", "quizzes")}
            {progressText(topic, states)}
          </span>
        </span>
        <ChevronRight aria-hidden className={ARROW} />
      </button>
    </li>
  );
}

function TopicQuizzes({
  month,
  topic,
  states,
  onBack,
  onQuiz,
}: {
  month: number;
  topic: QuizTopic;
  states: Map<string, MasteryState>;
  onBack: () => void;
  onQuiz: (title: string) => void;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  // A new view: focus its heading, so a keyboard or screen reader user starts at the topic, not the page top.
  useEffect(() => heading.current?.focus(), []);
  return (
    <div className="flex max-w-4xl flex-col gap-4">
      <nav aria-label="Breadcrumb">
        <ol className="flex flex-wrap items-center gap-1.5 text-sm text-text-secondary">
          <li>
            <button
              type="button"
              onClick={onBack}
              className="focus-ring -ml-2 inline-flex min-h-11 items-center gap-1.5 rounded-lg px-2 font-medium text-foreground hover:bg-surface-2"
            >
              <ArrowLeft aria-hidden className="h-4 w-4" />
              All topics
            </button>
          </li>
          <li aria-hidden>/</li>
          <li>Month {month}</li>
          <li aria-hidden>/</li>
          <li aria-current="page" className="text-foreground">
            {topic.title}
          </li>
        </ol>
      </nav>
      <div>
        <h4 ref={heading} tabIndex={-1} className="text-lg font-semibold text-foreground outline-none">
          {topic.title}
        </h4>
        <p className="mt-1 text-sm text-text-secondary">
          {plural(topic.quizzes.length, "quiz", "quizzes")}
          {progressText(topic, states)}
        </p>
      </div>
      <ul className={CARD_GRID} aria-label={`${topic.title} quizzes`}>
        {topic.quizzes.map((quiz) => (
          <QuizCard key={quiz.topicId} quiz={quiz} state={states.get(quiz.topicId)} onQuiz={onQuiz} />
        ))}
      </ul>
    </div>
  );
}

/** One quiz; the whole card asks for it in the month chat (its name says so: "Quiz me on …"). */
function QuizCard({
  quiz,
  topic,
  state,
  onQuiz,
}: {
  quiz: BankTopic;
  topic?: string;
  state: MasteryState | undefined;
  onQuiz: (title: string) => void;
}) {
  return (
    <li className="flex">
      <button
        type="button"
        aria-label={`Quiz me on ${quiz.title}${state ? `, ${STATE_LABEL[state]}` : ""}`}
        onClick={() => onQuiz(quiz.title)}
        className={`${CARD} flex min-h-32 w-full flex-col p-4 text-left sm:p-5`}
      >
        <span className="block text-pretty text-sm font-semibold leading-snug text-foreground sm:text-base">{quiz.title}</span>
        {topic && <span className="mt-1 block text-xs text-text-secondary">{topic}</span>}
        <span className="mt-auto block w-full pt-4">
          <span className={`${FOOT} justify-between`}>
            <span className="inline-flex items-center gap-1.5 font-semibold text-foreground">
              <ListChecks aria-hidden className="h-3.5 w-3.5 shrink-0 text-text-secondary" />
              Quiz me
            </span>
            {state && <span className="rounded-full bg-surface-2 px-2 py-0.5 text-text-secondary">{STATE_LABEL[state]}</span>}
            <ChevronRight aria-hidden className={ARROW} />
          </span>
        </span>
      </button>
    </li>
  );
}

function QuizSearch({ query, onQuery, found }: { query: string; onQuery: (value: string) => void; found: number | null }) {
  const id = useId();
  return (
    <div>
      <label htmlFor={id} className="sr-only">
        Search this month's quizzes
      </label>
      <div className="relative max-w-md">
        <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-secondary" />
        <input
          id={id}
          type="search"
          value={query}
          onChange={(event) => onQuery(event.target.value)}
          placeholder="Search quizzes and topics"
          autoComplete="off"
          className="focus-field h-11 w-full rounded-xl border border-input bg-card pl-9 pr-3 text-base text-foreground placeholder:text-text-secondary sm:text-sm md:h-10"
        />
      </div>
      <p role="status" className="sr-only">
        {found === null ? "" : `${plural(found, "quiz", "quizzes")} found`}
      </p>
    </div>
  );
}
