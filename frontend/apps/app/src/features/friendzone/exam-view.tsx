"use client";

import { Button } from "@v1/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@v1/ui/dialog";
import { Sheet, SheetClose, SheetContent, SheetTitle } from "@v1/ui/sheet";
import { cn } from "@v1/ui/utils";
import { AlarmClock, CircleCheck, CloudOff, ListOrdered, LoaderCircle, Send, TriangleAlert } from "lucide-react";
import {
  type KeyboardEvent,
  type RefObject,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

import { useUnsavedChanges } from "@/features/settings/use-unsaved-changes";
import type { Battle, Exam, ResponseItem } from "./battle-api";
import { nameOf, shortcutChoice } from "./battle-model";
import { secondsLeft } from "./clock";
import {
  type Countdown,
  PALETTE_LABEL,
  type SaveStatus,
  clockText,
  countdownLeft,
  durationWords,
  examCounts,
  firstQuestion,
  focusAfterClose,
  type LayerClose,
  questionAnnouncement,
  saveAnnouncement,
  saveStatus,
  shortcutAllowed,
  stateOf,
  summaryCounts,
  summaryText,
  warnLevel,
} from "./exam-model";
import { ExamPalette, StateGlyph } from "./exam-palette";
import { type ExamSnapshot, type ExamStore, runDeadline } from "./exam-store";
import { QUESTION_PARAM, searchForQuestion } from "./model";
import { PlayerAvatar } from "./player";
import { alertDialogProps } from "./safety-confirm";

/**
 * The exam battle's CBT screen (plan section 6): a sticky header with one countdown, the save status and Submit; one
 * question at a time with native radios; Previous / Clear Response / Mark for Review & Next / Save & Next; and the
 * question palette, beside the question from xl and in a sheet below it. Answers save as they change (exam-store.ts);
 * the server's state is the truth, and at the deadline whatever is unsaved goes with the auto-submit.
 */

const PANEL = "rounded-2xl border border-border bg-card p-4 sm:p-6";
const ACTION = "h-auto min-h-11 whitespace-normal";

/** Seconds left to a deadline that never go up within one exam, however the clock offset moves. */
export function useCountdown(endsAt: string, offset: number, now: number): number {
  const last = useRef<Countdown | null>(null);
  const left = countdownLeft(last.current, endsAt, secondsLeft(endsAt, offset, now));
  last.current = { endsAt, left };
  return left;
}

function snapshotOf(exam: Exam): Pick<ExamSnapshot, "rows" | "intents" | "visits"> {
  return { rows: new Map(exam.responses.map((row) => [row.round, row])), intents: new Map(), visits: new Set() };
}

export type ExamViewProps = {
  battle: Battle;
  exam: Exam;
  store: ExamStore;
  offset: number;
  now: number;
  /** A submit is being sent. */
  busy: boolean;
  /** The one live region's text, mirrored inside an open sheet or dialog (Radix hides everything else). */
  said: string;
  say: (text: string) => void;
  headingRef: RefObject<HTMLHeadingElement | null>;
  titleId: string;
  onSubmit: (items: ResponseItem[], auto: boolean) => void;
  /** The store saw the battle move on (re-read it) or go away (404). */
  onServerChange: (gone: boolean) => void;
};

export function ExamView({ battle, exam, store, offset, now, busy, said, say, headingRef, titleId, onSubmit, onServerChange }: ExamViewProps) {
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  const total = exam.questions.length;
  const [current, setCurrent] = useState(() =>
    firstQuestion(
      typeof window === "undefined" ? null : new URLSearchParams(window.location.search).get(QUESTION_PARAM),
      total,
      (round) => Boolean((snapshot.intents.get(round) ?? snapshot.rows.get(round))?.choice),
    ),
  );
  const [sheetOpen, setSheetOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const left = useCountdown(exam.endsAt, offset, now);
  const timeUp = left === 0;
  const locked = timeUp || busy;
  const lockedNow = useRef(locked); // read after an await: the deadline may have passed meanwhile
  lockedNow.current = locked;
  const region = useRef<HTMLElement>(null);
  const timeUpHeading = useRef<HTMLHeadingElement>(null);
  const questionsButton = useRef<HTMLButtonElement>(null);
  const submitButton = useRef<HTMLButtonElement>(null);
  const closeFocus = useRef<LayerClose>("dismiss");
  const them = nameOf(battle.opponent);
  const counts = examCounts(snapshot, total);
  const status = saveStatus(snapshot);
  const question = exam.questions.find((item) => item.round === current) ?? exam.questions[0];
  const shownOf = (round: number) => snapshot.intents.get(round) ?? snapshot.rows.get(round);
  const shown = shownOf(current);

  useUnsavedChanges(snapshot.intents.size > 0);

  // The current question: kept in the address (a reload comes back to it) and recorded as visited.
  useEffect(() => {
    window.history.replaceState(window.history.state, "", searchForQuestion(window.location.search, current) || window.location.pathname);
    store.visit(current);
  }, [current, store]);

  // The deadline: lock, close any layer, focus the time-up heading, then auto-submit the queue (plan section 2).
  useEffect(() => {
    if (!timeUp) return;
    closeFocus.current = "deadline";
    setSheetOpen(false);
    setConfirmOpen(false);
    say("Time's up. Submitting your answers.");
    timeUpHeading.current?.focus();
    const handle = runDeadline(store, (items) => onSubmit(items, true));
    return () => clearTimeout(handle as ReturnType<typeof setTimeout>);
  }, [timeUp]); // once, when time runs out

  // 5 minutes and 1 minute left, said once each (never every second).
  const warned = useRef(warnLevel(left));
  useEffect(() => {
    const level = warnLevel(left);
    if (level !== null && level !== warned.current && left > 0) say(level === 5 ? "5 minutes left." : "1 minute left.");
    warned.current = level;
  }, [left, say]);

  const opponentDone = useRef(exam.opponentSubmitted);
  useEffect(() => {
    if (exam.opponentSubmitted && !opponentDone.current) say(`${them} has submitted.`);
    opponentDone.current = exam.opponentSubmitted;
  }, [exam.opponentSubmitted, them, say]);

  // Save-status changes are said (Saving… is not); a return to Saved only after a problem.
  const lastStatus = useRef<SaveStatus["kind"]>(status.kind);
  useEffect(() => {
    if (status.kind === "saving" || status.kind === lastStatus.current) return;
    const before = lastStatus.current;
    lastStatus.current = status.kind;
    if (status.kind === "saved" && before === "saved") return;
    const words = saveAnnouncement(status);
    if (words) say(words);
  }, [status, say]);

  const seenStale = useRef(snapshot.stale);
  useEffect(() => {
    if (snapshot.gone) onServerChange(true);
    else if (snapshot.stale !== seenStale.current) onServerChange(false);
    seenStale.current = snapshot.stale;
  }, [snapshot.stale, snapshot.gone, onServerChange]);

  const go = useCallback(
    (round: number) => {
      if (timeUp || round < 1 || round > total) return;
      setCurrent(round);
      const state = stateOf(snapshot, round);
      say(questionAnnouncement(round, total, state === "not-visited" ? "not-answered" : state));
    },
    [snapshot, total, say, timeUp],
  );

  const pick = (choice: string | null) => !locked && store.set(current, { choice, marked: shown?.marked ?? false });
  const actions = {
    previous: () => !locked && go(current - 1),
    clear: () => pick(null),
    markNext: () => {
      if (locked) return;
      store.set(current, { choice: shown?.choice ?? null, marked: true });
      go(current + 1);
    },
    saveNext: () => {
      if (locked) return;
      if (shown?.marked) store.set(current, { choice: shown.choice, marked: false });
      go(current + 1);
    },
  };

  const openConfirm = async () => {
    if (locked || preparing) return;
    setPreparing(true);
    await store.settled(); // the summary shows what the server has
    setPreparing(false);
    if (lockedNow.current) return; // time ran out while the saves settled: the time-up panel keeps focus
    closeFocus.current = "dismiss";
    setConfirmOpen(true);
  };

  const openSheet = () => {
    closeFocus.current = "dismiss";
    setSheetOpen(true);
  };

  /** Where focus goes when the sheet or the dialog closes (exam-model.ts `focusAfterClose`). */
  const restoreFocus = (opener: RefObject<HTMLButtonElement | null>) => (event: Event) => {
    event.preventDefault();
    const target = focusAfterClose(closeFocus.current);
    closeFocus.current = "dismiss";
    if (target === "heading") headingRef.current?.focus();
    else if (target === "opener") opener.current?.focus();
  };

  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    const target = event.target as HTMLElement;
    const allowed = shortcutAllowed({
      key: event.key,
      altKey: event.altKey,
      ctrlKey: event.ctrlKey,
      metaKey: event.metaKey,
      shiftKey: event.shiftKey,
      repeat: event.repeat,
      target,
      inRegion: region.current?.contains(target) ?? false,
      layerOpen: sheetOpen || confirmOpen,
      locked,
    });
    const choice = allowed && question ? shortcutChoice(event.key, question.options) : null;
    if (!choice) return;
    event.preventDefault();
    pick(choice);
  };

  const palette = (inSheet: boolean) => (
    <ExamPalette
      total={total}
      current={current}
      stateOf={(round) => stateOf(snapshot, round)}
      unsaved={(round) => snapshot.intents.has(round)}
      counts={counts}
      locked={timeUp}
      onPick={(round) => {
        go(round);
        if (inSheet) {
          closeFocus.current = "pill";
          setSheetOpen(false);
        }
      }}
    />
  );

  return (
    <section ref={region} aria-labelledby={titleId} onKeyDown={onKeyDown} className="flex flex-col gap-4">
      <ExamHeader
        battle={battle}
        exam={exam}
        titleId={titleId}
        left={left}
        status={status}
        locked={locked}
        preparing={preparing}
        questionsRef={questionsButton}
        submitRef={submitButton}
        onRetry={() => store.flush()}
        onQuestions={openSheet}
        onSubmit={openConfirm}
      />
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_17rem] xl:items-start">
        <div className="flex min-w-0 flex-col gap-4">
          {timeUp ? (
            <div className={PANEL}>
              <h4 ref={timeUpHeading} tabIndex={-1} className="focus-ring rounded-md text-base font-semibold text-foreground">
                Time's up: submitting…
              </h4>
              <p className="mt-1 text-sm text-text-secondary">Your answers go in now. The result shows when you both have submitted.</p>
            </div>
          ) : (
            question && (
              <>
                <ExamQuestion
                  round={question.round}
                  total={total}
                  stem={question.stem}
                  options={question.options}
                  choice={shown?.choice ?? null}
                  state={stateOf(snapshot, current)}
                  unsaved={snapshot.intents.has(current)}
                  locked={locked}
                  headingRef={headingRef}
                  onPick={pick}
                />
                <div className="grid grid-cols-2 gap-2 sm:flex sm:flex-wrap">
                  <Button type="button" variant="outline" className={ACTION} aria-disabled={locked || current === 1} onClick={actions.previous}>
                    Previous
                  </Button>
                  <Button type="button" variant="outline" className={ACTION} aria-disabled={locked || !shown?.choice} onClick={actions.clear}>
                    Clear Response
                  </Button>
                  <Button type="button" variant="outline" className={ACTION} aria-disabled={locked} onClick={actions.markNext}>
                    Mark for Review &amp; Next
                  </Button>
                  <Button type="button" className={ACTION} aria-disabled={locked} onClick={actions.saveNext}>
                    Save &amp; Next
                  </Button>
                </div>
              </>
            )
          )}
        </div>
        <aside aria-label="Question palette" className="hidden xl:sticky xl:top-28 xl:block">
          <div className={cn(PANEL, "p-4 sm:p-4")}>{palette(false)}</div>
        </aside>
      </div>
      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetContent
          side="right"
          aria-describedby={undefined}
          className="w-[min(20rem,85vw)] gap-1.5 overflow-y-auto p-3"
          onCloseAutoFocus={restoreFocus(questionsButton)}
        >
          <div className="flex items-center justify-between gap-2">
            <SheetTitle className="text-base font-semibold text-foreground">Questions</SheetTitle>
            <SheetClose asChild>
              <Button type="button" variant="outline" size="sm" className="min-h-11">
                Close
              </Button>
            </SheetClose>
          </div>
          <p role="status" className="sr-only">
            {said}
          </p>
          {palette(true)}
        </SheetContent>
      </Sheet>
      <SubmitConfirm
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        said={said}
        description={`${clockText(left)} left. ${summaryText(counts)} You can't change your answers after you submit.`}
        onCloseAutoFocus={restoreFocus(submitButton)}
        onConfirm={() => {
          closeFocus.current = "submit";
          setConfirmOpen(false);
          onSubmit(store.queue(), false);
        }}
      />
    </section>
  );
}

