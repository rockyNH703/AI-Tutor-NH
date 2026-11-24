/**
 * NH programs (backend app/programs): every answer is decided on the server from the account's NH invoices; the web
 * app only names what it asks for (a program slug, a month) and shows what comes back. A 404 means "not yours".
 */

import { callRoute } from "./http";
import type { ConversationPage, Uuid } from "./types";

export type ProgramMaterial = { documents: number; lessons: number; questions: number };
export type ProgramMonth = { month: number; title: string; material: ProgramMaterial };
export type Program = { slug: string; title: string; months: ProgramMonth[] };
export type ProgramList = { enabled: boolean; programs: Program[]; invoicePrompt: boolean };
export type ProgramRef = { slug: string; title: string };
export type ClaimResult = { status: "claimed" | "already"; program: ProgramRef };
export type BankTopic = { topicId: string; title: string; chapter: string; book: string };
export type ProgramMonthDetail = {
  program: ProgramRef;
  month: number;
  title: string;
  material: ProgramMaterial;
  lessonIds: Uuid[];
  topics: BankTopic[];
  quizAvailable: boolean;
};

export function listPrograms(): Promise<ProgramList> {
  return callRoute<ProgramList>("GET /v1/programs");
}

export function claimInvoice(invoiceNumber: string): Promise<ClaimResult> {
  return callRoute<ClaimResult>("POST /v1/programs/claims", { body: { invoiceNumber } });
}

export function dismissInvoicePrompt(): Promise<void> {
  return callRoute<void>("POST /v1/programs/invoice-prompt/dismiss");
}

export function getProgramMonth(slug: string, month: number): Promise<ProgramMonthDetail> {
  return callRoute<ProgramMonthDetail>("GET /v1/programs/{slug}/months/{month}", {
    params: { slug, month: String(month) },
  });
}

/** One month's chats (month null: the program's QnA), newest first. */
export function listProgramChats(slug: string, month: number | null): Promise<ConversationPage> {
  return callRoute<ConversationPage>("GET /v1/conversations", {
    query: { program: slug, month: month ?? undefined, limit: 50 },
  });
}
