"use client";

import { Button } from "@v1/ui/button";
import { ErrorState } from "@v1/ui/empty-state";
import { Skeleton } from "@v1/ui/skeleton";
import { cn } from "@v1/ui/utils";
import { AlarmClock, ArrowLeft, Check, CircleCheck, CircleX, Lock, RotateCcw, SkipForward } from "lucide-react";
import { type ReactNode, type RefObject, useCallback, useEffect, useId, useRef, useState } from "react";

import { navigationGuard } from "@/features/navigation/navigation-guard";
import { LEARNING_EVENT } from "@/lib/activity";
import { ApiError } from "@/lib/api/errors";
import type { Uuid } from "@/lib/api/types";
import {
  type Battle,
  type BattleMove,
  type BattleQuestion,
  type BattleReview,
  type ResponseItem,
  getBattle,
  moveBattle,
  rematchBattle,
  submitExam,
} from "./battle-api";
import { type Phase, accuracy, battleFailure, battleMeta, existingBattle, battlePhase, lockedText, nameOf, outcome, phaseKey, warningFor } from "./battle-model";
import { clockOffset, fractionLeft, secondsLeft } from "./clock";
import { XP_RULE, completionTracker, countedQuestions, durationWords, marksText, readMessage, timeTakenText, xpLabel } from "./exam-model";
import { type ExamStore, dropExamStore, examStore } from "./exam-store";
import { ExamView, HowItWorks, WaitingPanel } from "./exam-view";
import { backoff, isTerminal, nextPollDelay } from "./poll";
import { canRematch } from "./progress-model";

/**
 * One battle, live (plan section 4 J5-J6, 10, 11; the exam: Friend Challenge plan Rev 2 section 6). The server's
 * answer is the whole truth: this screen reads it on the poll schedule (poll.ts), counts deadlines on the server's
 * clock (clock.ts), and only draws it. One polite live region says what changed (never every second); focus moves at
 * the exam's start, on submit or time up, and to the result.
 */

/** `unavailable`: the battle was running when it went away (Friend Zone switched off), not a link to someone else's. */
type Live = { battle: Battle | null; offset: number; gone: boolean; unavailable: boolean; failed: boolean };

function useBattle(battleId: Uuid, onApply: (battle: Battle) => void) {
  const [live, setLive] = useState<Live>({ battle: null, offset: 0, gone: false, unavailable: false, failed: false });
  const battle = useRef<Battle | null>(null);
  const offset = useRef(0);
  const failures = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const inFlight = useRef<AbortController | null>(null);
  const mounted = useRef(true);
  const applied = useRef(onApply);
  applied.current = onApply;

  const markGone = useCallback(() => {
    const unavailable = battle.current?.status === "active";
    setLive((current) => ({ ...current, gone: true, unavailable }));
  }, []);

  const apply = useCallback((next: Battle, sentAt: number, receivedAt: number) => {
    // A slow read can arrive after a newer answer (a move's reply): the older state never replaces the newer one.
    const shown = battle.current;
    if (shown && shown.battleId === next.battleId && Date.parse(next.serverTime) < Date.parse(shown.serverTime)) return;
    offset.current = clockOffset(next.serverTime, sentAt, receivedAt);
    battle.current = next;
    failures.current = 0;
    applied.current(next);
    setLive({ battle: next, offset: offset.current, gone: false, unavailable: false, failed: false });
  }, []);

  const schedule = useCallback((read: () => void) => {
    clearTimeout(timer.current);
    if (!mounted.current) return;
    const context = { offset: offset.current, localNow: Date.now(), hidden: document.visibilityState === "hidden", failures: failures.current };
    const delay = battle.current ? nextPollDelay(battle.current, context) : backoff(Math.max(1, failures.current));
    if (delay !== null) timer.current = setTimeout(read, delay);
  }, []);

  const read = useCallback(() => {
    if (inFlight.current || !mounted.current) return; // one read at a time
    const controller = new AbortController();
    inFlight.current = controller;
    const sentAt = Date.now();
    getBattle(battleId, controller.signal).then(
      (next) => {
        if (inFlight.current === controller) inFlight.current = null;
        if (!mounted.current) return;
        apply(next, sentAt, Date.now());
        schedule(read);
      },
      (error) => {
        if (inFlight.current === controller) inFlight.current = null;
        if (!mounted.current || controller.signal.aborted) return;
        if (error instanceof ApiError && error.code === "not_found") {
          markGone();
          return; // not this account's battle (or no longer, or Friend Zone is off): stop
        }
        failures.current += 1;
        setLive((current) => ({ ...current, failed: true }));
        schedule(read);
      },
    );
  }, [battleId, apply, schedule, markGone]);

  useEffect(() => {
    mounted.current = true;
    read();
    const shown = () => {
      if (document.visibilityState === "visible") {
        clearTimeout(timer.current);
        read();
      } else schedule(read);
    };
    document.addEventListener("visibilitychange", shown);
    return () => {
      mounted.current = false;
      clearTimeout(timer.current);
      inFlight.current?.abort();
      inFlight.current = null;
      document.removeEventListener("visibilitychange", shown);
    };
  }, [read, schedule]);

  /** A move or an answer: its answer is the new state; then the schedule starts again from it. */
  const send = useCallback(
    async (request: () => Promise<Battle>) => {
      const sentAt = Date.now();
      const next = await request();
      apply(next, sentAt, Date.now());
      schedule(read);
    },
    [apply, schedule, read],
  );
  const refresh = useCallback(() => {
    clearTimeout(timer.current);
    read();
  }, [read]);
  return { live, send, refresh, markGone };
}