function ExamHeader({
  battle,
  exam,
  titleId,
  left,
  status,
  locked,
  preparing,
  questionsRef,
  submitRef,
  onRetry,
  onQuestions,
  onSubmit,
}: {
  battle: Battle;
  exam: Exam;
  titleId: string;
  left: number;
  status: SaveStatus;
  locked: boolean;
  preparing: boolean;
  questionsRef: RefObject<HTMLButtonElement | null>;
  submitRef: RefObject<HTMLButtonElement | null>;
  onRetry: () => void;
  onQuestions: () => void;
  onSubmit: () => void;
}) {
  const low = left <= 60;
  return (
    <header className="sticky top-0 z-20 flex flex-col gap-3 rounded-2xl border border-border bg-card p-3 sm:p-4">
      <div className="flex min-w-0 items-center gap-3">
        <PlayerAvatar player={{ initials: battle.opponent.initials ?? "", avatarUrl: null }} />
        <div className="min-w-0 flex-1">
          <h3 id={titleId} className="break-words text-lg font-semibold text-foreground">
            Battle with <bdi dir="auto">{nameOf(battle.opponent)}</bdi>
          </h3>
          <p className="text-sm text-text-secondary">
            <bdi dir="auto">{nameOf(battle.opponent)}</bdi>: {exam.opponentSubmitted ? "Submitted" : "In progress"}
          </p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <p role="timer" aria-label={`Time left: ${durationWords(left)}`} className={cn("flex items-center gap-1.5 text-lg font-semibold tabular-nums", low ? "text-warning" : "text-foreground")}>
          <AlarmClock aria-hidden className="h-5 w-5" />
          {clockText(left)}
        </p>
        <SaveStatusLine status={status} locked={locked} onRetry={onRetry} />
        <div className="ml-auto flex flex-wrap gap-2">
          <Button ref={questionsRef} type="button" variant="outline" className="min-h-11 xl:hidden" onClick={onQuestions}>
            <ListOrdered aria-hidden className="h-4 w-4" />
            Questions
          </Button>
          <Button ref={submitRef} type="button" className="min-h-11" aria-disabled={locked || preparing} onClick={onSubmit}>
            <Send aria-hidden className="h-4 w-4" />
            {preparing ? "Saving…" : "Submit"}
          </Button>
        </div>
      </div>
    </header>
  );
}

