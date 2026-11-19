"use client";

import { Button } from "@v1/ui/button";
import { cn } from "@v1/ui/utils";
import { MessageSquareText } from "lucide-react";
import { type Ref, useState } from "react";

import type { Assessment, AssessmentQuestion } from "@/lib/api/types";
import type { TestState } from "../../context";
import { kindModule } from "./kinds";
import { ListingView } from "./questions/listing";

/** The chat's message limit: a feedback request never goes past it. */
export const ASK_MAX = 16_000;

const plural = (count: number, one: string, many: string) => (count === 1 ? one : many);

/** Whether a v2 record's rules apply (points, Show answer, modes); v1 keeps its own copy exactly. */
export const isV2 = (test: Assessment) => test.schemaVersion >= 2;

/** "Question 4 of 5 · 7 of 9 points so far" (v2) or "Question 2 of 2 · 1 correct so far" (v1). Not live. */
export function progressLine(test: Assessment, current: number): string {
  const at = `Question ${current + 1} of ${test.total}`;
  if (!test.answered) return at;
  if (!isV2(test)) return `${at} · ${test.earned} correct so far`;
  const possible = test.questions.reduce((sum, question) => sum + (question.response?.possible ?? 0), 0);
  return `${at} · ${test.earned} of ${possible} ${plural(possible, "point", "points")} so far`;
}

/** The final line: "You scored 7 of 12 points: 3 of 5 questions fully right, 1 partly." (v1: "You scored 1 out of 2."). */
export function scoreLine(test: Assessment): string {
  if (!isV2(test)) return `You scored ${test.earned} out of ${test.possible}.`;
  const responses = test.questions.flatMap((question) => (question.response ? [question.response] : []));
  const fully = responses.filter((response) => response.earned > 0 && response.earned === response.possible).length;
  const partly = responses.filter((response) => response.earned > 0 && response.earned < response.possible).length;
  return (
    `You scored ${test.earned} of ${test.possible} ${plural(test.possible, "point", "points")}: ` +
    `${fully} of ${test.total} ${plural(test.total, "question", "questions")} fully right${partly ? `, ${partly} partly` : ""}.`
  );
}

/** One line per result, for the status region: v1 exactly as before; v2 says partial marks and Show answer. */
export function resultLine(test: Assessment, index: number, single: boolean): string {
  const response = test.questions[index]?.response;
  if (!response) return "";
  if (!isV2(test)) {
    return `${single ? "" : `Question ${index + 1}: `}${response.status === "skipped" ? "skipped" : response.earned ? "correct" : "not quite"}.`;
  }
  const word =
    response.status === "skipped"
      ? "answer shown"
      : response.earned === response.possible && response.earned > 0
        ? "correct"
        : response.earned > 0
          ? `partly right, ${response.earned} of ${response.possible} points`
          : "not quite";
  return `Question ${index + 1}: ${word}.`;
}

/** "Question 3", "Questions 2 and 5", "Questions 1, 2 and 5" (0-based in, 1-based out). */
export function questionList(indices: readonly number[]): string {
  const numbers = [...indices].sort((a, b) => a - b).map((index) => String(index + 1));
  if (numbers.length < 2) return `Question ${numbers[0] ?? ""}`.trim();
  return `Questions ${numbers.slice(0, -1).join(", ")} and ${numbers.at(-1)}`;
}

/** A written answer that was graded: what Get feedback can send. */
export function canAskFeedback(question: AssessmentQuestion): boolean {
  const response = question.response;
  return (
    (question.kind === "short" || question.kind === "long") &&
    response?.status === "answered" &&
    typeof response.answer === "string" &&
    response.answer.trim() !== ""
  );
}

/**
 * The chat message Get feedback sends, built only from what the record revealed (the question is final,
 * or the exam submitted): the question, the key points found and missed, and the full answer.
 */
export function feedbackMessage(test: Assessment, question: AssessmentQuestion, index: number): string {
  const points = question.keyPoints ?? [];
  const marks = question.response?.marks ?? [];
  const missing = points.filter((_, at) => !marks[at]);
  const answer = typeof question.response?.answer === "string" ? question.response.answer : "";
  const head =
    `Review my answer to Q${index + 1} '${question.text ?? ""}'${test.title ? ` in '${test.title}'` : ""}. ` +
    `Key points found ${points.length - missing.length} of ${points.length}` +
    `${missing.length ? ` (missing: ${missing.join("; ")})` : ""}. My answer: `;
  return `${head}${answer}`.slice(0, ASK_MAX);
}

const ASKED: Record<"idle" | "sending" | "sent" | "busy" | "refused", string> = {
  idle: "",
  sending: "Asking…",
  sent: "Asked in the chat below.",
  busy: "A reply is still being written. Try again when it's done.",
  refused: "That couldn't be sent. Try again.",
};

/**
 * Get feedback on a written answer: asks the tutor in this chat (TestState.ask), never through the
 * composer. A busy chat says so and stays usable (the card has no signal for when the reply ends).
 */
function FeedbackButton({
  test,
  question,
  index,
  ask,
}: {
  test: Assessment;
  question: AssessmentQuestion;
  index: number;
  ask: NonNullable<TestState["ask"]>;
}) {
  const [state, setState] = useState<keyof typeof ASKED>("idle");
  const send = async () => {
    if (state === "sending") return;
    setState("sending");
    try {
      setState(await ask(feedbackMessage(test, question, index)));
    } catch {
      setState("refused");
    }
  };
  return (
    <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1">
      <Button
        type="button"
        variant="outline"
        aria-disabled={state === "sending"}
        className="aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
        onClick={() => void send()}
      >
        <MessageSquareText aria-hidden className="h-4 w-4" />
        Get feedback
      </Button>
      <span role="status" className="text-xs text-text-secondary">
        {ASKED[state]}
      </span>
    </div>
  );
}

/**
 * The answered questions, each drawn by its kind's Result (read-only: what was given, what was right,
 * marks, Why) and its listing, with Get feedback under a graded written answer. Practice opens the
 * latest one's Why; the exam review also draws each diagram in place (`compact`).
 */
export function ResultList({
  test,
  single,
  open,
  compact = false,
  ask,
  latestRef,
  className,
}: {
  test: Assessment;
  single: boolean;
  /** The question whose Why starts open (and the focus target of a one-question block), if any. */
  open: number | null;
  compact?: boolean;
  ask?: TestState["ask"];
  latestRef?: Ref<HTMLLIElement>;
  className?: string;
}) {
  return (
    <ol className={cn("space-y-2", className)}>
      {test.questions.map((question, index) => {
        const Result = question.response ? kindModule(question.kind)?.Result : undefined;
        if (!Result) return null;
        return (
          <li
            // biome-ignore lint/suspicious/noArrayIndexKey: questions are positions in the test
            key={index}
            ref={index === open ? latestRef : undefined}
            tabIndex={index === open && latestRef ? -1 : undefined}
            className="min-w-0 rounded-lg border border-border bg-card px-3 py-2 text-sm leading-6 outline-none"
          >
            <Result question={question} number={single ? null : index + 1} open={index === open} compact={compact} />
            {/* Once, for every kind; a lines result draws its own listing with the lines marked. */}
            {question.listing && question.kind !== "lines" ? <ListingView listing={question.listing} /> : null}
            {ask && canAskFeedback(question) ? <FeedbackButton test={test} question={question} index={index} ask={ask} /> : null}
          </li>
        );
      })}
    </ol>
  );
}