/** Settings > Appearance's Reduce motion (`<html data-motion="reduce">`, lib/appearance.ts), followed while shown. */
function useAppReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    const root = document.documentElement;
    const read = () => setReduced(root.dataset.motion === "reduce");
    read();
    const observer = new MutationObserver(read);
    observer.observe(root, { attributes: true, attributeFilter: ["data-motion"] });
    return () => observer.disconnect();
  }, []);
  return reduced;
}

/** This device's time, updated while something on screen counts down. */
function useNow(interval: number | null): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (interval === null) return;
    setNow(Date.now()); // the time when counting starts, not when this view first rendered
    const id = setInterval(() => setNow(Date.now()), interval);
    return () => clearInterval(id);
  }, [interval]);
  return now;
}

export function BattleView({ battleId, onClose, onOpen }: { battleId: Uuid; onClose: () => void; onOpen: (battleId: Uuid) => void }) {
  const [note, setNote] = useState<string | null>(null);
  // A 409 (or a failed auto-submit) asks for a re-read; its words come from the state that read brings (I21), as do a
  // submit's own when the server didn't apply it (`submitApplied: false`).
  const stale = useRef(false);
  const { live, send, refresh, markGone } = useBattle(battleId, (next) => {
    if (next.format !== "exam") return;
    let unsaved = 0;
    if (next.status === "active" && next.exam && !next.exam.submittedAt) {
      const store = examStore(next.battleId);
      store.adopt(next.exam.responses, next.exam.questions);
      unsaved = store.getSnapshot().intents.size;
    } else if (next.status !== "active" || next.exam?.submittedAt) unsaved = dropExamStore(next.battleId);
    const words = readMessage(next, unsaved, stale.current);
    if (words) setNote(words);
    // A re-read that finds the exam still running (a failed auto-submit inside the allowance) keeps the question open
    // until the end is read; then the "Checking the battle…" note goes if the end has nothing to add.
    if (words || next.status !== "active" || next.exam?.submittedAt) {
      if (stale.current && !words) setNote(null);
      stale.current = false;
    }
  });
  // One requestId per Rematch press, kept until the answer is known, so a retry after a dropped connection replays.
  const rematchId = useRef<string | null>(null);
  const counting = live.battle !== null && !isTerminal(live.battle.status);
  const now = useNow(counting ? 250 : null);
  const [busy, setBusy] = useState(false);
  // The streak and the bell refresh once, when a battle seen open turns completed (practice is recorded then).
  const [completed] = useState(completionTracker);
  const status = live.battle?.status;
  useEffect(() => {
    if (status && completed(status)) window.dispatchEvent(new Event(LEARNING_EVENT));
  }, [status, completed]);
  // In-page exits ask first while an answer isn't saved (links, reload and Back go through the same guard).
  const close = () => {
    if (navigationGuard.confirmLeave()) onClose();
  };

  const act = async (request: () => Promise<Battle>, action: "move" | "answer") => {
    setBusy(true);
    setNote(null);
    try {
      await send(request);
    } catch (error) {
      const failure = battleFailure(error, action);
      setNote(failure.message);
      if (failure.reload) refresh();
    } finally {
      setBusy(false);
    }
  };

  /** Submit: the explicit one sets busy; the deadline's own (`auto`) never does, and is never refused (plan section 2). */
  const submit = async (items: ResponseItem[], auto: boolean, retry = auto) => {
    if (!auto) setBusy(true);
    setNote(null);
    try {
      await send(() => submitExam(battleId, items, auto));
    } catch (error) {
      if (error instanceof ApiError && error.code === "not_found") return markGone();
      const refused = error instanceof ApiError && error.status === 409;
      if (retry && !refused) return submit(items, true, false); // once more inside the 2 s allowance
      stale.current = true;
      if (!refused)
        setNote(auto ? "Your answers couldn't be sent. Checking the battle…" : battleFailure(error, "answer").message);
      refresh();
    } finally {
      if (!auto) setBusy(false);
    }
  };
  const onServerChange = useCallback(
    (gone: boolean) => {
      if (gone) return markGone();
      stale.current = true;
      refresh();
    },
    [markGone, refresh],
  );

  const rematch = async (from: Uuid) => {
    rematchId.current ??= crypto.randomUUID();
    setBusy(true);
    setNote(null);
    try {
      const next = await rematchBattle(from, rematchId.current);
      rematchId.current = null;
      onOpen(next.battleId);
    } catch (error) {
      if (!(error instanceof ApiError && error.code === "network_error")) rematchId.current = null;
      const existing = existingBattle(error);
      if (existing) return onOpen(existing);
      setNote(battleFailure(error, "create").message);
    } finally {
      setBusy(false);
    }
  };

  if (live.gone)
    return (
      <BattleFrame onClose={onClose}>
        <BattleGone unavailable={live.unavailable} />
      </BattleFrame>
    );
  if (!live.battle)
    return (
      <BattleFrame onClose={onClose}>
        {live.failed ? (
          <ErrorState title="The battle couldn't be loaded" description="Trying again…" />
        ) : (
          <Skeleton aria-busy="true" className="h-64 w-full rounded-2xl" />
        )}
      </BattleFrame>
    );
  const battle = live.battle;
  return (
    <BattleScreen
      battle={battle}
      offset={live.offset}
      now={now}
      busy={busy}
      note={note ?? (live.failed ? "Connection trouble: trying again…" : null)}
      onClose={close}
      onMove={(move) => act(() => moveBattle(battle.battleId, move), "move")}
      onRematch={() => rematch(battle.battleId)}
      store={battle.format === "exam" && battle.status === "active" && battle.exam && !battle.exam.submittedAt ? examStore(battle.battleId) : undefined}
      onSubmit={submit}
      onServerChange={onServerChange}
    />
  );
}

