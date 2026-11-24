"use client";

import { Button } from "@v1/ui/button";
import { Skeleton } from "@v1/ui/skeleton";
import { cn } from "@v1/ui/utils";
import { Check, Trophy } from "lucide-react";
import { type KeyboardEvent, type RefObject, useCallback, useEffect, useId, useRef, useState } from "react";

import type { Program } from "@/lib/api/programs";
import { QuestionPanel } from "./battle-view";
import { lockedText, roundAnnouncement, shortcutChoice, warningFor } from "./battle-model";
import { clockOffset, secondsLeft } from "./clock";
import { DAILY_QUESTIONS, type Daily, type DailyFriend, type DailyReview, answerDaily, getDaily, nextDaily } from "./daily-api";
import { type DailyPhase, dailyFailure, dailyKey, dailyPhase, dailyScoreLine, nextDailyPoll } from "./progress-model";

/**
 * The daily challenge (plan J8, section 13): ten questions a day in this program, one at a time, each with its own
 * timer, at your own pace between them. Same clock, polling and accessibility rules as a battle: one polite live
 * region, focus to each new question and to the finish, the same answer keys. The server keeps the keys, the
 * explanations and your friends' results until you finish.
 */

type Live = { daily: Daily | null; offset: number; failed: boolean };

function useDaily(slug: string, pulse: number) {
  const [live, setLive] = useState<Live>({ daily: null, offset: 0, failed: false });
  const current = useRef<Daily | null>(null);
  const offset = useRef(0);
  const failures = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const inFlight = useRef<AbortController | null>(null);
  const mounted = useRef(true);

  const apply = useCallback((next: Daily, sentAt: number, receivedAt: number) => {
    const shown = current.current;
    if (shown && Date.parse(next.serverTime) < Date.parse(shown.serverTime)) return; // an older read than what's shown
    offset.current = clockOffset(next.serverTime, sentAt, receivedAt);
    current.current = next;
    failures.current = 0;
    setLive({ daily: next, offset: offset.current, failed: false });
  }, []);

  const schedule = useCallback((read: () => void) => {
    clearTimeout(timer.current);
    if (!mounted.current || !current.current) return;
    const delay = nextDailyPoll(current.current, {
      offset: offset.current,
      localNow: Date.now(),
      hidden: document.visibilityState === "hidden",
      failures: failures.current,
    });
    if (delay !== null) timer.current = setTimeout(read, delay);
  }, []);

  const read = useCallback(() => {
    if (inFlight.current || !mounted.current) return;
    const controller = new AbortController();
    inFlight.current = controller;
    const sentAt = Date.now();
    getDaily(slug, controller.signal).then(
      (next) => {
        if (inFlight.current === controller) inFlight.current = null;
        if (!mounted.current) return;
        apply(next, sentAt, Date.now());
        schedule(read);
      },
      () => {
        if (inFlight.current === controller) inFlight.current = null;
        if (!mounted.current || controller.signal.aborted) return;
        failures.current += 1;
        setLive((shown) => ({ ...shown, failed: true }));
        if (current.current) schedule(read);
      },
    );
  }, [slug, apply, schedule]);

  useEffect(() => {
    mounted.current = true;
    read();
    return () => {
      mounted.current = false;
      clearTimeout(timer.current);
      inFlight.current?.abort();
      inFlight.current = null;
    };
  }, [read]);
  useEffect(() => {
    if (pulse > 0 && !current.current?.question) read(); // the hub's pulse; never mid-question
  }, [pulse, read]);

  const send = useCallback(
    async (request: () => Promise<Daily>) => {
      const sentAt = Date.now();
      apply(await request(), sentAt, Date.now());
      schedule(read);
    },
    [apply, schedule, read],
  );
  const retry = useCallback(() => {
    clearTimeout(timer.current);
    read();
  }, [read]);
  return { live, send, retry };
}

function useNow(on: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!on) return;
    setNow(Date.now()); // the time when the question opens, not when this section first rendered
    const id = setInterval(() => setNow(Date.now()), 250);
    return () => clearInterval(id);
  }, [on]);
  return now;
}

