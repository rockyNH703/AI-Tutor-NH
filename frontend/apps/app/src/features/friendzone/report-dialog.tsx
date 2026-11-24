"use client";

import { Button } from "@v1/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@v1/ui/dialog";
import { type FormEvent, useId, useState } from "react";

import { CONTRACT } from "@/lib/api/contract.gen";
import { type FriendZonePlayer, type ReportReason, reportPlayer } from "./api";
import { type Failure, REPORT_NOTE, REPORT_REASONS, failureCopy } from "./model";
import { PlayerName } from "./player";

const DETAILS_MAX = CONTRACT.FriendZoneReportRequest.details.maxLength;
const REASONS = Object.entries(REPORT_REASONS) as [ReportReason, string][];

/** Report a member: a reason, optional details, and what reporting and blocking do. The member is not told. */
export function ReportDialog({
  player,
  open,
  onOpenChange,
  onSent,
  onStale,
}: {
  player: FriendZonePlayer;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSent: () => void;
  onStale: (failure: Failure) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        {/* Keyed by the dialog's opening, so every report starts empty. */}
        {open && <ReportForm player={player} onClose={() => onOpenChange(false)} onSent={onSent} onStale={onStale} />}
      </DialogContent>
    </Dialog>
  );
}

export function ReportForm({
  player,
  onClose,
  onSent,
  onStale,
}: {
  player: FriendZonePlayer;
  onClose: () => void;
  onSent: () => void;
  onStale: (failure: Failure) => void;
}) {
  const ids = useId();
  const [reason, setReason] = useState<ReportReason | null>(null);
  const [details, setDetails] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!reason) return setProblem("Choose a reason.");
    setBusy(true);
    setProblem(null);
    try {
      await reportPlayer(player.playerId, reason, details);
      onSent();
    } catch (error) {
      const failure = failureCopy(error, "report");
      if (failure.reload) onStale(failure);
      else setProblem(failure.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form noValidate onSubmit={submit} className="flex flex-col gap-4">
      <DialogHeader>
        <DialogTitle>Report a member</DialogTitle>
        <DialogDescription>
          Report <PlayerName name={player.displayName} /> if something isn't right. {REPORT_NOTE}
        </DialogDescription>
      </DialogHeader>
      <fieldset className="flex flex-col gap-1" aria-describedby={problem ? `${ids}-problem` : undefined}>
        <legend className="mb-1 text-sm font-bold text-foreground">Reason</legend>
        {REASONS.map(([value, label]) => (
          <label key={value} className="flex min-h-11 cursor-pointer items-center gap-3 rounded-lg px-2 text-sm text-foreground hover:bg-surface-2">
            <input
              type="radio"
              name={`${ids}-reason`}
              value={value}
              checked={reason === value}
              onChange={() => setReason(value)}
              className="focus-ring h-4 w-4 accent-brand"
            />
            {label}
          </label>
        ))}
      </fieldset>
      <div className="flex flex-col gap-1.5">
        <label htmlFor={`${ids}-details`} className="text-sm font-bold text-foreground">
          Details <span className="font-normal text-text-secondary">(optional)</span>
        </label>
        <textarea
          id={`${ids}-details`}
          aria-describedby={`${ids}-count`}
          dir="auto"
          rows={4}
          maxLength={DETAILS_MAX}
          value={details}
          onChange={(event) => setDetails(event.target.value)}
          className="focus-field min-h-24 w-full rounded-xl border border-input bg-card px-3 py-2 text-base text-foreground placeholder:text-text-secondary sm:text-sm"
        />
        <p id={`${ids}-count`} className="text-xs text-text-secondary">
          {details.length} of {DETAILS_MAX} characters. Don't include an invoice number or email.
        </p>
      </div>
      {problem && (
        <p id={`${ids}-problem`} role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      )}
      <DialogFooter className="gap-2 sm:gap-2">
        <Button type="button" variant="outline" className="min-h-11" disabled={busy} onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" className="min-h-11" disabled={busy}>
          {busy ? "Sending…" : "Send report"}
        </Button>
      </DialogFooter>
    </form>
  );
}
