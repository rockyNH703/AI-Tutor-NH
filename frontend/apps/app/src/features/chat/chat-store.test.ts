import { describe, expect, test } from "bun:test";
import type { StreamHandlers, StreamOutcome } from "@/lib/api/chat";
import { ApiError } from "@/lib/api/errors";
import type {
  Assessment,
  AssessmentQuestion,
  AssessmentResponse,
  AssessmentResponseRequest,
  ChatRequest,
  Conversation,
  Message,
  MessagePage,
  QuestionKind,
} from "@/lib/api/types";
import { type ChatApi, ChatStore } from "./chat-store";

const A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const uuid = (n: number) => `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`;

const conversation = (id: string, title = "Chat"): Conversation => ({
  id,
  title,
  createdAt: "2026-01-01T00:00:00Z",
  updatedAt: "2026-01-01T00:00:00Z",
});
const message = (id: string, conversationId: string, role: "user" | "assistant", content: string, status: Message["status"] = "complete"): Message => ({
  id,
  conversationId,
  role,
  content,
  status,
  createdAt: "2026-01-01T00:00:00Z",
});

type PendingStream = {
  kind: "chat" | "regenerate";
  request?: ChatRequest;
  conversationId?: string;
  handlers: StreamHandlers;
  signal?: AbortSignal;
  resolve: (outcome: StreamOutcome) => void;
  reject: (error: unknown) => void;
};

class FakeApi implements ChatApi {
  streams: PendingStream[] = [];
  cancelled: string[] = [];
  deleted: string[] = [];
  listCalls: { id: string; before?: string | null }[] = [];
  pages = new Map<string, MessagePage>();
  feedbackCalls: string[] = [];
  failFeedback = false;

  private open(stream: Omit<PendingStream, "resolve" | "reject">): Promise<StreamOutcome> {
    return new Promise((resolve, reject) => {
      this.streams.push({ ...stream, resolve, reject });
      stream.signal?.addEventListener("abort", () => resolve("aborted"));
    });
  }
  streamChat = (request: ChatRequest, handlers: StreamHandlers, options: { signal?: AbortSignal }) =>
    this.open({ kind: "chat", request, handlers, signal: options.signal });
  streamRegenerate = (conversationId: string, handlers: StreamHandlers, options: { signal?: AbortSignal }) =>
    this.open({ kind: "regenerate", conversationId, handlers, signal: options.signal });
  cancelMessage = async (id: string) => {
    this.cancelled.push(id);
    return { status: "cancelled" };
  };
  listMessages = async (id: string, options: { before?: string | null }) => {
    this.listCalls.push({ id, before: options.before });
    const page = this.pages.get(`${id}:${options.before ?? ""}`);
    if (!page) throw new ApiError(404, "not_found");
    return page;
  };
  setFeedback = async (id: string, rating: string) => {
    this.feedbackCalls.push(`${id}:${rating}`);
    if (this.failFeedback) throw new ApiError(503, "service_unavailable");
  };
  clearFeedback = async (id: string) => {
    this.feedbackCalls.push(`${id}:clear`);
  };
  deleteConversation = async (id: string) => {
    this.deleted.push(id);
  };
  /** Every test request sent, and the replies scripted for the next ones (in order). */
  assessmentCalls: { messageId: string; request: AssessmentResponseRequest }[] = [];
  private answers: (() => Promise<AssessmentResponse>)[] = [];
  reply(test: AssessmentResponse) {
    this.answers.push(() => Promise.resolve(test));
  }
  fail(error: unknown) {
    this.answers.push(() => Promise.reject(error));
  }
  /** A reply the test settles itself, later. */
  hold() {
    let resolve: (test: AssessmentResponse) => void = () => undefined;
    const promise = new Promise<AssessmentResponse>((settle) => {
      resolve = settle;
    });
    this.answers.push(() => promise);
    return { resolve: (test: AssessmentResponse) => resolve(test) };
  }
  respondToAssessment = (messageId: string, request: AssessmentResponseRequest) => {
    this.assessmentCalls.push({ messageId, request: { ...request } });
    return this.answers.shift()?.() ?? Promise.reject(new ApiError(404, "not_found"));
  };
}