function SaveStatusLine({ status, locked, onRetry }: { status: SaveStatus; locked: boolean; onRetry: () => void }) {
  const icon = {
    saved: <CircleCheck aria-hidden className="h-4 w-4 text-success" />,
    saving: <LoaderCircle aria-hidden className="h-4 w-4 text-text-secondary" />,
    unsaved: <TriangleAlert aria-hidden className="h-4 w-4 text-warning" />,
    offline: <CloudOff aria-hidden className="h-4 w-4 text-warning" />,
  }[status.kind];
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm text-foreground">
      <span className="flex items-center gap-1.5">
        {icon}
        {status.text}
      </span>
      {!locked && (status.kind === "unsaved" || status.kind === "offline") && (
        <Button type="button" variant="outline" size="sm" className="min-h-11" onClick={onRetry}>
          Save again
        </Button>
      )}
    </div>
  );
}

function ExamQuestion({
  round,
  total,
  stem,
  options,
  choice,
  state,
  unsaved,
  locked,
  headingRef,
  onPick,
}: {
  round: number;
  total: number;
  stem: string;
  options: { key: string; text: string }[];
  choice: string | null;
  state: ReturnType<typeof stateOf>;
  unsaved: boolean;
  locked: boolean;
  headingRef: RefObject<HTMLHeadingElement | null>;
  onPick: (choice: string) => void;
}) {
  const id = useId();
  const last = options[options.length - 1]?.key ?? "D";
  return (
    <div className={PANEL}>
      <div className="flex flex-wrap items-center gap-2">
        <h4 ref={headingRef} tabIndex={-1} className="focus-ring rounded-md text-base font-semibold text-foreground">
          Question {round} of {total}
        </h4>
        <span className="flex items-center gap-1 rounded-full border border-border bg-surface-2 px-2 py-0.5 text-xs font-medium text-foreground">
          <StateGlyph state={state === "not-visited" ? "not-answered" : state} unsaved={unsaved} className="h-3 w-3 shrink-0" />
          {PALETTE_LABEL[state === "not-visited" ? "not-answered" : state]}
          {unsaved && " · Not saved"}
        </span>
      </div>
      <p dir="auto" className="mt-4 whitespace-pre-wrap break-words text-base text-foreground">
        {stem}
      </p>
      <fieldset className="mt-4" aria-describedby={`${id}-help`}>
        <legend className="sr-only">Options for question {round}</legend>
        <div className="flex flex-col gap-2">
          {options.map((option, index) => {
            const chosen = choice === option.key;
            return (
              <label
                key={option.key}
                className={cn(
                  "flex min-h-11 items-start gap-3 rounded-xl border px-4 py-3 text-sm text-foreground transition-colors",
                  chosen ? "border-brand bg-brand-soft" : "border-border bg-card",
                  locked ? "cursor-default" : "cursor-pointer hover:border-brand/50",
                )}
              >
                <input
                  type="radio"
                  name={`${id}-options`}
                  value={option.key}
                  checked={chosen}
                  disabled={locked}
                  aria-keyshortcuts={`${index + 1} ${option.key}`}
                  onChange={() => onPick(option.key)}
                  className="focus-ring mt-1 shrink-0 accent-brand"
                />
                <span className="shrink-0 font-semibold">{option.key}.</span>
                <span dir="auto" className="min-w-0 flex-1 break-words">
                  {option.text}
                </span>
              </label>
            );
          })}
        </div>
      </fieldset>
      <p id={`${id}-help`} className="mt-3 text-xs text-text-secondary">
        Keys 1–{options.length} or A–{last} choose an option, and so do the arrow keys on the options. A wrong answer costs 1 mark; Clear
        Response removes an answer.
      </p>
    </div>
  );
}

