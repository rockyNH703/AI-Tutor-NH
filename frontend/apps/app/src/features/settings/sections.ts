import {
  Bell,
  Bot,
  Brain,
  Clock,
  Flame,
  LifeBuoy,
  type LucideIcon,
  Network,
  Palette,
  ShieldCheck,
  SlidersHorizontal,
  Target,
  Timer,
  TrendingUp,
  UserRound,
} from "lucide-react";

export type SettingsSectionInfo = {
  slug: string;
  title: string;
  description: string;
  icon: LucideIcon;
  /** Other words people search for (Settings > Search). Lower case. */
  keywords: readonly string[];
};

/**
 * The Settings sections, in the product's order: Profile, Engineering Profile, Skills & Certification
 * Goals, Learning Preferences, AI Assistant, Memory, Practice & Lab Time, Practice & Goals, Lab
 * Tracking, Practice Streak, Notifications, Appearance & Accessibility, Privacy & Security, Help &
 * Support. Only sections that work end to end are listed; there are no placeholder pages.
 *
 * Slugs are addresses: `goals` and `subjects` kept theirs when they were renamed for engineers.
 * The Academic Profile's `academic` became `engineering`; next.config.mjs redirects the old one
 * (SECTION_REDIRECTS).
 */
export const SETTINGS_SECTIONS = [
  {
    slug: "profile",
    title: "Profile",
    description: "Your photo, name, time zone and email.",
    icon: UserRound,
    keywords: ["photo", "picture", "avatar", "display name", "email address", "time zone", "timezone"],
  },
  {
    slug: "engineering",
    title: "Engineering Profile",
    description: "Your role, experience, focus areas, platforms and certification goal.",
    icon: Network,
    keywords: [
      "role",
      "job",
      "experience",
      "seniority",
      "focus areas",
      "technologies",
      "platforms",
      "vendor",
      "cisco",
      "juniper",
      "arista",
      "palo alto",
      "fortinet",
      "aws",
      "azure",
      "cloud",
      "certification",
      "ccna",
      "ccnp",
      "ccie",
      "oscp",
      "cissp",
      "comptia",
    ],
  },
  {
    slug: "goals",
    title: "Skills & Certification Goals",
    description: "What you're working towards: a certification, an exam date and the topics you find hard.",
    icon: Target,
    keywords: ["main goal", "certification exam", "exam date", "target score", "difficult topics", "weak topics", "short-term goal", "long-term goal", "career"],
  },
  {
    slug: "learning",
    title: "Learning Preferences",
    description: "Language, depth, pace and examples in answers.",
    icon: SlidersHorizontal,
    keywords: ["hindi", "english", "reply language", "depth", "explanation", "difficulty", "real-world examples", "visual"],
  },
  {
    slug: "tutor",
    title: "AI Assistant",
    description: "How NetSec AI talks and explains.",
    icon: Bot,
    keywords: [
      "tone",
      "personality",
      "answer length",
      "hints",
      "step by step",
      "practice questions",
      "mistakes",
      "key concepts",
      "what the assistant is told",
      "tutor",
    ],
  },
  {
    slug: "memory",
    title: "Memory",
    description: "Review and manage what NetSec AI remembers.",
    icon: Brain,
    keywords: ["remember", "memories", "proposals", "automatic", "personalization", "export", "forget"],
  },
  {
    slug: "subjects",
    title: "Practice & Lab Time",
    description: "Your daily and weekly practice goals, and when you like to practise.",
    icon: Clock,
    keywords: ["daily goal", "weekly goal", "practice time", "lab time", "preferred time", "minutes", "active time", "schedule"],
  },
  {
    slug: "progress",
    title: "Practice & Goals",
    description: "Your active time, questions and goals.",
    icon: TrendingUp,
    keywords: ["progress", "chart", "goals met", "lab time", "study time", "active time"],
  },
  {
    slug: "study-tracking",
    title: "Lab Tracking",
    description: "What counts as lab time: idle, hidden tabs and pauses.",
    icon: Timer,
    keywords: ["lab session", "lab activity", "study session", "idle", "inactivity", "pause", "resume", "hidden tab", "estimate"],
  },
  {
    slug: "streak",
    title: "Practice Streak",
    description: "Your practice streak, today's practice and your calendar.",
    icon: Flame,
    keywords: ["streak", "calendar", "heatmap", "practice days", "milestone", "longest streak", "header"],
  },
  {
    slug: "notifications",
    title: "Notifications",
    description: "Practice reminders and sign-in alerts by email.",
    icon: Bell,
    keywords: ["daily reminder", "streak reminder", "weekly summary", "security alerts", "unsubscribe", "in the app", "bell"],
  },
  {
    slug: "appearance",
    title: "Appearance & Accessibility",
    description: "Theme, text size, contrast, motion and reading.",
    icon: Palette,
    keywords: ["dark mode", "light mode", "system", "font size", "high contrast", "reduce motion", "animation", "reading comfort"],
  },
  {
    slug: "privacy",
    title: "Privacy & Security",
    description: "Password, signed-in devices and deleting your account.",
    icon: ShieldCheck,
    keywords: ["change password", "sessions", "sign out", "log out", "other devices", "delete account", "data"],
  },
  {
    slug: "help",
    title: "Help & Support",
    description: "Answers, and a way to reach us.",
    icon: LifeBuoy,
    keywords: ["contact", "faq", "question", "bug", "problem", "feedback", "about", "terms", "privacy policy"],
  },
] as const satisfies readonly SettingsSectionInfo[];

export type SettingsSection = (typeof SETTINGS_SECTIONS)[number];
export type SectionSlug = SettingsSection["slug"];

export const DEFAULT_SECTION: SectionSlug = "profile";

/** Old section addresses and where they went (next.config.mjs redirects them; sections.test.ts checks both). */
export const SECTION_REDIRECTS: Readonly<Record<string, SectionSlug>> = { academic: "engineering" };

export function findSection(slug: string | undefined): SettingsSection | undefined {
  return SETTINGS_SECTIONS.find((section) => section.slug === slug);
}

/**
 * Settings > Search: the sections that match every word of `query` (in any case) somewhere in
 * their title, description or keywords, in the usual order. An empty query matches them all.
 */
export function matchSections(query: string): SettingsSection[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return [...SETTINGS_SECTIONS];
  return SETTINGS_SECTIONS.filter((section) => {
    const text = [section.title, section.description, ...section.keywords].join(" ").toLowerCase();
    return words.every((word) => text.includes(word));
  });
}
