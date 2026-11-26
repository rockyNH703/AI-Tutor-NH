import { Activity, Bot, Brain, ChevronDown, KeyRound, Lock, Mic, ShieldAlert, Trash2 } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { APP_NAME } from "@/lib/brand";
import { HelpTopics } from "./help-topics";

/**
 * /help and /about. Plain server-rendered pages, written to be true of the app as it is
 * today: when a feature changes, change its answer here in the same commit.
 */

function inline(href: string, text: string) {
  return (
    <Link href={href} className="focus-ring rounded font-semibold text-brand underline underline-offset-2">
      {text}
    </Link>
  );
}

export function PageFrame({
  title,
  intro,
  wide = false,
  children,
}: {
  title: string;
  intro: string;
  /** Room for two columns on wide screens (About: what it does beside your data). */
  wide?: boolean;
  children: ReactNode;
}) {
  return (
    <div className="h-full overflow-y-auto">
      <article className={`page-container flex flex-col gap-6${wide ? " page-wide" : ""}`}>
        <header>
          <h2 className="font-display text-2xl font-bold tracking-tight text-foreground">{title}</h2>
          <p className="mt-1 max-w-prose text-sm leading-relaxed text-text-secondary">{intro}</p>
        </header>
        {children}
      </article>
    </div>
  );
}

type Faq = { question: string; answer: ReactNode };

export const FAQ: readonly { topic: string; items: readonly Faq[] }[] = [
  {
    topic: `Using ${APP_NAME}`,
    items: [
      {
        question: `What can I ask ${APP_NAME}?`,
        answer: (
          <>
            Anything about networks, security and infrastructure: configuration, troubleshooting, reading logs and packet
            captures, hardening, cloud networking, and preparing for certifications such as CCNA, CCNP, Security+ or OSCP.
            Say which platform you're on, and ask follow-up questions in the same chat.
          </>
        ),
      },
      {
        question: "How do I get started?",
        answer: (
          <>
            Set up your profile in {inline("/settings", "Settings")}, so answers fit the platforms you work with. Then paste
            a config, log or question into the chat. Remove passwords, keys, community strings and other secrets before you
            paste anything.
          </>
        ),
      },
      {
        question: "How do I make the answers fit how I work?",
        answer: (
          <>
            Fill in your profile, {inline("/settings/learning", "Learning Preferences")} and{" "}
            {inline("/settings/tutor", "AI Assistant")}. The AI Assistant page shows what your settings add to its instructions. Your
            name, email address and photo are never sent to it.
          </>
        ),
      },
      {
        question: "How do I stop a reply?",
        answer: <>Press Stop while it's writing. Leaving the page or closing the tab stops it too.</>,
      },
      {
        question: `Can ${APP_NAME} read my files?`,
        answer: (
          <>
            Yes. Attach a PDF, Word, PowerPoint, Excel, CSV or text file (configs and logs included) or a screenshot of
            printed text with the paperclip, or add it in the {inline("/library", "File Library")}. You can send once a
            file shows Ready. {APP_NAME} then uses the files attached in that chat; from a large file it reads the parts
            that fit your question. You have 100 MB of storage; deleting a file frees its space. Handwriting, diagrams and
            charts are not read. To find what a file says, its text is sent to our AI provider, like your messages, so
            remove secrets from it first.
          </>
        ),
      },
      {
        question: "How does dictation work?",
        answer: (
          <>
            Press the microphone, speak, and your words appear in the message box; nothing is sent until you press Send.
            Speech is turned into text by your browser (Chrome and Edge use Google's service, Safari uses Apple's);{" "}
            {APP_NAME} never receives or stores your voice. Firefox does not support dictation, so the microphone isn't
            shown there.
          </>
        ),
      },
      {
        question: "How does voice mode work?",
        answer: (
          <>
            In voice mode you speak and the answer is read aloud. Unlike dictation, your recording is sent to our speech
            provider to turn it into text, and the answer is sent to it to be spoken; {APP_NAME} never stores the
            recording. Voice mode is in English, and where it's available its button is in the chat. Use dictation if
            you'd rather your voice stay with your browser.
          </>
        ),
      },
    ],
  },
  {
    topic: "Practice and progress",
    items: [
      {
        question: "What is Skills & Certifications?",
        answer: (
          <>
            Your record from the certification practice tests you take in the chat: today's plan, topics to review, topic
            mastery, missed questions and recent tests. Only you can see it.
          </>
        ),
      },
      {
        question: "What is Lab Activity?",
        answer: (
          <>
            The estimated time you spend in lab sessions. Start a session when you sit down to practise; time counts only
            while {APP_NAME} is on screen, in the chat or your files, and you're active. It's an estimate, not a measure of
            attention.
          </>
        ),
      },
      {
        question: "How is active time measured?",
        answer: (
          <>
            A minute counts when {APP_NAME} is open and on screen, and within the last two minutes you used it (typed,
            clicked, scrolled) or a reply was being written. At most one minute counts per minute, however many tabs or
            devices you use. It measures time in the app, not all of your work.
          </>
        ),
      },
      {
        question: "What is a practice streak?",
        answer: (
          <>
            The number of days in a row on which NetSec AI fully answered at least one real question of yours, or you
            answered 5 practice-test questions in the chat. Greetings, stopped or failed replies and regenerated answers
            don&apos;t count. Days follow India time (IST) for everyone. See yours in{" "}
            {inline("/settings/progress", "Practice & Goals")}.
          </>
        ),
      },
    ],
  },
  {
    topic: "Emails",
    items: [
      {
        question: "Will you send me emails?",
        answer: (
          <>
            Practice reminders and summaries are off until you turn them on in{" "}
            {inline("/settings/notifications", "Notifications")}, and every one has a link to stop it. Security emails
            are on: when your password changes and, unless you turn it off, when your account is signed in on a new
            device. The Notifications page says if sending emails is paused.
          </>
        ),
      },
    ],
  },
  {
    topic: "Your account",
    items: [
      {
        question: "How do I change my email address?",
        answer: (
          <>
            In {inline("/settings/profile", "Profile")}. You'll need your password, and a code we send to the new
            address.
          </>
        ),
      },
      {
        question: "I think someone else is using my account.",
        answer: (
          <>
            In {inline("/settings/privacy", "Privacy & Security")}, change your password, then sign out every other
            device. You'll see there which devices are signed in.
          </>
        ),
      },
      {
        question: "How do I delete my account?",
        answer: (
          <>
            In {inline("/settings/privacy", "Privacy & Security")}. It deletes your account, chats, settings and
            progress straight away, and it can't be undone. Copies in our backups are removed when those backups expire.
          </>
        ),
      },
    ],
  },
];