export function DailySection({ program, pulse }: { program: Program; pulse: number }) {
  const headingId = useId();
  const { live, send, retry } = useDaily(program.slug, pulse);
  const now = useNow(Boolean(live.daily?.question));
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  const act = async (request: () => Promise<Daily>) => {
    setBusy(true);
    setNote(null);
    try {
      await send(request);
    } catch (error) {
      const failure = dailyFailure(error);
      setNote(failure.message);
      if (failure.reload) retry();
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby={headingId}>
      <h3 id={headingId} className="text-base font-semibold text-foreground">
        Daily challenge
      </h3>
      {!live.daily && !live.failed && <Skeleton aria-busy="true" className="mt-3 h-24 w-full rounded-xl" />}
      {!live.daily && live.failed && (
        <p className="mt-1 text-sm text-text-secondary">
          Today's challenge couldn't be loaded.{" "}
          <Button type="button" variant="link" className="h-auto min-h-11 p-0" onClick={retry}>
            Try again
          </Button>
        </p>
      )}
      {live.daily && (
        <DailyScreen
          daily={live.daily}
          offset={live.offset}
          now={now}
          busy={busy}
          note={note ?? (live.failed ? "Connection trouble: trying again…" : null)}
          onNext={(challengeId) => act(() => nextDaily(challengeId))}
          onAnswer={(challengeId, position, choice) => act(() => answerDaily(challengeId, position, choice))}
        />
      )}
    </section>
  );
}

const PANEL = "mt-3 rounded-2xl border border-border bg-card p-4 sm:p-6";

export type DailyScreenProps = {
  daily: Daily;
  offset: number;
  now: number;
  busy: boolean;
  note: string | null;
  onNext: (challengeId: string) => void;
  onAnswer: (challengeId: string, position: number, choice: string) => void;
};

/** Everything the daily challenge shows, drawn from one state of it (render-tested state by state). */
export function DailyScreen({ daily, offset, now, busy, note, onNext, onAnswer }: DailyScreenProps) {
  const phase = dailyPhase(daily, offset, now);
  const question = phase.kind === "question" ? phase.question : null;
  const left = question ? secondsLeft(question.closesAt, offset, now) : 0;
  const focusTarget = useRef<HTMLHeadingElement>(null);
  const [said, setSaid] = useState("");
  const key = dailyKey(phase);
  const warned = useRef<{ position: number; level: number | null }>({ position: 0, level: null });
  const opened = useRef(false);

  // Said once per new question or finish; focus moves to a new question and to the finish, never on load.
  useEffect(() => {
    if (phase.kind === "question") {
      setSaid(roundAnnouncement({ round: phase.question.position, seconds: phase.question.seconds }, DAILY_QUESTIONS));
      if (opened.current) focusTarget.current?.focus();
    } else if (phase.kind === "finished") {
      setSaid(`Daily challenge finished. ${dailyScoreLine(daily)}.`);
      if (opened.current) focusTarget.current?.focus();
    }
    opened.current = true;
  }, [key]); // once per new question or the finish, not on every read
  useEffect(() => {
    if (!question) return;
    if (warned.current.position !== question.position) warned.current = { position: question.position, level: null };
    const level = warningFor(left);
    if (level !== null && level !== warned.current.level && left > 0) {
      warned.current.level = level;
      setSaid(`${level} seconds left.`);
    }
  }, [question, left]);
  const myChoice = question?.myChoice ?? null;
  useEffect(() => {
    if (myChoice) setSaid(`${lockedText(myChoice)}.`);
  }, [myChoice]);

  const challengeId = daily.challengeId ?? "";
  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (!question || question.myChoice || busy || event.altKey || event.ctrlKey || event.metaKey) return;
    if ((event.target as HTMLElement).closest("input, textarea, select, [contenteditable='true']")) return;
    const choice = shortcutChoice(event.key, question.options);
    if (!choice) return;
    event.preventDefault();
    onAnswer(challengeId, question.position, choice);
  };

  return (
    // The answer keys work while focus is anywhere in the challenge (its buttons are what take focus).
    <div onKeyDown={onKeyDown}>
      <p role="status" className="sr-only">
        {said}
      </p>
      <Body phase={phase} daily={daily} offset={offset} now={now} left={left} busy={busy} focusTarget={focusTarget} onNext={onNext} onAnswer={onAnswer} />
      {note && <p className="mt-2 text-sm text-destructive">{note}</p>}
    </div>
  );
}