/**
 * The view's own title when the battle can't be shown: a heading that takes focus, then why (not an alert). A battle
 * that was running when it went away means Friend Zone itself was switched off (a 404 while active).
 */
export function BattleGone({ unavailable = false }: { unavailable?: boolean }) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus(), []);
  return (
    <div>
      <h3 ref={heading} tabIndex={-1} className="focus-ring rounded-md text-lg font-semibold text-foreground">
        {unavailable ? "Friend Zone is unavailable right now" : "This battle isn't available"}
      </h3>
      <p className="mt-1 text-sm text-text-secondary">{unavailable ? "Try again in a few minutes." : "It may have ended, or it isn't yours."}</p>
    </div>
  );
}

function BattleFrame({ onClose, children }: { onClose: () => void; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-4">
      <BackButton onClose={onClose} />
      {children}
    </div>
  );
}

function BackButton({ onClose }: { onClose: () => void }) {
  return (
    <Button type="button" variant="outline" size="sm" className="min-h-11 self-start" onClick={onClose}>
      <ArrowLeft aria-hidden className="h-4 w-4" />
      Back to Friend Zone
    </Button>
  );
}

export type ScreenProps = {
  battle: Battle;
  offset: number;
  now: number;
  busy: boolean;
  note: string | null;
  onClose: () => void;
  onMove: (move: BattleMove) => void;
  /** Offered on a battle decided by play (points, draw, forfeit). */
  onRematch?: () => void;
  /** A running exam's save store (BattleView passes the battle's own; tests pass one). */
  store?: ExamStore;
  onSubmit?: (items: ResponseItem[], auto: boolean) => void;
  onServerChange?: (gone: boolean) => void;
};

