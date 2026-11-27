"use client";

import { Button } from "@v1/ui/button";
import { Input } from "@v1/ui/input";
import { cn } from "@v1/ui/utils";
import { CheckCircle2 } from "lucide-react";
import { type FormEvent, useId, useState } from "react";

import { forgetQuizMeOffer } from "@/features/chat/quiz-me";
import { ApiError, describeError } from "@/lib/api/errors";
import { type ClaimResult, claimInvoice } from "@/lib/api/programs";
import { refreshPrograms } from "./use-programs";

/** What the form says after an attempt. Shown for this visit only: a refusal is never stored anywhere. */
export type ClaimFeedback = { tone: "success" | "error"; lines: string[] };

export const NOT_VERIFIED = [
  "Not verified: we couldn't verify this invoice. Check the number and try again.",
  "If this is your invoice, contact NetworkersHome.",
];
// The API allows 5 attempts an hour and 20 a day per account, and says nothing more (backend auth/service.py).
export const TOO_MANY = ["Too many attempts. Try again in an hour, or tomorrow if you've made many attempts today."];

/**
 * The answer to one claim, worded here (never the API's text). Every refusal is the API's one generic 404, whatever
 * the reason (unknown, another account's, revoked, expired), so nothing about other invoices can be learnt here.
 */
export function claimFeedback(outcome: { result: ClaimResult } | { error: unknown }): ClaimFeedback {
  if ("result" in outcome) {
    const { status, program } = outcome.result;
    return {
      tone: "success",
      lines: [status === "already" ? `Approved: ${program.title} is already on your account.` : `Approved: ${program.title} is open.`],
    };
  }
  const { error } = outcome;
  if (error instanceof ApiError && error.code === "not_found") return { tone: "error", lines: NOT_VERIFIED };
  if (error instanceof ApiError && error.code === "rate_limited") return { tone: "error", lines: TOO_MANY };
  return { tone: "error", lines: [describeError(error).message] };
}

/**
 * Enter an NH invoice number (My Programs, the one place for it): the server checks it against NetworkersHome's
 * records at once. Verifying keeps the button disabled, so one press is one attempt.
 */
export function InvoiceForm({ onClaimed, autoFocus = false }: { onClaimed: (result: ClaimResult) => void; autoFocus?: boolean }) {
  const id = useId();
  const [number, setNumber] = useState("");
  const [pending, setPending] = useState(false);
  const [feedback, setFeedback] = useState<ClaimFeedback | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (pending || !number.trim()) return;
    setPending(true);
    setFeedback(null);
    try {
      const result = await claimInvoice(number.trim());
      setNumber("");
      setFeedback(claimFeedback({ result }));
      refreshPrograms();
      forgetQuizMeOffer();
      onClaimed(result);
    } catch (error) {
      setFeedback(claimFeedback({ error }));
    } finally {
      setPending(false);
    }
  };

  const failed = feedback?.tone === "error";
  return (
    <form onSubmit={submit} className="flex flex-col gap-3" noValidate aria-busy={pending || undefined}>
      <label htmlFor={`${id}-invoice`} className="text-sm font-semibold text-foreground">
        NH invoice number
      </label>
      <Input
        id={`${id}-invoice`}
        value={number}
        onChange={(event) => setNumber(event.target.value)}
        autoComplete="off"
        spellCheck={false}
        maxLength={80}
        autoFocus={autoFocus}
        aria-invalid={failed || undefined}
        aria-describedby={`${id}-status`}
        placeholder="For example NH-2026-0012"
        className="max-w-sm"
      />
      <div id={`${id}-status`} role="status" className="min-h-5 text-sm">
        {pending ? (
          <p className="text-text-secondary">Verifying your invoice…</p>
        ) : (
          feedback && (
            <div className={cn("flex items-start gap-2", failed ? "text-destructive" : "text-success")}>
              {!failed && <CheckCircle2 aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />}
              <div>
                {feedback.lines.map((line, index) => (
                  <p key={line} className={index > 0 ? "text-text-secondary" : undefined}>
                    {line}
                  </p>
                ))}
              </div>
            </div>
          )
        )}
      </div>
      <Button type="submit" disabled={pending || !number.trim()} className="min-h-11 self-start">
        {pending ? "Verifying…" : "Verify invoice"}
      </Button>
    </form>
  );
}
