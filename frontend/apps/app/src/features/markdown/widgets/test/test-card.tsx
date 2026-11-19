"use client";

import { Button } from "@v1/ui/button";
import { ConfirmDialog } from "@v1/ui/confirm-dialog";
import { cn } from "@v1/ui/utils";
import { ChevronLeft, ChevronRight, ClipboardCheck, Eraser, Eye, ListChecks, SkipForward, Trophy } from "lucide-react";
import { type Ref, type RefObject, useContext, useEffect, useId, useRef, useState } from "react";

import { focusLost } from "@/features/navigation/route-focus";
import { ApiError } from "@/lib/api/errors";
import type { Assessment, AssessmentQuestion, AssessmentValue } from "@/lib/api/types";
import { type PracticeDrafts, ReleaseScrollContext, type TestState } from "../../context";
import { InlineMarkdown } from "../../message-content";
import { CardFrame } from "../../study-card";
import { prefersReducedMotion } from "../tokens";
import { kindModule, useKinds } from "./kinds";
import { Navigator } from "./navigator";
import type { KindModule, TestMode } from "./question-props";
import { ListingView } from "./questions/listing";
import { Preparing, failure } from "./record";
import { ResultList, isV2, progressLine, questionList, resultLine, scoreLine } from "./review";
import { ScenarioPanel } from "./scenario";

/** What the block says (with its source) when a kind's code would not load, or the record names an unknown kind. */
export const LOAD_FAILED = "This test couldn't be loaded. Reload to try again.";
/** A mock exam saves a question this long after its last edit (and at once on blur, navigation and Submit). */
const SAVE_DELAY_MS = 1000;

/**
 * A failed request's line for the card, or null for a 409: the chat store has already read the test
 * again and posts its own notice ("Changed in another tab…"), which the card shows instead.
 */
