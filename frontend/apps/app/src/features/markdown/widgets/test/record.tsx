"use client";

import { useContext, useEffect, useState } from "react";

import { ApiError } from "@/lib/api/errors";
import { KNOWN_SCHEMA_VERSIONS, type QuestionKind } from "@/lib/api/types";
import { testLocator } from "../../assessment";
import { TestContext } from "../../context";

/**
 * The server's record for a practice block (app/assessments), shared by the test and exercise
 * cards: found by the block's locator in what the reply's metadata says. While the reply is still
 * being written there is none yet (`waiting`). A block with no record (an old reply, or one the
 * server could not accept), or a record of a version this app doesn't know, gives up, so the
 * block's source shows with the reason.
 */
export function useRecord(source: string, onError: (note?: string) => void, noun: "test" | "exercise") {
  const tests = useContext(TestContext);
  // undefined while hashing; null when this browser cannot hash (the card then cannot bind).
  const [locator, setLocator] = useState<string | null | undefined>(undefined);
  useEffect(() => {
    let alive = true;
    testLocator(source).then(
      (found) => alive && setLocator(found),
      () => alive && setLocator(null),
    );
    return () => {
      alive = false;
    };
  }, [source]);

  const found = locator ? tests?.assessments.find((item) => item.locator === locator) : undefined;
  const known = found ? KNOWN_SCHEMA_VERSIONS.includes(found.schemaVersion) : true;
  const record = known ? found : undefined;
  const waiting = Boolean(tests?.streaming) || locator === undefined;
  const reason = !known
    ? `This ${noun} needs a newer version of the app. Reload the page to use it.`
    : tests?.error
      ? `This ${noun} couldn't be set up, so it can't be answered. Tap Regenerate for a new one.`
      : `This ${noun} can't be answered here (it was written before answers were saved). Ask for a new one.`;
  useEffect(() => {
    if (!waiting && !record) onError(reason);
  }, [waiting, record, reason, onError]);
  return { tests, record, waiting };
}

export function Preparing({ noun }: { noun: string }) {
  return (
    <p role="status" className="my-3 rounded-xl border border-border bg-study-card px-3.5 py-3 text-sm text-text-secondary">
      Preparing your {noun}…
    </p>
  );
}

/** A 422's reason (the router's `fields[].reason` for `value`) in the app's own words. */
const INVALID: Record<string, string> = {
  number: "Use digits, with an optional leading minus and a full stop for decimals (for example -12.5).",
  duplicate: "Each choice can be picked only once.",
  too_long: "This answer is too long. Shorten it and try again.",
  string_too_long: "This answer is too long. Shorten it and try again.",
  drafts_full: "Your saved answers are too long to keep. Shorten one and save again.",
};

/**
 * Why a request for this card failed, for its status line (or, for a 422, the question's own field). `kind`
 * names the question, so a 422 with no known reason still reads right for it (a numeric answer's format).
 */
export function failure(error: unknown, noun = "test", kind?: QuestionKind): string {
  if (error instanceof ApiError && error.status === 409) {
    return `This ${noun} moved on elsewhere (another tab?) or was replaced. It now shows where it is.`;
  }
  if (error instanceof ApiError && error.status === 422) {
    const reason = error.fields?.map((field) => field.reason).find((found) => found && found in INVALID);
    return INVALID[reason ?? (kind === "numeric" ? "number" : "")] ?? "This answer doesn't fit the question. Check it and try again.";
  }
  if (error instanceof ApiError && error.status === 429) {
    return "Too many answers at once. Wait a moment and try again.";
  }
  return "Your answer wasn't saved. Check your connection and try again.";
}
