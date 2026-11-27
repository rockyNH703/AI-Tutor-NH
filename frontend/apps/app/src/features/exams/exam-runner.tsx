"use client";

import { Button } from "@v1/ui/button";
import { Sheet, SheetClose, SheetContent, SheetTitle } from "@v1/ui/sheet";
import { cn } from "@v1/ui/utils";
import { AlarmClock, CircleCheck, CloudOff, ListOrdered, LoaderCircle, Send, TriangleAlert } from "lucide-react";
import { type KeyboardEvent, type RefObject, useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

import { shortcutChoice } from "@/features/friendzone/battle-model";
import { msUntil } from "@/features/friendzone/clock";
import {
  type LayerClose,
  clockText,
  durationWords,
  examCounts,
  firstQuestion,
  focusAfterClose,
  shortcutAllowed,
  stateOf,
  warnLevel,
} from "@/features/friendzone/exam-model";
import { ExamPalette } from "@/features/friendzone/exam-palette";
import { AUTO_SUBMIT_DELAY_MS } from "@/features/friendzone/exam-store";
import { backoff } from "@/features/friendzone/poll";
import { useUnsavedChanges } from "@/features/settings/use-unsaved-changes";
import { ApiError } from "@/lib/api/errors";
import { type PracticeExam, getExam, saveExamResponses, submitExam } from "@/lib/api/practice-exams";
import { EndTestDialog } from "./end-test-dialog";
import { createExamSaver } from "./exam-saver";
import { delay, onceOnline, useNow, useOnline, useSecondsLeft } from "./exam-hooks";
import { ExamQuestion } from "./exam-question";
import { type SaveLine, choiceOf, endTestSummary, keysOf, notCounted, saveLine, sessionStore, toggleKey } from "./practice-model";

/**
 * The practice exam (the owner's CBT reference): a dark navigator on the left (the palette, its legend and counts; a
 * sheet below the xl breakpoint), a top bar with the program, the countdown and End Test, one question at a time, and
 * Previous / Clear / Mark for review & Next / Save & Next. Answers save as they change (exam-saver.ts); the server's
 * clock and state decide, and the screen never calls an answer saved that the server doesn't have.
 */

const ACTION = "h-auto min-h-11 whitespace-normal";
const MANUAL_TRIES = 3;
const OFFLINE_SUBMIT = "Offline: your test will be submitted when you reconnect.";

export type Finished = { exam: PracticeExam; notCounted: number[] };

function retryable(error: unknown): boolean {
  return !(error instanceof ApiError) || error.code === "network_error" || error.status === 429 || error.status >= 500;
}

export function ExamRunner({ exam, offset, onFinished, onGone }: { exam: PracticeExam; offset: number; onFinished: (done: Finished) => void; onGone: () => void }) {
  const id = exam.examId;
  const [saver] = useState(() => {
    const created = createExamSaver({
      examId: id,
      rev: exam.rev,
      put: (baseRev, items, signal) => saveExamResponses(id, baseRev, items, signal),
      reread: () => getExam(id),
      storage: sessionStore(),
    });
    created.adopt(exam); // the server's answers from the first paint (the palette never starts all "Not visited")
    return created;
  });
  const store = saver.store;
  const snapshot = useSyncExternalStore(store.subscribe, store.getSnapshot, store.getSnapshot);
  const total = exam.questions.length;
  const online = useOnline();
  const now = useNow();
  const left = useSecondsLeft(exam.endsAt, offset, now);
  const timeUp = left === 0;
  const [current, setCurrent] = useState(() =>
    firstQuestion(null, total, (round) => exam.responses.some((row) => row.index === round - 1 && row.keys.length > 0)),
  );
  const [sheetOpen, setSheetOpen] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [said, setSaid] = useState("");
  const locked = timeUp || busy;
  const finished = useRef(false);
  const timeUpRef = useRef(timeUp);
  timeUpRef.current = timeUp;
  const headingRef = useRef<HTMLHeadingElement>(null);
  const timeUpHeading = useRef<HTMLHeadingElement>(null);
  const questionsButton = useRef<HTMLButtonElement>(null);
  const endButton = useRef<HTMLButtonElement>(null);
  const region = useRef<HTMLDivElement>(null);
  const closeFocus = useRef<LayerClose>("dismiss");
  const focusHeading = useRef(false);
  const counts = examCounts(snapshot, total);
  const status = saveLine(snapshot, online);
  const shownOf = (round: number) => snapshot.intents.get(round) ?? snapshot.rows.get(round);
  const shown = shownOf(current);
  const question = exam.questions[current - 1] ?? exam.questions[0];
  const answered = counts.answered + counts["answered-marked"];

  useUnsavedChanges(!finished.current && (snapshot.intents.size > 0 || busy));

  // Whatever this device kept from before a reload goes back in the queue (and is sent).
  useEffect(() => {
    saver.replay();
  }, [saver]);

  useEffect(() => store.visit(current), [current, store]);
  useEffect(() => {
    if (focusHeading.current) headingRef.current?.focus();
    focusHeading.current = false;
  }, [current]);

  const finish = useCallback(
    (done: PracticeExam) => {
      if (finished.current || done.status !== "submitted" || !done.result) return;
      finished.current = true;
      const lost = notCounted(done.result.review, saver.mirrored(), done.lateItems ?? []);
      saver.finish();
      onFinished({ exam: done, notCounted: lost });
    },
    [saver, onFinished],
  );

  /** After a time-up submit that failed: the server submits the saved answers itself at the cutoff; read until then. */
  const awaitServerSubmit = useCallback(async () => {
    setNote("Time's up. The server is submitting the answers it has saved.");
    for (let failures = 0; !finished.current; failures += 1) {
      const untilCutoff = msUntil(exam.cutoffAt, offset, Date.now()) ?? 0;
      await delay(untilCutoff > 0 ? untilCutoff + 300 : backoff(Math.max(1, failures)));
      await onceOnline();
      try {
        const latest = await getExam(id);
        if (latest.status === "submitted") return finish(latest);
      } catch (error) {
        if (error instanceof ApiError && error.code === "not_found") return onGone();
      }
    }
  }, [exam.cutoffAt, offset, id, finish, onGone]);

  /** A read after a submit the server didn't take: the result if it is there, else back to answering. */
  const readBack = useCallback(async () => {
    try {
      const latest = await getExam(id);
      if (latest.status === "submitted") return finish(latest);
      saver.adopt(latest);
    } catch (error) {
      if (error instanceof ApiError && error.code === "not_found") return onGone();
    }
    if (timeUpRef.current) return awaitServerSubmit();
    setBusy(false);
    setNote("Your test couldn't be submitted. Check your connection and try End test again.");
  }, [id, saver, finish, onGone, awaitServerSubmit]);

  const endTest = useCallback(async () => {
    if (finished.current) return;
    setBusy(true);
    await store.settled(); // a save in flight lands first, so its answer isn't sent twice
    for (let attempt = 1; !finished.current && !timeUpRef.current; attempt += 1) {
      if (!navigator.onLine) {
        setNote(OFFLINE_SUBMIT);
        setSaid(OFFLINE_SUBMIT);
        await onceOnline();
        continue;
      }
      setNote("Submitting…");
      try {
        return finish(await submitExam(id, { baseRev: null, items: saver.wire(), auto: false }));
      } catch (error) {
        if (!retryable(error) || attempt >= MANUAL_TRIES) return readBack();
        await delay(backoff(attempt));
      }
    }
  }, [store, id, saver, finish, readBack]);

  // Time's up: lock, close any layer, focus the notice, then one final submit with what is unsent.
  useEffect(() => {
    if (!timeUp || finished.current) return;
    closeFocus.current = "deadline";
    setSheetOpen(false);
    setConfirmOpen(false);
    store.lock();
    setSaid("Time's up. Submitting your answers.");
    timeUpHeading.current?.focus();
    const timer = setTimeout(() => {
      submitExam(id, { baseRev: null, items: saver.wire(), auto: true }).then(finish, () => void awaitServerSubmit());
    }, AUTO_SUBMIT_DELAY_MS);
    return () => clearTimeout(timer);
  }, [timeUp]); // once, when time runs out

  // 5 minutes and 1 minute left, said once each.
  const warned = useRef(warnLevel(left));
  useEffect(() => {
    const level = warnLevel(left);
    if (level !== null && level !== warned.current && left > 0) setSaid(level === 5 ? "5 minutes left." : "1 minute left.");
    warned.current = level;
  }, [left]);

  // The store saw a conflict or the exam go away: read it again (a submitted exam opens its result).
  const seenStale = useRef(snapshot.stale);
  useEffect(() => {
    if (snapshot.gone) onGone();
    else if (snapshot.stale !== seenStale.current && !finished.current) {
      getExam(id).then(
        (latest) => (latest.status === "submitted" ? finish(latest) : saver.adopt(latest)),
        (error) => error instanceof ApiError && error.code === "not_found" && onGone(),
      );
    }
    seenStale.current = snapshot.stale;
  }, [snapshot.stale, snapshot.gone, id, saver, finish, onGone]);

  const go = (round: number) => {
    if (timeUp || round < 1 || round > total) return;
    focusHeading.current = true;
    setCurrent(round);
  };
  const set = (choice: string | null, marked = shown?.marked ?? false) => !locked && store.set(current, { choice, marked });
  const choose = (key: string) => {
    if (locked || !question) return;
    set(question.multi ? choiceOf(toggleKey(keysOf(shown?.choice), key)) : key);
  };
  const actions = {
    previous: () => !locked && go(current - 1),
    clear: () => set(null),
    markNext: () => {
      if (locked) return;
      set(shown?.choice ?? null, true);
      go(current + 1);
    },
    saveNext: () => {
      if (locked) return;
      if (shown?.marked) set(shown.choice, false);
      go(current + 1);
    },
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
    const key = allowed && question ? shortcutChoice(event.key, question.options) : null;
    if (!key) return;
    event.preventDefault();
    choose(key);
  };

  const restoreFocus = (opener: RefObject<HTMLButtonElement | null>) => (event: Event) => {
    event.preventDefault();
    const target = focusAfterClose(closeFocus.current);
    closeFocus.current = "dismiss";
    if (target === "heading") headingRef.current?.focus();
    else if (target === "opener") opener.current?.focus();
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
    <div className="app-frame bg-background">
      <aside aria-label="Question navigator" className="rail hidden h-full w-72 shrink-0 flex-col gap-4 overflow-y-auto border-r border-border p-4 xl:flex">
        <p className="text-sm font-semibold">
          {answered} of {total} answered
        </p>
        {palette(false)}
      </aside>
      <div ref={region} onKeyDown={onKeyDown} className="flex min-w-0 flex-1 flex-col">
        <ExamHeader
          exam={exam}
          left={left}
          answered={answered}
          locked={locked}
          questionsRef={questionsButton}
          endRef={endButton}
          onQuestions={() => {
            closeFocus.current = "dismiss";
            setSheetOpen(true);
          }}
          onEnd={async () => {
            if (locked) return;
            await store.settled(); // the summary shows what the server has
            if (timeUpRef.current) return;
            closeFocus.current = "dismiss";
            setConfirmOpen(true);
          }}
        />
        <main id="main" className="scrollbar-subtle min-h-0 flex-1 overflow-y-auto overscroll-contain">
          <div className="page-container flex flex-col gap-4">
            <SaveStatus status={status} locked={locked} note={note} onRetry={() => store.flush()} />
            {timeUp ? (
              <div className="rounded-2xl border border-border bg-card p-4 sm:p-6">
                <h2 ref={timeUpHeading} tabIndex={-1} className="focus-ring rounded-md text-lg font-semibold text-foreground">
                  Time's up
                </h2>
                <p className="mt-1 text-sm text-text-secondary">
                  Your answers are being submitted. Answers that never reached the server can't be counted; the result will say which.
                </p>
              </div>
            ) : (
              question && (
                <ExamQuestion
                  question={question}
                  total={total}
                  rules={exam.rules}
                  keys={keysOf(shown?.choice)}
                  state={stateOf(snapshot, current)}
                  unsaved={snapshot.intents.has(current)}
                  locked={locked}
                  headingRef={headingRef}
                  onChoose={choose}
                />
              )
            )}
          </div>
        </main>
        {!timeUp && (
          <div className="grid shrink-0 grid-cols-2 gap-2 border-t border-border bg-card px-4 py-3 sm:flex sm:flex-wrap sm:justify-end sm:px-6">
            <Button type="button" variant="outline" className={ACTION} aria-disabled={locked || current === 1} onClick={actions.previous}>
              Previous
            </Button>
            <Button type="button" variant="outline" className={ACTION} aria-disabled={locked || !shown?.choice} onClick={actions.clear}>
              Clear
            </Button>
            <Button type="button" variant="outline" className={ACTION} aria-disabled={locked} onClick={actions.markNext}>
              Mark for review &amp; Next
            </Button>
            <Button type="button" className={ACTION} aria-disabled={locked} onClick={actions.saveNext}>
              Save &amp; Next
            </Button>
          </div>
        )}
        <p role="status" className="sr-only">
          {said}
        </p>
      </div>
      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetContent side="left" aria-describedby={undefined} className="rail w-[min(20rem,85vw)] gap-3 overflow-y-auto p-3" onCloseAutoFocus={restoreFocus(questionsButton)}>
          <div className="flex items-center justify-between gap-2">
            <SheetTitle className="text-base font-semibold">Questions</SheetTitle>
            <SheetClose asChild>
              <Button type="button" variant="outline" size="sm" className="min-h-11">
                Close
              </Button>
            </SheetClose>
          </div>
          <p className="text-sm">
            {answered} of {total} answered
          </p>
          <p role="status" className="sr-only">
            {said}
          </p>
          {palette(true)}
        </SheetContent>
      </Sheet>
      <EndTestDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        summary={endTestSummary(snapshot, total)}
        left={clockText(left)}
        online={online}
        said={said}
        onCloseAutoFocus={restoreFocus(endButton)}
        onConfirm={() => {
          closeFocus.current = "submit";
          setConfirmOpen(false);
          void endTest();
        }}
      />
    </div>
  );
}

function ExamHeader({
  exam,
  left,
  answered,
  locked,
  questionsRef,
  endRef,
  onQuestions,
  onEnd,
}: {
  exam: PracticeExam;
  left: number;
  answered: number;
  locked: boolean;
  questionsRef: RefObject<HTMLButtonElement | null>;
  endRef: RefObject<HTMLButtonElement | null>;
  onQuestions: () => void;
  onEnd: () => void;
}) {
  const low = left <= 60;
  return (
    <header className="z-10 flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-border bg-card px-4 py-2 sm:px-6">
      <div className="min-w-0 flex-1 basis-48">
        <p className="truncate text-xs text-text-secondary">
          <bdi dir="auto">{exam.program.title}</bdi> · Month {exam.month}
        </p>
        <h1 className="truncate text-base font-semibold text-foreground">
          <bdi dir="auto">{exam.title}</bdi>
        </h1>
      </div>
      <p
        role="timer"
        aria-label={`Time left: ${durationWords(left)}`}
        className={cn("flex items-center gap-1.5 text-lg font-semibold tabular-nums", low ? "text-warning" : "text-foreground")}
      >
        <AlarmClock aria-hidden className="h-5 w-5" />
        {clockText(left)}
      </p>
      <div className="flex flex-wrap gap-2">
        <Button ref={questionsRef} type="button" variant="outline" className="min-h-11 xl:hidden" onClick={onQuestions}>
          <ListOrdered aria-hidden className="h-4 w-4 max-sm:hidden" />
          Questions ({answered} answered)
        </Button>
        <Button ref={endRef} type="button" className="min-h-11" aria-disabled={locked || undefined} onClick={onEnd}>
          <Send aria-hidden className="h-4 w-4 max-sm:hidden" />
          End Test
        </Button>
      </div>
    </header>
  );
}

function SaveStatus({ status, locked, note, onRetry }: { status: SaveLine; locked: boolean; note: string; onRetry: () => void }) {
  const icon = {
    saved: <CircleCheck aria-hidden className="h-4 w-4 shrink-0 text-success" />,
    saving: <LoaderCircle aria-hidden className="h-4 w-4 shrink-0 text-text-secondary" />,
    unsaved: <TriangleAlert aria-hidden className="h-4 w-4 shrink-0 text-warning" />,
    offline: <CloudOff aria-hidden className="h-4 w-4 shrink-0 text-warning" />,
  }[status.kind];
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2 text-sm text-foreground">
        <p role="status" className="flex min-w-0 flex-1 items-start gap-1.5">
          {icon}
          <span>{status.text}</span>
        </p>
        {!locked && status.kind === "unsaved" && (
          <Button type="button" variant="outline" size="sm" className="min-h-11" onClick={onRetry}>
            Not saved: retry
          </Button>
        )}
      </div>
      <p role="status" className="text-sm font-medium text-foreground">
        {note}
      </p>
    </div>
  );
}
