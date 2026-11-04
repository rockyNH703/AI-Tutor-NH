import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import { AboutPage, FAQ } from "./help-pages";

/** The words a page shows, without markup. */
function words(html: string): string {
  return html
    .replace(/<[^>]+>/g, " ")
    .replace(/&#x27;/g, "'")
    .replace(/\s+/g, " ");
}

const faqText = (question: string) => {
  const item = FAQ.flatMap((group) => group.items).find((entry) => entry.question === question);
  return item ? words(renderToStaticMarkup(item.answer)) : "";
};

describe("what Help and About promise is true in production", () => {
  test("voice mode says the recording goes to a speech provider and is not stored", () => {
    const voice = faqText("How does voice mode work?");
    expect(voice).toContain("sent to our speech provider");
    expect(voice).toContain("never stores the recording");
  });

  test("About claims no added reference material or chat practice tests as a given, and lists memory and voice", () => {
    const about = words(renderToStaticMarkup(AboutPage()));
    expect(about).not.toContain("draw on reference material we've added");
    expect(about).not.toContain("practice tests in the chat");
    expect(about).toContain("Memories you save");
    expect(about).toContain("In voice mode, your recording");
  });
});
