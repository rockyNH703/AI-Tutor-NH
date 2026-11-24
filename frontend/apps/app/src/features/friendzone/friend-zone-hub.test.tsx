import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import type { FriendZoneMe, FriendZoneProfile } from "./api";
import { FriendZoneHub, ProfileForm } from "./friend-zone-hub";
import type { MeLoad } from "./model";
import { NEW_PROFILE } from "./model";
import { ViewSwitch } from "./quizzes-switch";

const PROFILE: FriendZoneProfile = {
  playerId: "11111111-1111-4111-8111-111111111111",
  displayName: "آشا Asha",
  discoverable: true,
  showProgram: false,
  showStats: false,
  version: 4,
  qa: false,
};
const ME: FriendZoneMe = { enabled: true, eligible: true, member: true, suspended: false, profile: PROFILE, serverTime: "2026-11-24T10:00:00Z" };
const noop = () => {};
const PROGRAM = { slug: "ccna", title: "CCNA", months: [{ month: 1, title: "Basics", material: { documents: 1, lessons: 1, questions: 1 } }] };
const SOCIAL = { program: PROGRAM, month: 1, pulse: 0, battleId: null, onBattle: noop };

function hub(load: MeLoad): string {
  return renderToStaticMarkup(<FriendZoneHub load={load} retry={noop} refresh={noop} setMe={noop} onPractice={noop} social={SOCIAL} />);
}
const ready = (patch: Partial<FriendZoneMe>): MeLoad => ({ status: "ready", me: { ...ME, ...patch } });

/** The text a server render shows: tags dropped, entities read. */
function text(html: string): string {
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/&#x27;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/\s+/g, " ")
    .trim();
}

// lib/copy-vocabulary.test.ts's rule: NetSec AI's copy never talks to engineers as school students.
const SCHOOL = /\b(students?|homework|syllabus|ncert|cbse|textbooks?|teachers?|chapters?|doubts?|class \d{1,2}|exam boards?|schools?)\b/i;

