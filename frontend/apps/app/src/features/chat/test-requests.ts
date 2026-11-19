/**
 * A chat's practice-test requests (chat-store.ts owns one): each test's queue (`${messageId}:${position}`) of card
 * saves, answers, submits and typed commands, sent one at a time; 429 retries; the questions whose save failed; the
 * practice drafts; and, after a 409, the conversation read again before the test's card is told.
 */

import { ApiError } from "@/lib/api/errors";
import type { Assessment, AssessmentResponse, AssessmentResponseRequest, AssessmentValue } from "@/lib/api/types";

/** A card's or a typed command's request: the queue adds the position (and a submit's revision). */
export type TestRequest = Omit<AssessmentResponseRequest, "position" | "revision">;

/** What the store does for the requests. `canonical` is a conversation's key in the store. */
export type TestHost = {
  /** Sends one request (the API). */
  respond: (messageId: string, body: AssessmentResponseRequest) => Promise<AssessmentResponse>;
  /** The test as the store holds it now. */
  findTest: (canonical: string, messageId: string, position: number) => Assessment | undefined;
  /** What the server recorded; `answered` for an answer or a submit (the streak may have changed). */
  applyRecord: (
    canonical: string,
    messageId: string,
    position: number,
    response: AssessmentResponse,
    answered: boolean,
  ) => void;
  /** After a 409: read the conversation again; false while a reply streams (`streamEnded` comes once it ends). */
  conflict: (canonical: string) => Promise<boolean>;
  /** Says something on a test's card (its status line). */
  notice: (canonical: string, messageId: string, position: number, text: string) => void;
};

/** A request refused with 429 is tried again this many times (after the retry delay). */
const RETRIES = 2;
export const CHANGED_ELSEWHERE = "Changed in another tab. The test now shows where it is.";
const NONE: readonly number[] = Object.freeze([]);
const ignore = () => undefined;

type TestQueue = {
  tail: Promise<void>;
  /** Bumped by a 409: what was queued before it is dropped (it was made on a test that has changed). */
  epoch: number;
  conflict: unknown;
  /** reset() / forget(): nothing queued is sent any more. */
  closed: boolean;
  /** Questions whose last save or clear failed. */
  unsaved: Set<number>;
};

export class TestRequests {
  private readonly host: TestHost;
  private readonly retryDelayMs: number;
  private readonly queues = new Map<string, TestQueue>();
  /** Practice answers typed but not sent, `${messageId}:${position}:${question}`: memory only, silent. */
  private readonly drafts = new Map<string, AssessmentValue>();
  /** Tests that changed elsewhere (409), per conversation, told so once it has been read again. */
  private readonly changed = new Map<string, { messageId: string; position: number }[]>();

  constructor(host: TestHost, retryDelayMs: number) {
    this.host = host;
    this.retryDelayMs = retryDelayMs;
  }

  /** Queues one request after what was asked of the same test before it; rejects with the request's error. */
  respond(canonical: string, messageId: string, position: number, request: TestRequest): Promise<void> {
    if (request.action === "save" || request.action === "clear" || request.action === "submit") {
      // A schemaVersion 1 record takes answer and skip only (the server would refuse the rest): never sent.
      if ((this.host.findTest(canonical, messageId, position)?.schemaVersion ?? 1) < 2) return Promise.resolve();
    }
    const id = `${messageId}:${position}`;
    let queue = this.queues.get(id);
    if (!queue) {
      queue = { tail: Promise.resolve(), epoch: 0, conflict: null, closed: false, unsaved: new Set() };
      this.queues.set(id, queue);
    }
    const own = queue;
    const epoch = own.epoch;
    const run = own.tail.then(() => {
      if (own.closed) return;
      if (own.epoch !== epoch) throw own.conflict;
      return this.send(canonical, messageId, position, request, own);
    });
    own.tail = run.then(ignore, ignore);
    return run;
  }

  /** Resolves once every request queued for the test so far (and any queued meanwhile) has settled. */
  async idle(messageId: string, position: number): Promise<void> {
    const id = `${messageId}:${position}`;
    let tail = this.queues.get(id)?.tail;
    while (tail) {
      await tail;
      const next = this.queues.get(id)?.tail;
      if (next === tail) return;
      tail = next;
    }
  }

