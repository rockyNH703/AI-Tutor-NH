import type { BankTopic } from "@/lib/api/programs";
import type { Mastery, MasteryState } from "@/lib/api/types";

/**
 * Quizzes & Games, grouped the way the question bank is (owner, 2026-11-27): a month's quizzes (bank topics) under
 * their track (book), then their domain (`chapter`, e.g. "ACL fundamentals"), both in the order the API lists them.
 * Nothing is invented: a quiz without a domain sits under "Other topics", every quiz appears exactly once.
 */
export type QuizTopic = { key: string; title: string; quizzes: BankTopic[] };
export type QuizTrack = { title: string; topics: QuizTopic[] };

export const UNCLASSIFIED = "Other topics";

/** A stable address for a topic within its month (`?topic=`): track and domain, lower-case words joined by "-". */
export function topicKey(track: string, chapter: string): string {
  const words = `${track} ${chapter}`.toLowerCase().match(/[a-z0-9]+/g) ?? [];
  return words.join("-") || "topic";
}

export function groupQuizzes(quizzes: BankTopic[]): QuizTrack[] {
  const tracks = new Map<string, Map<string, BankTopic[]>>();
  for (const quiz of quizzes) {
    const chapter = quiz.chapter.trim() || UNCLASSIFIED;
    const track = tracks.get(quiz.book) ?? new Map<string, BankTopic[]>();
    track.set(chapter, [...(track.get(chapter) ?? []), quiz]);
    tracks.set(quiz.book, track);
  }
  return [...tracks].map(([title, chapters]) => ({
    title,
    topics: [...chapters].map(([chapter, items]) => ({ key: topicKey(title, chapter), title: chapter, quizzes: items })),
  }));
}

/** What the practice record says about each bank topic the user has practised (GET /v1/progress/mastery). */
export function masteryByTopic(mastery: Mastery | null): Map<string, MasteryState> {
  const states = new Map<string, MasteryState>();
  for (const subject of mastery?.subjects ?? []) {
    for (const chapter of subject.chapters) {
      for (const topic of chapter.topics) states.set(topic.topicId, topic.state);
    }
  }
  return states;
}

export function topicProgress(topic: QuizTopic, states: Map<string, MasteryState>): { practised: number; mastered: number } {
  const known = topic.quizzes.map((quiz) => states.get(quiz.topicId)).filter((state) => state !== undefined);
  return { practised: known.length, mastered: known.filter((state) => state === "mastered").length };
}

/** A track's size and progress, for its collapsed header: topics, quizzes, and quizzes practised. */
export function trackTotals(track: QuizTrack, states: Map<string, MasteryState>) {
  const quizzes = track.topics.flatMap((topic) => topic.quizzes);
  return {
    topics: track.topics.length,
    quizzes: quizzes.length,
    practised: quizzes.filter((quiz) => states.has(quiz.topicId)).length,
  };
}

/** Every quiz whose title, domain or track holds each word of the query, in order; [] for an empty query. */
export function searchQuizzes(tracks: QuizTrack[], query: string): { quiz: BankTopic; topic: QuizTopic }[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return [];
  return tracks.flatMap((track) =>
    track.topics.flatMap((topic) =>
      topic.quizzes
        .filter((quiz) => {
          const text = `${quiz.title} ${topic.title} ${track.title}`.toLowerCase();
          return words.every((word) => text.includes(word));
        })
        .map((quiz) => ({ quiz, topic })),
    ),
  );
}

export const STATE_LABEL: Record<MasteryState, string> = {
  mastered: "Mastered",
  learning: "Learning",
  weak: "Needs practice",
  not_enough_evidence: "Started",
};

export const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;
