import { callRoute } from "./http";

export type KnowledgeAdminStatus = { enabled: boolean };

export function getKnowledgeAdminStatus(): Promise<KnowledgeAdminStatus> {
  return callRoute("GET /v1/knowledge/admin");
}
