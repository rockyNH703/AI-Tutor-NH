"use client";

import { Button } from "@v1/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@v1/ui/dialog";
import { CircleHelp, FileText, Layers, type LucideIcon, Maximize2, NotebookText } from "lucide-react";
import { type ReactNode, useCallback, useContext, useState } from "react";

import { ReleaseScrollContext } from "@/features/markdown/context";
import { CardFrame } from "@/features/markdown/study-card";
import Flashcards from "@/features/markdown/widgets/flashcards";
import { getNhprepItems, itemsKey } from "@/lib/api/nhprep";
import type { NhprepItem, NhprepItemKind, NhprepItemList, NhprepLesson, NhprepView } from "@/lib/api/types";
import { NhprepMarkdown } from "./nhprep-markdown";
import { Failure, Line, Loading } from "./states";
import { useRead } from "./use-read";

export type StudyViewName = Exclude<NhprepView, "video" | "chapters">;

const VIEWS: Record<StudyViewName, { kind: NhprepItemKind; label: string; icon: LucideIcon; noun: string; card: string }> = {
  notes: { kind: "note", label: "NHprep notes", icon: FileText, noun: "notes", card: "nhprep-notes" },
  cheat_sheet: { kind: "cheat_sheet", label: "NHprep cheat sheet", icon: NotebookText, noun: "cheat sheet", card: "nhprep-cheat-sheet" },
  flashcards: { kind: "flashcard", label: "NHprep flashcards", icon: Layers, noun: "flashcards", card: "nhprep-flashcards" },
  interview: {
    kind: "interview_question",
    label: "NHprep interview questions",
    icon: CircleHelp,
    noun: "interview questions",
    card: "nhprep-interview",
  },
};

export function isStudyView(view: string): view is StudyViewName {
  return Object.hasOwn(VIEWS, view);
}

/** The most cards the house flashcards widget takes (widgets/flashcards.tsx). */
export const MAX_CARDS = 100;

/** One requested study aid for the shown lesson: fetched only when NHprep has it, then drawn by kind. */
export function StudyView({ view, lesson }: { view: StudyViewName; lesson: NhprepLesson }) {
  const spec = VIEWS[view];
  const availability = lesson.availability[spec.kind];
  const wanted = availability !== "absent" && availability !== "unavailable" && availability !== "error";
  const read = useRead<NhprepItemList>(wanted ? itemsKey(lesson.id, spec.kind) : null, () => getNhprepItems(lesson.id, spec.kind));
  const items = read.data?.items.filter((item) => item.kind === spec.kind) ?? [];

  const frame = (body: ReactNode) => (
    <CardFrame kind={spec.card} icon={spec.icon} label={spec.label} title={lesson.title}>
      {body}
    </CardFrame>
  );
  if (availability === "absent") return frame(<Line>{`This lesson has no ${spec.noun} on NHprep.`}</Line>);
  if (!wanted) return frame(<Line>{`NHprep's ${spec.noun} for this lesson couldn't be brought over.`}</Line>);
  if (read.error) return frame(<Failure error={read.error} retry={read.retry} />);
  if (!read.data) return frame(<Loading label={`Loading the ${spec.noun}`} />);
  if (items.length === 0) return frame(<Line>{`This lesson has no ${spec.noun} on NHprep.`}</Line>);

  if (view === "flashcards") return <FlashcardsView lesson={lesson} items={items} frame={frame} />;
  if (view === "interview") return frame(<InterviewView items={items} />);
  return <TextView label={spec.label} spec={spec} lesson={lesson} items={items} />;
}

function TextItems({ items }: { items: NhprepItem[] }) {
  return items.map((item) => (
    <div key={item.id} className="mt-3 first:mt-0">
      {item.title ? <p className="mb-1 font-semibold">{item.title}</p> : null}
      <NhprepMarkdown text={item.bodyMd ?? ""} />
    </div>
  ));
}

