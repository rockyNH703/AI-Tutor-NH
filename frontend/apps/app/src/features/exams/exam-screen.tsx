"use client";

import { Button } from "@v1/ui/button";
import { EmptyState, ErrorState } from "@v1/ui/empty-state";
import { Skeleton, SkeletonGroup } from "@v1/ui/skeleton";
import { ArrowLeft, FileQuestion } from "lucide-react";
import Link from "next/link";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import { clockOffset } from "@/features/friendzone/clock";
import { ApiError, describeError } from "@/lib/api/errors";
import { type ExamResult, type PracticeExam, getExam } from "@/lib/api/practice-exams";
import type { Uuid } from "@/lib/api/types";
import { ExamResults } from "./exam-results";
import { ExamRunner, type Finished } from "./exam-runner";
import { clearMirror, notCounted, readMirror, sessionStore } from "./practice-model";
import { quizzesHref, topicHref } from "./setup-model";

/**
 * `/exam/[examId]`, a full window outside the app shell: the exam while it runs, its result once submitted (also
 * after a reload), a 404 for an exam that isn't the caller's. Reading an exam past its cutoff makes the server submit it.
 */

type View =
  | { kind: "loading" }
  | { kind: "gone" }
  | { kind: "error"; error: unknown }
  | { kind: "running"; exam: PracticeExam; offset: number }
  | { kind: "done"; exam: PracticeExam & { result: ExamResult }; notCounted: number[] };

function doneView(exam: PracticeExam, lost: number[]): View {
  return exam.result ? { kind: "done", exam: { ...exam, result: exam.result }, notCounted: lost } : { kind: "error", error: null };
}

export function ExamScreen({ examId, topic }: { examId: Uuid; topic: string | null }) {
  const [view, setView] = useState<View>({ kind: "loading" });
  const [attempt, setAttempt] = useState(0);
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    setView({ kind: "loading" });
    const sentAt = Date.now();
    getExam(examId, controller.signal).then(
      (exam) => {
        if (exam.status !== "submitted") return setView({ kind: "running", exam, offset: clockOffset(exam.serverTime, sentAt, Date.now()) });
        // Submitted while this tab was away (a reload after time-up): what this device still held was not counted.
        const mirrored = readMirror(sessionStore(), examId).items;
        setView(doneView(exam, exam.result ? notCounted(exam.result.review, mirrored, exam.lateItems ?? []) : []));
      },
      (error) => {
        if (controller.signal.aborted) return;
        setView(error instanceof ApiError && error.code === "not_found" ? { kind: "gone" } : { kind: "error", error });
      },
    );
    return () => controller.abort();
  }, [examId, attempt]);

  // The result has been shown with its notice: only now may this device forget the answers it kept.
  useEffect(() => {
    if (view.kind !== "done") return;
    clearMirror(sessionStore(), examId);
    heading.current?.focus();
  }, [view.kind, examId]);

  const onFinished = useCallback((done: Finished) => setView(doneView(done.exam, done.notCounted)), []);
  const onGone = useCallback(() => setView({ kind: "gone" }), []);

  if (view.kind === "running") return <ExamRunner exam={view.exam} offset={view.offset} onFinished={onFinished} onGone={onGone} />;

  const program = view.kind === "done" ? view.exam : null;
  return (
    <Frame title={program ? `${program.program.title} · Month ${program.month}` : "Practice test"}>
      {view.kind === "loading" && (
        <SkeletonGroup label="Loading the test" className="flex flex-col gap-4">
          <Skeleton className="h-28 w-full rounded-2xl" />
          <Skeleton className="h-48 w-full rounded-2xl" />
        </SkeletonGroup>
      )}
      {view.kind === "gone" && (
        <EmptyState
          icon={FileQuestion}
          title="Test not found"
          description="This test doesn't exist, isn't yours, or its program is no longer open to your account."
          action={
            <Button asChild variant="outline" className="min-h-11">
              <Link href="/">
                <ArrowLeft aria-hidden className="h-4 w-4" />
                Back to NetSec AI
              </Link>
            </Button>
          }
        />
      )}
      {view.kind === "error" && (
        <ErrorState
          title="The test couldn't be loaded"
          description={describeError(view.error).message}
          action={
            <Button type="button" variant="outline" className="min-h-11" onClick={() => setAttempt((n) => n + 1)}>
              Try again
            </Button>
          }
        />
      )}
      {view.kind === "done" && (
        <ExamResults
          exam={view.exam}
          notCounted={view.notCounted}
          topicHref={topic ? topicHref(view.exam.program.slug, view.exam.month, topic) : null}
          quizzesHref={quizzesHref(view.exam.program.slug, view.exam.month)}
          headingRef={heading}
        />
      )}
    </Frame>
  );
}

function Frame({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="app-frame bg-background">
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="z-10 flex h-header shrink-0 items-center border-b border-border bg-card px-4 sm:px-6">
          <p className="min-w-0 flex-1 truncate text-base font-semibold text-foreground">
            <bdi dir="auto">{title}</bdi>
          </p>
        </header>
        <main id="main" className="scrollbar-subtle min-h-0 flex-1 overflow-y-auto overscroll-contain">
          <div className="page-container">{children}</div>
        </main>
      </div>
    </div>
  );
}
