"use client";

import { Button } from "@v1/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@v1/ui/dialog";
import { type ReactNode, type RefObject, useId, useRef } from "react";

/**
 * The confirmation before Remove friend and Block: an `alertdialog` (it interrupts to warn about a consequence),
 * described by that consequence, opening on Cancel so Enter can't remove or block anyone by accident. The shared
 * `ConfirmDialog` takes no role, so this builds the same dialog from the `@v1/ui/dialog` parts.
 */

/** The dialog's role and what describes it: the consequence text. */
export function alertDialogProps(descriptionId: string) {
  return { role: "alertdialog" as const, "aria-describedby": descriptionId };
}

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: ReactNode;
  confirmLabel: string;
  pending: boolean;
  onConfirm: () => void;
  onCloseAutoFocus?: (event: Event) => void;
};

export function SafetyConfirm({ open, onOpenChange, onCloseAutoFocus, ...body }: Props) {
  const descriptionId = useId();
  const cancel = useRef<HTMLButtonElement>(null);
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        {...alertDialogProps(descriptionId)}
        className="sm:max-w-md"
        onOpenAutoFocus={(event) => {
          event.preventDefault();
          cancel.current?.focus();
        }}
        onCloseAutoFocus={onCloseAutoFocus}
      >
        <ConfirmBody {...body} descriptionId={descriptionId} cancelRef={cancel} onCancel={() => onOpenChange(false)} />
      </DialogContent>
    </Dialog>
  );
}

export function ConfirmBody({
  title,
  description,
  confirmLabel,
  pending,
  onConfirm,
  onCancel,
  descriptionId,
  cancelRef,
}: Omit<Props, "open" | "onOpenChange" | "onCloseAutoFocus"> & {
  onCancel: () => void;
  descriptionId: string;
  cancelRef?: RefObject<HTMLButtonElement | null>;
}) {
  return (
    <>
      <DialogHeader>
        <DialogTitle>{title}</DialogTitle>
        <DialogDescription id={descriptionId}>{description}</DialogDescription>
      </DialogHeader>
      <DialogFooter className="gap-2 sm:gap-2">
        <Button ref={cancelRef} type="button" variant="outline" className="min-h-11" disabled={pending} onClick={onCancel}>
          Cancel
        </Button>
        <Button type="button" variant="destructive" className="min-h-11" disabled={pending} onClick={onConfirm}>
          {pending ? "Working…" : confirmLabel}
        </Button>
      </DialogFooter>
    </>
  );
}