/** Notes and cheat sheets: a scroll region in the card, the whole text in a dialog. */
function TextView({
  label,
  spec,
  lesson,
  items,
}: { label: string; spec: (typeof VIEWS)[StudyViewName]; lesson: NhprepLesson; items: NhprepItem[] }) {
  const [open, setOpen] = useState(false);
  const release = useContext(ReleaseScrollContext);
  return (
    <CardFrame
      kind={spec.card}
      icon={spec.icon}
      label={label}
      title={lesson.title}
      actions={
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="min-h-11 sm:min-h-9"
          aria-label={`Expand ${label.replace("NHprep ", "")}`}
          onClick={() => {
            release();
            setOpen(true);
          }}
        >
          <Maximize2 aria-hidden className="h-4 w-4" />
          Expand
        </Button>
      }
    >
      <div
        // biome-ignore lint/a11y/noNoninteractiveTabindex: a scroll region must be reachable by keyboard (as table.tsx)
        tabIndex={0}
        role="region"
        aria-label={`${label}: ${lesson.title}`}
        className="focus-ring scrollbar-subtle max-h-96 overflow-y-auto rounded-lg pr-1"
      >
        <TextItems items={items} />
      </div>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-3xl p-4 sm:p-6">
          <DialogTitle>{`${label} · ${lesson.title}`}</DialogTitle>
          <DialogDescription className="sr-only">The full text; scroll to read all of it.</DialogDescription>
          <div className="scrollbar-subtle max-h-[75dvh] overflow-y-auto">
            <TextItems items={items} />
          </div>
        </DialogContent>
      </Dialog>
    </CardFrame>
  );
}

/** Interview questions: each one opens to its answer, which is drawn only while open. */
function InterviewView({ items }: { items: NhprepItem[] }) {
  const [open, setOpen] = useState<ReadonlySet<string>>(new Set());
  const release = useContext(ReleaseScrollContext);
  return (
    <>
      <p className="mb-2 text-sm text-text-secondary">
        {items.length === 1 ? "1 question. Open it to see its answer." : `${items.length} questions. Open one to see its answer.`}
      </p>
      <div className="flex flex-col gap-1">
        {items.map((item) => (
          <details
            key={item.id}
            className="rounded-lg border border-border bg-card"
            onToggle={(event) => {
              const shown = event.currentTarget.open;
              setOpen((current) => {
                const next = new Set(current);
                if (shown) next.add(item.id);
                else next.delete(item.id);
                return next;
              });
            }}
          >
            {/* biome-ignore lint/a11y/useKeyWithClickEvents: summary is natively keyboard-operable; the click only releases the scroll */}
            <summary onClick={release} className="focus-ring flex min-h-11 cursor-pointer items-center rounded-lg px-3 py-2 text-sm font-medium">
              {item.title ?? "Question"}
            </summary>
            {open.has(item.id) ? (
              <div className="border-t border-border px-3 py-2">
                <NhprepMarkdown text={item.bodyMd ?? ""} />
              </div>
            ) : null}
          </details>
        ))}
      </div>
    </>
  );
}

/** The house flashcards widget, unchanged, on the lesson's first 100 cards. */
function FlashcardsView({
  lesson,
  items,
  frame,
}: { lesson: NhprepLesson; items: NhprepItem[]; frame: (body: ReactNode) => ReactNode }) {
  const [broken, setBroken] = useState(false);
  const onError = useCallback(() => setBroken(true), []);
  const cards = items.filter((item) => item.title?.trim() && item.bodyMd?.trim());
  // A string, so the widget's own memo (and its per-tab progress key) holds across renders.
  const source = JSON.stringify({
    title: `NHprep · ${lesson.title}`.slice(0, 200),
    cards: cards.slice(0, MAX_CARDS).map((item) => ({ front: item.title, back: item.bodyMd })),
  });
  if (broken || cards.length === 0) return frame(<Line>These flashcards couldn't be shown.</Line>);
  return (
    <div>
      <Flashcards source={source} onError={onError} />
      {cards.length > MAX_CARDS ? (
        <p className="-mt-1 mb-3 text-xs text-text-secondary">{`Showing the first ${MAX_CARDS} of ${cards.length} cards.`}</p>
      ) : null}
    </div>
  );
}