export function HelpPage() {
  return (
    <PageFrame title="Help & FAQ" intro={`Answers to common questions about ${APP_NAME}.`}>
      <HelpTopics topics={FAQ.map((group) => ({ label: group.topic, count: group.items.length }))}>
        {FAQ.map((group) => (
          <div key={group.topic} className="divide-y divide-border rounded-2xl border border-border bg-card">
            {group.items.map((item) => (
              <details key={item.question} className="group">
                <summary className="focus-ring flex cursor-pointer list-none items-center justify-between gap-3 rounded-2xl px-4 py-3 text-sm font-semibold text-foreground [&::-webkit-details-marker]:hidden">
                  {item.question}
                  <ChevronDown aria-hidden className="h-4 w-4 shrink-0 text-text-secondary transition-transform group-open:rotate-180" />
                </summary>
                <p className="px-4 pb-4 text-sm leading-relaxed text-text-secondary">{item.answer}</p>
              </details>
            ))}
          </div>
        ))}
      </HelpTopics>
      <p className="text-sm text-text-secondary">
        Didn't find your answer? {inline("/settings/help", "Contact support")}.
      </p>
    </PageFrame>
  );
}

/** What happens to your data, one line each: the detail lives in Help and Settings. */
const DATA: readonly { icon: typeof Lock; title: string; text: ReactNode }[] = [
  { icon: Lock, title: "Private to you", text: "Chats, settings and progress belong to your account; no other user sees them." },
  {
    icon: Bot,
    title: "What the AI is told",
    text: "Your profile and preferences, never your name, email or photo. Messages go to our AI provider for the answer.",
  },
  { icon: KeyRound, title: "Secrets", text: "Remove passwords and keys before you paste a config or log, or attach a file." },
  {
    icon: Brain,
    title: "Memory",
    text: <>Memories you save shape later answers; manage them in {inline("/settings/memory", "Memory")}.</>,
  },
  { icon: Mic, title: "Voice", text: "In voice mode, your recording and the answer go to our speech provider; the recording isn't stored." },
  { icon: Activity, title: "Active time", text: "Measured only from your own use, never compared with others." },
  {
    icon: Trash2,
    title: "Deleting",
    text: <>Delete your account and everything in it in {inline("/settings/privacy", "Privacy & Security")}.</>,
  },
];

export function AboutPage() {
  return (
    <PageFrame title={`About ${APP_NAME}`} intro="What it is, and how it treats your data." wide>
      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <section aria-labelledby="about-what" className="flex flex-col gap-3 text-sm leading-relaxed text-foreground sm:gap-4">
          <h3 id="about-what" className="font-display text-lg font-bold tracking-tight">
            What it does
          </h3>
          <p>
            An assistant for network, security and infrastructure engineers: configuration, troubleshooting, logs and
            captures, cloud networking and certification prep. It answers from its AI model and the files you attach, adapts
            to your profile, and cites reference material when it uses it.
          </p>
          <p className="flex gap-3 rounded-xl border border-accent-warm/30 bg-accent-warm-soft px-3.5 py-3">
            <ShieldAlert aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-foreground" />
            <span>
              An aid, not a replacement for your judgement: check answers against your platform's documentation before
              changing a production network.
            </span>
          </p>
          <p className="text-text-secondary">
            Questions? {inline("/help", "Help & FAQ")} or {inline("/settings/help", "contact support")}.
          </p>
        </section>
        <section aria-labelledby="about-data" className="rounded-2xl border border-border bg-card p-4 elev-1 sm:p-5">
          <h3 id="about-data" className="mb-2 font-display text-lg font-bold tracking-tight text-foreground">
            Your data
          </h3>
          <ul className="flex flex-col divide-y divide-border">
            {DATA.map((item) => (
              <li key={item.title} className="flex gap-3 py-2 last:pb-0 sm:py-2.5">
                <item.icon aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-brand" />
                <p className="text-sm leading-relaxed text-text-secondary">
                  <span className="font-bold text-foreground">{item.title}. </span>
                  {item.text}
                </p>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </PageFrame>
  );
}