/** Before Submit: an alertdialog with the time left and the counts; Keep answering has focus (nothing is lost by Enter). */
function SubmitConfirm({
  open,
  onOpenChange,
  said,
  description,
  onCloseAutoFocus,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  said: string;
  description: string;
  onCloseAutoFocus: (event: Event) => void;
  onConfirm: () => void;
}) {
  const descriptionId = useId();
  const keep = useRef<HTMLButtonElement>(null);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        {...alertDialogProps(descriptionId)}
        className="sm:max-w-md"
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          keep.current?.focus();
        }}
        onCloseAutoFocus={onCloseAutoFocus}
      >
        <DialogHeader>
          <DialogTitle>Submit your answers?</DialogTitle>
          <DialogDescription id={descriptionId}>{description}</DialogDescription>
        </DialogHeader>
        <p role="status" className="sr-only">
          {said}
        </p>
        <DialogFooter className="gap-2 sm:gap-2">
          <Button ref={keep} type="button" variant="outline" className="min-h-11" onClick={() => onOpenChange(false)}>
            Keep answering
          </Button>
          <Button type="button" className="min-h-11" onClick={onConfirm}>
            Submit
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** After my submit: my own counts, the other player's status, and when the result comes. */
export function WaitingPanel({
  battle,
  exam,
  offset,
  now,
  headingRef,
}: {
  battle: Battle;
  exam: Exam;
  offset: number;
  now: number;
  headingRef: RefObject<HTMLHeadingElement | null>;
}) {
  const left = useCountdown(exam.endsAt, offset, now);
  const counts = summaryCounts(examCounts(snapshotOf(exam), exam.questions.length));
  return (
    <div className={PANEL}>
      <h4 ref={headingRef} tabIndex={-1} className="focus-ring rounded-md text-base font-semibold text-foreground">
        Submitted
      </h4>
      <dl className="mt-3 grid grid-cols-2 gap-x-4 gap-y-1 text-sm sm:grid-cols-4">
        {(
          [
            ["Answered", counts.answered],
            ["Not answered", counts.notAnswered],
            ["Marked, no answer", counts.marked],
            ["Not visited", counts.notVisited],
          ] as const
        ).map(([label, value]) => (
          <div key={label}>
            <dt className="text-text-secondary">{label}</dt>
            <dd className="font-semibold tabular-nums text-foreground">{value}</dd>
          </div>
        ))}
      </dl>
      <p className="mt-3 text-sm text-foreground">
        <bdi dir="auto">{nameOf(battle.opponent)}</bdi>: {exam.opponentSubmitted ? "Submitted" : "In progress"}
      </p>
      <p className="mt-1 text-sm text-text-secondary">
        The result shows when you both have submitted, or at the deadline ({clockText(left)} left).
      </p>
    </div>
  );
}

/** "How it works", on the invitation and Ready screens. */
export function HowItWorks({ length }: { length: number }) {
  return (
    <div className="mt-4">
      <h4 className="text-sm font-semibold text-foreground">How it works</h4>
      <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-text-secondary">
        <li>
          One timer for the whole exam: {length} questions in {length} minutes, the same paper for both of you.
        </li>
        <li>Answer in any order, and mark questions to review later.</li>
        <li>Change any answer until you submit. Every pick saves at once.</li>
        <li>Marks: +4 for a correct answer, −1 for an incorrect one, 0 for no answer.</li>
        <li>When time runs out, your saved answers are submitted for you.</li>
      </ul>
    </div>
  );
}

