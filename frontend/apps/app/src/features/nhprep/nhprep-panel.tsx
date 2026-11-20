"use client";

import { Button } from "@v1/ui/button";
import { ChevronLeft, ChevronRight, MonitorPlay } from "lucide-react";
import { type ReactNode, useContext, useEffect, useId, useRef, useState } from "react";

import { ReleaseScrollContext } from "@/features/markdown/context";
import { CardFrame } from "@/features/markdown/study-card";
import { formatTime } from "@/features/markdown/widgets/video";
import { getNhprepLesson, getNhprepLessons, lessonKey, listKey } from "@/lib/api/nhprep";
import type { NhprepCard, NhprepLesson, NhprepLessonList, NhprepLessonSummary, Uuid } from "@/lib/api/types";
import { Failure, Loading } from "./states";
import { StudyView, isStudyView } from "./study-views";
import { useRead } from "./use-read";
import { VideoSection } from "./video-player";

export const PANEL_COPY = {
  ambiguous: "Several NHprep lessons match your question. This is the closest; the others are listed below.",
  removed: "This lesson was removed from NHprep.",
  others: "Other matching videos",
} as const;

/** "CCNA 200-301 · Lesson 12 of 40 · Trainer: Sam Rao · 58:37" (the trainer part only when known). */
export function lessonMeta(lesson: NhprepLessonSummary): string {
  const trainers = lesson.trainers.length ? `${lesson.trainers.length === 1 ? "Trainer" : "Trainers"}: ${lesson.trainers.join(", ")}` : null;
  const length = lesson.durationSeconds ? formatTime(lesson.durationSeconds) : null;
  return [lesson.course.title, `Lesson ${lesson.position} of ${lesson.lessonCount}`, trainers, length].filter(Boolean).join(" · ");
}

const sameTitle = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();

function Description({ text }: { text: string }) {
  const body = <p className="whitespace-pre-line break-words text-sm text-text-secondary">{text}</p>;
  if (text.length <= 280) return <div className="mt-2">{body}</div>;
  return (
    <details className="mt-2">
      <summary className="focus-ring inline-flex min-h-11 cursor-pointer items-center rounded-md text-sm font-medium sm:min-h-9">
        About this lesson
      </summary>
      {body}
    </details>
  );
}

/**
 * An NHprep reply's card: the recommended lesson (its video, details, Jump to, previous and next), the other
 * matching lessons, and the study aids the question asked for, all for the lesson shown. Picking another
 * lesson redraws everything below its header. Previous and Next live outside the part keyed by the lesson,
 * so the pressed button keeps focus.
 */
