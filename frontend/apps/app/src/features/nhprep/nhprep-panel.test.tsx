import { afterEach, describe, expect, test } from "bun:test";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { renderToStaticMarkup } from "react-dom/server";

import { cachedRead, clearNhprepCache, itemsKey, lessonKey, listKey } from "@/lib/api/nhprep";
import type { NhprepCard, NhprepItemKind, NhprepItemList, NhprepLesson, NhprepLessonList } from "@/lib/api/types";
import NhprepPanel, { PANEL_COPY, lessonMeta } from "./nhprep-panel";
import { NhprepSlot, COPY as SLOT_COPY } from "./nhprep-slot";
import { MAX_CARDS } from "./study-views";
import { ALTERNATIVES, CARD, IDS, LESSON, items, prime } from "./test-data";
import { COPY as VIDEO_COPY, FIRST_SECTIONS, VideoSection, allowedSrc } from "./video-player";

afterEach(clearNhprepCache);

/** Visible text and accessible names, as the copy check reads them. */
const shown = (markup: string) => [
  ...markup
    .replace(/<[^>]+>/g, "\n")
    .replace(/&#x27;/g, "'")
    .replace(/&amp;/g, "&")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean),
  ...[...markup.matchAll(/aria-label="([^"]*)"/g)].map((match) => match[1] ?? ""),
];
const SCHOOL = /\b(students?|chapters?)\b/i;

describe("the player's link check", () => {
  const id = "0f8fad5b-d9cb-469f-a165-70867728950e";
  test("takes only https://videos.nhprep.com/{uuid}.mp4, with its signature", () => {
    expect(allowedSrc(`https://videos.nhprep.com/${id}.mp4?exp=1&sig=abc`)).toBe(`https://videos.nhprep.com/${id}.mp4?exp=1&sig=abc`);
    for (const bad of [
      `http://videos.nhprep.com/${id}.mp4`,
      `https://videos.nhprep.com:8443/${id}.mp4`,
      `https://user:pw@videos.nhprep.com/${id}.mp4`,
      `https://videos.nhprep.com.evil.tld/${id}.mp4`,
      `https://nhprep.com/${id}.mp4`,
      `https://videos.nhprep.com/x/${id}.mp4`,
      `https://videos.nhprep.com/${id}.m3u8`,
      "https://videos.nhprep.com/not-a-uuid.mp4",
      "javascript:alert(1)",
      "",
      null,
      42,
    ]) {
      expect(allowedSrc(bad)).toBeNull();
    }
  });
});

describe("the video section", () => {
  const render = (video: "ready" | "no_video" | "not_configured", matchedId: string | null = null) =>
    renderToStaticMarkup(
      <VideoSection lessonId={IDS.lesson} title="OSPF areas" video={video} sections={LESSON.chapters} matchedId={matchedId} />,
    );

  test("ready: Play only (nothing loaded), the first sections as Jump to, then Show all", () => {
    const markup = render("ready");
    expect(markup).not.toContain("<video");
    expect(markup).toContain(">Play<");
    expect(markup).toContain("Jump to");
    expect(markup.match(/Part \d+/g)).toHaveLength(FIRST_SECTIONS);
    expect(markup).toContain("Show all 10");
    expect(shown(markup).filter((line) => SCHOOL.test(line))).toEqual([]);
  });

  test("a matched section: Play from its time, marked in the list", () => {
    const markup = render("ready", IDS.section(1));
    expect(markup).toContain("Play from 2:15");
    expect(markup).toContain("Matches your question");
    expect(markup).toContain('aria-current="true"');
  });

  test("not ready: the honest line from the start; the sections are a plain list", () => {
    for (const video of ["not_configured", "no_video"] as const) {
      const markup = render(video);
      expect(shown(markup)).toContain(video === "no_video" ? VIDEO_COPY.noVideo : VIDEO_COPY.unavailable);
      expect(markup).not.toContain(">Play<");
      expect(markup).toContain("In this video");
      expect(markup).not.toContain("Jump to");
      expect(markup.match(/<button/g)).toHaveLength(1); // Show all, and no seek or play control
    }
  });
});

