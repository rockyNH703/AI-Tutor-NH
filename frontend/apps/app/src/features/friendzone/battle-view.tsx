"use client";

import { Button } from "@v1/ui/button";
import { ErrorState } from "@v1/ui/empty-state";
import { Skeleton } from "@v1/ui/skeleton";
import { cn } from "@v1/ui/utils";
import { AlarmClock, ArrowLeft, Check, Lock, RotateCcw } from "lucide-react";
import { type KeyboardEvent, type ReactNode, type RefObject, useCallback, useEffect, useId, useRef, useState } from "react";

import { ApiError } from "@/lib/api/errors";
import type { Uuid } from "@/lib/api/types";
import {
  type Battle,
  type BattleMove,
  type BattleQuestion,
  type BattleReview,
  answerBattle,
  getBattle,
  moveBattle,
  rematchBattle,
} from "./battle-api";
import {
  type Phase,
  accuracy,
  battleFailure,
  battleMeta,
  existingBattle,
  battlePhase,
  lockedText,
  nameOf,
  outcome,
  phaseKey,
  revealText,
  roundAnnouncement,
  shortcutChoice,
  warningFor,
} from "./battle-model";
import { clockOffset, fractionLeft, secondsLeft } from "./clock";
import { backoff, isTerminal, nextPollDelay } from "./poll";
import { canRematch } from "./progress-model";

/**
 * One battle, live (plan section 4 J5-J6, 10, 11). The server's answer is the whole truth: this screen reads it on the
 * poll schedule (poll.ts), counts deadlines on the server's clock (clock.ts), and only draws it. One polite live
 * region says what changed (never every second); focus moves to a new question and to the result, never during the
 * reveal.
 */

type Live = { battle: Battle | null; offset: number; gone: boolean; failed: boolean };

function useBattle(battleId: Uuid) {
  const [live, setLive] = useState<Live>({ battle: null, offset: 0, gone: false, failed: false });
  const battle = useRef<Battle | null>(null);
  const offset = useRef(0);
  const failures = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const inFlight = useRef<AbortController | null>(null);
  const mounted = useRef(true);

  const apply = useCallback((next: Battle, sentAt: number, receivedAt: number) => {
    // A slow read can arrive after a newer answer (a move's reply): the older state never replaces the newer one.
    const shown = battle.current;
    if (shown && shown.battleId === next.battleId && Date.parse(next.serverTime) < Date.parse(shown.serverTime)) return;
    offset.current = clockOffset(next.serverTime, sentAt, receivedAt);
    battle.current = next;
    failures.current = 0;
    setLive({ battle: next, offset: offset.current, gone: false, failed: false });
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
          setLive((current) => ({ ...current, gone: true }));
          return; // not this account's battle (or no longer): stop
        }
        failures.current += 1;
        setLive((current) => ({ ...current, failed: true }));
        schedule(read);
      },
    );
  }, [battleId, apply, schedule]);

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
  return { live, send, refresh };
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
  const { live, send, refresh } = useBattle(battleId);
  // One requestId per Rematch press, kept until the answer is known, so a retry after a dropped connection replays.
  const rematchId = useRef<string | null>(null);
  const counting = live.battle !== null && !isTerminal(live.battle.status);
  const now = useNow(counting ? 250 : null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

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
        <BattleGone />
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
      onClose={onClose}
      onMove={(move) => act(() => moveBattle(battle.battleId, move), "move")}
      onAnswer={(round, choice) => act(() => answerBattle(battle.battleId, round, choice), "answer")}
      onRematch={() => rematch(battle.battleId)}
    />
  );
}

/** The view's own title when the battle can't be shown: a heading that takes focus, then why (not an alert). */
export function BattleGone() {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus(), []);
  return (
    <div>
      <h3 ref={heading} tabIndex={-1} className="focus-ring rounded-md text-lg font-semibold text-foreground">
        This battle isn't available
      </h3>
      <p className="mt-1 text-sm text-text-secondary">It may have ended, or it isn't yours.</p>
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
  onAnswer: (round: number, choice: string) => void;
  /** Offered on a battle decided by play (points, draw, forfeit). */
  onRematch?: () => void;
};