export default function NhprepPanel({ card }: { card: NhprepCard }) {
  const release = useContext(ReleaseScrollContext);
  const [lessonId, setLessonId] = useState<Uuid>(card.primaryLessonId);
  const [moved, setMoved] = useState(false); // the reader changed lesson: announce the new one
  const [last, setLast] = useState<NhprepLesson | null>(null);
  const title = useRef<HTMLSpanElement>(null);
  const othersId = useId();

  const ids = [card.primaryLessonId, ...card.alternativeLessonIds.slice(0, 5)];
  const list = useRead<NhprepLessonList>(ids.length > 1 ? listKey(ids) : null, () => getNhprepLessons(ids));
  const detail = useRead<NhprepLesson>(lessonKey(lessonId), () => getNhprepLesson(lessonId));
  const lesson = detail.data;

  useEffect(() => {
    if (lesson) setLast(lesson);
  }, [lesson]);

  const open = (id: Uuid | null, focusTitle: boolean) => {
    if (!id || id === lessonId) return;
    release();
    setMoved(true);
    setLessonId(id);
    if (focusTitle) title.current?.focus({ preventScroll: true });
  };

  const summary = list.data?.items.find((item) => item.id === lessonId);
  const shownTitle = lesson?.title ?? summary?.title ?? (last?.id === lessonId ? last.title : "Loading…");
  const others = (list.data?.items ?? []).filter((item) => item.id !== lessonId);
  const wantsVideo = card.views.includes("video") || card.views.includes("chapters");
  const studyViews = card.views.filter(isStudyView);
  const nav = lesson ?? last;

  let body: ReactNode;
  if (detail.error) body = <Failure error={detail.error} retry={detail.retry} />;
  else if (!lesson) body = <Loading label="Loading the lesson" />;
  else if (lesson.removed) body = <p role="status" className="py-1 text-sm text-text-secondary">{PANEL_COPY.removed}</p>;
  else {
    body = (
      <>
        <p className="text-sm text-text-secondary">{lessonMeta(lesson)}</p>
        {lesson.description?.trim() ? <Description text={lesson.description.trim()} /> : null}
        {wantsVideo ? (
          <VideoSection
            lessonId={lesson.id}
            title={lesson.title}
            video={lesson.video}
            sections={lesson.chapters}
            matchedId={lesson.id === card.primaryLessonId ? card.matchedItemId : null}
          />
        ) : null}
      </>
    );
  }

  const step = (id: Uuid | null, label: string, Icon: typeof ChevronLeft, after: boolean) => (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className="min-h-11 sm:min-h-9"
      aria-disabled={!id || !lesson || undefined}
      onClick={() => lesson && open(id, false)}
    >
      {after ? null : <Icon aria-hidden className="h-4 w-4" />}
      {label}
      {after ? <Icon aria-hidden className="h-4 w-4" /> : null}
    </Button>
  );

  return (
    <>
      <CardFrame
        kind="nhprep-lesson"
        icon={MonitorPlay}
        label="NHprep video"
        title={
          <span ref={title} tabIndex={-1} className="focus-ring rounded-sm">
            {shownTitle}
          </span>
        }
      >
        {card.status === "ambiguous" ? <p className="mb-2 text-sm">{PANEL_COPY.ambiguous}</p> : null}
        <p role="status" className="sr-only">
          {moved && lesson ? `Lesson ${lesson.position} of ${lesson.lessonCount}: ${lesson.title}` : ""}
        </p>
        <div key={lessonId}>{body}</div>

        {nav && !nav.removed ? (
          <nav aria-label="Lessons in this course" className="mt-3 flex flex-wrap justify-between gap-2">
            {step(nav.previousLessonId, "Previous lesson", ChevronLeft, false)}
            {step(nav.nextLessonId, "Next lesson", ChevronRight, true)}
          </nav>
        ) : null}

        {others.length ? (
          <div className="mt-3 border-t border-border pt-3">
            <p id={othersId} className="mb-1 text-xs font-semibold text-text-secondary">
              {PANEL_COPY.others}
            </p>
            <ul aria-labelledby={othersId} className="flex flex-col gap-0.5">
              {others.map((item) => (
                <li key={item.id}>
                  <Button
                    type="button"
                    variant="ghost"
                    className="h-auto min-h-11 w-full flex-col items-start gap-0 whitespace-normal px-2 py-1.5 text-left font-normal sm:min-h-10"
                    onClick={() => open(item.id, true)}
                  >
                    <span className="break-words font-medium">{item.title}</span>
                    <span className="break-words text-xs text-text-secondary">
                      {sameTitle(item.title, shownTitle) && item.trainers.length
                        ? `Also taught by ${item.trainers.join(", ")} · ${item.course.title}`
                        : lessonMeta(item)}
                    </span>
                  </Button>
                </li>
              ))}
            </ul>
          </div>
        ) : list.error ? (
          <div className="mt-3 border-t border-border pt-3">
            <Failure error={list.error} retry={list.retry} />
          </div>
        ) : null}
      </CardFrame>

      {lesson && !lesson.removed && !detail.error
        ? studyViews.map((view) => <StudyView key={`${lesson.id}:${view}`} view={view} lesson={lesson} />)
        : null}
    </>
  );
}
