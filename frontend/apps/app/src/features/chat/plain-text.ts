import { replaceFences } from "@/features/markdown/assessment";

/** What a drawn block is called instead of reading its source (a practice test's would give its answers). */
const FENCE_NAMES = new Map([
  ["quiz", "(practice test)"],
  ["mcq", "(practice test)"],
  ["fill-blank", "(practice test)"],
  ["question-bank", "(practice test)"],
  ["flashcards", "(flashcards)"],
  ["video", "(video)"],
  ["topology", "(network diagram)"],
  ["diagram", "(diagram)"],
  ["mermaid", "(diagram)"],
  ["chart", "(chart)"],
  ["graph", "(chart)"],
]);

/**
 * Roughly de-Markdown a reply for the screen-reader announcement.
 *
 * Only for `aria-live` text: the reply itself is rendered properly on screen and
 * can be navigated in full. This just keeps the announcement from reading out
 * asterisks, hashes and URLs, or any fenced block's source.
 */
export function plainText(markdown: string, limit = 1000): string {
  const text = replaceFences(markdown, ({ language }) => ` ${FENCE_NAMES.get(language) ?? "(code)"} `)
    // Study cards read as their titles; card fences, ::file lines and alert markers not at all.
    .replace(/^\s{0,3}:{3,}\s*[\w-]+(?:\[([^\]\n]*)\])?[^\n]*$/gm, "$1")
    .replace(/^\s{0,3}:{2,}[^\n]*$/gm, "")
    .replace(/\[!(?:NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]/gi, "")
    .replace(/`([^`]*)`/g, "$1")
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s*>\s?/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/(\*\*|__|\*|_|~~)(.+?)\1/g, "$2")
    .replace(/\$\$?([^$]+)\$\$?/g, "$1")
    .replace(/\|/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return text.length > limit ? `${text.slice(0, limit).trimEnd()}…` : text;
}
