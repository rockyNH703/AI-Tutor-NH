"use client";

import { Button } from "@v1/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@v1/ui/dialog";
import { useId, useRef } from "react";

import { alertDialogProps } from "@/features/friendzone/safety-confirm";
import { numberList } from "./practice-model";

/**
 * Before End Test: an alertdialog (it interrupts to warn about a consequence) listing the questions without an
 * answer, the marked ones and any answer still only on this device. Keep answering has focus, so Enter loses nothing.
 */

export type EndTestSummary = { unanswered: number[]; marked: number[]; unsent: number[] };

export function EndTestBody({ summary, left, online }: { summary: EndTestSummary; left: string; online: boolean }) {
  const line = (label: string, numbers: number[]) => (
    <li>
      <span className="font-semibold text-foreground">{label}:</span> {numbers.length ? `${numbers.length} (${numbers.length === 1 ? "question" : "questions"} ${numberList(numbers)})` : "none"}
    </li>
  );
  return (
    <>
      <ul className="flex flex-col gap-1 text-sm text-text-secondary">
        {line("Unanswered", summary.unanswered)}
        {line("Marked for review", summary.marked)}
        {summary.unsent.length > 0 && line("Answers not saved yet", summary.unsent)}
      </ul>
      {summary.unsent.length > 0 && (
        <p className="text-sm text-foreground">
          {online
            ? "Unsaved answers are sent with your submission."
            : "You're offline: your test will be submitted with these answers when you reconnect, if that is before time runs out."}
        </p>
      )}
      <p className="text-sm text-text-secondary">{left} left. You can't change your answers after you end the test.</p>
    </>
  );
}

export function EndTestDialog({
  open,
  onOpenChange,
  summary,
  left,
  online,
  said,
  onConfirm,
  onCloseAutoFocus,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  summary: EndTestSummary;
  left: string;
  online: boolean;
  /** The page's live region, mirrored here: an open dialog hides the rest of the page from assistive technology. */
  said: string;
  onConfirm: () => void;
  onCloseAutoFocus: (event: Event) => void;
}) {
  const descriptionId = useId();
  const keep = useRef<HTMLButtonElement>(null);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        {...alertDialogProps(descriptionId)}
        className="sm:max-w-md"
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          keep.current?.focus();
        }}
        onCloseAutoFocus={onCloseAutoFocus}
      >
        <DialogHeader>
          <DialogTitle>End the test?</DialogTitle>
          <DialogDescription id={descriptionId} asChild>
            <div className="flex flex-col gap-2">
              <EndTestBody summary={summary} left={left} online={online} />
            </div>
          </DialogDescription>
        </DialogHeader>
        <p role="status" className="sr-only">
          {said}
        </p>
        <DialogFooter className="gap-2 sm:gap-2">
          <Button ref={keep} type="button" variant="outline" className="min-h-11" onClick={() => onOpenChange(false)}>
            Keep answering
          </Button>
          <Button type="button" className="min-h-11" onClick={onConfirm}>
            End test
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
