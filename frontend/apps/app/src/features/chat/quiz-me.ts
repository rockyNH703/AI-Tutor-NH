"use client";

import { useEffect, useState } from "react";

import { getBankAccess } from "@/lib/api/progress";

/**
 * The composer's Quiz me button (owner, 2026-11-20): a request the certification bank always understands, so a
 * student never has to guess the wording. The bank's rules read typed requests too; this is the sure way in.
 *   - a chat with messages, an empty box and no files: send "Quiz me on this" (questions on what was discussed);
 *   - otherwise put "Quiz me on " before what is in the box and let the student finish and send.
 * Files are never sent with it: the bank answers no message that carries files.
 */
export type QuizMeAction = { kind: "send" | "fill"; text: string };

const ASK = "Quiz me on ";

export function quizMeAction({
  draft,
  hasMessages,
  hasFiles,
}: {
  draft: string;
  hasMessages: boolean;
  hasFiles: boolean;
}): QuizMeAction {
  const topic = draft.trim();
  if (!topic && hasMessages && !hasFiles) return { kind: "send", text: `${ASK}this` };
  if (/^quiz me\b/i.test(topic)) return { kind: "fill", text: topic };
  return { kind: "fill", text: `${ASK}${topic}` };
}

/** Asked once per page: whether the bank answers this student's quiz requests (each chat turn checks again). */
let offered: Promise<boolean> | null = null;

export function useQuizMeOffered(): boolean {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    let alive = true;
    offered ??= getBankAccess().then(
      (access) => access.enabled,
      () => {
        offered = null; // asked again on the next chat screen
        return false;
      },
    );
    void offered.then((enabled) => alive && setShown(enabled));
    return () => {
      alive = false;
    };
  }, []);
  return shown;
}
