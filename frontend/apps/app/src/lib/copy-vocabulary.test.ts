import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createElement, isValidElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { NAV_ITEMS } from "@/components/nav-items";
import { AboutPage, FAQ, HelpPage } from "@/features/help/help-pages";
import { MessageContent } from "@/features/markdown/message-content";
import Flashcards from "@/features/markdown/widgets/flashcards";
import Video from "@/features/markdown/widgets/video";
import { EngineeringFields } from "@/features/settings/engineering-section";
import { MAIN_GOAL_LABELS, PREFERENCES } from "@/features/settings/learning-labels";
import { SETTINGS_SECTIONS } from "@/features/settings/sections";
import { EMAIL_KINDS } from "@/features/unsubscribe/token";
import NhprepPanel, { PANEL_COPY } from "@/features/nhprep/nhprep-panel";
import { CARD, IDS, LESSON, prime } from "@/features/nhprep/test-data";
import { placeText } from "@/features/programs/program-nav";
import { COPY as NHPREP_VIDEO_COPY, VideoSection } from "@/features/nhprep/video-player";
import { describeCode } from "@/lib/api/errors";
import { clearNhprepCache } from "@/lib/api/nhprep";
import { BRAND } from "@/lib/brand";
import { GREETINGS } from "@/lib/greeting";

/**
 * NetSec AI is for network, security and infrastructure engineers: the copy people read must not
 * talk to them as school students. Internal ids, route paths and API field names may keep their
 * old names (the `subjects` section, the `homework_help` goal); what is shown may not.
 */
const SCHOOL =
  /\b(students?|homework|syllabus|ncert|cbse|textbooks?|teachers?|chapters?|doubts?|class \d{1,2}|exam boards?|schools?)\b/i;
const settings = join(dirname(fileURLToPath(import.meta.url)), "../features/settings");

