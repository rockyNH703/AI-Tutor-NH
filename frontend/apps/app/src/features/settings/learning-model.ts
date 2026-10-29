/** Pure helpers for the learning forms: tested in learning-model.test.ts, no React. */

import type { EngineeringDraft, SubjectItem } from "@/lib/api/types";

type Empty = null | "" | undefined;

function isEmpty(value: unknown): value is Empty {
  return value === null || value === undefined || value === "" || (Array.isArray(value) && value.length === 0);
}

function same(a: unknown, b: unknown): boolean {
  if (isEmpty(a) && isEmpty(b)) return true;
  return JSON.stringify(a) === JSON.stringify(b);
}

/**
 * The versioned edit that turns `base` into `draft` for `fields`: a changed field with a value
 * is `set`, a changed field that is now empty is `reset`, anything unchanged is left out.
 */
export function editFor<T extends Record<string, unknown>>(
  base: Partial<T>,
  draft: Partial<T>,
  fields: readonly (keyof T & string)[],
): { set: Partial<T>; reset: (keyof T & string)[] } {
  const set: Partial<T> = {};
  const reset: (keyof T & string)[] = [];
  for (const field of fields) {
    if (same(base[field], draft[field])) continue;
    if (isEmpty(draft[field])) reset.push(field);
    else set[field] = draft[field];
  }
  return { set, reset };
}

export function hasChanges(edit: { set: object; reset: unknown[] }): boolean {
  return Object.keys(edit.set).length > 0 || edit.reset.length > 0;
}

/** One line, trimmed, like the API stores it. */
export function cleanTopic(text: string): string {
  return text.split(/\s+/).filter(Boolean).join(" ");
}

/** Add a topic unless it is empty, too long, a repeat (any case) or one too many. */
export function addTopic(
  topics: readonly string[],
  text: string,
  limits: { max: number; maxLength: number },
): { topics: string[]; error: string | null } {
  const topic = cleanTopic(text);
  if (!topic) return { topics: [...topics], error: null };
  if (topic.length > limits.maxLength) return { topics: [...topics], error: `Keep a topic under ${limits.maxLength + 1} characters.` };
  if (topics.some((existing) => existing.toLocaleLowerCase() === topic.toLocaleLowerCase()))
    return { topics: [...topics], error: "That topic is already on the list." };
  if (topics.length >= limits.max) return { topics: [...topics], error: `You can list up to ${limits.max} topics.` };
  return { topics: [...topics, topic], error: null };
}

export function moveSubject(items: readonly SubjectItem[], index: number, delta: -1 | 1): SubjectItem[] {
  const target = index + delta;
  if (index < 0 || index >= items.length || target < 0 || target >= items.length) return [...items];
  const next = [...items];
  [next[index], next[target]] = [next[target] as SubjectItem, next[index] as SubjectItem];
  return next;
}

export function addSubject(items: readonly SubjectItem[], code: string, max: number): SubjectItem[] {
  if (!code || items.length >= max || items.some((item) => item.code === code)) return [...items];
  return [...items, { code, isPriority: false, needsImprovement: false }];
}

export function removeSubject(items: readonly SubjectItem[], code: string): SubjectItem[] {
  return items.filter((item) => item.code !== code);
}

export function toggleSubject(
  items: readonly SubjectItem[],
  code: string,
  flag: "isPriority" | "needsImprovement",
): SubjectItem[] {
  return items.map((item) => (item.code === code ? { ...item, [flag]: !item[flag] } : item));
}

/** A whole number within [0, max], or null for empty. */
export function wholeNumber(text: string, max: number): number | null {
  if (text.trim() === "") return null;
  const value = Math.round(Number(text));
  return Number.isFinite(value) ? Math.min(Math.max(value, 0), max) : null;
}

/**
 * Check or uncheck one code of a multiple choice (focus areas, platforms): a checked code is
 * removed; an unchecked one is added at the end, once, and only while the list has room.
 */
export function toggleCode(codes: readonly string[], code: string, max: number): string[] {
  if (codes.includes(code)) return codes.filter((existing) => existing !== code);
  if (!code || codes.length >= max) return [...codes];
  return [...codes, code];
}

/** Whether a code can be toggled now: a checked one always (to uncheck it), an unchecked one only with room left. */
export function canToggle(codes: readonly string[], code: string, max: number): boolean {
  return codes.includes(code) || codes.length < max;
}

/** "3 of 8 chosen": how full a multiple choice is, said the same way everywhere. */
export function chosenText(count: number, max: number): string {
  return count >= max ? `${count} of ${max} chosen, the most you can choose` : `${count} of ${max} chosen`;
}

/**
 * The Engineering Profile form as the preview's `engineering` draft: every field, so the preview is
 * exactly what saving the form would tell the assistant (`null` and [] clear a field there).
 */
export function engineeringPreview(draft: EngineeringDraft): Required<EngineeringDraft> {
  return {
    role: draft.role || null,
    experienceLevel: draft.experienceLevel || null,
    certificationGoal: draft.certificationGoal || null,
    focusAreas: [...(draft.focusAreas ?? [])],
    platforms: [...(draft.platforms ?? [])],
  };
}
