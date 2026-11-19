/**
 * The route table must agree with the backend contract.
 *
 * It reads the repository's single contract, `docs/openapi.json`, which the backend
 * regenerates from its code (and CI checks for drift). A backend change to a route or
 * a request schema therefore fails this suite in the same commit, naming the
 * frontend call that no longer matches.
 */

import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { ROUTES, type RouteKey, type RouteSpec, buildPath, matchRoute, splitRouteKey } from "./routes";

type Operation = {
  requestBody?: { content?: Record<string, { schema?: { $ref?: string } }>; "x-max-bytes"?: number };
  responses?: Record<string, { content?: Record<string, unknown> }>;
};

type OpenApi = {
  paths: Record<string, Record<string, Operation>>;
  components: { schemas: Record<string, { properties?: Record<string, unknown>; required?: string[] }> };
};

const openapi = JSON.parse(
  readFileSync(join(dirname(fileURLToPath(import.meta.url)), "../../../../../../docs/openapi.json"), "utf8"),
) as OpenApi;

const keys = Object.keys(ROUTES) as RouteKey[];
const ID = "11111111-1111-4111-8111-111111111111";

describe("route table vs backend contract", () => {
  for (const key of keys) {
    const { method, pattern } = splitRouteKey(key);
    const spec = ROUTES[key] as { body?: string };

    test(`${key} exists in the contract`, () => {
      expect(openapi.paths[pattern]?.[method.toLowerCase()]).toBeDefined();
    });

    test(`${key} request body schema matches`, () => {
      const operation = openapi.paths[pattern]?.[method.toLowerCase()];
      const ref = operation?.requestBody?.content?.["application/json"]?.schema?.$ref;
      const schemaName = ref?.split("/").pop();
      expect(schemaName).toBe(spec.body as never);
      if (schemaName) expect(openapi.components.schemas[schemaName]).toBeDefined();
    });
  }
});

/**
 * The exact bodies `lib/api/*` sends, per schema. The API rejects unknown fields
 * (422), so every key here must be a declared property, and every required
 * property must be present.
 */
const SENT_BODIES: Record<string, Record<string, unknown>[]> = {
  UpdateMemoryPreferencesRequest: [{ expectedVersion: 0, automaticProposals: true }],
  ApproveProposalRequest: [{ expectedVersion: 1 }, { expectedVersion: 2, content: "Use concise examples." }],
  RejectProposalRequest: [{ expectedVersion: 1 }],
  CreateMemoryRequest: [{ kind: "learning_context", content: "Use train examples." }],
  CorrectMemoryRequest: [{ expectedVersion: 1, content: "Use bus examples." }],
  DeleteMemoryRequest: [{ expectedVersion: 1 }],
  UpdateProfileRequest: [
    { version: 0 },
    { version: 1, set: { displayName: "A", timezone: "Asia/Kolkata" } },
    { version: 2, reset: ["timezone"] },
  ],
  EmailChangeRequest: [{ newEmail: "a@b.c", password: "x" }],
  EmailConfirmRequest: [{ newEmail: "a@b.c", code: "123456" }],
  CreateImportBatch: [
    { sourceId: "11111111-1111-4111-8111-111111111111" },
    { sourceId: "11111111-1111-4111-8111-111111111111", expectedFileCount: 12, expectedTotalBytes: 1048576 },
  ],
  UpdateLearnerProfileRequest: [
    { version: 0 },
    { version: 1, set: { grade: "class_10", board: "state", boardState: "kerala", weakTopics: ["Optics"] } },
    { version: 2, reset: ["boardState", "weakTopics"] },
  ],
  UpdateEngineeringProfileRequest: [
    { version: 0, set: {}, reset: [] },
    {
      version: 1,
      set: {
        role: "network_engineer",
        experienceLevel: "mid",
        focusAreas: ["routing_switching", "firewalls"],
        platforms: ["cisco_ios", "palo_alto"],
        certificationGoal: "ccnp_enterprise",
      },
      reset: [],
    },
    { version: 2, set: {}, reset: ["role", "experienceLevel", "focusAreas", "platforms", "certificationGoal"] },
  ],
  ReplaceSubjectsRequest: [{ version: 0, items: [{ code: "physics", isPriority: true, needsImprovement: false }] }],
  UpdateLearningPreferencesRequest: [{ version: 0, set: { pace: "slow" } }, { version: 1, reset: ["pace"] }],
  UpdateNotificationPreferencesRequest: [
    { version: 0, set: { dailyReminder: true, securityAlerts: false } },
    { version: 1, reset: ["securityAlerts"] },
    { version: 2, set: { inAppReminders: false } },
  ],
  UnsubscribeRequest: [{ token: "abc.def0123456789" }],
  MarkReadRequest: [{}, { ids: ["7d9f5f2c-3f7e-4a57-9d0c-3b1f0b6f2a11"] }],
  UpdateAppearanceRequest: [
    { version: 0, set: { theme: "dark", fontSize: "large", highContrast: true, reduceMotion: false } },
    { version: 1, reset: ["fontSize"], set: { readingComfort: true, showStreak: false } },
  ],
  SupportRequestIn: [
    { requestId: "11111111-1111-4111-8111-111111111111", kind: "bug", subject: "S", message: "Long enough." },
    {
      requestId: "11111111-1111-4111-8111-111111111111",
      kind: "question",
      subject: "S",
      message: "Long enough.",
      page: "/settings/help",
    },
  ],
  TutorPreviewRequest: [
    {},
    { profile: { grade: "class_9" }, subjects: [{ code: "physics" }], preferences: { pace: "fast" } },
    {
      engineering: {
        role: "security_engineer",
        experienceLevel: null,
        focusAreas: ["vpn"],
        platforms: [],
        certificationGoal: null,
      },
    },
  ],
  DeleteMeRequest: [{ password: "x" }, {}],
  RenameConversationRequest: [{ title: "T" }],
  ChatRequest: [
    { message: "hi" },
    { conversationId: ID, message: "hi" },
    { message: "hi", attachmentIds: [ID] },
    { message: "hi", mode: "voice" },
    { message: "remember that I like examples", clientRequestId: ID },
  ],
  SpeechRequest: [{ messageId: ID, text: "Osmosis is water moving." }],
  FeedbackRequest: [{ rating: "up" }],
  AssessmentResponseRequest: [
    { position: 0, hash: "a".repeat(64), question: 0, action: "answer", value: 1 },
    { position: 0, hash: "a".repeat(64), question: 1, action: "skip" },
    { position: 0, hash: "a".repeat(64), question: 0, action: "answer", value: ["100", "zero"] },
    // A mock exam: a draft saved (a match's choices, one still open), cleared, then the submit with its revision.
    { position: 0, hash: "a".repeat(64), question: 2, action: "save", value: [1, null, 0] },
    { position: 0, hash: "a".repeat(64), question: 2, action: "clear" },
    { position: 0, hash: "a".repeat(64), question: 0, action: "submit", revision: 3 },
  ],
  StudyStartIn: [
    { clientId: ID, activity: "reading" },
    { clientId: ID, subject: "science", chapterId: "sci-8-en-ch02", activity: "practice" },
  ],
  StudyContextChangeIn: [{ clientId: ID, expectedSegmentId: ID, subject: null, chapterId: null, activity: "test" }],
  StudyTransitionIn: [{ clientId: ID, expectedSegmentId: ID }],
  StudyBeatIn: [{ beatId: ID, clientId: ID, wasActive: true, nowActive: false, studyView: true, visible: true }],
};