export function problemOf(error: unknown, kind?: AssessmentQuestion["kind"]): string | null {
  return error instanceof ApiError && error.status === 409 ? null : failure(error, "test", kind);
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
// useKinds has loaded every kind the record uses before any of this draws.
const moduleOf = (question: AssessmentQuestion) => kindModule(question.kind) as KindModule<unknown>;
const without = <T,>(all: Readonly<Record<number, T>>, index: number) =>
  Object.fromEntries(Object.entries(all).filter(([key]) => key !== String(index))) as Record<number, T>;

function scrollTo(target: Element | null | undefined) {
  target?.scrollIntoView({ block: "nearest", behavior: prefersReducedMotion() ? "auto" : "smooth" });
}

/** The card's state from its kinds': "failed" (an unknown kind, or a chunk that wouldn't load) gives up. */
export function cardState(kinds: ReturnType<typeof useKinds>, test: Assessment): ReturnType<typeof useKinds> {
  return kinds === "ready" && !test.questions.every((question) => kindModule(question.kind)) ? "failed" : kinds;
}

/**
 * A refused value (a client check or the server's 422) made heard: its input takes focus, text selected, so the
 * error it is described by is read; a kind that binds no input (match, order, lines, diagram) has it said instead.
 */
export function sayRefused(field: HTMLElement | null, message: string, say: (line: string) => void): void {
  if (!field) {
    say(message);
    return;
  }
  field.focus({ preventScroll: true });
  if ("select" in field && typeof field.select === "function") field.select();
}

/** Exam: whether the server holds a complete answer (an order never touched holds none, and is graded skipped). */
export function holdsAnswer<V>(question: AssessmentQuestion, kind: KindModule<V>): boolean {
  const held = kind.fromRecord(question);
  return held !== null && kind.isComplete(question, held);
}

/**
 * Exam: what saving a value sends. The empty value clears, unless it is an answer (an order touched, left as shown);
 * null (Clear answer) always clears.
 */
export function examRequest<V>(
  question: AssessmentQuestion,
  kind: KindModule<V>,
  value: V | null,
): { action: "clear" } | { action: "save"; value: AssessmentValue } {
  return value === null || (same(value, kind.empty(question)) && !kind.isComplete(question, value))
    ? { action: "clear" }
    : { action: "save", value: kind.toRequest(question, value) };
}

/** Exam: the status line after a question's save failed or was refused. */
export const notSaved = (index: number) => `${questionList([index])} wasn't saved.`;

/** Exam: the status line once question `index` saved: its "wasn't saved" goes, any other line stays. */
export const afterSave = (line: string | null, index: number) => (line === notSaved(index) ? null : line);

/**
 * ```mcq and ```quiz: a test the server keeps (app/assessments). The card draws only the record: the
 * questions, the answers given (final, graded there), the waiting question, drafts and the score; nothing
 * is scored here. Each question is drawn by its kind's module (questions/*), loaded before the card draws.
 * Practice: one question at a time. A mock exam: any order, saved as drafts, graded on Submit, then reviewed.
 */
export function TestCard({
  test,
  single,
  tests,
  onError,
}: {
  test: Assessment;
  single: boolean;
  tests: TestState;
  onError: (note?: string) => void;
}) {
  const state = cardState(useKinds(test), test);
  useEffect(() => {
    if (state === "failed") onError(LOAD_FAILED);
  }, [state, onError]);
  if (state === "failed") return null;
  if (state === "loading") return <Preparing noun="test" />;
  return isV2(test) && test.mode === "exam" ? (
    <ExamCard test={test} tests={tests} />
  ) : (
    <PracticeCard test={test} single={single} tests={tests} />
  );
}

/** The test's scenario, once at the top, and the ids that describe each question's group. */
function useScenario(test: Assessment) {
  const id = useId();
  if (!test.scenario) return { panel: null, describedBy: undefined };
  const titleId = `${id}-scenario`;
  const summaryId = `${id}-summary`;
  return {
    panel: <ScenarioPanel scenario={test.scenario} titleId={titleId} summaryId={summaryId} />,
    describedBy: `${titleId} ${summaryId}`,
  };
}

/** One question's group: its legend (the name every input uses), its listing, and its kind's input. */
function QuestionGroup({
  question,
  index,
  single,
  mode,
  value,
  onChange,
  onCommit,
  onSubmit,
  disabled,
  tries,
  invalid,
  describedBy,
  fieldsetRef,
  inputRef,
}: {
  question: AssessmentQuestion;
  index: number;
  single: boolean;
  mode: TestMode;
  value: unknown;
  onChange: (value: unknown) => void;
  onCommit?: () => void;
  onSubmit?: () => void;
  disabled: boolean;
  tries: readonly string[];
  invalid?: string | null;
  describedBy?: string;
  fieldsetRef?: Ref<HTMLFieldSetElement>;
  inputRef?: Ref<HTMLElement>;
}) {
  const legend = useId();
  const { Input } = moduleOf(question);
  return (
    <fieldset
      ref={fieldsetRef}
      tabIndex={-1}
      aria-describedby={describedBy}
      className="min-w-0 scroll-my-4 outline-none"
      disabled={disabled}
    >
      <legend id={legend} className="mb-2 font-medium leading-6">
        {!single && <span className="mr-1 text-text-secondary">Q{index + 1}.</span>}
        <InlineMarkdown text={question.text ?? (question.kind === "fill_blank" ? "Fill in the blanks." : "")} />
      </legend>
      {/* A lines question's input is its listing (a list of selectable lines). */}
      {question.listing && question.kind !== "lines" ? <ListingView listing={question.listing} /> : null}
      {/* Keyed by question: an input's own state (a preview toggle, a half-typed value) never carries over. */}
      <Input
        key={index}
        question={question}
        number={index + 1}
        value={value}
        onChange={onChange}
        onCommit={onCommit}
        onSubmit={onSubmit}
        disabled={disabled}
        mode={mode}
        tries={tries}
        labelledBy={legend}
        invalid={invalid}
        inputRef={inputRef}
      />
    </fieldset>
  );
}

// ------------------------------------------------------------------ practice

type Acted = { question: number; tries: number; used: Element | null };

function PracticeCard({ test, single, tests }: { test: Assessment; single: boolean; tests: TestState }) {
  const release = useContext(ReleaseScrollContext);
  const scenario = useScenario(test);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  // A refused value of a kind with no single input, said only by the status line (its field shows it too).
  const [refused, setRefused] = useState<string | null>(null);
  const area = useRef<HTMLDivElement>(null);
  const fieldset = useRef<HTMLFieldSetElement>(null);
  const input = useRef<HTMLElement>(null);
  const score = useRef<HTMLParagraphElement>(null);
  const latest = useRef<HTMLLIElement>(null);
  // Focus and scroll only after this card's own action (or a typed command for this test): never on first draw.
  const acted = useRef<Acted | null>(null);
  const notice = tests.notice?.position === test.position ? tests.notice : null;
  const seenNotice = useRef(notice?.seq);
  const v2 = isV2(test);
  const current = test.current;
  const waiting = current === null ? null : test.questions[current];
  const tries = current !== null && test.pending?.question === current ? test.pending.tries : [];
  const lastAnswered = test.answered - 1;
  // A wrong try's input, waiting to be enabled again before it takes focus.
  const retry = useRef<HTMLElement | null>(null);
  const refocus = (field: HTMLElement) => {
    if (!focusLost() && !area.current?.contains(document.activeElement)) return; // the user went elsewhere
    field.focus({ preventScroll: true });
    if (field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement) field.select();
  };
  useEffect(() => {
    const field = retry.current;
    if (saving || !field) return;
    retry.current = null;
    refocus(field);
  }, [saving]);

  useEffect(() => {
    setRefused(null); // it was about the question as it was before this response or notice
    const typed = notice !== null && notice.seq !== seenNotice.current;
    if (typed) seenNotice.current = notice?.seq;
    const act = acted.current;
    acted.current = null;
    if (!act && !typed) return;
    if (act && act.question === current) {
      // A wrong try: back to the same input, its text selected to type over. While the try is still being
      // checked the input is disabled and can't take focus: then it does once `saving` ends (the effect below).
      const field = input.current;
      if (field && saving) retry.current = field;
      else if (field) refocus(field);
      scrollTo(field ?? area.current);
      return;
    }
    // A final answer: its draft is done with; focus goes to the next question's group, after the last to the
    // score (or the result of a single question).
    if (act) tests.drafts?.set(test.position, act.question, undefined);
    const target = current !== null ? fieldset.current : single ? latest.current : score.current;
    const usedGone = act && document.activeElement === act.used && (act.used as HTMLButtonElement).disabled === true;
    if (act && target && (focusLost() || usedGone)) target.focus({ preventScroll: true });
    scrollTo(target ?? area.current);
  }, [current, tries.length, notice]);

  const respond = async (action: "answer" | "skip", value?: AssessmentValue): Promise<string | null> => {
    if (current === null || saving) return null;
    release();
    setSaving(true);
    setProblem(null);
    setRefused(null);
    acted.current = { question: current, tries: tries.length, used: document.activeElement };
    try {
      await tests.respond(test.position, {
        hash: test.hash,
        question: current,
        action,
        ...(action === "answer" ? { value } : {}),
      });
      return null;
    } catch (error) {
      acted.current = null;
      const message = problemOf(error, waiting?.kind);
      if (error instanceof ApiError && error.status === 422) return message; // shown on the question's field
      setProblem(message); // null on a 409: the store's notice says it
      return null;
    } finally {
      setSaving(false);
    }
  };

  const left = (waiting?.maxTries ?? 3) - tries.length;
  const triesNote = tries.length ? `Not quite: ${left} ${left === 1 ? "try" : "tries"} left.` : null;
  // Read out after each response, a wrong try or a typed command (a live region announces changes, not a reload).
  const status =
    problem ?? refused ?? notice?.text ?? triesNote ?? (lastAnswered >= 0 ? resultLine(test, lastAnswered, single) : "");
  return (
    <CardFrame
      kind={single ? "mcq" : "quiz"}
      icon={ListChecks}
      label={single ? "Multiple choice" : "Practice test"}
      title={test.title ?? undefined}
    >
      {scenario.panel}
      {!single && current !== null && (
        <p className="mb-2 text-xs font-medium text-text-secondary">{progressLine(test, current)}</p>
      )}
      {test.questions.some((question) => question.response) && (
        <ResultList
          test={test}
          single={single}
          open={lastAnswered}
          ask={v2 ? tests.ask : undefined}
          latestRef={single ? latest : undefined}
          className={cn(waiting && "mb-3")}
        />
      )}
      <div ref={area} className="scroll-my-4">
        {current === null ? (
          !single ? (
            <p ref={score} tabIndex={-1} className="flex items-center gap-1.5 text-base font-semibold outline-none">
              <Trophy aria-hidden className="h-4 w-4 shrink-0 text-brand" />
              {scoreLine(test)}
            </p>
          ) : null
        ) : waiting ? (
          <PracticeQuestion
            key={current}
            test={test}
            index={current}
            single={single}
            saving={saving}
            tries={tries}
            drafts={tests.drafts}
            describedBy={scenario.describedBy}
            fieldsetRef={fieldset}
            inputRef={input}
            onRespond={respond}
            onRefused={setRefused}
          />
        ) : null}
      </div>
      <p
        role="status"
        className={cn(
          "text-xs",
          problem || notice || triesNote ? "mt-2" : "sr-only",
          problem ? "text-destructive" : "text-text-secondary",
        )}
      >
        {status}
      </p>
    </CardFrame>
  );
}

/** The waiting question, its value kept here (and in the chat's practice drafts) until it is checked. */
function PracticeQuestion({
  test,
  index,
  single,
  saving,
  tries,
  drafts,
  describedBy,
  fieldsetRef,
  inputRef,
  onRespond,
  onRefused,
}: {
  test: Assessment;
  index: number;
  single: boolean;
  saving: boolean;
  tries: readonly string[];
  drafts?: PracticeDrafts;
  describedBy?: string;
  fieldsetRef: Ref<HTMLFieldSetElement>;
  inputRef: RefObject<HTMLElement | null>;
  onRespond: (action: "answer" | "skip", value?: AssessmentValue) => Promise<string | null>;
  onRefused: (message: string | null) => void;
}) {
  const question = test.questions[index] as AssessmentQuestion;
  const kind = moduleOf(question);
  const v2 = isV2(test);
  const [value, setValue] = useState<unknown>(
    () => drafts?.get(test.position, index) ?? kind.fromRecord(question) ?? kind.empty(question),
  );
  const [invalid, setInvalid] = useState<string | null>(null);
  // Counts refusals, so the same message refused again is heard again (once its field is drawn and enabled).
  const [refusals, setRefusals] = useState(0);
  const complete = kind.isComplete(question, value);
  useEffect(() => {
    if (refusals && invalid) sayRefused(inputRef.current, invalid, onRefused);
  }, [refusals]);

  const change = (next: unknown) => {
    setValue(next);
    setInvalid(null);
    onRefused(null);
    drafts?.set(test.position, index, same(next, kind.empty(question)) ? undefined : (next as AssessmentValue));
  };
  const refuse = (message: string | null) => {
    setInvalid(message);
    if (message) setRefusals((count) => count + 1);
  };
  const check = async () => {
    if (saving || !complete) return;
    const pre = kind.check?.(question, value) ?? null;
    if (pre) return refuse(pre);
    refuse(await onRespond("answer", kind.toRequest(question, value)));
  };

  return (
    <div>
      <QuestionGroup
        question={question}
        index={index}
        single={single}
        mode="practice"
        value={value}
        onChange={change}
        onSubmit={() => void check()}
        disabled={!test.answerable || saving}
        tries={tries}
        invalid={invalid}
        describedBy={describedBy}
        fieldsetRef={fieldsetRef}
        inputRef={inputRef}
      />
      {test.answerable ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button type="button" disabled={!complete || saving} onClick={() => void check()}>
            {saving ? "Saving…" : tries.length ? "Try again" : "Check answer"}
          </Button>
          <Button type="button" variant="outline" disabled={saving} onClick={() => void onRespond("skip")}>
            {v2 ? <Eye aria-hidden className="h-4 w-4" /> : <SkipForward aria-hidden className="h-4 w-4" />}
            {v2 ? "Show answer" : "Skip"}
          </Button>
        </div>
      ) : (
        <p className="mt-3 text-xs text-text-secondary">This test can no longer be answered.</p>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ mock exam

/** The Submit dialog's line, from the server's drafts once every save has settled. */
export function missingLine(missing: number, total: number): string {
  if (!missing) return `All ${total} questions have an answer. You can't change them after you submit.`;
  const verb = missing === 1 ? "isn't" : "aren't";
  return `${missing} of ${total} questions ${verb} fully answered; anything left blank scores 0. You can't change answers after you submit.`;
}

type Phase = "idle" | "checking" | "ready" | "submitting";

function ExamCard({ test, tests }: { test: Assessment; tests: TestState }) {
  const release = useContext(ReleaseScrollContext);
  const scenario = useScenario(test);
  const [at, setAt] = useState(0);
  // Edits not yet confirmed by a reload: what the user sees over the server's drafts.
  const [local, setLocal] = useState<Readonly<Record<number, unknown>>>({});
  const [invalid, setInvalid] = useState<Readonly<Record<number, string>>>({});
  const [failed, setFailed] = useState<ReadonlySet<number>>(() => new Set());
  const [problem, setProblem] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const score = useRef<HTMLParagraphElement>(null);
  const submitButton = useRef<HTMLButtonElement>(null);
  const status = useRef<HTMLDivElement>(null);
  const retried = useRef(false);
  const timers = useRef(new Map<number, { value: unknown; timer: ReturnType<typeof setTimeout> }>());
  const inflight = useRef(new Set<Promise<void>>());
  // Timers and settled requests act on the record and the store as they are now, not as when they were set.
  const now = useRef({ test, tests });
  now.current = { test, tests };
  const notice = tests.notice?.position === test.position ? tests.notice : null;
  const seenNotice = useRef(notice?.seq);

  // A local null is a cleared answer (Clear answer): shown as the kind's empty value, sent as clear. Anything
  // else held (a partly matched question too, which is not yet an answer) can be cleared.
  const heldValue = (index: number) => {
    const question = test.questions[index] as AssessmentQuestion;
    return index in local ? local[index] : moduleOf(question).fromRecord(question);
  };
  const shownValue = (index: number) => {
    const question = test.questions[index] as AssessmentQuestion;
    return heldValue(index) ?? moduleOf(question).empty(question);
  };
  const mark = (index: number, unsaved: boolean) =>
    setFailed((all) => new Set(unsaved ? [...all, index] : [...all].filter((item) => item !== index)));
  // A 409: the store has dropped the queued saves and read the test again; its notice says so (not a line here).
  const reseed = () => {
    setLocal({});
    setFailed(new Set());
    setInvalid({});
    setProblem(null);
  };

  const send = (index: number, value: unknown) => {
    const { test: record, tests: store } = now.current;
    const question = record.questions[index];
    if (!question || record.submitted) return;
    const kind = moduleOf(question);
    const pre = value === null ? null : (kind.check?.(question, value) ?? null);
    if (pre) {
      setInvalid((all) => ({ ...all, [index]: pre }));
      mark(index, true);
      setProblem(notSaved(index)); // the status line says it (the field's error is not read out by itself)
      return;
    }
    const request = examRequest(question, kind, value);
    // Fire-and-forget (also from the unmount flush): every rejection ends here, never unhandled. Each
    // settle sets state, and that render reads the store's unsaved list (which notifies no one).
    const done: Promise<void> = store
      .respond(record.position, { hash: record.hash, question: index, ...request })
      .then(() => {
        mark(index, false);
        setInvalid((all) => without(all, index));
        setProblem((line) => afterSave(line, index));
      })
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.status === 409) return reseed();
        if (error instanceof ApiError && error.status === 422) {
          const message = failure(error, "test", question.kind);
          setInvalid((all) => ({ ...all, [index]: message }));
        }
        mark(index, true);
        setProblem(notSaved(index)); // said once: the same line again is not re-read
      })
      .finally(() => inflight.current.delete(done));
    inflight.current.add(done);
  };
  const schedule = (index: number, value: unknown) => {
    const pending = timers.current.get(index);
    if (pending) clearTimeout(pending.timer);
    const timer = setTimeout(() => {
      timers.current.delete(index);
      send(index, value);
    }, SAVE_DELAY_MS);
    timers.current.set(index, { value, timer });
  };
  const flush = (only?: number) => {
    for (const [index, { value, timer }] of timers.current) {
      if (only !== undefined && only !== index) continue;
      clearTimeout(timer);
      timers.current.delete(index);
      send(index, value);
    }
  };
  // On unmount (a chat switch, a reload of the list): every pending edit goes to the store's queue now.
  // `flush` reads only refs and state setters, so the first render's copy is enough.
  useEffect(() => () => flush(), []);

  const unsaved = [...new Set([...failed, ...(tests.unsaved?.(test.position) ?? [])])].sort((a, b) => a - b);
  useEffect(() => {
    if (!retried.current || unsaved.length) return;
    retried.current = false; // Save again is gone once everything saved: Submit is next
    if (focusLost()) submitButton.current?.focus({ preventScroll: true });
  }, [unsaved.length]);
  useEffect(() => {
    if (!notice || notice.seq === seenNotice.current) return;
    seenNotice.current = notice.seq;
    scrollTo(status.current);
  }, [notice]);

  const change = (index: number) => (value: unknown) => {
    setLocal((all) => ({ ...all, [index]: value }));
    setInvalid((all) => without(all, index));
    // An edit while Submit is blocked ends that check: the dialog never opens by itself once saves settle.
    setPhase((was) => (was === "ready" ? "idle" : was));
    schedule(index, value);
  };
  // Clear answer: the question shown holds nothing again (its edit still waiting to be saved is dropped).
  const clearAnswer = () => {
    const pending = timers.current.get(at);
    if (pending) clearTimeout(pending.timer);
    timers.current.delete(at);
    setLocal((all) => ({ ...all, [at]: null }));
    setInvalid((all) => without(all, at));
    setPhase((was) => (was === "ready" ? "idle" : was));
    send(at, null);
  };
  const go = (index: number) => {
    if (index < 0 || index >= test.questions.length || index === at) return;
    release(); // the user moved: the chat stops following the latest reply
    flush(at);
    setAt(index);
  };
  const blocked = phase === "ready" && unsaved.length > 0;
  const submit = async () => {
    if ((phase !== "idle" && !blocked) || !test.answerable) return;
    flush();
    setProblem(null);
    setPhase("checking");
    await Promise.allSettled([...inflight.current]);
    await tests.idle?.(test.position).catch(() => undefined);
    setPhase("ready"); // the dialog opens unless a question is still unsaved (read from the settled state)
  };
  const confirm = async () => {
    if (phase !== "ready") return;
    release();
    setPhase("submitting");
    try {
      await tests.respond(test.position, { hash: test.hash, question: 0, action: "submit" });
      setSaid("Submitted.");
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) reseed();
      else if (error instanceof ApiError && error.status === 429) setProblem(failure(error));
      else setProblem("Your answers weren't submitted. Check your connection and try again.");
    } finally {
      setPhase("idle");
    }
  };
  const retry = () => {
    setPhase("idle");
    setProblem(null);
    retried.current = true;
    for (const index of unsaved) send(index, index in local ? local[index] : shownValue(index)); // null: a clear
  };

  // Counted from what is held, not from what is shown: the dialog (once saves settle) from the server's drafts,
  // the pills from an edit not yet confirmed or else the server's draft.
  const missing = test.questions.filter((question) => !holdsAnswer(question, moduleOf(question))).length;
  const answered = test.questions.map((item, index) =>
    index in local
      ? local[index] !== null && moduleOf(item).isComplete(item, local[index])
      : holdsAnswer(item, moduleOf(item)),
  );
  const line = blocked
    ? `${questionList(unsaved)} ${unsaved.length === 1 ? "isn't" : "aren't"} saved yet.`
    : (problem ?? notice?.text ?? said ?? "");
  const question = test.questions[at];
  return (
    <CardFrame kind="exam" icon={ClipboardCheck} label="Mock exam" title={test.title ?? undefined}>
      {scenario.panel}
      {test.submitted ? (
        <>
          <p ref={score} tabIndex={-1} className="mb-3 flex items-center gap-1.5 text-base font-semibold outline-none">
            <Trophy aria-hidden className="h-4 w-4 shrink-0 text-brand" />
            {scoreLine(test)}
          </p>
          <ResultList test={test} single={false} open={null} compact ask={tests.ask} />
        </>
      ) : (
        <>
          <Navigator
            count={test.questions.length}
            at={at}
            answered={answered}
            unsaved={new Set(unsaved)}
            onPick={go}
          />
          {question ? (
            <QuestionGroup
              key={at}
              question={question}
              index={at}
              single={false}
              mode="exam"
              value={shownValue(at)}
              onChange={change(at)}
              onCommit={() => flush(at)}
              onSubmit={() => flush(at)}
              disabled={!test.answerable}
              tries={[]}
              invalid={invalid[at] ?? null}
              describedBy={scenario.describedBy}
            />
          ) : null}
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {/* aria-disabled, not disabled: at an end the button keeps focus (the focus table). */}
            <Button
              type="button"
              variant="outline"
              aria-disabled={at === 0}
              className="aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
              onClick={() => go(at - 1)}
            >
              <ChevronLeft aria-hidden className="h-4 w-4" />
              Previous
            </Button>
            <Button
              type="button"
              variant="outline"
              aria-disabled={at === test.questions.length - 1}
              className="aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
              onClick={() => go(at + 1)}
            >
              Next
              <ChevronRight aria-hidden className="h-4 w-4" />
            </Button>
            {test.answerable ? (
              <Button
                type="button"
                variant="outline"
                aria-disabled={heldValue(at) === null}
                className="aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
                onClick={() => heldValue(at) !== null && clearAnswer()}
              >
                <Eraser aria-hidden className="h-4 w-4" />
                Clear answer
              </Button>
            ) : null}
            {test.answerable ? (
              <Button
                ref={submitButton}
                type="button"
                aria-disabled={phase !== "idle" && !blocked}
                className="ml-auto aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
                onClick={() => void submit()}
              >
                {phase === "checking" ? "Saving…" : "Submit"}
              </Button>
            ) : (
              <p className="text-xs text-text-secondary">This test can no longer be answered.</p>
            )}
          </div>
        </>
      )}
      <div ref={status} className={cn("flex flex-wrap items-center gap-x-3 gap-y-1 scroll-my-4", line && "mt-2")}>
        <p
          role="status"
          className={cn("text-xs", line ? "" : "sr-only", problem || blocked ? "text-destructive" : "text-text-secondary")}
        >
          {line}
        </p>
        {unsaved.length > 0 && !test.submitted ? (
          <Button type="button" variant="outline" onClick={retry}>
            Save again
          </Button>
        ) : null}
      </div>
      <ConfirmDialog
        open={(phase === "ready" && !unsaved.length) || phase === "submitting"}
        onOpenChange={(open) => {
          if (!open && phase !== "submitting") setPhase("idle");
        }}
        title="Submit your answers?"
        description={missingLine(missing, test.questions.length)}
        confirmLabel="Submit"
        pending={phase === "submitting"}
        onConfirm={confirm}
        onCloseAutoFocus={(event) => {
          // Decided by the record: the score is drawn only once the store holds a submitted one (it stores before
          // respond resolves; a 409 may show another tab's submit). Cancelled or not submitted: focus returns to Submit.
          if (!score.current) return;
          event.preventDefault();
          score.current.focus({ preventScroll: true });
          scrollTo(score.current);
        }}
      />
    </CardFrame>
  );
}
