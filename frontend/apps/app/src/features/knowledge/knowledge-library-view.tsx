"use client";

import { Button } from "@v1/ui/button";
import { BookOpenCheck, Database, ShieldCheck } from "lucide-react";
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

  if (state === "loading") {
    return <p className="page-container text-sm text-text-secondary">Checking operator access…</p>;
  }
  if (state === "denied") {
    return (
      <div className="page-container">
        <h1 className="text-2xl font-bold">Knowledge Library</h1>
        <p className="mt-3 text-text-secondary">
          This area is available only to an explicitly granted Knowledge Library operator.
        </p>
      </div>
    );
  }
  if (state === "error") {
    return (
      <div className="page-container">
        <h1 className="text-2xl font-bold">Knowledge Library unavailable</h1>
        <p className="mt-3 text-text-secondary">Operator access could not be checked. No import action was enabled.</p>
      </div>
    );
  }

  return (
    <div className="page-container">
      <div className="rounded-2xl border border-border bg-card p-6 elev-1">
        <div className="flex items-start gap-4">
          <div className="rounded-xl bg-brand/10 p-3 text-brand">
            <BookOpenCheck aria-hidden className="h-6 w-6" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-foreground">Knowledge Library</h1>
            <p className="mt-2 text-sm text-text-secondary">
              Owner review for immutable PDF imports, duplicate decisions, processing revisions, publication and citations.
            </p>
          </div>
        </div>
      </div>

      <div className="mt-5 grid gap-4 md:grid-cols-2">
        <section className="rounded-2xl border border-border bg-card p-5">
          <Database aria-hidden className="h-5 w-5 text-brand" />
          <h2 className="mt-3 font-bold">Import is API-gated</h2>
          <p className="mt-2 text-sm text-text-secondary">
            The resumable importer uses a short-lived scoped secret, sequential offsets and a final SHA-256 check. A browser folder picker is intentionally not enabled yet.
          </p>
        </section>
        <section className="rounded-2xl border border-border bg-card p-5">
          <ShieldCheck aria-hidden className="h-5 w-5 text-brand" />
          <h2 className="mt-3 font-bold">Publication remains manual</h2>
          <p className="mt-2 text-sm text-text-secondary">
            Distribution rights, provider egress, extraction quality and duplicate review must pass before a revision can be published.
          </p>
        </section>
      </div>

      <div className="mt-5 rounded-2xl border border-amber-300/40 bg-amber-500/5 p-5">
        <h2 className="font-bold">Controlled rollout</h2>
        <p className="mt-2 text-sm text-text-secondary">
          Use the guarded operator workflow and Windows importer after the storage and parser gates pass. Real CCNA books are not accepted through the normal File Library.
        </p>
        <Button asChild variant="outline" className="mt-4">
          <Link href="/library">Open personal File Library</Link>
        </Button>
      </div>
    </div>
  );
}
