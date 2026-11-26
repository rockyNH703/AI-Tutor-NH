"use client";

import { Fragment, useLayoutEffect, useRef, useState } from "react";

import { placeText } from "@/features/programs/program-nav";
import { ProgramWelcome } from "@/features/programs/program-welcome";
import type { ChatPlace } from "@/lib/api/types";
import { accentWord, pickGreeting } from "@/lib/greeting";

/**
 * What a new, empty chat shows above the composer: a greeting picked at random from a fixed pool,
 * kept for this chat (lib/greeting.ts), and nothing else. It sits at the bottom of the transcript's
 * space, so it is directly above the composer, which the chat view centres while the chat is empty.
 *
 * Set the way Networkers Home sets its headlines: Lato Black in ink, with the one key word in NH red
 * (solid colour, no gradient text; NH red clears 4.87:1 light and 6.35:1 dark). It is
 * one heading with the whole sentence, so a screen reader reads it once, and it arrives once as a
 * line (reduced motion: at once). It is one line on every screen: the CSS size is the largest, and
 * a sentence too wide for it is set smaller (fitOneLine).
 *
 * A program chat (`place`) greets with its own place instead ("What shall we learn in Month 2?"), and
 * under it says what the tutor answers from, with a suggestion or two (features/programs/program-welcome.tsx).
 */
export function ChatEmptyState({
  chatKey,
  place = null,
}: { chatKey: string; place?: ChatPlace | null }) {
  // Picked on the client: the server cannot know the random pick, and a mismatch would flash.
  const [greeting, setGreeting] = useState<string | null>(null);
  const placeGreeting = place ? placeText(place, null).greeting : null;
  useLayoutEffect(
    () => setGreeting(placeGreeting ?? pickGreeting(chatKey)),
    [chatKey, placeGreeting],
  );
  const ref = useOneLine(greeting);
  const accent = greeting === null ? null : accentWord(greeting);

  // The chat column (.chat-column), like the messages and the composer. 24px above the composer's own
  // 8px: 32px from the greeting to the box.
  return (
    <div className="chat-column flex flex-1 flex-col justify-end pb-6 pt-10">
      <h2
        key={greeting ?? ""}
        ref={ref}
        data-greeting=""
        className="animate-greeting-in text-balance text-center font-display text-display font-black text-foreground"
      >
        {greeting === null ? (
          <span className="invisible">&nbsp;</span>
        ) : (
          greeting.split(" ").map((word, index) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: the words of one fixed sentence, keyed with it
            <Fragment key={index}>
              {index > 0 && " "}
              <span
                data-greeting-word=""
                className={index === accent ? "text-signal" : undefined}
              >
                {word}
              </span>
            </Fragment>
          ))
        )}
      </h2>
      {place && <ProgramWelcome place={place} />}
    </div>
  );
}

/** The smallest the greeting is set to stay on one line; a longer one wraps (balanced) at this size. */
const SMALLEST_PX = 12;

/**
 * Shrinks the heading's font in proportion when the sentence is wider than the heading, then measures again: glyph
 * widths do not scale exactly with the size (hinting and rounding differ by platform; Linux rounds more than Windows),
 * so a pass that left the last word over the edge takes off what is still too wide. At most three passes.
 */
export function fitOneLine(heading: HTMLElement) {
  heading.style.fontSize = "";
  heading.style.whiteSpace = "nowrap";
  let size = Number.parseFloat(getComputedStyle(heading).fontSize);
  for (let pass = 0; pass < 3 && heading.scrollWidth > heading.clientWidth && size > SMALLEST_PX; pass += 1) {
    // 1% to spare, so rounding never pushes the last word onto a second line.
    size = Math.max(SMALLEST_PX, (size * heading.clientWidth * 0.99) / heading.scrollWidth);
    heading.style.fontSize = `${size}px`;
  }
  heading.style.whiteSpace = "";
}

/** Keeps the greeting on one line: now, whenever the heading resizes, and once the web font arrives. */
function useOneLine(greeting: string | null) {
  const ref = useRef<HTMLHeadingElement>(null);
  useLayoutEffect(() => {
    const heading = ref.current;
    if (!heading || greeting === null) return;
    const fit = () => fitOneLine(heading);
    fit();
    let mounted = true;
    const observer = new ResizeObserver(fit);
    observer.observe(heading);
    void document.fonts?.ready.then(() => mounted && fit());
    return () => {
      mounted = false;
      observer.disconnect();
    };
  }, [greeting]);
  return ref;
}
