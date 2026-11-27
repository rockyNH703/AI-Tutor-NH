"use client";

import { Button } from "@v1/ui/button";
import { cn } from "@v1/ui/utils";
import { ArrowLeft, Check, Minus, TriangleAlert, X } from "lucide-react";
import Link from "next/link";
import type { RefObject } from "react";

import type { ExamResult, PaperQuestion, PracticeExam, ReviewItem, ReviewOutcome } from "@/lib/api/practice-exams";
import { NOT_COUNTED, OUTCOME_LABEL, numberList } from "./practice-model";

/**
 * A submitted practice exam: the score, the counts, a table per Topic, and every question with your answer, the
 * correct one and the explanation. Answers that never reached the server are named "Not counted", never as saved.
 */

const PANEL = "rounded-2xl border border-border bg-card p-4 sm:p-6";

const OUTCOME_STYLE: Record<ReviewOutcome, string> = {
  correct: "border-success/40 bg-success-soft text-success",
  incorrect: "border-destructive/40 bg-destructive/5 text-destructive",
  unanswered: "border-border bg-surface-2 text-text-secondary",
  void: "border-border bg-surface-2 text-text-secondary",
};

function OutcomeGlyph({ outcome }: { outcome: ReviewOutcome }) {
  const Icon = outcome === "correct" ? Check : outcome === "incorrect" ? X : Minus;
  return <Icon aria-hidden className="h-3.5 w-3.5 shrink-0" />;
}

export type ExamResultsProps = {
  exam: PracticeExam & { result: ExamResult };
  notCounted: readonly number[];
  topicHref: string | null;
  quizzesHref: string;
  headingRef?: RefObject<HTMLHeadingElement | null>;
};

