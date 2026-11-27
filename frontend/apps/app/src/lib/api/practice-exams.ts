/**
 * Practice exams (Quizzes & Games › setup › exam › results; contract: netsec-handoffs/exams-contract.md, session 62).
 *
 * Names: the API keeps the bank's field names, so `topicId` is a QbTopic id, which the UI calls a **Quiz**; `chapter`
 * (a QbChapter) is the UI's **Topic**; `book` (a QbBook) is the UI's **Track**. The UI never calls a QbTopic a topic.
 *
 * The server owns everything that counts: which questions, the deadline (`endsAt`, `cutoffAt`, `serverTime`), the
 * saved answers (`rev`, compare-and-set) and the score. Keys and explanations exist only once `status` is submitted.
 */

import { callRoute } from "./http";
import type { ProgramRef } from "./programs";
import type { Uuid } from "./types";

export type ExamDifficulty = "mixed" | "easy" | "medium" | "hard";

export type QuizAvailability = { easy: number; medium: number; hard: number; total: number };

/** One quiz (a QbTopic) of the month, with the servable questions the caller may get, per difficulty. */
export type ExamQuiz = { topicId: string; title: string; chapter: string; book: string; available: QuizAvailability };

export type ExamRules = { marksPerQuestion: number; negativeMarking: number };

export type ExamDurations = { secondsPerQuestion: number; multipliers: number[]; maxMinutes: number };

export type ExamOptions = {
  program: ProgramRef;
  month: number;
  quizzes: ExamQuiz[];
  lengths: number[];
  maxQuestions: number;
  durations: ExamDurations;
  rules: ExamRules;
};

export type CreateExamBody = {
  requestId: Uuid;
  program: string;
  month: number;
  topicIds: string[];
  difficulty: ExamDifficulty;
  length: number;
  minutes: number;
};

export type ExamOption = { key: string; text: string };

/** A question of the paper. `quiz` is the QbTopic's title, `topic` the QbChapter's (the UI's Quiz and Topic). */
export type PaperQuestion = { index: number; stem: string; options: ExamOption[]; multi: boolean; quiz: string; topic: string };

/** One question's saved answer. `keys` [] means no answer. */
export type ExamResponseItem = { index: number; keys: string[]; marked: boolean; visited: boolean };

/** `void`: the question's content changed after the test began, so it is not graded and not in `total`. */
export type ReviewOutcome = "correct" | "incorrect" | "unanswered" | "void";

export type ReviewItem = { index: number; keys: string[]; correctKeys: string[]; explanation: string; outcome: ReviewOutcome };

export type ExamResult = {
  score: number;
  total: number;
  percentage: number;
  correct: number;
  incorrect: number;
  unanswered: number;
  autoSubmitted: boolean;
  submittedAt: string;
  byTopic: { topic: string; correct: number; total: number }[];
  review: ReviewItem[];
};

export type PracticeExam = {
  examId: Uuid;
  status: "in_progress" | "submitted";
  program: ProgramRef;
  month: number;
  title: string;
  difficulty: ExamDifficulty;
  length: number;
  minutes: number;
  startedAt: string;
  endsAt: string;
  cutoffAt: string;
  serverTime: string;
  rev: number;
  rules: ExamRules;
  questions: PaperQuestion[];
  responses: ExamResponseItem[];
  result: ExamResult | null;
  /** Submit only: indices of items that arrived after the cutoff and were NOT applied. */
  lateItems?: number[];
};

export type SaveResult = { rev: number; serverTime: string };

export function getExamOptions(slug: string, month: number, signal?: AbortSignal): Promise<ExamOptions> {
  return callRoute<ExamOptions>("GET /v1/programs/{slug}/months/{month}/exam-options", {
    params: { slug, month: String(month) },
    signal,
  });
}

/** 201 for a new exam; the same `requestId` again gives 200 and the same exam (idempotent). */
export function createExam(body: CreateExamBody): Promise<{ examId: Uuid }> {
  return callRoute<{ examId: Uuid }>("POST /v1/practice-exams", { body });
}

export function getExam(examId: Uuid, signal?: AbortSignal): Promise<PracticeExam> {
  return callRoute<PracticeExam>("GET /v1/practice-exams/{exam_id}", { params: { exam_id: examId }, signal });
}

/** Compare-and-set on the exam's `rev`: a 409 means another write (or the cutoff) came first. */
export function saveExamResponses(examId: Uuid, baseRev: number, items: ExamResponseItem[], signal?: AbortSignal): Promise<SaveResult> {
  return callRoute<SaveResult>("PUT /v1/practice-exams/{exam_id}/responses", {
    params: { exam_id: examId },
    body: { baseRev, items },
    signal,
  });
}

/** Idempotent: an exam already submitted answers with its stored result. Items after the cutoff come back in `lateItems`. */
export function submitExam(
  examId: Uuid,
  body: { baseRev: number | null; items: ExamResponseItem[]; auto: boolean },
  signal?: AbortSignal,
): Promise<PracticeExam> {
  return callRoute<PracticeExam>("POST /v1/practice-exams/{exam_id}/submit", { params: { exam_id: examId }, body, signal });
}