/** Everything a battle shows, drawn from one state of it (render-tested state by state). */
export function BattleScreen({ battle, offset, now, busy, note, onClose, onMove, onAnswer, onRematch }: ScreenProps) {
  const headingId = useId();
  const phase = battlePhase(battle);
  const them = nameOf(battle.opponent);
  const question = phase.kind === "question" ? phase.question : null;
  const left = question ? secondsLeft(question.closesAt, offset, now) : 0;
  const focusTarget = useRef<HTMLHeadingElement>(null);
  const title = useRef<HTMLHeadingElement>(null);
  const opened = useRef(false);
  const [said, setSaid] = useState("");
  const key = phaseKey(phase);
  const warned = useRef<{ round: number; level: number | null }>({ round: 0, level: null });

  // What changed is said once, in the one live region; focus moves only to a new question or to the ending.
  useEffect(() => {
    if (phase.kind === "question") {
      setSaid(roundAnnouncement(phase.question, battle.length));
      focusTarget.current?.focus();
    } else if (phase.kind === "countdown") setSaid("The battle starts in 3 seconds.");
    else if (phase.kind === "reveal") setSaid(revealText(phase.round, them));
    else if (phase.kind === "result" || phase.kind === "ended") {
      const { headline, detail } = outcome(battle);
      setSaid(`${headline}. ${detail}`);
      focusTarget.current?.focus();
    }
    // The list that opened this view is gone: a state that focuses nothing else starts at the battle's heading.
    if (!opened.current && phase.kind !== "question" && phase.kind !== "result" && phase.kind !== "ended") title.current?.focus();
    opened.current = true;
  }, [key]); // once per new phase or round, not on every poll
  useEffect(() => {
    if (!question) return;
    const level = warningFor(left);
    if (warned.current.round !== question.round) warned.current = { round: question.round, level: null };
    if (level !== null && level !== warned.current.level && left > 0) {
      warned.current.level = level;
      setSaid(`${level} seconds left.`);
    }
  }, [question, left]);
  const myChoice = question?.myChoice ?? null;
  useEffect(() => {
    if (myChoice) setSaid(`${lockedText(myChoice)}.`);
  }, [myChoice]);

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (!question || question.myChoice || busy || event.altKey || event.ctrlKey || event.metaKey) return;
    const target = event.target as HTMLElement;
    if (target.closest("input, textarea, select, [contenteditable='true']")) return;
    const choice = shortcutChoice(event.key, question.options);
    if (!choice) return;
    event.preventDefault();
    onAnswer(question.round, choice);
  };

  // The answer shortcuts work while focus is anywhere in the battle (its buttons are what take focus).
  return (
    <section aria-labelledby={headingId} onKeyDown={onKeyDown} className="flex flex-col gap-4">
      <p role="status" className="sr-only">
        {said}
      </p>
      <BackButton onClose={onClose} />
      <header>
        <h3 id={headingId} ref={title} tabIndex={-1} className="focus-ring rounded-md text-lg font-semibold text-foreground">
          Battle with <bdi dir="auto">{them}</bdi>
        </h3>
        <p className="text-sm text-text-secondary">{battleMeta(battle)}</p>
      </header>
      {(phase.kind === "question" || phase.kind === "reveal") && <Scoreboard battle={battle} />}
      <Body
        battle={battle}
        phase={phase}
        offset={offset}
        now={now}
        left={left}
        busy={busy}
        focusTarget={focusTarget}
        onMove={onMove}
        onAnswer={onAnswer}
        onRematch={onRematch}
      />
      {note && <p className="text-sm text-destructive">{note}</p>}
    </section>
  );
}

const PANEL = "rounded-2xl border border-border bg-card p-4 sm:p-6";

function Body({
  battle,
  phase,
  offset,
  now,
  left,
  busy,
  focusTarget,
  onMove,
  onAnswer,
  onRematch,
}: {
  battle: Battle;
  phase: Phase;
  offset: number;
  now: number;
  left: number;
  busy: boolean;
  focusTarget: RefObject<HTMLHeadingElement | null>;
  onMove: (move: BattleMove) => void;
  onAnswer: (round: number, choice: string) => void;
  onRematch?: () => void;
}) {
  const them = <bdi dir="auto">{nameOf(battle.opponent)}</bdi>;
  const minutes = Math.max(1, Math.ceil(secondsLeft(battle.expiresAt, offset, now) / 60));
  const expires = battle.expiresAt ? ` It expires in ${minutes} ${minutes === 1 ? "minute" : "minutes"}.` : "";
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
            {them} challenged you to a live battle of {battle.length} questions.{expires} Accepting means you're ready to play now.
          </p>
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
          <div className="mt-4 flex flex-wrap gap-2">{move("Cancel challenge", "cancel")}</div>
        </div>
      );
    case "ready":
      return (
        <div className={PANEL}>
          <p className="text-sm text-foreground">
            {them} accepted. Press Ready to start: a 3-second countdown, then the first question.{expires}
          </p>
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
    case "question":
      return (
        <QuestionPanel
          question={phase.question}
          length={battle.length}
          offset={offset}
          now={now}
          left={left}
          busy={busy}
          headingRef={focusTarget}
          onAnswer={onAnswer}
        />
      );
    case "reveal":
      return (
        <div className={PANEL}>
          <h4 className="text-base font-semibold text-foreground">Round {phase.round.round} closed</h4>
          <p className="mt-1 text-sm text-foreground">{revealText(phase.round, nameOf(battle.opponent))}</p>
          <p className="mt-2 text-xs text-text-secondary">The next question opens in a moment.</p>
        </div>
      );
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

function Scoreboard({ battle }: { battle: Battle }) {
  return (
    <p className="flex flex-wrap gap-x-4 gap-y-1 text-sm text-foreground">
      <span>
        You <span className="font-semibold tabular-nums">{battle.me.score}</span>
      </span>
      <span>
        <bdi dir="auto">{nameOf(battle.opponent)}</bdi> <span className="font-semibold tabular-nums">{battle.opponent.score}</span>
      </span>
    </p>
  );
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
            {(
              [
                ["Score", String(battle.me.score), String(battle.opponent.score)],
                ["Correct", `${battle.me.correct} of ${battle.me.answered}`, `${battle.opponent.correct} of ${battle.opponent.answered}`],
                ["Accuracy", accuracy(battle.me), accuracy(battle.opponent)],
              ] as const
            ).map(([label, mine, theirs]) => (
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
      {battle.review && battle.review.length > 0 && <Review items={battle.review} them={them} />}
    </div>
  );
}

function Review({ items, them }: { items: BattleReview[]; them: string }) {
  return (
    <section aria-label="Review" className="flex flex-col gap-3">
      <h4 className="text-base font-semibold text-foreground">Review</h4>
      <ol className="flex flex-col gap-3">
        {items.map((item) => (
          <li key={item.round} className={PANEL}>
            <p className="text-xs font-semibold text-text-secondary">Question {item.round}</p>
            <p dir="auto" className="mt-1 whitespace-pre-wrap break-words text-sm text-foreground">
              {item.stem}
            </p>
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
