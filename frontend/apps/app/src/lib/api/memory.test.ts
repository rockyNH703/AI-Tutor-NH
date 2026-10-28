import { afterEach, describe, expect, test } from "bun:test";

import {
  approveMemoryProposal,
  clearMemories,
  correctMemory,
  createMemory,
  deleteMemory,
  exportMemories,
  getMemoryPreferences,
  listMemories,
  listMemoryProposals,
  rejectMemoryProposal,
  updateMemoryPreferences,
} from "./memory";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

function mockJson(body: unknown = {}) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init });
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
  return calls;
}

function requestBody(call: { init?: RequestInit }): unknown {
  return call.init?.body ? JSON.parse(String(call.init.body)) : undefined;
}

describe("memory API", () => {
  test("builds preference and paginated list requests", async () => {
    const calls = mockJson({ items: [], nextCursor: null });
    await getMemoryPreferences();
    await updateMemoryPreferences({ automaticProposals: true, expectedVersion: 2 });
    await listMemoryProposals({ limit: 25, cursor: "next" });
    await listMemories({ limit: 10, kind: "study_context" });

    expect(calls.map((call) => [call.init?.method, call.url])).toEqual([
      ["GET", "/api/v1/memories/preferences"],
      ["PUT", "/api/v1/memories/preferences"],
      ["GET", "/api/v1/memories/proposals?limit=25&cursor=next"],
      ["GET", "/api/v1/memories?limit=10&kind=study_context"],
    ]);
    expect(requestBody(calls[1] ?? {})).toEqual({ automaticProposals: true, expectedVersion: 2 });
  });

  test("sends proposal review bodies and path parameters", async () => {
    const calls = mockJson();
    await approveMemoryProposal("11111111-1111-4111-8111-111111111111", {
      expectedVersion: 3,
      kind: "learning_context",
      content: "Uses diagrams to understand networks.",
      expiresAt: null,
    });
    await rejectMemoryProposal("22222222-2222-4222-8222-222222222222", 4);

    expect(calls[0]?.url).toBe("/api/v1/memories/proposals/11111111-1111-4111-8111-111111111111/approve");
    expect(requestBody(calls[0] ?? {})).toEqual({
      expectedVersion: 3,
      kind: "learning_context",
      content: "Uses diagrams to understand networks.",
      expiresAt: null,
    });
    expect(calls[1]?.url).toBe("/api/v1/memories/proposals/22222222-2222-4222-8222-222222222222/reject");
    expect(requestBody(calls[1] ?? {})).toEqual({ expectedVersion: 4 });
  });

  test("supports create, correct, delete, clear, and export", async () => {
    const calls = mockJson();
    await createMemory({ kind: "study_context", content: "Revising subnetting this week." });
    await correctMemory("33333333-3333-4333-8333-333333333333", { expectedVersion: 1, content: "Revising routing this week." });
    await deleteMemory("44444444-4444-4444-8444-444444444444", 7);
    await clearMemories();
    await exportMemories();

    expect(calls.map((call) => [call.init?.method, call.url])).toEqual([
      ["POST", "/api/v1/memories"],
      ["PUT", "/api/v1/memories/33333333-3333-4333-8333-333333333333"],
      ["DELETE", "/api/v1/memories/44444444-4444-4444-8444-444444444444"],
      ["DELETE", "/api/v1/memories"],
      ["GET", "/api/v1/memories/export"],
    ]);
    expect(requestBody(calls[2] ?? {})).toEqual({ expectedVersion: 7 });
    expect(calls[3]?.init?.body).toBeUndefined();
  });
});
