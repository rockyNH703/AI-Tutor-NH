import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import Flashcards, {
  FlashcardDeck,
  type Progress,
  deckKey,
  freshProgress,
  markCard,
  readProgress,
  reviewLearning,
} from "./flashcards";

const DECK = {
  title: "OSPF",
  cards: [
    { front: "OSPF's protocol number?", back: "IP protocol 89" },
    { front: "Area 0 is called?", back: "The backbone area" },
    { front: "Default hello interval on broadcast?", back: "10 seconds" },
  ],
};
const stored = (value: unknown) => JSON.stringify(value);

describe("the deck validator", () => {
  test("takes today's JSON and refuses empty, oversized or malformed decks", () => {
    expect(FlashcardDeck.safeParse(DECK).success).toBe(true);
    expect(FlashcardDeck.safeParse({ cards: [] }).success).toBe(false);
    expect(FlashcardDeck.safeParse({ cards: [{ front: "", back: "x" }] }).success).toBe(false);
    expect(FlashcardDeck.safeParse({ cards: [{ front: "x".repeat(1001), back: "x" }] }).success).toBe(false);
    expect(FlashcardDeck.safeParse({ cards: Array(101).fill({ front: "a", back: "b" }) }).success).toBe(false);
  });
});

describe("the storage key", () => {
  test("is the same for the same source and differs for another", () => {
    const source = JSON.stringify(DECK);
    expect(deckKey(source)).toBe(deckKey(source));
    expect(deckKey(source)).toMatch(/^netsecai\.flashcards\.[0-9a-z]+$/);
    expect(deckKey(source)).not.toBe(deckKey(`${source} `));
  });
});

describe("saved progress", () => {
  const fresh = freshProgress(3);

  test("nothing, junk or the wrong shape starts afresh", () => {
    expect(fresh).toEqual({ queue: [0, 1, 2], at: 0, marks: [null, null, null], done: false });
    for (const raw of [null, "", "{", "null", "[]", stored({ queue: [0], at: 0, marks: [null], done: false })]) {
      expect(readProgress(raw, 3)).toEqual(fresh);
    }
    expect(readProgress(stored({ queue: [0, 1, 2], at: 0, marks: ["maybe", null, null], done: false }), 3)).toEqual(fresh);
    expect(readProgress(stored({ queue: [0, 1, 2], at: 0.5, marks: [null, null, null], done: false }), 3)).toEqual(fresh);
  });

  test("a valid save is restored", () => {
    const saved: Progress = { queue: [0, 1, 2], at: 2, marks: ["known", "learning", null], done: false };
    expect(readProgress(stored(saved), 3)).toEqual(saved);
  });

  test("positions are clamped and cards outside the deck dropped", () => {
    const marks: ("learning" | null)[] = [null, "learning", "learning"];
    expect(readProgress(stored({ queue: [0, 1, 2], at: 99, marks, done: false }), 3).at).toBe(2);
    expect(readProgress(stored({ queue: [0, 1, 2], at: -4, marks, done: false }), 3).at).toBe(0);
    const review = readProgress(stored({ queue: [2, 2, 7, -1, 1], at: 5, marks, done: true }), 3);
    expect(review).toEqual({ queue: [2, 1], at: 1, marks, done: true });
    expect(readProgress(stored({ queue: [9], at: 0, marks, done: false }), 3)).toEqual(fresh);
  });
});

describe("marking and rounds", () => {
  test("a mark moves on, and the round's last card shows the summary", () => {
    const start = freshProgress(2);
    const first = markCard(start, "known");
    expect(first).toEqual({ queue: [0, 1], at: 1, marks: ["known", null], done: false });
    expect(start.marks).toEqual([null, null]); // never changed in place
    expect(markCard(first, "learning")).toEqual({ queue: [0, 1], at: 1, marks: ["known", "learning"], done: true });
  });

  test("review takes only the cards still being learned", () => {
    const done = { queue: [0, 1, 2], at: 2, marks: ["learning", "known", "learning"] as const, done: true };
    expect(reviewLearning({ ...done, marks: [...done.marks] })).toEqual({
      queue: [0, 2],
      at: 0,
      marks: ["learning", "known", "learning"],
      done: false,
    });
    const allKnown = { ...done, marks: ["known" as const, "known" as const, "known" as const] };
    expect(reviewLearning(allKnown)).toBe(allKnown);
  });
});

describe("the first render", () => {
  const html = renderToStaticMarkup(<Flashcards source={JSON.stringify(DECK)} onError={() => {}} />);

  test("shows the question and never the answer before the card is turned", () => {
    expect(html).toContain("OSPF&#x27;s protocol number?");
    for (const card of DECK.cards) expect(html).not.toContain(card.back);
  });

  test("names the face and its controls, with Previous unavailable at the start", () => {
    expect(html).toContain('role="region"');
    expect(html).toContain('aria-label="Card 1 of 3, question"');
    expect(html).toContain('aria-live="polite"');
    expect(html).toContain(">1 of 3<");
    expect(html).toContain("Show answer");
    expect(html).toContain("Know it");
    expect(html).toContain("Still learning");
    expect(html).toMatch(/aria-disabled="true"[^>]*>(<svg[\s\S]*?<\/svg>)?Previous/);
    expect(html).toMatch(/aria-disabled="false"[^>]*>Next/);
  });

  test("a deck that is not valid draws nothing", () => {
    expect(renderToStaticMarkup(<Flashcards source="{" onError={() => {}} />)).toBe("");
  });
});