export function ExamResults({ exam, notCounted, topicHref, quizzesHref, headingRef }: ExamResultsProps) {
  const { result } = exam;
  const lost = new Set(notCounted);
  const questions = new Map(exam.questions.map((question) => [question.index, question]));
  return (
    <div className="flex flex-col gap-6">
      <section aria-labelledby="exam-result-title" className={PANEL}>
        <h1 id="exam-result-title" ref={headingRef} tabIndex={-1} className="focus-ring rounded-md text-xl font-semibold text-foreground">
          Result: <bdi dir="auto">{exam.title}</bdi>
        </h1>
        <p className="mt-3 text-2xl font-semibold tabular-nums text-foreground">
          {result.score} / {result.total}
          <span className="ml-3 text-lg text-text-secondary">{result.percentage}%</span>
        </p>
        <p className="mt-1 text-sm text-text-secondary">
          1 mark per correct answer, no negative marking.
          {result.autoSubmitted && " Submitted automatically when time ran out."}
        </p>
        <dl className="mt-4 grid grid-cols-3 gap-3 text-sm">
          {(
            [
              ["Correct", result.correct],
              ["Incorrect", result.incorrect],
              ["Unanswered", result.unanswered],
            ] as const
          ).map(([label, value]) => (
            <div key={label} className="rounded-xl border border-border px-3 py-2">
              <dt className="text-text-secondary">{label}</dt>
              <dd className="text-lg font-semibold tabular-nums text-foreground">{value}</dd>
            </div>
          ))}
        </dl>
      </section>

      {notCounted.length > 0 && (
        <section aria-labelledby="exam-not-counted" className="flex gap-3 rounded-2xl border border-warning/50 bg-warning-soft p-4 text-sm text-foreground">
          <TriangleAlert aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
          <div>
            <h2 id="exam-not-counted" className="font-semibold">
              {notCounted.length === 1 ? "1 answer was not counted" : `${notCounted.length} answers were not counted`}
            </h2>
            <p className="mt-1">
              {notCounted.length === 1 ? "Question" : "Questions"} {numberList(notCounted.map((index) => index + 1))}: {NOT_COUNTED}. Those
              answers stayed on this device and never reached the server, so the server scored what it had saved.
            </p>
          </div>
        </section>
      )}

      {result.byTopic.length > 0 && (
        <section aria-labelledby="exam-by-topic" className={PANEL}>
          <h2 id="exam-by-topic" className="text-lg font-semibold text-foreground">
            By topic
          </h2>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="text-text-secondary">
                <tr className="border-b border-border">
                  <th scope="col" className="py-2 pr-3 font-medium">
                    Topic
                  </th>
                  <th scope="col" className="py-2 pr-3 text-right font-medium">
                    Correct
                  </th>
                  <th scope="col" className="py-2 pr-3 text-right font-medium">
                    Questions
                  </th>
                  <th scope="col" className="py-2 text-right font-medium">
                    Score
                  </th>
                </tr>
              </thead>
              <tbody>
                {result.byTopic.map((row) => (
                  <tr key={row.topic} className="border-b border-border last:border-0">
                    <th scope="row" className="py-2 pr-3 font-medium text-foreground">
                      <bdi dir="auto">{row.topic}</bdi>
                    </th>
                    <td className="py-2 pr-3 text-right tabular-nums">{row.correct}</td>
                    <td className="py-2 pr-3 text-right tabular-nums">{row.total}</td>
                    <td className="py-2 text-right tabular-nums">{row.total ? Math.round((row.correct / row.total) * 100) : 0}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section aria-labelledby="exam-review" className="flex flex-col gap-3">
        <h2 id="exam-review" className="text-lg font-semibold text-foreground">
          Answer review
        </h2>
        <ol className="flex flex-col gap-3">
          {result.review.map((item) => (
            <ReviewCard
              key={item.index}
              item={item}
              question={questions.get(item.index)}
              total={result.review.length}
              notCounted={lost.has(item.index)}
            />
          ))}
        </ol>
      </section>

      <nav aria-label="After the test" className="flex flex-col gap-2 sm:flex-row">
        {topicHref && (
          <Button asChild className="min-h-11">
            <Link href={topicHref}>
              <ArrowLeft aria-hidden className="h-4 w-4" />
              Back to topic
            </Link>
          </Button>
        )}
        <Button asChild variant="outline" className="min-h-11">
          <Link href={quizzesHref}>Back to Quizzes &amp; Games</Link>
        </Button>
      </nav>
    </div>
  );
}

function ReviewCard({ item, question, total, notCounted }: { item: ReviewItem; question: PaperQuestion | undefined; total: number; notCounted: boolean }) {
  const yours = new Set(item.keys);
  const right = new Set(item.correctKeys);
  return (
    <li className={PANEL}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-base font-semibold text-foreground">
          Question {item.index + 1} of {total}
        </h3>
        <span className={cn("inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-semibold", OUTCOME_STYLE[item.outcome])}>
          <OutcomeGlyph outcome={item.outcome} />
          {OUTCOME_LABEL[item.outcome]}
        </span>
        {notCounted && (
          <span className="inline-flex items-center gap-1 rounded-full border border-warning/50 bg-warning-soft px-2 py-0.5 text-xs font-semibold text-foreground">
            <TriangleAlert aria-hidden className="h-3.5 w-3.5 shrink-0 text-warning" />
            {NOT_COUNTED}
          </span>
        )}
      </div>
      {question && (
        <>
          <p className="mt-1 text-xs text-text-secondary">
            Quiz: <bdi dir="auto">{question.quiz}</bdi>
          </p>
          <p dir="auto" className="mt-3 whitespace-pre-wrap break-words text-base text-foreground">
            {question.stem}
          </p>
          <ul className="mt-3 flex flex-col gap-1.5 text-sm">
            {question.options.map((option) => (
              <li
                key={option.key}
                className={cn(
                  "flex flex-wrap items-start gap-x-2 gap-y-1 rounded-xl border px-3 py-2",
                  right.has(option.key) ? "border-success/50" : yours.has(option.key) ? "border-destructive/50" : "border-border",
                )}
              >
                <span className="font-semibold">{option.key}.</span>
                <span dir="auto" className="min-w-0 flex-1 break-words">
                  {option.text}
                </span>
                {yours.has(option.key) && <span className="text-xs font-semibold text-foreground">Your answer</span>}
                {right.has(option.key) && <span className="text-xs font-semibold text-success">Correct answer</span>}
              </li>
            ))}
          </ul>
        </>
      )}
      {item.outcome === "void" ? (
        <p className="mt-3 text-sm text-text-secondary">
          This question was changed after your test began, so it isn't graded and doesn't count toward your score.
        </p>
      ) : (
        <dl className="mt-3 grid gap-1 text-sm sm:grid-cols-[auto_1fr] sm:gap-x-3">
          <dt className="text-text-secondary">Your answer</dt>
          <dd className="text-foreground">{item.keys.length ? item.keys.join(", ") : "No answer"}</dd>
          <dt className="text-text-secondary">Correct answer</dt>
          <dd className="text-foreground">{item.correctKeys.join(", ")}</dd>
        </dl>
      )}
      {item.explanation && (
        <p dir="auto" className="mt-3 whitespace-pre-wrap break-words border-t border-border pt-3 text-sm text-foreground">
          {item.explanation}
        </p>
      )}
    </li>
  );
}