const noop = () => {};

/** Everything a battle shows, drawn from one state of it (render-tested state by state). */
export function BattleScreen({ battle, offset, now, busy, note, onClose, onMove, onRematch, store, onSubmit = noop, onServerChange = noop }: ScreenProps) {
  const headingId = useId();
  const phase = battlePhase(battle);
  const focusTarget = useRef<HTMLHeadingElement>(null);
  const title = useRef<HTMLHeadingElement>(null);
  const opened = useRef(false);
  const [said, setSaid] = useState("");
  const key = phaseKey(phase);

  // What changed is said once, in the one live region; focus moves at the exam's start, on submit and at the end.
  useEffect(() => {
    if (phase.kind === "exam") {
      const count = phase.exam.questions.length;
      setSaid(`The exam has started: ${count} questions, ${durationWords(count * phase.exam.secondsPerQuestion)}.`);
      focusTarget.current?.focus();
    } else if (phase.kind === "submitted") {
      setSaid("Submitted. The result shows when you both have submitted, or at the deadline.");
      focusTarget.current?.focus();
    } else if (phase.kind === "countdown") setSaid("The battle starts in 3 seconds.");
    else if (phase.kind === "result" || phase.kind === "ended") {
      const { headline, detail } = outcome(battle);
      setSaid(`${headline}. ${detail}`);
      focusTarget.current?.focus();
    }
    // The list that opened this view is gone: a state that focuses nothing else starts at the battle's heading.
    const own = phase.kind === "exam" || phase.kind === "submitted" || phase.kind === "result" || phase.kind === "ended";
    if (!opened.current && !own) title.current?.focus();
    opened.current = true;
  }, [key]); // once per new phase, not on every poll
  // A note (answers not saved, the battle ended) is said too, in the same live line, after the phase's own words.
  useEffect(() => {
    if (note) setSaid(note);
  }, [note]);

  const live = (
    <p role="status" className="sr-only">
      {said}
    </p>
  );
  // One root for every phase, so the one live region stays mounted across phase changes (a fresh region isn't read).
  return (
    <div className="flex flex-col gap-4">
      {live}
      <BackButton onClose={onClose} />
      {phase.kind === "exam" ? (
        <ExamView
          battle={battle}
          exam={phase.exam}
          store={store ?? examStore(battle.battleId)}
          offset={offset}
          now={now}
          busy={busy}
          said={said}
          say={setSaid}
          headingRef={focusTarget}
          titleId={headingId}
          onSubmit={onSubmit}
          onServerChange={onServerChange}
        />
      ) : (
        <section aria-labelledby={headingId} className="flex flex-col gap-4">
          <header>
            <h3 id={headingId} ref={title} tabIndex={-1} className="focus-ring rounded-md text-lg font-semibold text-foreground">
              Battle with <bdi dir="auto">{nameOf(battle.opponent)}</bdi>
            </h3>
            <p className="text-sm text-text-secondary">{battleMeta(battle)}</p>
            {(battle.repeatsFromPrevious ?? 0) > 0 && <p className="text-sm text-text-secondary">{repeatsText(battle.repeatsFromPrevious ?? 0)}</p>}
          </header>
          <Body battle={battle} phase={phase} offset={offset} now={now} busy={busy} focusTarget={focusTarget} onMove={onMove} onRematch={onRematch} />
        </section>
      )}
      {note && <p className="text-sm text-destructive">{note}</p>}
    </div>
  );
}

