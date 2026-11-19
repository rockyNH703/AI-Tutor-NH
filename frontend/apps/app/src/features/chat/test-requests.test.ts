import { describe, expect, test } from "bun:test";

import { ApiError } from "@/lib/api/errors";
import type { Assessment, AssessmentResponse, AssessmentResponseRequest } from "@/lib/api/types";
import { CHANGED_ELSEWHERE, type TestHost, TestRequests } from "./test-requests";

const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

/** A store stand-in: what was sent, stored and said, and the replies scripted for the next requests. */
function host(overrides: Partial<TestHost> = {}) {
  const sent: AssessmentResponseRequest[] = [];
  const applied: boolean[] = [];
  const notices: string[] = [];
  const replies: (() => Promise<AssessmentResponse>)[] = [];
  const fake: TestHost = {
    respond: (_messageId, body) => {
      sent.push(body);
      return replies.shift()?.() ?? Promise.reject(new ApiError(404, "not_found"));
    },
    findTest: () => ({ schemaVersion: 2, revision: 4 }) as Assessment,
    applyRecord: (_canonical, _messageId, _position, _response, answered) => {
      applied.push(answered);
    },
    conflict: async () => true,
    notice: (_canonical, _messageId, _position, text) => {
      notices.push(text);
    },
    ...overrides,
  };
  return { fake, sent, applied, notices, replies };
}

describe("TestRequests", () => {
  test("a 409 drops what was queued; the card is told once its conversation can be read again", async () => {
    let readable = false; // a reply is streaming: the store can't read the conversation yet
    const { fake, sent, notices, replies } = host({ conflict: async () => readable });
    replies.push(() => Promise.reject(new ApiError(409, "conflict")));
    const requests = new TestRequests(fake, 0);

    const first = requests.respond("A", "m", 0, { hash: "h", question: 0, action: "save", value: 1 });
    const second = requests.respond("A", "m", 0, { hash: "h", question: 1, action: "save", value: 2 });
    await expect(first).rejects.toMatchObject({ status: 409 });
    await expect(second).rejects.toMatchObject({ status: 409 });
    expect(sent.length).toBe(1);
    expect(notices).toEqual([]);
    expect(requests.unsaved("m", 0)).toEqual([]);

    readable = true;
    requests.streamEnded("A");
    await settle();
    expect(notices).toEqual([CHANGED_ELSEWHERE]);
    requests.streamEnded("A"); // said once
    await settle();
    expect(notices).toEqual([CHANGED_ELSEWHERE]);
  });

  test("forget closes the reply's queues (nothing waiting is sent or stored) and drops only its drafts", async () => {
    const { fake, sent, applied, replies } = host();
    let release = () => {};
    replies.push(
      () =>
        new Promise((resolve) => {
          release = () => resolve({} as AssessmentResponse);
        }),
    );
    const requests = new TestRequests(fake, 0);
    requests.setDraft("m", 0, 1, "12.5");
    requests.setDraft("n", 0, 1, "7");

    const sending = requests.respond("A", "m", 0, { hash: "h", question: 0, action: "answer", value: 1 });
    const waiting = requests.respond("A", "m", 0, { hash: "h", question: 1, action: "answer", value: 0 });
    await settle();
    requests.forget("A", new Set(["m"]));
    release();
    await Promise.all([sending, waiting]);

    expect(sent.length).toBe(1);
    expect(applied).toEqual([]);
    expect(requests.getDraft("m", 0, 1)).toBeUndefined();
    expect(requests.getDraft("n", 0, 1)).toBe("7");
  });

  test("a submit carries the revision the store holds when it is sent; only answers and submits count as answered", async () => {
    const { fake, sent, applied, replies } = host();
    for (let index = 0; index < 2; index += 1) replies.push(() => Promise.resolve({} as AssessmentResponse));
    const requests = new TestRequests(fake, 0);
    await requests.respond("A", "m", 0, { hash: "h", question: 2, action: "save", value: [1, null, 0] });
    await requests.respond("A", "m", 0, { hash: "h", question: 0, action: "submit" });
    expect(sent).toEqual([
      { position: 0, hash: "h", question: 2, action: "save", value: [1, null, 0] },
      { position: 0, hash: "h", question: 0, action: "submit", revision: 4 },
    ]);
    expect(applied).toEqual([false, true]);
  });
});
