/**
 * What each learning choice is called in the app. Every map is keyed by the contract's own
 * enum (lib/api/contract.gen.ts), so a value the API adds is a compile error here until it has
 * a name, rather than a blank on screen.
 */

import type { LearningPreferenceField, LearningPreferenceValues, MainGoal } from "@/lib/api/types";

/**
 * The main goal, said for engineers. The codes are the API's and permanent (stored goals keep
 * them); only what is shown changed when the app became NetSec AI.
 */
export const MAIN_GOAL_LABELS: Record<MainGoal, string> = {
  board_exams: "Pass a certification exam",
  competitive_exam: "Prepare for an advanced certification (CCNP, CCIE, OSCP)",
  better_marks: "Improve my practice scores",
  understand_concepts: "Understand protocols and technologies in depth",
  homework_help: "Get help with lab and work tasks",
  other: "Something else",
};

type Options<K extends LearningPreferenceField> = Record<LearningPreferenceValues[K], string>;

export type PreferenceInfo<K extends LearningPreferenceField> = {
  label: string;
  description: string;
  options: Options<K>;
};

const NONE = "No preference";

export const PREFERENCES: { [K in LearningPreferenceField]: PreferenceInfo<K> } = {
  language: {
    label: "Reply language",
    description: "The language NetSec AI answers in, unless you ask it for another one. Hindi and Hinglish turn voice replies off.",
    options: { auto: NONE, english: "English", hindi: "Hindi", hinglish: "Hinglish" },
  },
  explanationLevel: {
    label: "Depth",
    description: "How deep answers go: plain terms, working knowledge assumed, or protocol internals.",
    options: { auto: NONE, simple: "Simple", standard: "Standard", detailed: "Detailed" },
  },
  pace: {
    label: "Pace",
    description: "How quickly answers move through a topic.",
    options: { auto: NONE, slow: "Slow", steady: "Steady", fast: "Fast" },
  },
  difficulty: {
    label: "Difficulty",
    description: "How hard examples and practice problems are.",
    options: { auto: NONE, easy: "Easy", medium: "Medium", hard: "Hard" },
  },
  realWorldExamples: {
    label: "Real-world examples",
    description: "Connecting ideas to real networks and systems.",
    options: { auto: NONE, more: "More", fewer: "Fewer" },
  },
  visualExplanations: {
    label: "Visual explanations",
    description: "Tables, step lists and diagrams.",
    options: { auto: NONE, more: "More", fewer: "Fewer" },
  },
  personality: {
    label: "Tone",
    description: "How NetSec AI talks to you.",
    options: { auto: NONE, friendly: "Friendly", encouraging: "Encouraging", professional: "Professional" },
  },
  responseLength: {
    label: "Answer length",
    description: "How long answers are, unless you ask for more or less.",
    options: { auto: NONE, short: "Short", medium: "Medium", long: "Long" },
  },
  answerMode: {
    label: "How it answers problems",
    description:
      "Straight answers, a hint first, or guiding questions that lead you there. Hints and guiding questions apply to practice problems and labs, never live incidents.",
    options: { auto: NONE, direct: "Answer directly", hints_first: "Hint first", socratic: "Guiding questions" },
  },
  stepByStep: {
    label: "Step-by-step",
    description: "Procedures as numbered steps, each with its exact command and how to check it worked.",
    options: { auto: NONE, on: "Always", off: "Only when I ask" },
  },
  askBeforeAnswer: {
    label: "Check what I know first",
    description: "One short question about what you already know before a new topic.",
    options: { auto: NONE, on: "Yes", off: "No" },
  },
  practiceQuestions: {
    label: "Practice questions",
    description: "A question or two to try after an explanation.",
    options: { auto: NONE, on: "Yes", off: "Only when I ask" },
  },
  mistakeCorrection: {
    label: "Correcting mistakes",
    description: "How NetSec AI points out what went wrong.",
    options: { auto: NONE, gentle: "Gently", direct: "Directly" },
  },
  highlightKeyConcepts: {
    label: "Highlight key ideas",
    description: "Key terms in bold and a one-line summary at the end.",
    options: { auto: NONE, on: "Yes", off: "No" },
  },
};

/**
 * The preferences that reach the assistant (the API's app/learning/tutor.py): all 14 since session
 * 62 (the page split is MAIN_FIELDS, below).
 */
export const ASSISTANT_FIELDS = [
  "language",
  "explanationLevel",
  "pace",
  "difficulty",
  "realWorldExamples",
  "visualExplanations",
  "personality",
  "responseLength",
  "answerMode",
  "stepByStep",
  "askBeforeAnswer",
  "practiceQuestions",
  "mistakeCorrection",
  "highlightKeyConcepts",
] as const satisfies readonly LearningPreferenceField[];

export function reachesAssistant(field: LearningPreferenceField): boolean {
  return (ASSISTANT_FIELDS as readonly LearningPreferenceField[]).includes(field);
}

/**
 * The three choices each settings page shows first; the rest (also used) sit under "More preferences", so each
 * page stays about one screen.
 */
export const MAIN_FIELDS = [
  "language",
  "explanationLevel",
  "visualExplanations",
  "responseLength",
  "answerMode",
  "stepByStep",
] as const satisfies readonly LearningPreferenceField[];

export function isMainField(field: LearningPreferenceField): boolean {
  return (MAIN_FIELDS as readonly LearningPreferenceField[]).includes(field);
}

/** The Learning Preferences section, then the AI Assistant section: every field exactly once. */
export const LEARNING_FIELDS = [
  "language",
  "explanationLevel",
  "pace",
  "difficulty",
  "realWorldExamples",
  "visualExplanations",
] as const satisfies readonly LearningPreferenceField[];

export const TUTOR_FIELDS = [
  "personality",
  "responseLength",
  "answerMode",
  "stepByStep",
  "askBeforeAnswer",
  "practiceQuestions",
  "mistakeCorrection",
  "highlightKeyConcepts",
] as const satisfies readonly LearningPreferenceField[];
