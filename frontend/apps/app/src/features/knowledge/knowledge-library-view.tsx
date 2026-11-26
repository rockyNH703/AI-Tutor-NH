"use client";

import { Button } from "@v1/ui/button";
import { EmptyState, ErrorState } from "@v1/ui/empty-state";
import { Skeleton, SkeletonGroup } from "@v1/ui/skeleton";
import { Lock } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { describeError } from "@/lib/api/errors";
import { getKnowledgeAdminStatus } from "@/lib/api/knowledge";

export function KnowledgeLibraryView() {
  const [state, setState] = useState<"loading" | "allowed" | "denied" | "error">("loading");

  useEffect(() => {
    void getKnowledgeAdminStatus().then(
      () => setState("allowed"),
      (error) => setState(describeError(error).code === "not_found" ? "denied" : "error"),
    );
  }, []);

  // The app header names the page (its h1): these states start at h2.
  if (state === "loading") {
    return (
      <div className="page-container">
        <SkeletonGroup label="Checking operator access" className="flex flex-col gap-3">
          <Skeleton className="h-7 w-56 rounded-md" />
          <Skeleton className="h-4 w-80 max-w-full rounded-md" />
        </SkeletonGroup>
      </div>
    );
  }
  if (state === "denied") {
    return (
      <div className="page-container">
        <EmptyState
          icon={Lock}
          title="Operators only"
          description="Only granted operators can open it. Your own files are in the File Library."
          action={
            <Button asChild variant="outline">
              <Link href="/library">Open your File Library</Link>
            </Button>
          }
        />
      </div>
    );
  }
  if (state === "error") {
    return (
      <div className="page-container">
        <ErrorState title="Knowledge Library unavailable" description="Operator access couldn't be checked, so nothing is enabled." />
      </div>
    );
  }

  return (
    <div className="page-container flex flex-col gap-5">
      <header>
        <h2 className="font-display text-2xl font-bold tracking-tight text-foreground">Knowledge Library</h2>
        <p className="mt-1 max-w-prose text-sm text-text-secondary">
          Owner review of PDF imports, from upload to a published, citable revision.
        </p>
      </header>

      <section aria-labelledby="knowledge-steps" className="rounded-2xl border border-border bg-card p-5 elev-1">
        <h3 id="knowledge-steps" className="mb-1 text-sm font-bold text-foreground">
          How a book gets in
        </h3>
        <ol className="flex flex-col divide-y divide-border">
          {[
            ["Import", "Through the API only: a short-lived scoped secret, resumable offsets and a final SHA-256 check."],
            ["Review", "Duplicates, extraction quality and each processing revision are decided by the owner."],
            ["Publish", "Manual, once distribution rights and provider egress are cleared."],
          ].map(([step, text], index) => (
            <li key={step} className="flex gap-3 py-3 last:pb-0">
              <span
                aria-hidden
                className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-brand-soft text-2xs font-bold text-brand"
              >
                {index + 1}
              </span>
              <p className="text-sm leading-relaxed text-text-secondary">
                <span className="font-bold text-foreground">{step}. </span>
                {text}
              </p>
            </li>
          ))}
        </ol>
      </section>

      <div className="flex flex-col gap-3 rounded-2xl border border-warning/40 bg-warning-soft p-4 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-sm text-foreground">
          Controlled rollout: the operator workflow and the Windows importer only. Books are never taken through the File
          Library.
        </p>
        <Button asChild variant="outline" className="shrink-0">
          <Link href="/library">Open your File Library</Link>
        </Button>
      </div>
    </div>
  );
}