function Body({
  phase,
  daily,
  offset,
  now,
  left,
  busy,
  focusTarget,
  onNext,
  onAnswer,
}: Omit<DailyScreenProps, "note"> & { phase: DailyPhase; left: number; focusTarget: RefObject<HTMLHeadingElement | null> }) {
  const challengeId = daily.challengeId ?? "";
  const next = (label: string, primary = true) => (
    <Button type="button" variant={primary ? "default" : "outline"} className="min-h-11" disabled={busy} onClick={() => onNext(challengeId)}>
      {busy ? "Loading…" : label}
    </Button>
  );
  switch (phase.kind) {
    case "unavailable":
      return (
        <p className="mt-1 text-sm text-text-secondary">
          No daily challenge today. There aren't enough checked questions in {daily.program.title} for one yet; Battles and Practice are
          still here.
        </p>
      );
    case "closed":
      return <p className="mt-1 text-sm text-text-secondary">Today's challenge has closed. A new one comes tomorrow.</p>;
    case "start":
      return (
        <div className={PANEL}>
          <p className="text-sm text-foreground">
            Today's challenge: {DAILY_QUESTIONS} questions from {daily.program.title}, one at a time, each with its own timer. Your
            friends' results show once you finish.
          </p>
          <div className="mt-4">{next("Start today's challenge")}</div>
        </div>
      );
    case "between": {
      const done = phase.next > DAILY_QUESTIONS;
      return (
        <div className={PANEL}>
          <p className="text-sm text-foreground">
            {done ? "That was the last question." : `Ready for question ${phase.next} of ${DAILY_QUESTIONS}. Its timer starts when it opens.`}
          </p>
          <div className="mt-4">{next(done ? "Finish" : "Next question")}</div>
        </div>
      );
    }
    case "question":
      return (
        <div className="mt-3">
          <QuestionPanel
            question={{ ...phase.question, round: phase.question.position, opensAt: phase.question.servedAt }}
            length={DAILY_QUESTIONS}
            offset={offset}
            now={now}
            left={left}
            busy={busy}
            headingRef={focusTarget}
            onAnswer={(position, choice) => onAnswer(challengeId, position, choice)}
            lockedNote="Answers show when you finish."
            where="challenge"
            footer={
              phase.question.myChoice ? (
                <div className="mt-4">{next(phase.question.position >= DAILY_QUESTIONS ? "Finish" : "Next question")}</div>
              ) : null
            }
          />
        </div>
      );
    case "finished":
      return (
        <div className="mt-3 flex flex-col gap-4">
          <div className="rounded-2xl border border-border bg-card p-4 sm:p-6">
            <h4 ref={focusTarget} tabIndex={-1} className="focus-ring flex items-center gap-2 rounded-md text-lg font-semibold text-foreground">
              <Trophy aria-hidden className="h-5 w-5 text-brand" />
              Finished
            </h4>
            <p className="mt-1 text-sm text-foreground">{dailyScoreLine(daily)}</p>
          </div>
          {daily.friends && <FriendResults friends={daily.friends} />}
          {daily.review && daily.review.length > 0 && <Review items={daily.review} />}
        </div>
      );
  }
}

function FriendResults({ friends }: { friends: DailyFriend[] }) {
  return (
    <div>
      <h4 className="text-sm font-semibold text-foreground">Friends today</h4>
      {friends.length === 0 ? (
        <p className="mt-1 text-sm text-text-secondary">None of your friends has played today's challenge yet.</p>
      ) : (
        <ul className="mt-2 divide-y divide-border rounded-xl border border-border bg-card">
          {friends.map((friend) => (
            <li key={friend.playerId} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1 px-4 py-2.5 text-sm">
              <bdi dir="auto" className="min-w-0 break-words font-semibold text-foreground">
                {friend.displayName}
              </bdi>
              <span className="text-text-secondary">
                {friend.status === "in_progress"
                  ? "In progress"
                  : friend.score === null
                    ? "Completed"
                    : `${friend.score} points · ${friend.correct ?? 0} correct`}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Review({ items }: { items: DailyReview[] }) {
  return (
    <section aria-label="Review" className="flex flex-col gap-3">
      <h4 className="text-sm font-semibold text-foreground">Review</h4>
      <ol className="flex flex-col gap-3">
        {items.map((item) => (
          <li key={item.position} className="rounded-2xl border border-border bg-card p-4 sm:p-6">
            <p className="text-xs font-semibold text-text-secondary">
              Question {item.position} · {item.voided ? "Question withdrawn: no points" : `${item.points} points`}
            </p>
            <p dir="auto" className="mt-1 whitespace-pre-wrap break-words text-sm text-foreground">
              {item.stem}
            </p>
            {item.myChoice === null && <p className="mt-2 text-sm text-text-secondary">You didn't answer this one.</p>}
            <ul className="mt-3 flex flex-col gap-1.5">
              {item.options.map((option) => {
                const marks = [option.key === item.correctKey ? "Correct answer" : null, option.key === item.myChoice ? "You" : null].filter(Boolean);
                return (
                  <li
                    key={option.key}
                    className={cn(
                      "flex flex-wrap items-start gap-2 rounded-lg border px-3 py-2 text-sm",
                      option.key === item.correctKey ? "border-success/40 bg-success-soft" : "border-border",
                    )}
                  >
                    <span className="font-semibold">{option.key}.</span>
                    <span dir="auto" className="min-w-0 flex-1 break-words">
                      {option.text}
                    </span>
                    {marks.length > 0 && (
                      <span className="flex items-center gap-1 text-xs font-semibold">
                        {option.key === item.correctKey && <Check aria-hidden className="h-3.5 w-3.5" />}
                        {marks.join(" · ")}
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
            <p className="mt-3 text-sm text-foreground">
              <span className="font-semibold">Why: </span>
              <span dir="auto">{item.explanation}</span>
            </p>
          </li>
        ))}
      </ol>
    </section>
  );
}
