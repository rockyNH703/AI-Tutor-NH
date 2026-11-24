import { describe, expect, test } from "bun:test";
import { Dialog } from "@v1/ui/dialog";
import { renderToStaticMarkup } from "react-dom/server";

import type { FriendZonePlayer, FriendZoneRequests } from "./api";
import { avatarSrc } from "./api";
import { REPORT_REASONS } from "./model";
import { ReportForm } from "./report-dialog";
import { AddFriend, BlockedSection, FriendsSection, RequestsSection } from "./social";

const player = (n: number, patch: Partial<FriendZonePlayer> = {}): FriendZonePlayer => ({
  playerId: `11111111-1111-4111-8111-11111111111${n}`,
  displayName: n === 1 ? "ریحان Rehan" : `Member ${n}`,
  initials: n === 1 ? "رR" : `M${n}`,
  relation: "friend",
  avatarUrl: null,
  programs: null,
  ...patch,
});
const request = (n: number, relation: "incoming" | "outgoing") => ({
  requestId: `22222222-2222-4222-8222-22222222222${n}`,
  player: player(n, { relation }),
  createdAt: "2026-11-24T10:00:00Z",
  expiresAt: "2026-12-24T10:00:00Z",
});
const noop = () => {};
const PROGRAM = { slug: "ccna", title: "CCNA", months: [{ month: 1, title: "Basics", material: { documents: 1, lessons: 1, questions: 1 } }] };
const FRIENDS = { reload: noop, program: PROGRAM, month: 1, onBattle: noop };

function text(html: string): string {
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/&#x27;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ")
    .trim();
}
const buttons = (html: string) => [...html.matchAll(/<button[^>]*>([\s\S]*?)<\/button>/g)].map((match) => text(match[1] ?? ""));
const SCHOOL = /\b(students?|homework|syllabus|ncert|cbse|textbooks?|teachers?|chapters?|doubts?|class \d{1,2}|exam boards?|schools?)\b/i;

describe("requests", () => {
  test("none: a short empty line", () => {
    const html = renderToStaticMarkup(<RequestsSection requests={{ incoming: [], outgoing: [] }} reload={noop} />);
    expect(text(html)).toContain("No requests right now.");
    expect(buttons(html)).toEqual([]);
  });

  test("received ones offer Accept and Decline, sent ones Cancel request, names in their own direction", () => {
    const requests: FriendZoneRequests = { incoming: [request(1, "incoming")], outgoing: [request(2, "outgoing")] };
    const html = renderToStaticMarkup(<RequestsSection requests={requests} reload={noop} />);
    expect(text(html)).toContain("Received (1)");
    expect(text(html)).toContain("Sent (1)");
    // Block and Report on a received request too: self-protection without having to accept first.
    expect(buttons(html)).toEqual(["Accept", "Decline", "Block", "Report", "Cancel request"]);
    expect(html).toContain('<bdi dir="auto"');
    expect(text(html)).toContain("رR"); // initials stand in for a photo nobody may see yet
    expect(html).not.toContain("<img");
    expect(html).toContain('role="status"'); // where the outcome is announced
  });
});

describe("friends", () => {
  test("none: says how to add one", () => {
    const html = renderToStaticMarkup(<FriendsSection friends={[]} {...FRIENDS} />);
    expect(text(html)).toContain("No friends yet. Add a classmate with their invoice number below.");
  });

  test("each friend is a closed disclosure; nothing destructive is one click away", () => {
    const html = renderToStaticMarkup(<FriendsSection friends={[player(1), player(2)]} {...FRIENDS} />);
    expect(text(html)).toContain("Friends (2)");
    expect(html.match(/aria-expanded="false"/g)?.length).toBe(2);
    expect(buttons(html).join(" ")).not.toMatch(/Remove|Block|Report|Challenge/);
  });
});

describe("add a friend", () => {
  test("asks for the invoice number, says it is never shown, and finds", () => {
    const html = renderToStaticMarkup(<AddFriend reload={noop} />);
    const shown = text(html);
    expect(shown).toContain("Classmate's invoice number");
    expect(shown).toContain("It's never shown to anyone.");
    expect(buttons(html)).toEqual(["Find"]);
    expect(html).toContain('autoComplete="off"');
    expect(html).toContain('maxLength="60"');
  });
});

describe("blocked", () => {
  test("lists blocked members with Unblock, or says there are none", () => {
    expect(text(renderToStaticMarkup(<BlockedSection blocked={[]} reload={noop} />))).toContain("You haven't blocked anyone.");
    const html = renderToStaticMarkup(<BlockedSection blocked={[player(2, { relation: "blocked" })]} reload={noop} />);
    expect(buttons(html)).toEqual(["Unblock"]);
  });
});

describe("the report dialog", () => {
  test("names every reason, says blocking protects at once and who reviews, and caps the details", () => {
    const html = renderToStaticMarkup(
      <Dialog open>
        <ReportForm player={player(1)} onClose={noop} onSent={noop} onStale={noop} />
      </Dialog>,
    );
    const shown = text(html);
    for (const label of Object.values(REPORT_REASONS)) expect(shown).toContain(label);
    expect(html.match(/type="radio"/g)?.length).toBe(5);
    expect(html).not.toContain("checked");
    expect(shown).toContain("Blocking protects you straight away");
    expect(shown).toContain("reviewed privately by the NetSec AI owner");
    expect(html).toContain('maxLength="1000"');
    expect(buttons(html)).toEqual(["Cancel", "Send report"]);
  });
});

describe("photos", () => {
  test("load only from the API's own avatar route, through the BFF", () => {
    const id = "11111111-1111-4111-8111-111111111111";
    const hash = "0123456789abcdef0123456789abcdef";
    expect(avatarSrc(`/v1/friend-zone/players/${id}/avatar?v=${hash}`)).toBe(`/api/v1/friend-zone/players/${id}/avatar?v=${hash}`);
    expect(avatarSrc(null)).toBeNull();
    for (const url of ["https://evil.example/x.png", `/v1/me/avatar?v=${hash}`, `//evil.example/v1/friend-zone/players/${id}/avatar?v=${hash}`])
      expect(avatarSrc(url)).toBeNull();
  });
});

describe("copy", () => {
  test("no section uses school words or shows an invoice number, an email or an id", () => {
    const requests: FriendZoneRequests = { incoming: [request(1, "incoming")], outgoing: [request(2, "outgoing")] };
    const pages = [
      <RequestsSection key="r" requests={requests} reload={noop} />,
      <FriendsSection key="f" friends={[player(1)]} {...FRIENDS} />,
      <AddFriend key="a" reload={noop} />,
      <BlockedSection key="b" blocked={[player(2, { relation: "blocked" })]} reload={noop} />,
      <Dialog key="d" open>
        <ReportForm player={player(1)} onClose={noop} onSent={noop} onStale={noop} />
      </Dialog>,
    ];
    for (const page of pages) {
      const shown = text(renderToStaticMarkup(page));
      expect(shown).not.toMatch(SCHOOL);
      expect(shown).not.toMatch(/@|[0-9a-f]{8}-[0-9a-f]{4}/i);
    }
  });
});