describe("the panel", () => {
  test("draws the lesson, its details, Previous/Next, the other matches and the asked-for study aids", async () => {
    await prime();
    const markup = renderToStaticMarkup(<NhprepPanel card={CARD} />);
    expect(markup).toContain(lessonMeta(LESSON));
    expect(lessonMeta(LESSON)).toBe("CCNA 200-301 · Lesson 12 of 40 · Trainer: Sam Rao · 58:37");
    expect(markup).toContain("Previous lesson");
    expect(markup).toContain("Next lesson");
    expect(markup).toContain(PANEL_COPY.others);
    expect(markup).toContain(`Also taught by Lee Chan · ${ALTERNATIVES[0]?.course.title}`); // a tie: same title
    expect(markup).toContain("OSPF LSA types");
    expect(markup).toContain("NHprep notes");
    expect(markup).toContain("<strong>backbone</strong>");
    expect(markup).toContain('role="region"');
    expect(markup).toContain("Expand");
    expect(markup).toContain("2 questions. Open one to see its answer.");
    expect(markup).not.toContain("Answer 1"); // an answer is drawn only once opened
    expect(markup).toContain("Question 1"); // the flashcards widget's first card
    expect(markup).not.toContain(PANEL_COPY.ambiguous);
    expect(shown(markup).filter((line) => SCHOOL.test(line))).toEqual([]);
  });

  test("ambiguous: the same card with the neutral lead line", async () => {
    await prime();
    const markup = renderToStaticMarkup(<NhprepPanel card={{ ...CARD, status: "ambiguous" }} />);
    expect(markup).toContain(PANEL_COPY.ambiguous);
    expect(markup).toContain("Play");
  });

  test("a removed lesson: its title and one line; no player, study aids or Previous/Next", async () => {
    await prime({ ...LESSON, removed: true });
    const markup = renderToStaticMarkup(<NhprepPanel card={CARD} />);
    expect(markup).toContain(PANEL_COPY.removed);
    expect(markup).toContain("OSPF areas");
    expect(markup).not.toContain("Play");
    expect(markup).not.toContain("NHprep notes");
    expect(markup).not.toContain("Next lesson");
  });

  test("study aids NHprep doesn't have, or that couldn't be brought over, say so", async () => {
    await prime({ ...LESSON, availability: { note: "absent", interview_question: "error", flashcard: "present" } });
    const markup = renderToStaticMarkup(<NhprepPanel card={{ ...CARD, views: ["notes", "interview", "cheat_sheet"] }} />);
    expect(markup).toContain("This lesson has no notes on NHprep.");
    expect(markup).toContain("NHprep&#x27;s interview questions for this lesson couldn&#x27;t be brought over.");
    expect(markup).toContain("This lesson has no cheat sheet on NHprep."); // present-or-unknown, fetched, empty
    expect(markup).not.toContain("Play"); // no video asked for
  });

  test("more than 100 flashcards: the first 100, and a note saying so", async () => {
    await prime(LESSON, { flashcard: items("flashcard", MAX_CARDS + 20) });
    const markup = renderToStaticMarkup(<NhprepPanel card={{ ...CARD, views: ["flashcards"] }} />);
    expect(markup).toContain(`Showing the first ${MAX_CARDS} of ${MAX_CARDS + 20} cards.`);
  });

  test("nothing read yet: a loading status, not a blank", () => {
    const markup = renderToStaticMarkup(<NhprepPanel card={CARD} />);
    expect(markup).toContain("Loading the lesson");
    expect(markup).toContain('aria-busy="true"');
  });
});

describe("the slot", () => {
  test("an unknown version shows the house reload line and loads nothing", () => {
    const markup = renderToStaticMarkup(<NhprepSlot card={{ ...CARD, version: 2 }} eager />);
    expect(markup).toContain(SLOT_COPY.newerVersion.replace("isn't", "isn&#x27;t"));
    expect(markup).not.toContain("data-nhprep-slot");
  });

  test("version 1 renders the slot wrapper the axe checks scope to", () => {
    expect(renderToStaticMarkup(<NhprepSlot card={CARD} eager={false} />)).toContain("data-nhprep-slot");
  });
});

/** backend/tests/fixtures/nhprep/cards.json (L4) pins the wire shapes the API and the chat send; until it exists, CARD is the stub. */
describe("the pinned NHprep fixture", () => {
  const path = join(dirname(fileURLToPath(import.meta.url)), "../../../../../../backend/tests/fixtures/nhprep/cards.json");
  const fixture = existsSync(path)
    ? (JSON.parse(readFileSync(path, "utf8")) as {
        cards: NhprepCard[];
        lesson: NhprepLesson;
        lessons: NhprepLessonList;
        items: Record<string, NhprepItemList>;
        playback: { ready: { src: string } };
      })
    : null;
  const cards = fixture?.cards ?? [CARD];
  const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  const VIEWS = ["video", "chapters", "notes", "cheat_sheet", "flashcards", "interview"];

  test(`every card has exactly the pinned keys and values (${fixture ? "fixture" : "stub"})`, () => {
    expect(cards.length).toBeGreaterThan(0);
    for (const card of cards) {
      expect(Object.keys(card).sort()).toEqual(
        ["alternativeLessonIds", "matchedItemId", "primaryLessonId", "status", "version", "views"].sort(),
      );
      expect(card.version).toBe(1);
      expect(["confident", "ambiguous"]).toContain(card.status);
      expect(card.primaryLessonId).toMatch(UUID);
      expect(card.alternativeLessonIds.length).toBeLessThanOrEqual(5);
      for (const id of card.alternativeLessonIds) expect(id).toMatch(UUID);
      if (card.matchedItemId !== null) expect(card.matchedItemId).toMatch(UUID);
      for (const view of card.views) expect(VIEWS).toContain(view);
    }
  });

  test.skipIf(!fixture)("the API's sample responses draw: lesson, summaries, items, and a playable link", async () => {
    if (!fixture) return;
    expect(Object.keys(fixture.lesson).sort()).toEqual(Object.keys(LESSON).concat("removed").sort());
    expect(allowedSrc(fixture.playback.ready.src)).not.toBeNull();
    const card = { ...(fixture.cards[1] as NhprepCard), views: ["video", "notes", "flashcards", "interview", "cheat_sheet"] as NhprepCard["views"] };
    clearNhprepCache();
    await cachedRead(lessonKey(fixture.lesson.id), async () => fixture.lesson);
    await cachedRead(listKey([card.primaryLessonId, ...card.alternativeLessonIds]), async () => ({
      items: [fixture.lesson, ...fixture.lessons.items],
    }));
    for (const [kind, list] of Object.entries(fixture.items)) {
      await cachedRead(itemsKey(fixture.lesson.id, kind as NhprepItemKind), async () => list);
    }
    const markup = renderToStaticMarkup(<NhprepPanel card={card} />);
    expect(markup).toContain(fixture.lesson.title);
    expect(markup).toContain("Static routes point at a next hop.");
    expect(markup).toContain("OSPF Areas"); // the other match
    expect(markup).toContain("This lesson has no cheat sheet on NHprep."); // availability: absent
    expect(markup).toContain("couldn&#x27;t be brought over"); // interview_question: unavailable
    expect(shown(markup).filter((line) => SCHOOL.test(line))).toEqual([]);
  });
});
