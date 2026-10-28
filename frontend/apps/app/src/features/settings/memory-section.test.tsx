import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import type { MemoryItem, MemoryProposal } from "@/lib/api/types";
import { shortDate } from "@/lib/time";
import { MemoryItemDetails, MemoryProposalDetails } from "./memory-section";

const proposal: MemoryProposal = {
  id: "proposal-1",
  operation: "add",
  targetMemoryId: null,
  kind: "learning_context",
  content: "Prefers visual explanations for networking concepts.",
  status: "pending",
  version: 1,
  confidence: 0.87,
  evidenceReason: "The learner explicitly described a durable study preference.",
  sourceMessageId: "message-1",
  assistantMessageId: "message-2",
  observedAt: "2026-10-25T10:00:00Z",
  expiresAt: "2026-12-31T23:59:59Z",
  validFrom: null,
  validTo: null,
  appliedMemoryId: null,
  reviewedAt: null,
  createdAt: "2026-10-25T10:00:00Z",
  updatedAt: "2026-10-25T10:00:00Z",
};

const memory: MemoryItem = {
  id: "memory-1",
  kind: "study_context",
  content: "Revising subnetting before the lab exam.",
  status: "active",
  version: 2,
  supersedesId: null,
  sourceMessageId: "message-1",
  origin: "approved_extraction",
  proposalId: "proposal-1",
  lastConfirmedAt: "2026-10-26T10:00:00Z",
  validFrom: "2026-10-20T00:00:00Z",
  validTo: "2026-11-20T00:00:00Z",
  processorVersion: "memory-worker-v1",
  expiresAt: null,
  createdAt: "2026-10-26T10:00:00Z",
  updatedAt: "2026-10-26T10:00:00Z",
};

describe("memory details", () => {
  test("shows proposal provenance, confidence, expiry, and review actions", () => {
    const html = renderToStaticMarkup(
      <MemoryProposalDetails proposal={proposal} busy={false} onApprove={() => {}} onEdit={() => {}} onReject={() => {}} />,
    );
    expect(html).toContain("87% confidence");
    expect(html).toContain("Why proposed:");
    expect(html).toContain(shortDate(proposal.expiresAt ?? ""));
    expect(html).toContain("Edit &amp; approve");
  });

  test("shows active-memory provenance and validity", () => {
    const html = renderToStaticMarkup(
      <MemoryItemDetails memory={memory} busy={false} onEdit={() => {}} onDelete={() => {}} />,
    );
    expect(html).toContain("Approved suggestion");
    expect(html).toContain("Valid from:");
    expect(html).toContain("Valid until:");
    expect(html).toContain("Correct");
  });
});