export function repeatsText(count: number): string {
  return count === 1 ? "1 question repeats from your last battle." : `${count} questions repeat from your last battle.`;
}

const PANEL = "rounded-2xl border border-border bg-card p-4 sm:p-6";

function Body({
  battle,
  phase,
  offset,
  now,
  busy,
  focusTarget,
  onMove,
  onRematch,
}: {
  battle: Battle;
  phase: Phase;
  offset: number;
  now: number;
  busy: boolean;
  focusTarget: RefObject<HTMLHeadingElement | null>;
  onMove: (move: BattleMove) => void;
  onRematch?: () => void;
}) {
  const them = <bdi dir="auto">{nameOf(battle.opponent)}</bdi>;
  const exam = battle.format === "exam";
  const minutes = Math.max(1, Math.ceil(secondsLeft(battle.expiresAt, offset, now) / 60));
  const expires = battle.expiresAt ? ` It expires in ${minutes} ${minutes === 1 ? "minute" : "minutes"}.` : "";
  const what = exam ? `an exam battle of ${battle.length} questions (${battle.length} minutes)` : `a live battle of ${battle.length} questions`;
  const how = exam && <HowItWorks length={battle.length} />;
  const move = (label: string, kind: BattleMove, primary = false) => (
    <Button type="button" variant={primary ? "default" : "outline"} className="min-h-11" disabled={busy} onClick={() => onMove(kind)}>
      {label}
    </Button>
  );
  switch (phase.kind) {
    case "invited-in":
      return (
        <div className={PANEL}>
          <p className="text-sm text-foreground">
            {them} challenged you to {what}.{expires} Accepting means you're ready to play now.
          </p>
          {how}
          <div className="mt-4 flex flex-wrap gap-2">
            {move("Accept and play", "accept", true)}
            {move("Decline", "decline")}
          </div>
        </div>
      );
    case "invited-out":
      return (
        <div className={PANEL}>
          <p className="text-sm text-foreground">
            Waiting for {them} to accept.{expires}
          </p>
          {how}
          <div className="mt-4 flex flex-wrap gap-2">{move("Cancel challenge", "cancel")}</div>
        </div>
      );
    case "ready":
      return (
        <div className={PANEL}>
          <p className="text-sm text-foreground">
            {them} accepted. Press Ready to start: a 3-second countdown, then the {exam ? "exam" : "first question"}.{expires}
          </p>
          {how}
          <div className="mt-4 flex flex-wrap gap-2">
            {move("Ready", "ready", true)}
            {move("Cancel", "cancel")}
          </div>
        </div>
      );
    case "waiting-start":
      return (
        <div className={PANEL}>
          <p className="text-sm text-foreground">
            You're in. Waiting for {them} to start the battle.{expires}
          </p>
          {how}
        </div>
      );
    case "countdown":
      return (
        <div className={cn(PANEL, "text-center")}>
          <p className="text-sm text-text-secondary">Get ready</p>
          <p aria-hidden className="mt-2 text-display font-semibold tabular-nums text-foreground">
            {Math.max(1, secondsLeft(battle.startsAt, offset, now))}
          </p>
        </div>
      );
    case "exam":
      return null; // BattleScreen draws the running exam itself
    case "submitted":
      return <WaitingPanel battle={battle} exam={phase.exam} offset={offset} now={now} headingRef={focusTarget} />;
    case "result":
      return <ResultPanel battle={battle} headingRef={focusTarget} busy={busy} onRematch={onRematch} />;
    case "ended": {
      const { headline, detail } = outcome(battle);
      return (
        <div className={PANEL}>
          <h4 ref={focusTarget} tabIndex={-1} className="focus-ring rounded-md text-base font-semibold text-foreground">
            {headline}
          </h4>
          <p className="mt-1 text-sm text-text-secondary">{detail}</p>
        </div>
      );
    }
  }
}

