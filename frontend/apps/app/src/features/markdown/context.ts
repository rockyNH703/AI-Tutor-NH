import { createContext } from "react";

import type { Assessment, AssessmentResponseRequest, AssessmentValue, Citation } from "@/lib/api/types";

/**
 * True only for the last block of a reply that is still streaming. Widgets that need their
 * whole source (a diagram, a chart, a quiz) wait for it instead of drawing half of it.
 */
export const LiveContext = createContext(false);

/**
 * What the reply's [1] and [F1] markers point at. `undefined` = not known yet (the reply is
 * still streaming, or it came from before citations existed): never "missing".
 */
export const CitationsContext = createContext<Citation[] | undefined>(undefined);

/** A typed command that reached a reply's test (chat-store.ts): the card shows it and scrolls to it. */
export type TestNotice = { position: number; text: string; seq: number };

/**
 * A reply's practice test as the server recorded it, for the card that draws it (widgets/quiz.tsx).
 * `null` outside a chat reply. While the reply is still being written there is no test yet.
 */
export type TestState = {
  streaming: boolean;
  assessments: readonly Assessment[];
  /** Why the reply's test was not accepted (no test was kept). */
  error: string | null;
  notice: TestNotice | null;
  /**
   * Sends one request through the test's queue in the chat store (`${messageId}:${position}`), so
   * card saves, answers, submits and typed commands go one at a time; the store fills `revision`.
   */
  respond: (position: number, request: Omit<AssessmentResponseRequest, "position" | "revision">) => Promise<void>;
  /** Resolves once the test's queued requests have all settled (e.g. before counting unanswered). */
  idle?: (position: number) => Promise<void>;
  /** The questions of a test whose last save failed (still unsaved after its retries). */
  unsaved?: (position: number) => readonly number[];
  /** Practice answers typed but not yet sent: in memory, survive switching chats, never sent. */
  drafts?: PracticeDrafts;
  /**
   * Get feedback: sends `text` as the user's next message in this chat (not through the composer,
   * whose draft stays as typed). `busy`: a reply is still being written; `refused`: not sent.
   */
  ask?: (text: string) => Promise<"sent" | "busy" | "refused">;
};

export type PracticeDrafts = {
  get: (position: number, question: number) => AssessmentValue | undefined;
  set: (position: number, question: number, value: AssessmentValue | undefined) => void;
};

export const TestContext = createContext<TestState | null>(null);

/**
 * Lets go of "stick to the bottom" before a card grows by the user's own action (expand all, a review,
 * a preview), so the transcript doesn't scroll the control they just pressed out of view. Stable.
 */
export const ReleaseScrollContext = createContext<() => void>(() => {});