  /** The test's questions whose last save or clear failed (after its retries), in order. */
  unsaved(messageId: string, position: number): readonly number[] {
    const unsaved = this.queues.get(`${messageId}:${position}`)?.unsaved;
    return unsaved?.size ? [...unsaved].sort((a, b) => a - b) : NONE;
  }

  getDraft(messageId: string, position: number, question: number): AssessmentValue | undefined {
    return this.drafts.get(`${messageId}:${position}:${question}`);
  }

  setDraft(messageId: string, position: number, question: number, value: AssessmentValue | undefined): void {
    const id = `${messageId}:${position}:${question}`;
    if (value === undefined) this.drafts.delete(id);
    else this.drafts.set(id, value);
  }

  /** A reply ended in this conversation: a test that changed while it streamed is read again now. */
  streamEnded(canonical: string): void {
    if (this.changed.has(canonical)) void this.reread(canonical);
  }

  /** The conversation is gone: its replies' queues are closed (nothing queued is sent), their drafts forgotten. */
  forget(canonical: string, messageIds: ReadonlySet<string>): void {
    const owner = (id: string) => messageIds.has(id.slice(0, id.indexOf(":")));
    for (const [id, queue] of this.queues) {
      if (!owner(id)) continue;
      queue.closed = true;
      this.queues.delete(id);
    }
    for (const id of this.drafts.keys()) if (owner(id)) this.drafts.delete(id);
    this.changed.delete(canonical);
  }

  /** Sign-out: nothing queued is sent, and nothing is kept. */
  reset(): void {
    for (const queue of this.queues.values()) queue.closed = true;
    this.queues.clear();
    this.drafts.clear();
    this.changed.clear();
  }

  private async send(
    canonical: string,
    messageId: string,
    position: number,
    request: TestRequest,
    queue: TestQueue,
  ): Promise<void> {
    const body: AssessmentResponseRequest = { position, ...request };
    if (request.action === "submit") {
      // The revision the store holds now, after the test's own saves queued before it.
      const revision = this.host.findTest(canonical, messageId, position)?.revision;
      if (typeof revision === "number") body.revision = revision;
    }
    const saving = request.action === "save" || request.action === "clear";
    let response: AssessmentResponse;
    try {
      response = await this.withRetries(messageId, body, queue);
    } catch (error) {
      if (queue.closed) throw error;
      if (error instanceof ApiError && error.status === 409) {
        queue.epoch += 1;
        queue.conflict = error;
        queue.unsaved.clear(); // the card re-seeds from the record as it is now
        await this.changedElsewhere(canonical, messageId, position);
      } else if (saving) queue.unsaved.add(request.question);
      throw error;
    }
    if (queue.closed) return;
    if (saving) queue.unsaved.delete(request.question);
    const answered = request.action === "answer" || request.action === "submit";
    this.host.applyRecord(canonical, messageId, position, response, answered);
  }

  private async withRetries(
    messageId: string,
    body: AssessmentResponseRequest,
    queue: TestQueue,
  ): Promise<AssessmentResponse> {
    for (let attempt = 0; ; attempt += 1) {
      try {
        return await this.host.respond(messageId, body);
      } catch (error) {
        const limited = error instanceof ApiError && error.status === 429;
        if (!limited || attempt >= RETRIES || queue.closed) throw error;
        await new Promise((resolve) => setTimeout(resolve, this.retryDelayMs));
      }
    }
  }

  /**
   * A test changed elsewhere (another tab saved, answered or submitted first) or was replaced: read the
   * conversation again, then tell its card. While a reply streams the server's page is older than what is
   * on screen, so that waits until the stream ends (`streamEnded`).
   */
  private async changedElsewhere(canonical: string, messageId: string, position: number): Promise<void> {
    const told = this.changed.get(canonical) ?? [];
    if (!told.some((item) => item.messageId === messageId && item.position === position)) {
      this.changed.set(canonical, [...told, { messageId, position }]);
    }
    await this.reread(canonical);
  }

  private async reread(canonical: string): Promise<void> {
    if (!(await this.host.conflict(canonical))) return;
    const told = this.changed.get(canonical) ?? [];
    this.changed.delete(canonical);
    for (const { messageId, position } of told) this.host.notice(canonical, messageId, position, CHANGED_ELSEWHERE);
  }
}
