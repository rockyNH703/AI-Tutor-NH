/**
 * The exam setup's rules as plain data (setup-model.test.ts). Names (exams-contract.md): a Track is a QbBook, a Topic
 * a QbChapter, a Quiz a QbTopic. Counts come only from the API's `available`; nothing here invents or pads a paper.
 */

import { type QuizTopic, UNCLASSIFIED, groupQuizzes, topicKey } from "@/features/programs/quiz-topics";
import type { ExamDifficulty, ExamDurations, ExamQuiz } from "@/lib/api/practice-exams";

export const DIFFICULTIES: readonly ExamDifficulty[] = ["mixed", "easy", "medium", "hard"];

export const DIFFICULTY_LABEL: Record<ExamDifficulty, string> = { mixed: "Mixed", easy: "Easy", medium: "Medium", hard: "Hard" };

export const SCORING = "1 mark each · no negative marking";

/** The Topic page's key for this Quiz (the same one Quizzes & Games puts in `?topic=`). */
export function keyOf(quiz: Pick<ExamQuiz, "book" | "chapter">): string {
  return topicKey(quiz.book, quiz.chapter.trim() || UNCLASSIFIED);
}

/** The Track a test is drawn from: the clicked Quiz's, else the Topic's. Null when neither is in this month. */
export function trackFor(quizzes: readonly ExamQuiz[], topic: string, quizId: string | null): string | null {
  const clicked = quizId ? quizzes.find((quiz) => quiz.topicId === quizId) : undefined;
  return clicked?.book ?? quizzes.find((quiz) => keyOf(quiz) === topic)?.book ?? null;
}

/** The Track's Quizzes under their Topics, in the API's order. */
export function trackTopics(quizzes: readonly ExamQuiz[], track: string): (Omit<QuizTopic, "quizzes"> & { quizzes: ExamQuiz[] })[] {
  const own = quizzes.filter((quiz) => quiz.book === track);
  const byId = new Map(own.map((quiz) => [quiz.topicId, quiz]));
  return (groupQuizzes(own)[0]?.topics ?? []).map((topic) => ({
    ...topic,
    quizzes: topic.quizzes.flatMap((item) => byId.get(item.topicId) ?? []),
  }));
}

/** The clicked Quiz; for a topic test (no Quiz), every Quiz of the Topic. */
export function defaultSelection(quizzes: readonly ExamQuiz[], topic: string, quizId: string | null): Set<string> {
  if (quizId && quizzes.some((quiz) => quiz.topicId === quizId)) return new Set([quizId]);
  return new Set(quizzes.filter((quiz) => keyOf(quiz) === topic).map((quiz) => quiz.topicId));
}

export function availableByDifficulty(quizzes: readonly ExamQuiz[], selected: ReadonlySet<string>): Record<ExamDifficulty, number> {
  const sum = { mixed: 0, easy: 0, medium: 0, hard: 0 };
  for (const quiz of quizzes) {
    if (!selected.has(quiz.topicId)) continue;
    sum.mixed += quiz.available.total;
    sum.easy += quiz.available.easy;
    sum.medium += quiz.available.medium;
    sum.hard += quiz.available.hard;
  }
  return sum;
}

/** A typed count, kept within 1..min(maxQuestions, available), with the reason when it had to move. */
export function clampCount(raw: string, available: number, maxQuestions: number): { value: number; message: string | null } {
  const most = Math.min(maxQuestions, available);
  if (most < 1) return { value: 0, message: "No questions are available for this choice." };
  const asked = Number(raw.trim());
  if (!Number.isInteger(asked) || asked < 1) return { value: 1, message: `Enter a number from 1 to ${most}.` };
  if (asked > maxQuestions && maxQuestions <= available) return { value: most, message: `A test has at most ${maxQuestions} questions.` };
  if (asked > available) return { value: most, message: `Only ${available} questions are available for this choice.` };
  return { value: asked, message: null };
}

const PREFERRED_COUNT = 10;

export function defaultCount(lengths: readonly number[], available: number, maxQuestions: number): number {
  const most = Math.min(maxQuestions, available);
  if (PREFERRED_COUNT <= most) return PREFERRED_COUNT;
  const fits = lengths.filter((length) => length <= most);
  return fits.length ? Math.max(...fits) : Math.max(0, most);
}

/** secondsPerQuestion × count × multiplier, rounded up to whole minutes, at least 1, at most maxMinutes. */
export function durationMinutes(count: number, durations: ExamDurations, multiplier: number): number {
  const minutes = Math.ceil((count * durations.secondsPerQuestion * multiplier) / 60);
  return Math.min(durations.maxMinutes, Math.max(1, minutes));
}

export function durationOptions(count: number, durations: ExamDurations): { multiplier: number; minutes: number }[] {
  const seen = new Set<number>();
  return durations.multipliers.flatMap((multiplier) => {
    const minutes = durationMinutes(count, durations, multiplier);
    if (seen.has(minutes)) return [];
    seen.add(minutes);
    return [{ multiplier, minutes }];
  });
}

export const minutesText = (minutes: number) => `${minutes} ${minutes === 1 ? "minute" : "minutes"}`;

const base = (slug: string) => `/programs/${encodeURIComponent(slug)}/quizzes`;

/** A quiz card's (or a topic test's) way into the setup. */
export function setupHref(slug: string, month: number, topic: string, quizId?: string): string {
  const quiz = quizId ? `&quiz=${encodeURIComponent(quizId)}` : "";
  return `${base(slug)}/setup?month=${month}&topic=${encodeURIComponent(topic)}${quiz}`;
}

export function topicHref(slug: string, month: number, topic: string): string {
  return `${base(slug)}?month=${month}&topic=${encodeURIComponent(topic)}`;
}

export function quizzesHref(slug: string, month: number): string {
  return `${base(slug)}?month=${month}`;
}
