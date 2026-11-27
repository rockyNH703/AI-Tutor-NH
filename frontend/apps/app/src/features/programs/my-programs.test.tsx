import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import { ApiError } from "@/lib/api/errors";
import type { Program, ProgramClaim, ProgramList } from "@/lib/api/programs";
import { NOT_VERIFIED, TOO_MANY, claimFeedback } from "./invoice-flow";
import { ProgramNavView } from "./program-nav-bar";
import { MyProgramsView } from "./program-pages";

const program = (slug: string, title: string, months = 8): Program => ({
  slug,
  title,
  months: Array.from({ length: months }, (_, index) => ({
    month: index + 1,
    title: `Month ${index + 1}`,
    material: { documents: 0, lessons: 0, questions: 0 },
  })),
});
const NETWORKING = program("networking-ai", "Full Stack Networking with AI");
const SECURITY = program("network-security-ai", "Network Security with AI", 5);

function list(overrides: Partial<ProgramList> = {}): ProgramList {
  return { enabled: true, programs: [], invoicePrompt: false, claims: [], ...overrides };
}

const page = (data: ProgramList | null, status: "loading" | "ready" | "error" = data ? "ready" : "loading") =>
  renderToStaticMarkup(<MyProgramsView state={{ status, data }} />);

describe("My Programs", () => {
  test("a card per open program: its title, its months and a link to open it, then Add another invoice", () => {
    const html = page(list({ programs: [NETWORKING, SECURITY] }));
    expect(html).toContain('href="/programs/networking-ai"');
    expect(html).toContain('href="/programs/network-security-ai"');
    expect(html).toContain("Full Stack Networking with AI");
    expect(html).toContain("8 months");
    expect(html).toContain("5 months");
    expect(html).toContain("Open program");
    expect(html).toContain("Add another invoice");
    expect(html).toContain("NH invoice number");
    expect(html).not.toContain("Add an invoice to open your program");
  });

  test("no program: the empty state is the invoice form", () => {
    const html = page(list());
    expect(html).toContain("Add an invoice to open your program");
    expect(html).toContain("Verify invoice");
    expect(html).not.toContain("Open program");
  });

  test("an ended claim is a card without a link, with its reason; a program still open is never shown as ended", () => {
    const ended: ProgramClaim[] = [
      { program: { slug: "cyber-cloud-security-ai", title: "Cyber Cloud Security" }, state: "revoked", claimedAt: "2026-11-02T00:00:00Z" },
      { program: { slug: "networking-ai", title: NETWORKING.title }, state: "expired", claimedAt: "2026-11-01T00:00:00Z" },
    ];
    const html = page(list({ programs: [NETWORKING], claims: ended }));
    expect(html).toContain("Cyber Cloud Security");
    expect(html).toContain("Access ended");
    expect(html).toContain("Its invoice is no longer active");
    expect(html).not.toContain('href="/programs/cyber-cloud-security-ai"');
    expect(html.match(/Access ended/g)?.length).toBe(1); // networking-ai is open through another invoice
    expect(page(list({ claims: [{ ...ended[1]!, state: "expired" }] }))).toContain("Its invoice has expired");
  });

  test("programs switched off: a calm line and no form", () => {
    const html = page(list({ enabled: false }));
    expect(html).toContain("Programs aren&#x27;t available right now");
    expect(html).not.toContain("NH invoice number");
  });

  test("loading shows placeholders; a failed read says so and offers Try again", () => {
    expect(page(null)).not.toContain("NH invoice number");
    const failed = page(null, "error");
    expect(failed).toContain("Your programs couldn&#x27;t be loaded");
    expect(failed).toContain("Try again");
  });
});

describe("claimFeedback: what the form says after an attempt", () => {
  const ref = { slug: "networking-ai", title: NETWORKING.title };
  test("approved, now or already this account's", () => {
    expect(claimFeedback({ result: { status: "claimed", program: ref } })).toEqual({
      tone: "success",
      lines: [`Approved: ${NETWORKING.title} is open.`],
    });
    expect(claimFeedback({ result: { status: "already", program: ref } }).lines[0]).toContain("already on your account");
  });

  test("every refusal is one generic Not verified, with whom to contact; too many attempts says when to retry", () => {
    expect(claimFeedback({ error: new ApiError(404, "invoice_not_verified") })).toEqual({ tone: "error", lines: NOT_VERIFIED });
    expect(NOT_VERIFIED.join(" ")).toContain("If this is your invoice, contact NetworkersHome");
    expect(claimFeedback({ error: new ApiError(429, "rate_limited") })).toEqual({ tone: "error", lines: TOO_MANY });
    expect(TOO_MANY[0]).toContain("Try again in an hour");
  });
});

describe("ProgramNavView: the program bar", () => {
  const programs = [
    { slug: NETWORKING.slug, title: NETWORKING.title },
    { slug: SECURITY.slug, title: SECURITY.title },
  ];

  test("back to My Programs, Overview and the five areas, only the current one marked", () => {
    const html = renderToStaticMarkup(<ProgramNavView slug="networking-ai" area="quizzes" programs={programs.slice(0, 1)} />);
    expect(html).toContain('aria-label="Program"');
    expect(html).toContain('href="/programs"');
    for (const label of ["Overview", "Course Module", "QnA", "Quizzes &amp; Games", "Mock Interviews", "Videos &amp; Links"]) {
      expect(html).toContain(`>${label}</a>`);
    }
    expect(html.match(/aria-current="page"/g)?.length).toBe(1);
    expect(html).toMatch(/aria-current="page"[^>]*href="\/programs\/networking-ai\/quizzes"/);
    expect(html).not.toContain("switch program"); // one program: its title, no switcher
    expect(html).toContain(NETWORKING.title);
  });

  test("the program's own page is Overview; more than one program gets a switcher", () => {
    const html = renderToStaticMarkup(<ProgramNavView slug="networking-ai" area={null} programs={programs} />);
    expect(html).toMatch(/aria-current="page"[^>]*href="\/programs\/networking-ai"/);
    expect(html).toContain(`aria-label="${NETWORKING.title}: switch program"`);
  });
});
