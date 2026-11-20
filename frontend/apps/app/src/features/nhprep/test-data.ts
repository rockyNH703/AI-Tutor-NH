/** Synthetic NHprep data for unit tests (not real lessons): one course, a lesson with 10 sections, its study aids. */
import { cachedRead, clearNhprepCache, itemsKey, lessonKey, listKey } from "@/lib/api/nhprep";
import type { NhprepCard, NhprepItem, NhprepItemKind, NhprepLesson, NhprepLessonSummary } from "@/lib/api/types";

export const IDS = {
  lesson: "11111111-1111-4111-8111-111111111111",
  tie: "22222222-2222-4222-8222-222222222222",
  other: "33333333-3333-4333-8333-333333333333",
  previous: "44444444-4444-4444-8444-444444444444",
  next: "55555555-5555-4555-8555-555555555555",
  section: (n: number) => `66666666-6666-4666-8666-${String(n).padStart(12, "0")}`,
  item: (n: number) => `77777777-7777-4777-8777-${String(n).padStart(12, "0")}`,
} as const;

const course = { id: "88888888-8888-4888-8888-888888888888", title: "CCNA 200-301" };

export const SUMMARY: NhprepLessonSummary = {
  id: IDS.lesson,
  title: "OSPF areas",
  course,
  trainers: ["Sam Rao"],
  position: 12,
  lessonCount: 40,
  durationSeconds: 3517,
};

export const LESSON: NhprepLesson = {
  ...SUMMARY,
  description: "Why OSPF splits a network into areas, and what area 0 does.",
  chapters: Array.from({ length: 10 }, (_, index) => ({
    id: IDS.section(index),
    title: `Part ${index + 1}`,
    startSeconds: index * 135,
  })),
  availability: { note: "present", cheat_sheet: "absent", flashcard: "present", interview_question: "present" },
  previousLessonId: IDS.previous,
  nextLessonId: IDS.next,
  video: "ready",
};

export const ALTERNATIVES: NhprepLessonSummary[] = [
  { ...SUMMARY, id: IDS.tie, trainers: ["Lee Chan"], course: { id: course.id, title: "CCNA Bootcamp" } },
  { ...SUMMARY, id: IDS.other, title: "OSPF LSA types", position: 13 },
];

export const CARD: NhprepCard = {
  version: 1,
  views: ["video", "notes", "flashcards", "interview"],
  status: "confident",
  primaryLessonId: IDS.lesson,
  alternativeLessonIds: [IDS.tie, IDS.other],
  matchedItemId: IDS.section(1),
};

export function items(kind: NhprepItemKind, count: number, body = (n: number) => `Answer ${n}`): NhprepItem[] {
  return Array.from({ length: count }, (_, n) => ({ id: IDS.item(n), kind, title: `Question ${n + 1}`, bodyMd: body(n) }));
}

/** Fills the per-tab cache as the API would have, so a static render draws the data. */
export async function prime(lesson: NhprepLesson = LESSON, extra: Partial<Record<NhprepItemKind, NhprepItem[]>> = {}) {
  clearNhprepCache();
  const ids = [CARD.primaryLessonId, ...CARD.alternativeLessonIds];
  await cachedRead(listKey(ids), async () => ({ items: [{ ...SUMMARY, title: lesson.title }, ...ALTERNATIVES] }));
  await cachedRead(lessonKey(lesson.id), async () => lesson);
  const defaults: Record<NhprepItemKind, NhprepItem[]> = {
    note: [{ id: IDS.item(0), kind: "note", title: "Areas", bodyMd: "Area 0 is the **backbone**." }],
    cheat_sheet: [],
    flashcard: items("flashcard", 3),
    interview_question: items("interview_question", 2),
  };
  for (const kind of Object.keys(defaults) as NhprepItemKind[]) {
    await cachedRead(itemsKey(lesson.id, kind), async () => ({ items: extra[kind] ?? defaults[kind] }));
  }
}
