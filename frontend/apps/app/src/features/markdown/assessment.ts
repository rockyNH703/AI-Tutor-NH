/**
 * Practice tests on the client: which card a server record belongs to, and what a typed message
 * means to the waiting test. Everything else (the questions, the order, the grading, the score)
 * is the server's (lib/api/types.ts `Assessment`); nothing here keeps or scores answers.
 */

import { sha256Hex } from "@/lib/api/files";
import type { Assessment } from "@/lib/api/types";

export const LETTERS = "ABCDEFGH";

/** A test block's source as the server hashed it: \n line ends, no spaces or breaks around it. */
export function testSource(source: string): string {
  return source.replace(/\r\n?/g, "\n").replace(/^[ \t\n]+|[ \t\n]+$/g, "");
}

/** The record's `locator` when this block is the reply's test. */
export function testLocator(source: string): Promise<string> {
  return sha256Hex(new Blob([testSource(source)]));
}

/** One fenced block of a reply: what opens it (`prefix`: list or quote markers), its language and body. */
export type Fence = { prefix: string; language: string; body: string };

// The server's fence reader (app/assessments/state.py `_FENCE`), so both see the same blocks. One space or tab
// after each marker, then `[ \t>]*`: the same lines as `[ \t]+[ \t>]*`, without its exponential backtracking.
const FENCE = /^([ \t>]*(?:(?:[-+*]|\d{1,9}[.)])[ \t][ \t>]*)*)(`{3,}|~{3,})(.*)$/;

/**
 * The text with fenced blocks replaced: `replace` returns a block's replacement line, or null to keep it.
 * Reads fences as the server does (state.py `_blocks`): ``` or ~~~ of 3 or more, also inside list items
 * and quotes, closed by the same character at least as long, or open to the end (a reply still
 * streaming). Returns `text` itself when nothing was replaced.
 */
export function replaceFences(text: string, replace: (fence: Fence) => string | null): string {
  const lines = text.split(/\r\n?|\n/);
  const out: string[] = [];
  let changed = false;
  let index = 0;
  while (index < lines.length) {
    const match = FENCE.exec(lines[index] ?? "");
    const [, prefix = "", fence = "", info = ""] = match ?? [];
    if (!match || (fence[0] === "`" && info.includes("`"))) {
      out.push(lines[index] ?? "");
      index += 1;
      continue;
    }
    const close = new RegExp(`^[ \\t>]*${fence[0] === "`" ? "`" : "~"}{${fence.length},}[ \\t]*$`);
    let end = index + 1;
    while (end < lines.length && !close.test(lines[end] ?? "")) end += 1;
    const language = info.trim().split(/\s+/)[0] ?? "";
    const replacement = replace({ prefix, language, body: lines.slice(index + 1, end).join("\n") });
    if (replacement === null) out.push(...lines.slice(index, end + 1));
    else {
      out.push(replacement);
      changed = true;
    }
    index = end + 1;
  }
  return changed ? out.join("\n") : text;
}

const EXAM_MODE = /"mode"\s*:\s*"exam"/;

/** Whether a quiz fence's own JSON makes it a mock exam (its keys must not be shown before submit). */
export function isExamSource(source: string): boolean {
  if (EXAM_MODE.test(source)) return true;
  // An escaped value ("exam") is caught by parsing; a fence in a quote is read again without its markers.
  for (const candidate of [source, source.replace(/^[ \t>]*/gm, "")]) {
    let parsed: unknown;
    try {
      parsed = JSON.parse(candidate);
    } catch {
      continue; // not JSON as written: try without quote markers, then give up (the regex already looked)
    }
    return typeof parsed === "object" && parsed !== null && (parsed as { mode?: unknown }).mode === "exam";
  }
  return false;
}

/** What a hidden practice fence becomes in copied or plain text. */
export const MASKED_TEST = "(Practice test: answer it in the chat.)";

/**
 * The reply's text with each practice fence that must not be read yet replaced by a short placeholder:
 * a quiz whose JSON says `"mode": "exam"` unless the reply's record says it was submitted, and, while
 * the reply streams or before its records are known (`assessments` undefined), every quiz/mcq fence.
 * A reply holds at most one practice block, so no hashing is needed. Used by Copy reply, the plain-text
 * fallback and a cheatsheet's Copy/Download.
 */
export function maskExamFences(
  text: string,
  assessments: readonly Assessment[] | undefined,
  streaming = false,
): string {
  const every = streaming || assessments === undefined;
  const submitted = assessments?.some((record) => record.mode === "exam" && record.submitted === true) ?? false;
  return replaceFences(text, ({ prefix, language, body }) =>
    (language === "quiz" || language === "mcq") && (every || (!submitted && isExamSource(body)))
      ? `${prefix}${MASKED_TEST}`
      : null,
  );
}

export type TestCommand ={ kind: "answer"; value: number } | { kind: "skip" } | { kind: "show" };

const SHOW = /^(?:next|next question|continue|go on|repeat|repeat (?:the )?question|show (?:the )?question)$/;
const SKIP = /^(?:skip|skip it|skip this|skip (?:this |the )?question)$/;
const ANSWER = /^(?:(?:option|answer|ans|my answer is|i choose|i pick)\s*[:-]?\s*)?\(?([a-h])\)?$/;

/**
 * A typed message that acts on the waiting test: a letter (`B`, `option c`, `(d)`), `skip`, or
 * `next` / `continue` / `repeat` (show the waiting question). Anything else (`explain`, a
 * question) is a message for the tutor. The chat applies it to its latest unfinished test only.
 */
export function parseTestCommand(text: string): TestCommand | null {
  const said = text.trim().toLowerCase().replace(/\s+/g, " ").replace(/[.!]+$/, "");
  if (SHOW.test(said)) return { kind: "show" };
  if (SKIP.test(said)) return { kind: "skip" };
  const letter = ANSWER.exec(said)?.[1];
  return letter ? { kind: "answer", value: LETTERS.indexOf(letter.toUpperCase()) } : null;
}
