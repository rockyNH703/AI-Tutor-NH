import type { ComponentType, Ref } from "react";

import type { AssessmentQuestion, AssessmentValue, TestMode } from "@/lib/api/types";

export type { TestMode };

/**
 * The contract between the test card (test-card.tsx) and one kind of question (questions/*.tsx).
 * The card owns the flow (practice, exam, review), the request queue, focus after an action and the
 * status line; a kind owns only how its question is answered and how its result reads. Nothing here
 * grades: the server does (app/assessments/kinds.py), and a kind never sees a key before it is final.
 */

export type InputProps<V> = {
  question: AssessmentQuestion;
  /** 1-based position in the test, for names ("Question 3"). */
  number: number;
  /** The local, unsent value (from `empty`, a practice draft, or `fromRecord`). */
  value: V;
  onChange: (value: V) => void;
  /** A text kind's "done editing" (blur, or Enter where it checks): the card flushes a pending save. */
  onCommit?: () => void;
  /** Submit from inside the input (Enter in numeric/cli, Ctrl/⌘+Enter in written): the card checks it. */
  onSubmit?: () => void;
  disabled: boolean;
  mode: TestMode;
  /** Practice numeric/cli: the values already tried for this question (the server's `pending.tries`). */
  tries: readonly string[];
  /** Id of the element naming the question (its legend), for aria-labelledby. */
  labelledBy: string;
  /** Id of extra help to read with the input (the scenario, a format hint), for aria-describedby. */
  describedBy?: string;
  /** The field's error (a client pre-check or the server's 422), shown and bound with aria-invalid. */
  invalid?: string | null;
  /** Where focus goes after a wrong try (the card selects the text). */
  inputRef?: Ref<HTMLElement>;
};

export type ResultProps = {
  question: AssessmentQuestion;
  /** 1-based, or null for a one-question block (no "Q1."). */
  number: number | null;
  /** Review: the question's listing/diagram drawn read-only in place. */
  compact?: boolean;
  /** Open its "Why" (the latest answered question). */
  open?: boolean;
};

export type KindModule<V = unknown> = {
  /** How the question is answered (never shows the answer: the record has none until final). */
  Input: ComponentType<InputProps<V>>;
  /** A final (or submitted) question: what was given, what was right, marks, and Why. */
  Result: ComponentType<ResultProps>;
  /** The starting local value. */
  empty: (question: AssessmentQuestion) => V;
  /** Whether the value is enough to check (practice) or to count as answered (exam). */
  isComplete: (question: AssessmentQuestion, value: V) => boolean;
  /** A client pre-check (numeric format, length): the message to show, or null when fine. */
  check?: (question: AssessmentQuestion, value: V) => string | null;
  /** The request's `value`. */
  toRequest: (question: AssessmentQuestion, value: V) => AssessmentValue;
  /** The value the server holds for this question (an exam draft, a final answer), if any. */
  fromRecord: (question: AssessmentQuestion) => V | null;
};