export function QuestionPanel({
  question,
  length,
  offset,
  now,
  left,
  busy,
  headingRef,
  onAnswer,
  lockedNote = "The answer shows when the round closes.",
  footer,
  where = "battle",
}: {
  question: BattleQuestion;
  length: number;
  offset: number;
  now: number;
  left: number;
  busy: boolean;
  headingRef: RefObject<HTMLHeadingElement | null>;
  onAnswer: (round: number, choice: string) => void;
  lockedNote?: string;
  footer?: ReactNode;
  /** Where the answer keys work, in the help line. */
  where?: string;
}) {
  const locked = question.myChoice !== null;
  const warning = warningFor(left);
  const appReduced = useAppReducedMotion();
  const helpId = useId();
  const last = question.options.length;
  return (
    <div className={PANEL}>
      <h4 ref={headingRef} tabIndex={-1} className="focus-ring rounded-md text-base font-semibold text-foreground">
        Question {question.round} of {length}
      </h4>
      <div className="mt-3 flex items-center gap-3">
        <p className={cn("flex shrink-0 items-center gap-1.5 text-sm font-semibold tabular-nums", warning ? "text-warning" : "text-foreground")}>
          {warning && <AlarmClock aria-hidden className="h-4 w-4" />}
          {warning ? `${left} seconds left` : `${left} s`}
        </p>
        {/* Moving content: not drawn at all under reduced motion (the device's or the app's), where only the number changes. */}
        {!appReduced && (
          <div aria-hidden className="h-2 flex-1 overflow-hidden rounded-full bg-surface-2 motion-reduce:hidden">
            <div
              className="h-full origin-left bg-brand transition-transform duration-base ease-linear"
              style={{ transform: `scaleX(${fractionLeft(question.closesAt, question.seconds, offset, now)})` }}
            />
          </div>
        )}
      </div>
      <p dir="auto" className="mt-4 whitespace-pre-wrap break-words text-base text-foreground">
        {question.stem}
      </p>
      <ul className="mt-4 flex flex-col gap-2" aria-describedby={helpId}>
        {question.options.map((option, index) => {
          const chosen = question.myChoice === option.key;
          return (
            <li key={option.key}>
              <button
                type="button"
                // aria-disabled, not disabled: the pressed button keeps focus after the answer locks
                aria-disabled={locked || busy}
                aria-pressed={chosen}
                aria-keyshortcuts={`${index + 1} ${option.key}`}
                onClick={() => !locked && !busy && onAnswer(question.round, option.key)}
                className={cn(
                  "focus-ring flex min-h-11 w-full items-start gap-3 rounded-xl border px-4 py-3 text-left text-sm transition-colors",
                  chosen ? "border-brand bg-brand-soft text-foreground" : "border-border bg-card text-foreground",
                  !locked && !busy ? "hover:bg-surface-2" : "cursor-default",
                  locked && !chosen && "text-text-secondary",
                )}
              >
                <span aria-hidden className="grid h-7 w-7 shrink-0 place-items-center rounded-lg border border-border bg-surface-2 text-xs font-semibold">
                  {option.key}
                </span>
                <span className="sr-only">{option.key}. </span>
                <span dir="auto" className="min-w-0 flex-1 break-words pt-0.5">
                  {option.text}
                </span>
                {chosen && (
                  <span className="flex shrink-0 items-center gap-1 pt-0.5 text-xs font-semibold">
                    <Lock aria-hidden className="h-3.5 w-3.5" />
                    Your answer
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-sm text-foreground">{locked ? `${lockedText(question.myChoice ?? "")}. ${lockedNote}` : "One tap is final."}</p>
      <p id={helpId} className="mt-1 text-xs text-text-secondary">
        Keys: press 1–{last} or A–{question.options[last - 1]?.key ?? "D"} to answer while focus is in the {where}.
      </p>
      {footer}
    </div>
  );
}

/** The scores table's rows: an exam's marks (+4/−1), or a round battle's points (history). */
function scoreRows(battle: Battle): (readonly [string, string, string])[] {
  const { me, opponent } = battle;
  if (battle.format !== "exam")
    return [
      ["Score", String(me.score), String(opponent.score)],
      ["Correct", `${me.correct} of ${me.answered}`, `${opponent.correct} of ${opponent.answered}`],
      ["Accuracy", accuracy(me), accuracy(opponent)],
    ];
  const counted = countedQuestions(battle);
  return [
    ["Marks", marksText(me.score), marksText(opponent.score)],
    ["Correct", String(me.correct), String(opponent.correct)],
    ["Incorrect", String(me.answered - me.correct), String(opponent.answered - opponent.correct)],
    ["Unanswered", String(Math.max(0, counted - me.answered)), String(Math.max(0, counted - opponent.answered))],
    ["Accuracy", accuracy(me), accuracy(opponent)],
    ["Time taken", timeTakenText(me, battle), timeTakenText(opponent, battle)],
  ];
}

function ResultPanel({
  battle,
  headingRef,
  busy,
  onRematch,
}: {
  battle: Battle;
  headingRef: RefObject<HTMLHeadingElement | null>;
  busy: boolean;
  onRematch?: () => void;
}) {
  const { headline, detail } = outcome(battle);
  const them = nameOf(battle.opponent);
  return (
    <div className="flex flex-col gap-4">
      <div className={PANEL}>
        <h4 ref={headingRef} tabIndex={-1} className="focus-ring rounded-md text-xl font-semibold text-foreground">
          {headline}
        </h4>
        <p className="mt-1 text-sm text-text-secondary">{detail}</p>
        <table className="mt-4 w-full text-left text-sm">
          <caption className="sr-only">Scores</caption>
          <thead>
            <tr className="border-b border-border">
              <th scope="col" className="py-2 font-normal text-text-secondary">
                <span className="sr-only">Measure</span>
              </th>
              <th scope="col" className="py-2 font-semibold text-foreground">
                You
              </th>
              <th scope="col" className="py-2 font-semibold text-foreground">
                <bdi dir="auto">{them}</bdi>
              </th>
            </tr>
          </thead>
          <tbody className="tabular-nums">
            {scoreRows(battle).map(([label, mine, theirs]) => (
              <tr key={label} className="border-b border-border last:border-0">
                <th scope="row" className="py-2 font-normal text-text-secondary">
                  {label}
                </th>
                <td className="py-2 text-foreground">{mine}</td>
                <td className="py-2 text-foreground">{theirs}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {battle.format === "exam" && battle.xp && <XpLines lines={battle.xp} />}
        {onRematch && canRematch(battle.status, battle.result) && (
          <div className="mt-4">
            <Button type="button" className="min-h-11" disabled={busy} onClick={onRematch}>
              <RotateCcw aria-hidden className="h-4 w-4" />
              {busy ? "Sending…" : "Rematch"}
            </Button>
            <p className="mt-2 text-xs text-text-secondary">Same friend and settings, fresh questions where there are enough.</p>
          </div>
        )}
      </div>
      {battle.review && battle.review.length > 0 && <Review items={battle.review} them={them} exam={battle.format === "exam"} />}
    </div>
  );
}

function XpLines({ lines }: { lines: NonNullable<Battle["xp"]> }) {
  const total = lines.reduce((sum, line) => sum + line.amount, 0);
  return (
    <div className="mt-4">
      <h5 className="text-sm font-semibold text-foreground">Your XP</h5>
      {lines.length > 0 ? (
        <ul className="mt-1 flex flex-col gap-0.5 text-sm text-foreground">
          {lines.map((line) => (
            <li key={line.reason} className="flex gap-2 tabular-nums">
              <span className="min-w-0 flex-1">{xpLabel(line.reason)}</span>
              <span>+{line.amount}</span>
            </li>
          ))}
          <li className="flex gap-2 border-t border-border pt-1 font-semibold tabular-nums">
            <span className="min-w-0 flex-1">Total</span>
            <span>+{total} XP</span>
          </li>
        </ul>
      ) : (
        <>
          <p className="mt-1 text-sm text-foreground">No XP for this battle.</p>
          <p className="mt-1 text-xs text-text-secondary">{XP_RULE}</p>
        </>
      )}
    </div>
  );
}

/** One player's pick in the review, as an icon and words (never colour alone). */
function PickLine({ who, choice, correctKey }: { who: ReactNode; choice: string | null; correctKey: string | null }) {
  const right = choice !== null && choice === correctKey;
  const Icon = choice === null ? SkipForward : right ? CircleCheck : CircleX;
  return (
    <p className="flex items-start gap-1.5 text-sm text-foreground">
      <Icon aria-hidden className={cn("mt-0.5 h-4 w-4 shrink-0", choice === null ? "text-text-secondary" : right ? "text-success" : "text-destructive")} />
      <span className="min-w-0 flex-1">
        {who}: {choice === null ? "not answered" : `${choice}, ${right ? "correct" : "incorrect"}`}
      </span>
    </p>
  );
}

function Review({ items, them, exam }: { items: BattleReview[]; them: string; exam: boolean }) {
  return (
    <section aria-label="Review" className="flex flex-col gap-3">
      <h4 className="text-base font-semibold text-foreground">Review</h4>
      <ol className="flex flex-col gap-3">
        {items.map((item) =>
          exam && item.voided ? (
            <li key={item.round} className={PANEL}>
              <p className="text-xs font-semibold text-text-secondary">Question {item.round}</p>
              <p className="mt-1 text-sm text-foreground">Changed or withdrawn after the exam started: not scored.</p>
            </li>
          ) : (
            <li key={item.round} className={PANEL}>
              <p className="text-xs font-semibold text-text-secondary">Question {item.round}</p>
              {item.stem && (
                <p dir="auto" className="mt-1 whitespace-pre-wrap break-words text-sm text-foreground">
                  {item.stem}
                </p>
              )}
              {item.voided && <p className="mt-2 text-sm font-semibold text-foreground">Question withdrawn: no points.</p>}
              <ul className="mt-3 flex flex-col gap-1.5">
                {item.options.map((option) => {
                  const marks = [
                    option.key === item.correctKey ? "Correct answer" : null,
                    option.key === item.myChoice ? "You" : null,
                    option.key === item.theirChoice ? them : null,
                  ].filter(Boolean);
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
                          <bdi dir="auto">{marks.join(" · ")}</bdi>
                        </span>
                      )}
                    </li>
                  );
                })}
              </ul>
              {exam && (
                <div className="mt-3 flex flex-col gap-1">
                  <PickLine who="You" choice={item.myChoice} correctKey={item.correctKey} />
                  <PickLine who={<bdi dir="auto">{them}</bdi>} choice={item.theirChoice} correctKey={item.correctKey} />
                </div>
              )}
              {item.explanation && (
                <p className="mt-3 text-sm text-foreground">
                  <span className="font-semibold">Why: </span>
                  <span dir="auto">{item.explanation}</span>
                </p>
              )}
            </li>
          ),
        )}
      </ol>
    </section>
  );
}