describe("request bodies the client sends", () => {
  for (const [schemaName, bodies] of Object.entries(SENT_BODIES)) {
    test(`${schemaName} bodies use only declared fields`, () => {
      const schema = openapi.components.schemas[schemaName];
      expect(schema).toBeDefined();
      const declared = Object.keys(schema?.properties ?? {});
      for (const body of bodies) {
        for (const field of Object.keys(body)) expect(declared).toContain(field);
        for (const field of schema?.required ?? []) expect(Object.keys(body)).toContain(field);
      }
    });
  }

  test("Engineering Profile edits and previews use only the profile's declared fields", () => {
    const declared = Object.keys(openapi.components.schemas.EngineeringProfileSet?.properties ?? {});
    expect(declared.sort()).toEqual(["certificationGoal", "experienceLevel", "focusAreas", "platforms", "role"]);
    const nested = [
      ...(SENT_BODIES.UpdateEngineeringProfileRequest ?? []).map((body) => body.set),
      ...(SENT_BODIES.TutorPreviewRequest ?? []).map((body) => body.engineering),
    ].filter((values): values is Record<string, unknown> => typeof values === "object" && values !== null);
    expect(nested.length).toBeGreaterThan(2);
    for (const values of nested) for (const field of Object.keys(values)) expect(declared).toContain(field);
  });

  test("every body schema in the route table is covered above", () => {
    for (const key of keys) {
      const body = (ROUTES[key] as { body?: string }).body;
      if (body) expect(Object.keys(SENT_BODIES)).toContain(body);
    }
  });
});

describe("file routes", () => {
  for (const key of keys) {
    const { method, pattern } = splitRouteKey(key);
    const spec = ROUTES[key] as RouteSpec;
    const operation = openapi.paths[pattern]?.[method.toLowerCase()];

    if (spec.upload) {
      test(`${key} accepts exactly the upload types and size the API declares`, () => {
        expect([...(spec.upload?.types ?? [])].sort()).toEqual(Object.keys(operation?.requestBody?.content ?? {}).sort());
        expect(spec.upload?.maxBytes).toBe(operation?.requestBody?.["x-max-bytes"] as never);
      });
    }
    if (spec.file) {
      test(`${key} passes exactly the file types the API answers with`, () => {
        expect([...(spec.file?.types ?? [])].sort()).toEqual(Object.keys(operation?.responses?.["200"]?.content ?? {}).sort());
      });
    }
  }
});

describe("buildPath / matchRoute", () => {
  test("fills UUID params and round-trips through matchRoute", () => {
    const path = buildPath("GET /v1/conversations/{conversation_id}/messages", { conversation_id: ID });
    expect(path).toBe(`/v1/conversations/${ID}/messages`);
    expect(matchRoute("GET", path)?.key).toBe("GET /v1/conversations/{conversation_id}/messages");
  });

  test("rejects anything that is not a listed route", () => {
    expect(matchRoute("GET", "/v1/me/settings")).toBeNull();
    expect(matchRoute("POST", "/v1/chat")).toBeNull();
    expect(matchRoute("DELETE", "/v1/conversations")).toBeNull();
    expect(matchRoute("GET", "/health")).toBeNull();
    expect(matchRoute("GET", "/v1/conversations/not-a-uuid")).toBeNull();
    expect(matchRoute("GET", `/v1/conversations/${ID}/../../me`)).toBeNull();
  });

  test("buildPath refuses a non-UUID id", () => {
    expect(() => buildPath("DELETE /v1/conversations/{conversation_id}", { conversation_id: "../me" })).toThrow();
  });
});