/** A scheduler the test drives: nothing flushes until `tick()`. */
function manualScheduler() {
  let queue: (() => void)[] = [];
  return {
    schedule: (run: () => void) => {
      queue.push(run);
      return () => {
        queue = queue.filter((item) => item !== run);
      };
    },
    tick: () => {
      const current = queue;
      queue = [];
      for (const run of current) run();
    },
  };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

function setup(options: { stopGraceMs?: number } = {}) {
  const api = new FakeApi();
  const clock = manualScheduler();
  const created: { conversation: Conversation; draftKey: string }[] = [];
  const updated: Conversation[] = [];
  const store = new ChatStore({
    api,
    schedule: clock.schedule,
    stopGraceMs: options.stopGraceMs ?? 3000,
    testRetryDelayMs: 0,
    onConversationCreated: (c, draftKey) => created.push({ conversation: c, draftKey }),
    onConversationUpdated: (c) => updated.push(c),
  });
  return { api, clock, store, created, updated };
}

function meta(conversationId: string, assistantId: string, userMessage: Message | null, replaces: string | null = null) {
  return {
    conversationId,
    conversation: conversation(conversationId),
    userMessage,
    assistantMessageId: assistantId,
    replacesMessageId: replaces,
  };
}

describe("a new chat", () => {
  test("draft → meta re-keys to the server's id; tokens batch; done is authoritative", async () => {
    const { api, clock, store, created, updated } = setup();
    const draft = store.freshDraftKey();
    store.activeKey = draft;

    const sending = store.send(draft, "  Hello  ");
    const entry = store.getEntry(draft);
    expect(entry?.order.length).toBe(1);
    expect(store.getMessage(draft, entry?.order[0] ?? "")?.content).toBe("Hello");
    expect(entry?.stream?.phase).toBe("starting");
    expect(api.streams[0]?.request).toEqual({ conversationId: null, message: "Hello" });

    const user = message(uuid(1), A, "user", "Hello");
    api.streams[0]?.handlers.onMeta?.(meta(A, uuid(2), user));

    expect(store.resolve(draft)).toBe(A);
    expect(store.activeKey).toBe(A);
    expect(store.getEntry(A)?.order).toEqual([uuid(1), uuid(2)]);
    expect(store.getEntry(draft)).toBe(store.getEntry(A));
    expect(store.getEntry(A)?.stream).toEqual({ phase: "streaming", assistantId: uuid(2) });
    expect(created.map((item) => [item.conversation.id, item.draftKey])).toEqual([[A, draft]]);

    api.streams[0]?.handlers.onToken?.({ text: "Hi" });
    api.streams[0]?.handlers.onToken?.({ text: " there" });
    expect(store.getMessage(A, uuid(2))?.content).toBe(""); // batched, not yet flushed
    clock.tick();
    expect(store.getMessage(A, uuid(2))?.content).toBe("Hi there");

    api.streams[0]?.handlers.onDone?.({
      message: message(uuid(2), A, "assistant", "Hi there!"),
      conversation: conversation(A, "Hello"),
    });
    api.streams[0]?.resolve("done");
    await sending;

    expect(store.getMessage(A, uuid(2))?.content).toBe("Hi there!");
    expect(store.getMessage(A, uuid(2))?.status).toBe("complete");
    expect(store.getEntry(A)?.stream).toBeNull();
    expect(updated.at(-1)?.title).toBe("Hello");
    expect(api.streams.length).toBe(1);
  });

  test("a token notifies only the streaming message's subscribers", async () => {
    const { api, clock, store } = setup();
    const draft = store.freshDraftKey();
    let entryNotifications = 0;
    let replyNotifications = 0;
    let userNotifications = 0;
    store.subscribe(draft, () => {
      entryNotifications += 1;
    });

    void store.send(draft, "Hello");
    api.streams[0]?.handlers.onMeta?.(meta(A, uuid(2), message(uuid(1), A, "user", "Hello")));
    store.subscribeMessage(A, uuid(2), () => {
      replyNotifications += 1;
    });
    store.subscribeMessage(A, uuid(1), () => {
      userNotifications += 1;
    });
    const entryBefore = entryNotifications;

    for (let index = 0; index < 20; index += 1) api.streams[0]?.handlers.onToken?.({ text: "x" });
    clock.tick();

    expect(replyNotifications).toBe(1); // 20 tokens, one batched flush
    expect(userNotifications).toBe(0);
    expect(entryNotifications).toBe(entryBefore); // the list did not re-render
    expect(store.listenerCount("entry", A)).toBe(1); // the draft's listener followed the re-key
  });

  test("two new chats started back to back stay separate", async () => {
    const { api, store } = setup();
    const first = store.freshDraftKey();
    void store.send(first, "one");
    const second = store.freshDraftKey();
    expect(second).not.toBe(first);
    void store.send(second, "two");
    expect(api.streams.length).toBe(2);
    api.streams[0]?.handlers.onMeta?.(meta(A, uuid(2), message(uuid(1), A, "user", "one")));
    api.streams[1]?.handlers.onMeta?.(meta(B, uuid(4), message(uuid(3), B, "user", "two")));
    expect(store.resolve(first)).toBe(A);
    expect(store.resolve(second)).toBe(B);
    expect(store.getEntry(A)?.order).toEqual([uuid(1), uuid(2)]);
    expect(store.getEntry(B)?.order).toEqual([uuid(3), uuid(4)]);
  });

  test("an unused draft is reused rather than piling up", () => {
    const { store } = setup();
    expect(store.freshDraftKey()).toBe(store.freshDraftKey());
  });
});

describe("per-conversation state", () => {
  async function loaded(store: ChatStore, api: FakeApi, id: string) {
    api.pages.set(`${id}:`, { items: [message(uuid(90), id, "user", "q"), message(uuid(91), id, "assistant", "a")], nextCursor: null });
    await store.ensureLoaded(id);
  }

  test("a stream in chat A never blocks chat B", async () => {
    const { api, store } = setup();
    await loaded(store, api, A);
    await loaded(store, api, B);
    void store.send(A, "long question");
    expect(store.isStreaming(A)).toBe(true);
    void store.send(B, "other question");
    expect(store.isStreaming(B)).toBe(true);
    expect(api.streams.map((stream) => stream.request?.conversationId)).toEqual([A, B]);
    // …and a second send in A while it streams is ignored.
    void store.send(A, "again");
    expect(api.streams.length).toBe(2);
  });

  test("unsubscribing (the page unmounting) does not stop the stream", async () => {
    const { api, clock, store } = setup();
    await loaded(store, api, A);
    const off = store.subscribe(A, () => undefined);
    const sending = store.send(A, "q2");
    api.streams[0]?.handlers.onMeta?.(meta(A, uuid(2), message(uuid(1), A, "user", "q2")));
    off();
    api.streams[0]?.handlers.onToken?.({ text: "still coming" });
    clock.tick();
    expect(store.getMessage(A, uuid(2))?.content).toBe("still coming");
    api.streams[0]?.handlers.onDone?.({ message: message(uuid(2), A, "assistant", "still coming"), conversation: conversation(A) });
    api.streams[0]?.resolve("done");
    await sending;
    expect(api.streams[0]?.signal?.aborted).toBe(false);
  });

  test("loading a missing or foreign conversation shows not-found", async () => {
    const { store } = setup();
    await store.ensureLoaded(A);
    expect(store.getEntry(A)?.status).toBe("not_found");
  });

  test("older messages are prepended", async () => {
    const { api, store } = setup();
    api.pages.set(`${A}:`, { items: [message(uuid(3), A, "user", "new")], nextCursor: "c1" });
    api.pages.set(`${A}:c1`, { items: [message(uuid(1), A, "user", "old"), message(uuid(2), A, "assistant", "old a")], nextCursor: null });
    await store.ensureLoaded(A);
    await store.loadOlder(A);
    expect(store.getEntry(A)?.order).toEqual([uuid(1), uuid(2), uuid(3)]);
    expect(store.getEntry(A)?.nextCursor).toBeNull();
  });
});

describe("failures", () => {
  test("an `error` event marks the reply failed — no reload, no second request", async () => {
    const { api, store } = setup();
    const draft = store.freshDraftKey();
    const sending = store.send(draft, "Hello");
    api.streams[0]?.handlers.onMeta?.(meta(A, uuid(2), message(uuid(1), A, "user", "Hello")));
    api.streams[0]?.handlers.onError?.({ code: "llm_timeout", message: "x", retryable: true, messageId: uuid(2), requestId: "r9" });
    api.streams[0]?.resolve("error");
    await sending;
    const reply = store.getMessage(A, uuid(2));
    expect(reply?.status).toBe("error");
    expect(reply?.failure).toEqual({ code: "llm_timeout", retryable: true, requestId: "r9" });
    expect(api.streams.length).toBe(1);
    expect(api.listCalls.length).toBe(0);
  });

  test("a dropped stream keeps what arrived and re-READS the conversation, never re-sends", async () => {
    const { api, clock, store } = setup();
    const draft = store.freshDraftKey();
    const sending = store.send(draft, "Hello");
    api.streams[0]?.handlers.onMeta?.(meta(A, uuid(2), message(uuid(1), A, "user", "Hello")));
    api.streams[0]?.handlers.onToken?.({ text: "partial" });
    clock.tick();
    api.pages.set(`${A}:`, {
      items: [message(uuid(1), A, "user", "Hello"), message(uuid(2), A, "assistant", "partial answer", "cancelled")],
      nextCursor: null,
    });
    api.streams[0]?.resolve("interrupted");
    await sending;
    await settle();
    expect(api.streams.length).toBe(1);
    expect(api.listCalls).toEqual([{ id: A, before: undefined }]);
    expect(store.getMessage(A, uuid(2))?.content).toBe("partial answer");
  });

  test("a send refused before the turn existed gives the text back (409, 429, 413, offline)", async () => {
    const { api, store } = setup();
    const draft = store.freshDraftKey();
    const sending = store.send(draft, "Hello");
    api.streams[0]?.reject(new ApiError(409, "conflict"));
    await sending;
    const entry = store.getEntry(draft);
    expect(entry?.order).toEqual([]);
    expect(entry?.stream).toBeNull();
    expect(store.getDraft(draft)).toBe("Hello");
    expect(entry?.composerError?.code).toBe("conflict");
  });

  test("a message over the limit is refused locally, with no request", async () => {
    const { api, store } = setup();
    const draft = store.freshDraftKey();
    await store.send(draft, "x".repeat(16_001));
    expect(api.streams.length).toBe(0);
    expect(store.getEntry(draft)?.composerError?.code).toBe("payload_too_large");
  });
});

describe("stop, regenerate, delete", () => {
  test("a pre-meta Stop removes the optimistic turn, restores the draft, and refreshes committed state", async () => {
    const { api, store } = setup();
    api.pages.set(`${A}:`, {
      items: [message(uuid(90), A, "user", "q"), message(uuid(91), A, "assistant", "a")],
      nextCursor: null,
    });
    await store.ensureLoaded(A);
    const sending = store.send(A, "q2");
    const optimisticId = store.getEntry(A)?.order.at(-1);
    expect(store.getMessage(A, optimisticId ?? "")?.pending).toBe(true);

    api.pages.set(`${A}:`, {
      items: [
        message(uuid(90), A, "user", "q"),
        message(uuid(91), A, "assistant", "a"),
        message(uuid(92), A, "user", "q2"),
        message(uuid(93), A, "assistant", "", "cancelled"),
      ],
      nextCursor: null,
    });
    await store.stop(A);
    await sending;

    expect(api.cancelled).toEqual([]);
    expect(api.streams[0]?.signal?.aborted).toBe(true);
    expect(store.getDraft(A)).toBe("q2");
    expect(store.getEntry(A)?.stream).toBeNull();
    expect(store.getMessage(A, optimisticId ?? "")).toBeUndefined();
    expect(api.listCalls).toEqual([
      { id: A, before: undefined },
      { id: A, before: undefined },
    ]);
    expect(store.getEntry(A)?.order).toEqual([uuid(90), uuid(91), uuid(92), uuid(93)]);
  });

  test("stop cancels via the API and keeps reading until the persisted `done`", async () => {
    const { api, store } = setup();
    const draft = store.freshDraftKey();
    const sending = store.send(draft, "Hello");
    api.streams[0]?.handlers.onMeta?.(meta(A, uuid(2), message(uuid(1), A, "user", "Hello")));
    const stopping = store.stop(A);
    expect(store.getEntry(A)?.stream?.phase).toBe("stopping");
    await stopping;
    expect(api.cancelled).toEqual([uuid(2)]);
    expect(api.streams[0]?.signal?.aborted).toBe(false); // still listening for `done`
    api.streams[0]?.handlers.onDone?.({ message: message(uuid(2), A, "assistant", "Hel", "cancelled"), conversation: conversation(A) });
    api.streams[0]?.resolve("done");
    await sending;
    expect(store.getMessage(A, uuid(2))?.status).toBe("cancelled");
    expect(store.getMessage(A, uuid(2))?.content).toBe("Hel");
    expect(store.getEntry(A)?.stream).toBeNull();
  });

  test("if `done` never comes, stop cuts the connection after the grace period", async () => {
    const { api, store } = setup({ stopGraceMs: 5 });
    const draft = store.freshDraftKey();
    const sending = store.send(draft, "Hello");
    api.streams[0]?.handlers.onMeta?.(meta(A, uuid(2), message(uuid(1), A, "user", "Hello")));
    await store.stop(A);
    await sending;
    expect(api.streams[0]?.signal?.aborted).toBe(true);
    expect(store.getMessage(A, uuid(2))?.status).toBe("cancelled");
  });

  test("regenerate replaces the last reply", async () => {
    const { api, store } = setup();
    api.pages.set(`${A}:`, { items: [message(uuid(1), A, "user", "q"), message(uuid(2), A, "assistant", "old")], nextCursor: null });
    await store.ensureLoaded(A);
    expect(store.lastAssistantId(A)).toBe(uuid(2));
    const regenerating = store.regenerate(A);
    expect(api.streams[0]?.kind).toBe("regenerate");
    api.streams[0]?.handlers.onMeta?.(meta(A, uuid(3), null, uuid(2)));
    expect(store.getEntry(A)?.order).toEqual([uuid(1), uuid(3)]);
    api.streams[0]?.handlers.onDone?.({ message: message(uuid(3), A, "assistant", "new"), conversation: conversation(A) });
    api.streams[0]?.resolve("done");
    await regenerating;
    expect(store.getMessage(A, uuid(3))?.content).toBe("new");
  });

  test("a failed regeneration puts back the reply it was meant to replace (the server keeps it too)", async () => {
    const { api, store } = setup();
    api.pages.set(`${A}:`, { items: [message(uuid(1), A, "user", "q"), message(uuid(2), A, "assistant", "old")], nextCursor: null });
    await store.ensureLoaded(A);
    const regenerating = store.regenerate(A);
    api.streams[0]?.handlers.onMeta?.(meta(A, uuid(3), null, uuid(2)));
    expect(store.getEntry(A)?.order).toEqual([uuid(1), uuid(3)]);
    api.streams[0]?.handlers.onError?.({ code: "llm_unavailable", message: "x", retryable: true, messageId: uuid(3), requestId: "r1" });
    api.streams[0]?.resolve("error");
    await regenerating;
    expect(store.getEntry(A)?.order).toEqual([uuid(1), uuid(2), uuid(3)]);
    expect(store.getMessage(A, uuid(2))?.content).toBe("old");
  });

  test("deleting a streaming chat stops it first and nothing writes to it afterwards", async () => {
    const { api, store, updated } = setup();
    const draft = store.freshDraftKey();
    const sending = store.send(draft, "Hello");
    api.streams[0]?.handlers.onMeta?.(meta(A, uuid(2), message(uuid(1), A, "user", "Hello")));
    const updatesBefore = updated.length;
    await store.deleteConversation(A);
    await sending;
    expect(api.cancelled).toEqual([uuid(2)]);
    expect(api.streams[0]?.signal?.aborted).toBe(true);
    expect(api.deleted).toEqual([A]);
    expect(store.getEntry(A)?.status).toBe("not_found");
    expect(store.getEntry(A)?.order).toEqual([]);
    expect(updated.length).toBe(updatesBefore);
  });

  test("sign-out aborts every stream", async () => {
    const { api, store } = setup();
    void store.send(store.freshDraftKey(), "one");
    void store.send(store.freshDraftKey(), "two");
    store.reset();
    expect(api.streams.every((stream) => stream.signal?.aborted)).toBe(true);
  });
});

describe("feedback", () => {
  test("is optimistic and rolls back if the API refuses", async () => {
    const { api, store } = setup();
    api.pages.set(`${A}:`, { items: [message(uuid(1), A, "user", "q"), message(uuid(2), A, "assistant", "a")], nextCursor: null });
    await store.ensureLoaded(A);
    await store.setFeedback(A, uuid(2), "up");
    expect(store.getMessage(A, uuid(2))?.feedback).toBe("up");
    await store.setFeedback(A, uuid(2), null);
    expect(store.getMessage(A, uuid(2))?.feedback).toBeNull();
    api.failFeedback = true;
    await expect(store.setFeedback(A, uuid(2), "down")).rejects.toBeInstanceOf(ApiError);
    expect(store.getMessage(A, uuid(2))?.feedback).toBeNull();
    expect(api.feedbackCalls).toEqual([`${uuid(2)}:up`, `${uuid(2)}:clear`, `${uuid(2)}:down`]);
  });

  test("user messages cannot be rated", async () => {
    const { api, store } = setup();
    api.pages.set(`${A}:`, { items: [message(uuid(1), A, "user", "q")], nextCursor: null });
    await store.ensureLoaded(A);
    await store.setFeedback(A, uuid(1), "up");
    expect(api.feedbackCalls).toEqual([]);
  });
});

// ------------------------------------------------------------------ practice tests

const question = (kind: QuestionKind = "mcq"): AssessmentQuestion => ({
  kind,
  text: "Which one?",
  options: kind === "mcq" ? ["a", "b", "c"] : null,
  segments: null,
  blanks: null,
  response: null,
  correct: null,
  answers: null,
  explanation: null,
});

/** A schemaVersion 1 practice test waiting on question 0, unless overridden. */
const record = (overrides: Partial<Assessment> = {}): Assessment => ({
  id: "t1",
  position: 0,
  schemaVersion: 1,
  kind: "test",
  title: null,
  hash: "h1",
  locator: "l1",
  state: "active",
  answerable: true,
  total: 3,
  answered: 0,
  current: 0,
  earned: 0,
  possible: 3,
  questions: [question(), question(), question()],
  ...overrides,
});

const exam = (overrides: Partial<Assessment> = {}) =>
  record({ schemaVersion: 2, mode: "exam", submitted: false, revision: 3, current: null, ...overrides });

/** Conversation A: a question, then a reply holding `test`; returns what the store keeps of the test. */
function testReply(test: Assessment) {
  return {
    items: [
      message(uuid(1), A, "user", "Test me"),
      { ...message(uuid(2), A, "assistant", "```quiz\n{}\n```"), metadata: { assessments: [test] } },
    ],
    nextCursor: null,
  };
}

async function withTest(test: Assessment) {
  const context = setup();
  context.api.pages.set(`${A}:`, testReply(test));
  await context.store.ensureLoaded(A);
  const stored = () => context.store.getMessage(A, uuid(2))?.metadata?.assessments?.[0];
  const notice = () => context.store.getMessage(A, uuid(2))?.testNotice?.text;
  return { ...context, stored, notice };
}

const save = (q: number, value: number) => ({ hash: "h1", question: q, action: "save" as const, value });
const submit = { hash: "h1", question: 0, action: "submit" as const };

describe("practice tests", () => {
  test("an adaptive record: typed b answers h1/q0, the grown record is kept, typed c answers h2/q1; a late reply is ignored", async () => {
    const { api, store, stored, notice } = await withTest(record({ total: 1, questions: [question()] }));
    api.reply(record({ hash: "h2", total: 2, answered: 1, current: 1, questions: [question(), question()] }));
    await store.send(A, "b");
    expect(api.assessmentCalls[0]).toEqual({
      messageId: uuid(2),
      request: { position: 0, hash: "h1", question: 0, action: "answer", value: 1 },
    });
    expect(stored()?.hash).toBe("h2");
    expect(notice()).toBe("You answered B to question 1.");

    api.reply(record({ hash: "h3", total: 3, answered: 2, current: 2 }));
    await store.send(A, "option c");
    expect(api.assessmentCalls[1]?.request).toEqual({ position: 0, hash: "h2", question: 1, action: "answer", value: 2 });
    expect(stored()?.hash).toBe("h3");

    // An older state arriving late never puts the record back.
    api.reply(record({ hash: "h2", total: 2, answered: 1, current: 1 }));
    await store.respondToTest(A, uuid(2), 0, { hash: "h2", question: 1, action: "answer", value: 2 });
    expect(stored()?.hash).toBe("h3");
    expect(api.streams.length).toBe(0); // nothing went to the tutor
  });

  test("save then submit go one at a time; the submit carries the revision the save returned", async () => {
    const { api, store, stored } = await withTest(exam());
    const answered: number[] = [];
    store.onPracticeAnswered = (_learning, _id, count) => answered.push(count);
    const held = api.hold();
    api.reply(exam({ revision: 5, submitted: true, answered: 3 }));

    const saving = store.respondToTest(A, uuid(2), 0, save(0, 1));
    const submitting = store.respondToTest(A, uuid(2), 0, submit);
    let idle = false;
    const waiting = store.testIdle(uuid(2), 0).then(() => {
      idle = true;
    });
    await settle();
    expect(api.assessmentCalls.length).toBe(1); // the submit waits for the save
    expect(idle).toBe(false);

    held.resolve(exam({ revision: 4 }));
    await Promise.all([saving, submitting, waiting]);
    expect(idle).toBe(true);
    expect(api.assessmentCalls.map((call) => call.request)).toEqual([
      { position: 0, hash: "h1", question: 0, action: "save", value: 1 },
      { position: 0, hash: "h1", question: 0, action: "submit", revision: 4 },
    ]);
    expect(stored()?.submitted).toBe(true);
    expect(answered).toEqual([3]); // a submit is an answer for the streak; a save is not
  });

  test("a 409 drops what was queued, reads the test again, then says it changed elsewhere", async () => {
    const { api, store, stored, notice } = await withTest(exam());
    api.fail(new ApiError(409, "conflict"));
    api.pages.set(`${A}:`, testReply(exam({ revision: 9 })));

    const first = store.respondToTest(A, uuid(2), 0, save(0, 1));
    const second = store.respondToTest(A, uuid(2), 0, save(1, 2));
    const third = store.respondToTest(A, uuid(2), 0, submit);
    for (const request of [first, second, third]) await expect(request).rejects.toMatchObject({ status: 409 });
    expect(api.assessmentCalls.length).toBe(1);
    expect(api.listCalls.length).toBe(2); // opened, then read again
    expect(stored()?.revision).toBe(9);
    expect(notice()).toBe("Changed in another tab. The test now shows where it is.");
    expect(store.unsavedQuestions(uuid(2), 0)).toEqual([]);

    // What is asked after the conflict is sent, with the revision read again.
    api.reply(exam({ revision: 10, submitted: true }));
    await store.respondToTest(A, uuid(2), 0, submit);
    expect(api.assessmentCalls[1]?.request).toEqual({ position: 0, hash: "h1", question: 0, action: "submit", revision: 9 });
  });

  test("a 409 while a reply streams reads the test again only once the stream has ended", async () => {
    const { api, store, notice } = await withTest(exam());
    const sending = store.send(A, "Explain OSPF areas");
    api.fail(new ApiError(409, "conflict"));
    await expect(store.respondToTest(A, uuid(2), 0, save(0, 1))).rejects.toMatchObject({ status: 409 });
    expect(api.listCalls.length).toBe(1);

    api.streams[0]?.handlers.onMeta?.(meta(A, uuid(4), message(uuid(3), A, "user", "Explain OSPF areas")));
    api.pages.set(`${A}:`, {
      items: [...testReply(exam({ revision: 7 })).items, message(uuid(3), A, "user", "q"), message(uuid(4), A, "assistant", "a")],
      nextCursor: null,
    });
    api.streams[0]?.handlers.onDone?.({ message: message(uuid(4), A, "assistant", "a"), conversation: conversation(A) });
    api.streams[0]?.resolve("done");
    await sending;
    await settle();
    expect(api.listCalls.length).toBe(2);
    expect(notice()).toBe("Changed in another tab. The test now shows where it is.");
  });

  test("a 429 is retried twice, then the question is unsaved; a failed save never blocks the next", async () => {
    const { api, store } = await withTest(exam());
    for (let index = 0; index < 3; index += 1) api.fail(new ApiError(429, "rate_limited"));
    api.fail(new ApiError(503, "service_unavailable"));
    api.reply(exam({ revision: 4 }));

    const limited = store.respondToTest(A, uuid(2), 0, save(1, 0));
    const offline = store.respondToTest(A, uuid(2), 0, save(2, 1));
    const fine = store.respondToTest(A, uuid(2), 0, save(0, 2));
    await expect(limited).rejects.toMatchObject({ status: 429 });
    await expect(offline).rejects.toMatchObject({ status: 503 });
    await fine;
    expect(api.assessmentCalls.map((call) => call.request.question)).toEqual([1, 1, 1, 2, 0]);
    expect(store.unsavedQuestions(uuid(2), 0)).toEqual([1, 2]);

    // Saved again: no longer unsaved. One 429 then success needs no second retry.
    api.fail(new ApiError(429, "rate_limited"));
    api.reply(exam({ revision: 5 }));
    await store.respondToTest(A, uuid(2), 0, save(1, 0));
    expect(store.unsavedQuestions(uuid(2), 0)).toEqual([2]);
    expect(api.assessmentCalls.length).toBe(7);
  });

  test("a test's notice goes once a later response for it is stored; a typed command still says its own", async () => {
    const { api, store, notice } = await withTest(record());
    api.reply(record({ answered: 1, current: 1 }));
    await store.send(A, "b");
    expect(notice()).toBe("You answered B to question 1.");

    // The card's own answer: its result line shows again, not the old notice.
    api.reply(record({ answered: 2, current: 2 }));
    await store.respondToTest(A, uuid(2), 0, { hash: "h1", question: 1, action: "answer", value: 0 });
    expect(notice()).toBeUndefined();

    // A typed command's notice is posted after its response is stored, so it shows.
    api.reply(record({ answered: 3, current: null }));
    await store.send(A, "c");
    expect(notice()).toBe("You answered C to question 3.");
  });

  test("a schemaVersion 1 record never sends save, clear or submit", async () => {
    const { api, store } = await withTest(record());
    await store.respondToTest(A, uuid(2), 0, save(0, 1));
    await store.respondToTest(A, uuid(2), 0, { hash: "h1", question: 0, action: "clear" });
    await store.respondToTest(A, uuid(2), 0, submit);
    expect(api.assessmentCalls).toEqual([]);
  });

  test("typed commands on a mock exam are answered with a notice, and nothing is sent", async () => {
    const { api, store, notice } = await withTest(exam());
    for (const typed of ["b", "skip", "next"]) {
      await store.send(A, typed);
      expect(notice()).toBe("Answer in the mock exam card; nothing is graded until you submit.");
    }
    expect(api.assessmentCalls).toEqual([]);
    expect(api.streams.length).toBe(0);
  });

  test("typed commands on a newer practice question: a letter only for single choice, skip shows the answer", async () => {
    const practice = record({
      schemaVersion: 2,
      mode: "practice",
      revision: 1,
      answered: 1,
      current: 1,
      questions: [question(), question("numeric"), question()],
    });
    const { api, store, notice } = await withTest(practice);
    await store.send(A, "b");
    expect(notice()).toBe("Question 2 is answered in its card.");
    await store.send(A, "next");
    expect(notice()).toBe("Question 2 of 3 is waiting: answer it in its card, or type skip.");
    expect(api.assessmentCalls).toEqual([]);

    api.reply({ ...practice, revision: 2, answered: 2, current: 2 });
    await store.send(A, "skip");
    expect(api.assessmentCalls[0]?.request).toEqual({ position: 0, hash: "h1", question: 1, action: "skip" });
    expect(notice()).toBe("Question 2: answer shown.");
  });

  test("a submitted exam takes no typed commands: the message goes to the tutor", async () => {
    const { api, store } = await withTest(exam({ submitted: true, answered: 3, state: "completed" }));
    void store.send(A, "b");
    expect(api.streams[0]?.request?.message).toBe("b");
  });

  test("a card's send leaves the typed draft and the composer alone, even when refused, and is never a command", async () => {
    const { api, store } = await withTest(record());
    store.setDraft(A, "half-typed question");

    const refused = store.ask(A, "Review my answer to Q1");
    api.streams[0]?.reject(new ApiError(409, "conflict"));
    expect(await refused).toBe("refused");
    expect(store.getDraft(A)).toBe("half-typed question");
    expect(store.getEntry(A)?.composerError).toBeNull();
    expect(store.getEntry(A)?.order).toEqual([uuid(1), uuid(2)]);

    const asking = store.ask(A, "b"); // a letter, yet a message for the tutor: the card built it
    expect(api.streams[1]?.request?.message).toBe("b");
    expect(await store.ask(A, "again")).toBe("busy");
    api.streams[1]?.handlers.onMeta?.(meta(A, uuid(4), message(uuid(3), A, "user", "b")));
    expect(await asking).toBe("sent");
    expect(store.getDraft(A)).toBe("half-typed question");
    expect(api.assessmentCalls).toEqual([]);

    api.streams[1]?.handlers.onDone?.({ message: message(uuid(4), A, "assistant", "Here is a review."), conversation: conversation(A) });
    api.streams[1]?.resolve("done");
    await settle();
    expect(await store.ask(A, "x".repeat(16_001))).toBe("refused");
    expect(store.getEntry(A)?.composerError).toBeNull();
    expect(api.streams.length).toBe(2);
  });

  test("practice drafts are kept per question in memory and forgotten with their chat", async () => {
    const { store } = await withTest(record());
    store.setPracticeDraft(uuid(2), 0, 1, "12.5");
    store.setPracticeDraft(uuid(2), 0, 2, [1, 0]);
    expect(store.getPracticeDraft(uuid(2), 0, 1)).toBe("12.5");
    store.setPracticeDraft(uuid(2), 0, 1, undefined);
    expect(store.getPracticeDraft(uuid(2), 0, 1)).toBeUndefined();
    store.forget(A);
    expect(store.getPracticeDraft(uuid(2), 0, 2)).toBeUndefined();
  });
});
