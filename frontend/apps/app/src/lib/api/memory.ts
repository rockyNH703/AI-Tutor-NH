import { callRoute } from "./http";
import type {
  ApproveMemoryProposalRequest,
  CorrectMemoryRequest,
  CreateMemoryRequest,
  MemoryExport,
  MemoryItem,
  MemoryKind,
  MemoryPage,
  MemoryPreferences,
  MemoryProposalPage,
  UpdateMemoryPreferencesRequest,
} from "./types";

export const MEMORY_KINDS = ["learning_context", "study_context"] as const satisfies readonly MemoryKind[];

export function getMemoryPreferences(): Promise<MemoryPreferences> {
  return callRoute("GET /v1/memories/preferences");
}

export function updateMemoryPreferences(body: UpdateMemoryPreferencesRequest): Promise<MemoryPreferences> {
  return callRoute("PUT /v1/memories/preferences", { body });
}

export function listMemoryProposals(options: { limit?: number; cursor?: string } = {}): Promise<MemoryProposalPage> {
  return callRoute("GET /v1/memories/proposals", { query: options });
}

export function approveMemoryProposal(proposalId: string, body: ApproveMemoryProposalRequest): Promise<MemoryItem> {
  return callRoute("POST /v1/memories/proposals/{proposal_id}/approve", {
    params: { proposal_id: proposalId },
    body,
  });
}

export function rejectMemoryProposal(proposalId: string, expectedVersion: number): Promise<void> {
  return callRoute("POST /v1/memories/proposals/{proposal_id}/reject", {
    params: { proposal_id: proposalId },
    body: { expectedVersion },
  });
}

export function listMemories(options: { limit?: number; cursor?: string; kind?: MemoryKind } = {}): Promise<MemoryPage> {
  return callRoute("GET /v1/memories", { query: options });
}

export function createMemory(body: CreateMemoryRequest): Promise<MemoryItem> {
  return callRoute("POST /v1/memories", { body });
}

export function correctMemory(memoryId: string, body: CorrectMemoryRequest): Promise<MemoryItem> {
  return callRoute("PUT /v1/memories/{memory_id}", { params: { memory_id: memoryId }, body });
}

export function deleteMemory(memoryId: string, expectedVersion: number): Promise<void> {
  return callRoute("DELETE /v1/memories/{memory_id}", {
    params: { memory_id: memoryId },
    body: { expectedVersion },
  });
}

export function clearMemories(): Promise<void> {
  return callRoute("DELETE /v1/memories");
}

export function exportMemories(): Promise<MemoryExport> {
  return callRoute("GET /v1/memories/export");
}
