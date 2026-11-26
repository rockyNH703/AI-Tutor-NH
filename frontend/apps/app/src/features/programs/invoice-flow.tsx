"use client";

import { Button } from "@v1/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@v1/ui/dialog";
import { Input } from "@v1/ui/input";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, type ReactNode, useId, useState } from "react";

import { forgetQuizMeOffer } from "@/features/chat/quiz-me";
import { SettingsCard } from "@/features/settings/primitives";
import { ApiError, describeError } from "@/lib/api/errors";
import { type ClaimResult, claimInvoice, dismissInvoicePrompt } from "@/lib/api/programs";
import { refreshPrograms, usePrograms } from "./use-programs";

const NOT_VERIFIED = "We couldn't verify this invoice. Check the number, or contact NetworkersHome if it's yours.";

/**
 * Enter an NH invoice number; the server checks it against NH's records (one generic answer for every failure, so
 * nothing about other invoices can be learnt here).
 */
export function InvoiceForm({
  onClaimed,
  autoFocus = false,
  secondary,
}: {
  onClaimed: (result: ClaimResult) => void;
  autoFocus?: boolean;
  /** Shown beside Verify invoice, on the same row (the first-run dialog's Continue without an invoice). */
  secondary?: ReactNode;
}) {
  const id = useId();
  const [number, setNumber] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (pending || !number.trim()) return;
    setPending(true);
    setError(null);
    try {
      const result = await claimInvoice(number.trim());
      refreshPrograms();
      forgetQuizMeOffer();
      onClaimed(result);
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "not_found") setError(NOT_VERIFIED);
      else if (caught instanceof ApiError && caught.code === "rate_limited")
        setError("Too many attempts for now. Please try again later.");
      else setError(describeError(caught).message);
    } finally {
      setPending(false);
    }
  };

  return (
    <form onSubmit={submit} className="flex flex-col gap-3" noValidate>
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
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : undefined}
        placeholder="For example NH-2026-0012"
      />
      <p id={`${id}-error`} role="status" className="min-h-5 text-sm text-destructive">
        {error ?? ""}
      </p>
      {/* One row with the dialog's way out (owner: save vertical space); it wraps below on a narrow phone. */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Button type="submit" disabled={pending || !number.trim()} className="min-h-11">
          {pending ? "Checking…" : "Verify invoice"}
        </Button>
        {secondary}
      </div>
    </form>
  );
}

/** First sign-in: "Are you an NH student?" Continue without an invoice is remembered by the server. */
export function InvoicePrompt() {
  const { data } = usePrograms();
  const router = useRouter();
  const [closed, setClosed] = useState(false);
  const open = !closed && data?.invoicePrompt === true;

  const continueWithout = async () => {
    setClosed(true);
    try {
      await dismissInvoicePrompt();
    } catch {
      // Not remembered: the prompt simply comes back on the next visit, which is harmless.
    }
  };

  return (
    <Dialog open={open} onOpenChange={(next) => !next && setClosed(true)}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          {/* Centred on a phone: padding on both sides keeps the close button off the title. */}
          <DialogTitle className="px-6 sm:px-0">Are you a NetworkersHome student?</DialogTitle>
          <DialogDescription>
            Enter the invoice number of your NH program to open it here: a tutor for every month of your course,
            quizzes, mock interviews and videos.
          </DialogDescription>
        </DialogHeader>
        <div className="mt-2">
          <InvoiceForm
            autoFocus
            onClaimed={(result) => {
              setClosed(true);
              router.push(`/programs/${result.program.slug}`);
            }}
            secondary={
              <Button type="button" variant="ghost" className="min-h-11" onClick={continueWithout}>
                Continue without an invoice
              </Button>
            }
          />
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** Settings > Profile: the programs the account's invoices open, and another invoice to add. */
export function NhEnrolmentCard() {
  const { data } = usePrograms();
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState<string | null>(null);
  if (!data?.enabled) return null;
  return (
    <SettingsCard
      title="NH enrolment"
      description={
        data.programs.length === 0 ? "No program yet: an invoice number opens one." : "Your NetworkersHome programs, opened by your invoice numbers."
      }
      footer={
        adding ? null : (
          <Button type="button" size="sm" variant="outline" className="min-h-11" onClick={() => setAdding(true)}>
            Add an invoice
          </Button>
        )
      }
    >
      {data.programs.length > 0 && (
        <ul className="flex flex-col gap-1">
          {data.programs.map((program) => (
            <li key={program.slug}>
              <Link href={`/programs/${program.slug}`} className="focus-ring rounded text-sm font-semibold text-brand hover:underline">
                {program.title}
              </Link>
            </li>
          ))}
        </ul>
      )}
      {added && (
        <p role="status" className="mt-2 text-sm text-foreground">
          Added: {added}
        </p>
      )}
      {adding && (
        <div className="mt-4">
          <InvoiceForm
            autoFocus
            onClaimed={(result) => {
              setAdded(result.program.title);
              setAdding(false);
            }}
          />
        </div>
      )}
    </SettingsCard>
  );
}