/** The code of a source file without its comments, line by line: every string it can show is in it. */
function sourceLines(file: string): string[] {
  return readFileSync(join(settings, file), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .split("\n")
    .filter((line) => !/^\s*\/\//.test(line));
}

/** The text a server render shows: tags dropped, entities read. */
function visibleText(html: string): string[] {
  return html
    .replace(/<[^>]+>/g, "\n")
    .replace(/&#x27;/g, "'")
    .replace(/&amp;/g, "&")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

/** Every string a React tree shows: its text, and the text props a reader sees or hears. */
function textOf(node: ReactNode | (() => ReactNode)): string[] {
  if (typeof node === "function") return textOf(node());
  if (typeof node === "string" || typeof node === "number") return [String(node)];
  if (Array.isArray(node)) return node.flatMap((child) => textOf(child));
  if (!isValidElement(node)) return [];
  const props = node.props as Record<string, unknown>;
  const shown = ["title", "intro", "label", "description", "aria-label", "placeholder"]
    .map((name) => props[name])
    .filter((value): value is string => typeof value === "string");
  return [...shown, ...textOf(props.children as ReactNode)];
}

function offenders(lines: readonly string[]): string[] {
  return lines.filter((line) => SCHOOL.test(line));
}

describe("user-visible copy has no school vocabulary", () => {
  test("the sidebar's tools and the chat's own name", () => {
    expect(offenders(NAV_ITEMS.flatMap((item) => [item.label, item.description]))).toEqual([]);
    expect(NAV_ITEMS.find((item) => item.href === "/")?.label).toBe("NetSec AI");
  });

  test("the new chat's greetings", () => {
    expect(GREETINGS.length).toBeGreaterThan(1);
    expect(offenders(GREETINGS)).toEqual([]);
  });

  test("Help & FAQ and About", () => {
    const faq = FAQ.flatMap((group) => [group.topic, ...group.items.flatMap((item) => [item.question, ...textOf(item.answer)])]);
    expect(faq.join(" ")).toContain("secrets"); // getting started: redact before pasting
    expect(offenders(faq)).toEqual([]);
    const pages = [...textOf(HelpPage), ...textOf(AboutPage)];
    expect(pages.join(" ")).toContain("network, security and infrastructure engineers"); // the walk reached the text
    expect(offenders(pages)).toEqual([]);
  });

  test("Settings sections: every title, description and search word", () => {
    const shown = SETTINGS_SECTIONS.flatMap((section) => [section.title, section.description, ...section.keywords]);
    expect(shown).toContain("Engineering Profile");
    expect(offenders(shown)).toEqual([]);
  });

  test("Engineering Profile, Skills & Certification Goals, Practice & Lab Time and the answer preferences", () => {
    const labels = [
      ...Object.values(MAIN_GOAL_LABELS),
      ...Object.values(PREFERENCES).flatMap((info) => [info.label, info.description, ...Object.values(info.options)]),
    ];
    expect(labels).toContain("Pass a certification exam");
    expect(offenders(labels)).toEqual([]);

    for (const file of ["engineering-section.tsx", "goals-section.tsx", "subjects-section.tsx", "preferences-sections.tsx"]) {
      expect(offenders(sourceLines(file)), file).toEqual([]);
    }

    const entry = (code: string, name: string) => ({ code, name, deprecated: false });
    const form = renderToStaticMarkup(
      createElement(EngineeringFields, {
        catalog: {
          version: 1,
          roles: [entry("network_engineer", "Network Engineer")],
          experienceLevels: [entry("mid", "Mid-level (3–5 years)")],
          focusAreas: [entry("routing_switching", "Routing and Switching")],
          platforms: [entry("cisco_ios", "Cisco IOS / IOS XE")],
          certifications: [entry("ccna", "CCNA")],
        },
        base: null,
        draft: { focusAreas: ["routing_switching"] },
        change: () => {},
        invalid: () => true,
        notice: "This was just changed somewhere else.",
      }),
    );
    const text = visibleText(form);
    expect(text).toContain("Focus areas"); // the render reached the form
    expect(offenders(text)).toEqual([]);
  });

  test("a cheat sheet's own copy: its frame, controls and their names", () => {
    const content = ":::cheatsheet[OSPF timers]\n## Hello\nEvery 10 s.\n\n## Dead\nFour times hello.\n:::";
    const html = renderToStaticMarkup(createElement(MessageContent, { content }));
    const names = [...html.matchAll(/aria-label="([^"]*)"/g)].map((match) => match[1] ?? "");
    const shown = [...visibleText(html), ...names];
    expect(shown).toContain("Collapse all"); // the render reached the controls
    expect(shown).toContain("Print cheat sheet");
    expect(offenders(shown)).toEqual([]);
  });

  test("flashcards and video: their frames, controls and their names", () => {
    const shownBy = (html: string) => [
      ...visibleText(html),
      ...[...html.matchAll(/aria-label="([^"]*)"/g)].map((match) => match[1] ?? ""),
    ];
    const deck = { title: "OSPF", cards: [{ front: "Area 0 is called?", back: "The backbone area" }] };
    const cards = shownBy(renderToStaticMarkup(createElement(Flashcards, { source: JSON.stringify(deck), onError: () => {} })));
    expect(cards).toContain("Area 0 is called?"); // the render reached the deck
    expect(offenders(cards)).toEqual([]);

    const video = {
      youtube: "dQw4w9WgXcQ",
      title: "OSPF in ten minutes",
      channel: "Lab Notes",
      chapters: [
        { at: 0, title: "Neighbours" },
        { at: 135, title: "OSPF areas" },
      ],
    };
    const player = shownBy(renderToStaticMarkup(createElement(Video, { source: JSON.stringify(video), onError: () => {} })));
    expect(player).toContain("Jump to"); // the render reached the list, which is never called "Chapters"
    expect(offenders(player)).toEqual([]);
  });

  test("NHprep lesson cards: the lesson, its video, study aids and their names", async () => {
    await prime();
    const shownBy = (html: string) => [
      ...visibleText(html),
      ...[...html.matchAll(/aria-label="([^"]*)"/g)].map((match) => match[1] ?? ""),
    ];
    const card = shownBy(renderToStaticMarkup(createElement(NhprepPanel, { card: { ...CARD, status: "ambiguous" } })));
    expect(card).toContain("Jump to"); // the render reached the sections, never called "Chapters"
    expect(card).toContain("Other matching videos");
    const unavailable = shownBy(
      renderToStaticMarkup(
        createElement(VideoSection, { lessonId: IDS.lesson, title: "x", video: "not_configured", sections: LESSON.chapters, matchedId: null }),
      ),
    );
    expect(unavailable).toContain("In this video");
    expect(offenders([...card, ...unavailable, ...Object.values(NHPREP_VIDEO_COPY), ...Object.values(PANEL_COPY)])).toEqual([]);
    clearNhprepCache();
  });

  test("NH program chats: what each place says (greeting, placeholder, header)", () => {
    const program = { title: "Full Stack Networking with AI", months: [{ month: 1, title: "Networking Fundamentals & CCNA Core" }] };
    const shown = (["tutor", "interview"] as const).flatMap((mode) =>
      [1, null].flatMap((month) => {
        const text = placeText({ program: "networking-ai", month, mode }, program);
        return [text.label ?? "", text.title, text.greeting, text.placeholder, text.tabTitle, text.back?.label ?? ""];
      }),
    );
    expect(shown).toContain("What shall we learn in Month 1?"); // the walk reached the copy
    expect(offenders(shown)).toEqual([]);
  });

  test("brand, emails and errors", () => {
    expect(offenders([BRAND.description, ...Object.values(EMAIL_KINDS)])).toEqual([]);
    const voice = describeCode("voice_busy");
    expect(offenders([voice.title, voice.message])).toEqual([]);
  });

  test("the check itself catches school words", () => {
    expect(
      offenders(["Ask your doubt", "Class 10 CBSE", "a chapter from NCERT", "Ask New Doubt", "Recent Doubts", "at school"]),
    ).toHaveLength(6);
    expect(offenders(["Message NetSec AI", "Domain 3: Routing", "OSPF classless routing"])).toEqual([]);
  });
});