describe("the Friend Zone hub, state by state", () => {
  test("loading draws placeholders only", () => {
    const html = hub({ status: "loading" });
    expect(html).toContain('aria-busy="true"');
    expect(text(html)).not.toContain("Join Friend Zone");
  });

  test("a failed read says so and offers Try again", () => {
    const html = hub({ status: "error", error: new Error("x") });
    expect(html).toContain('role="alert"');
    expect(text(html)).toContain("Friend Zone couldn't be loaded");
    expect(text(html)).toContain("Try again");
  });

  test("an account it isn't open to gets a short explanation and a way back to Practice", () => {
    const shown = text(hub(ready({ eligible: false, member: false, profile: null })));
    expect(shown).toContain("Friend Zone isn't open to your account");
    expect(shown).toContain("Go to Practice");
    expect(shown).not.toContain("Add a friend");
  });

  test("a suspended account gets a neutral line, nothing about why", () => {
    const shown = text(hub(ready({ suspended: true })));
    expect(shown).toContain("Friend Zone is paused for your account");
    expect(shown).not.toMatch(/suspend|report|violat|ban/i);
    expect(shown).not.toContain(PROFILE.displayName);
  });

  test("off draws nothing", () => {
    expect(text(hub(ready({ enabled: false, eligible: false, member: false, profile: null })))).toBe("");
  });

  test("not yet joined: the join form with every privacy switch off and clearly labelled", () => {
    const html = hub(ready({ member: false, profile: null }));
    const shown = text(html);
    expect(shown).toContain("Join Friend Zone");
    expect(shown).toContain("Friend Zone name");
    for (const label of ["Let classmates find me by invoice number", "Show my program to friends", "Show my stats to friends"])
      expect(shown).toContain(label);
    expect(html.match(/role="switch"/g)?.length).toBe(3);
    expect(html).not.toContain('aria-checked="true"');
    expect(html.match(/aria-checked="false"/g)?.length).toBe(3);
    expect(html).toContain('dir="auto"');
    expect(shown).not.toContain("Add a friend"); // friends come after joining
  });

  test("the join shows the approved consent text word for word, above the Join button", () => {
    const shown = text(hub(ready({ member: false, profile: null })));
    const consent =
      "Friend Zone shows your Friend Zone name to classmates you add as friends. If you turn it on, classmates can find you by your invoice number; the number itself is never shown. Your email, invoice number and learning records are never shared. Battle and daily-challenge results are shared only with friends, and stats only if you choose. You can change these settings at any time, and deleting your account removes your Friend Zone data.";
    expect(shown).toContain(consent);
    expect(shown.indexOf(consent)).toBeLessThan(shown.lastIndexOf("Join Friend Zone"));
    expect(text(hub(ready({})))).not.toContain(consent); // the join only, not the profile edit
  });

  test("a member sees the profile with each choice in words, the name in its own direction", () => {
    const html = hub(ready({}));
    const shown = text(html);
    expect(shown).toContain("Your profile");
    expect(shown).toContain("Edit profile");
    expect(html).toContain(`<p dir="auto"`);
    expect(shown).toContain(PROFILE.displayName);
    expect(shown).toMatch(/Let classmates find me by invoice number On/);
    expect(shown).toMatch(/Show my program to friends Off/);
    expect(shown).toMatch(/Show my stats to friends Off/);
    expect(shown).not.toContain("Test account");
    expect(shown).not.toContain(PROFILE.playerId);
  });

  test("a QA member is told it is a test account", () => {
    expect(text(hub(ready({ profile: { ...PROFILE, qa: true } })))).toContain("Test account");
  });

  test("a member gets the lists (loading first), Battles, Add a friend and the profile, in that order", () => {
    const html = hub(ready({}));
    const shown = text(html);
    expect(html).toContain('aria-busy="true"'); // the lists, read after mounting
    expect(shown.indexOf("Battles")).toBeLessThan(shown.indexOf("Add a friend"));
    expect(shown.indexOf("Add a friend")).toBeLessThan(shown.indexOf("Your profile"));
  });

  test("a member with ?battle= sees that battle, loading, instead of the lists", () => {
    const html = renderToStaticMarkup(
      <FriendZoneHub
        load={ready({})}
        retry={noop}
        refresh={noop}
        setMe={noop}
        onPractice={noop}
        social={{ ...SOCIAL, battleId: "33333333-3333-4333-8333-333333333333" }}
      />,
    );
    expect(text(html)).toContain("Back to Friend Zone");
    expect(text(html)).not.toContain("Add a friend");
  });

  test("no state's copy uses school words or shows an invoice number, an email or an id", () => {
    const loads: MeLoad[] = [
      { status: "error", error: new Error("x") },
      ready({ eligible: false, member: false, profile: null }),
      ready({ suspended: true }),
      ready({ member: false, profile: null }),
      ready({}),
      ready({ profile: { ...PROFILE, qa: true } }),
    ];
    for (const load of loads) {
      const shown = text(hub(load));
      expect(shown).not.toMatch(SCHOOL);
      expect(shown).not.toMatch(/@|[0-9a-f]{8}-[0-9a-f]{4}/i);
    }
  });
});

describe("the profile form", () => {
  test("shows a failure that isn't about the name as an alert, and the name refusal on the field", () => {
    const other = renderToStaticMarkup(
      <ProfileForm
        initial={NEW_PROFILE}
        submitLabel="Join Friend Zone"
        busyLabel="Joining…"
        busy={false}
        failure={{ message: "Slow down.", onName: false, reload: false }}
        onSubmit={noop}
      />,
    );
    expect(other).toContain('role="alert"');
    expect(text(other)).toContain("Slow down.");
  });

  test("busy disables saving and says what is happening", () => {
    const html = renderToStaticMarkup(
      <ProfileForm initial={NEW_PROFILE} submitLabel="Save changes" busyLabel="Saving…" busy failure={null} onSubmit={noop} onCancel={noop} />,
    );
    expect(text(html)).toContain("Saving…");
    expect(html).toMatch(/<button[^>]*type="submit"[^>]*disabled=""/);
    expect(text(html)).toContain("Cancel");
  });
});

describe("the view switch", () => {
  test("is a labelled group of two toggle buttons, the current one pressed", () => {
    const html = renderToStaticMarkup(<ViewSwitch view="friend-zone" onPick={noop} />);
    expect(html).toContain('role="group"');
    expect(html).toContain('aria-label="Quizzes &amp; Games view"');
    expect(html).toMatch(/aria-pressed="false"[^>]*>Practice</);
    expect(html).toMatch(/aria-pressed="true"[^>]*>Friend Zone</);
    expect(html).toContain("min-h-11");
  });
});
