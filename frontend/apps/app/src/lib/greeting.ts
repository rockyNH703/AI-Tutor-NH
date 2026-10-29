/**
 * The greeting above a new chat's composer: one line of a fixed pool, picked at random for each
 * new chat and kept for it (the same chat shows the same line when the user comes back to it).
 * Nothing is fetched and nothing is stored in the browser.
 */

export const GREETINGS = [
  "What are we troubleshooting today?",
  "Paste a config, log or question.",
  "Which network are we working on?",
  "What are you building or securing today?",
  "Describe the symptom and the platform.",
] as const;

/**
 * A picker that gives each chat one greeting and keeps it, and never gives a new chat the
 * greeting the last one got.
 */
export function greetingPicker(pool: readonly string[] = GREETINGS, random: () => number = Math.random) {
  const chosen = new Map<string, string>();
  let last: string | null = null;
  return (chatKey: string): string => {
    const kept = chosen.get(chatKey);
    if (kept !== undefined) return kept;
    const choices = pool.length > 1 ? pool.filter((text) => text !== last) : pool;
    const text = choices[Math.min(choices.length - 1, Math.floor(random() * choices.length))] ?? GREETINGS[0];
    chosen.set(chatKey, text);
    last = text;
    return text;
  };
}

/** The page's picker: in memory only, for as long as the tab is open. */
export const pickGreeting = greetingPicker();
