import { describe, expect, test } from "bun:test";
import type { ComponentPropsWithoutRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";

import { NOTES, SpeechChunker, speak } from "@/features/voice/speech-text";
import { type SheetNode, sectionize, sheetFilename, sheetMarkdown } from "./cheatsheet";
import { MessageContent } from "./message-content";
import { remarkTutor } from "./remark-tutor";
import { sanitizeSchema } from "./sanitize-schema";

const render = (content: string) => renderToStaticMarkup(<MessageContent content={content} />);

describe("Markdown rendering", () => {
  test("paragraphs, emphasis and headings (shifted below the page's own)", () => {
    const html = render("# Title\n\nSome **bold** and *italic* text.\n\n## Sub");
    expect(html).toContain("<h3");
    expect(html).toContain("<h4");
    expect(html).not.toContain("<h1");
    expect(html).toContain("<strong>bold</strong>");
    expect(html).toContain("<em>italic</em>");
  });

  test("lists and blockquotes", () => {
    const html = render("- one\n- two\n\n1. first\n2. second\n\n> quoted");
    expect(html).toContain("<ul");
    expect(html).toContain("<ol");
    expect(html.match(/<li/g)?.length).toBe(4);
    expect(html).toContain("<blockquote");
  });

  test("links open safely in a new tab", () => {
    const html = render("[docs](https://example.com/docs) and <https://auto.example.com>");
    expect(html).toContain('href="https://example.com/docs"');
    expect(html).toContain('target="_blank"');
    expect(html).toContain('rel="noopener noreferrer nofollow"');
    expect(html).toContain('href="https://auto.example.com"');
  });

  test("GFM tables render as tables inside one focusable scroll container", () => {
    const html = render("| a | b |\n|---|---|\n| 1 | 2 |");
    expect(html).toContain("<table");
    expect(html).toContain("<th");
    expect(html).toContain("<td");
    // Keyboard users can scroll a wide table, and there is one scroller, not two.
    expect(html).toContain('tabindex="0" role="region" aria-label="Table"');
    expect(html.match(/overflow-x-auto/g)).toHaveLength(1);
  });

  test("fenced code: language label, highlighting and a copy button", () => {
    const html = render("```ts\nconst answer: number = 42;\n```");
    expect(html).toContain("<pre");
    expect(html).toContain("language-ts");
    expect(html).toContain("hljs");
    expect(html).toContain('aria-label="Copy code"');
    expect(html).toContain(">ts<");
    // A long line scrolls inside the block, so the block must take keyboard focus.
    expect(html).toContain('<pre tabindex="0" role="region" aria-label="ts code"');
  });

  test("a fence without a language is still a block, not inline code", () => {
    const html = render("```\nplain block\n```");
    expect(html).toContain("<pre");
    expect(html).toContain(">code<");
    expect(html).not.toContain("rounded-md bg-surface-2 px-1.5");
  });

  test("inline code is styled inline", () => {
    const html = render("Run `npm test` now.");
    expect(html).toContain("rounded-md bg-surface-2 px-1.5");
    expect(html).not.toContain("<pre");
  });

  test("inline and display maths render with KaTeX", () => {
    const inline = render("Euler: $e^{i\\pi} + 1 = 0$");
    expect(inline).toContain('class="katex"');
    expect(inline).not.toContain("katex-error");
    expect(inline).not.toContain("katex-display");
    const display = render("$$\n\\frac{a}{b}\n$$");
    expect(display).toContain("katex-display");
    expect(display).not.toContain("katex-error");
  });

  test("invalid maths does not break the reply", () => {
    const html = render("Bad: $\\frac{$ and after");
    expect(html).toContain("after");
  });
});

describe("safety", () => {
  test("raw HTML is shown as text, never as markup", () => {
    const html = render('Hello <script>alert(1)</script> <b>bold?</b> <img src=x onerror="alert(1)"> <iframe src="https://x"></iframe>');
    expect(html).not.toContain("<script");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("<iframe");
    expect(html).not.toContain("<b>");
    expect(html).toContain("&lt;script&gt;");
    expect(html).toContain("&lt;b&gt;bold?&lt;/b&gt;");
  });

  test("javascript:, data: and relative links lose their href", () => {
    for (const markdown of [
      "[x](javascript:alert(1))",
      "[x](JaVaScRiPt:alert(1))",
      "[x](data:text/html;base64,PHNjcmlwdD4=)",
      "[x](/settings)",
      "[x](vbscript:msgbox)",
    ]) {
      const html = render(markdown);
      expect(html).not.toContain("href=");
      expect(html.toLowerCase()).not.toContain("javascript:");
    }
  });

  test("mailto links are allowed", () => {
    expect(render("[mail](mailto:a@example.com)")).toContain('href="mailto:a@example.com"');
  });

  test("images become links; nothing is loaded", () => {
    const html = render("![a chart](https://tracker.example/pixel.png)");
    expect(html).not.toContain("<img");
    expect(html).toContain('href="https://tracker.example/pixel.png"');
    expect(html).toContain("[Image: a chart]");
  });

  test("KaTeX cannot create links or run macros that need trust", () => {
    const html = render("$\\href{javascript:alert(1)}{click}$ $\\url{javascript:alert(2)}$");
    expect(html).not.toContain("<a ");
    expect(html.toLowerCase()).not.toContain('href="javascript:');
  });

  test("attribute injection through code fence language is neutralised", () => {
    const html = render('```js" onmouseover="alert(1)\nx\n```');
    expect(html).not.toContain("onmouseover=");
  });
});

const SHEET = [
  "::::cheatsheet[OSPF timers]",
  "Defaults on broadcast links.",
  "",
  "## Hello and dead",
  "Hello every **10 s**, dead after 40 s.",
  "",
  "### NBMA",
  "Hello every 30 s.",
  "",
  "## Commands",
  "```",
  "show ip ospf interface",
  "```",
  "",
  ":::note",
  "Timers must match on both ends.",
  ":::",
  "::::",
].join("\n");

describe("cheat sheets", () => {
  test("a sheet renders its title, open sections under heading buttons, and the header actions", () => {
    const html = render(SHEET);
    expect(html).toContain('data-card="cheatsheet"');
    expect(html).toContain("Cheat sheet");
    expect(html).toContain("OSPF timers");
    // Three sections (## Hello and dead, ### NBMA, ## Commands), all open; each heading holds its button.
    expect(html.match(/aria-expanded="true"/g)).toHaveLength(3);
    expect(html).not.toContain('aria-expanded="false"');
    expect(html).not.toContain("cheatsheet-closed");
    expect(html).toMatch(/<h4[^>]*><button type="button" aria-expanded="true" aria-controls="[^"]+"/);
    expect(html).toMatch(/<h5[^>]*><button type="button" aria-expanded="true"/);
    expect(html).toContain(`id="${aria("aria-controls", html)}"`);
    expect(html).toContain(">Collapse all<");
    expect(html).toContain('aria-label="Copy as Markdown"');
    expect(html).toContain('aria-label="Download .md"');
    expect(html).toContain('aria-label="Print cheat sheet"');
    expect(html).toContain('aria-live="polite"');
    // The study card inside is still a card (drawn flat by markdown.css), and the code keeps its block.
    expect(html).toContain('data-card="note"');
    expect(html).toContain('aria-label="Copy code"');
  });

  test("hostile Markdown in a sheet stays text and loads nothing", () => {
    const html = render(
      [
        ":::cheatsheet[<img src=x onerror=alert(1)>]",
        "## <script>alert(1)</script>",
        '<iframe src="https://evil.example/frame"></iframe>',
        "",
        "![pixel](https://tracker.example/p.png)",
        "",
        "[x](javascript:alert(1)) and <b>bold?</b>",
        ":::",
      ].join("\n"),
    );
    expect(html).toContain('data-card="cheatsheet"');
    for (const tag of ["<script", "<img", "<iframe", "<b>"]) expect(html).not.toContain(tag);
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(html).toContain("&lt;img src=x onerror=alert(1)&gt;");
    expect(html).not.toMatch(/\ssrc="/);
    expect(html.toLowerCase()).not.toContain('href="javascript:');
    expect(html).not.toContain('href="https://evil.example');
  });

  test("an unknown container, and a sheet without sections, still render as a plain div", () => {
    for (const markdown of [":::foo[Bar]\nSome text.\n:::", ":::cheatsheet[Bar]\n- 22 SSH\n- 23 Telnet\n:::"]) {
      const html = render(markdown);
      expect(html).toContain('<div><p class="my-3 first:mt-0 last:mb-0">Bar</p>');
      expect(html).not.toContain("data-card");
      expect(html).not.toContain("aria-expanded");
    }
  });

  test("sections nest by heading level, in reading order", () => {
    const levels: Record<string, number> = { A: 2, B: 3, C: 2, X: 3 };
    const { parts, count } = sectionize(["intro", "A", "a", "B", "b", "C", "c"], (item) => levels[item] ?? null);
    expect(count).toBe(3);
    expect(parts).toEqual([
      { kind: "item", item: "intro" },
      {
        kind: "section",
        id: 0,
        heading: "A",
        parts: [
          { kind: "item", item: "a" },
          { kind: "section", id: 1, heading: "B", parts: [{ kind: "item", item: "b" }] },
        ],
      },
      { kind: "section", id: 2, heading: "C", parts: [{ kind: "item", item: "c" }] },
    ]);
    // A `###` before any `##` is a section of its own, and the next `##` is its sibling.
    expect(sectionize(["X", "x", "A"], (item) => levels[item] ?? null).parts.map((part) => part.kind)).toEqual([
      "section",
      "section",
    ]);
  });

  test("Copy and Download take the sheet's own Markdown: its title, then its body as written", () => {
    const text = "::::cheatsheet[OSPF *timers*]\n## Hello\nEvery **10 s** [1].\n\n## Commands\n```\nshow ip ospf\n```\n::::";
    let sheet: SheetNode | undefined;
    const plugins = [remarkTutor] as NonNullable<ComponentPropsWithoutRef<typeof ReactMarkdown>["remarkPlugins"]>;
    renderToStaticMarkup(
      <ReactMarkdown
        remarkPlugins={plugins}
        rehypePlugins={[[rehypeSanitize, sanitizeSchema]]}
        components={{
          div: ({ node, children }) => {
            if (node?.properties?.dataSheet === "true") sheet = node as SheetNode;
            return <div>{children}</div>;
          },
        }}
      >
        {text}
      </ReactMarkdown>,
    );
    expect(sheet).toBeDefined();
    expect(sheetMarkdown(text, sheet)).toBe("# OSPF timers\n\n## Hello\nEvery **10 s** [1].\n\n## Commands\n```\nshow ip ospf\n```\n");
    expect(sheetMarkdown(text, undefined)).toBe("\n");
  });

  test("a download is named after the sheet", () => {
    expect(sheetFilename("OSPF timers & areas")).toBe("ospf-timers-areas.md");
    expect(sheetFilename("Ünïcode — 802.1Q trunks")).toBe("unicode-802-1q-trunks.md");
    expect(sheetFilename("  ✓ ")).toBe("cheat-sheet.md");
    expect(sheetFilename("x".repeat(80))).toBe(`${"x".repeat(60)}.md`);
  });

  test("voice mode names a cheat sheet, a video and a topology instead of reading them", () => {
    const sheet = speak(`Here are the timers.\n\n${SHEET}`).join(" ");
    expect(sheet).toContain(NOTES.cheatsheet);
    expect(sheet).not.toContain("10 s");
    expect(speak('Watch this first.\n\n```video\n{"youtube": "dQw4w9WgXcQ", "title": "OSPF"}\n```').join(" ")).toContain(NOTES.video);
    expect(speak("The lab network.\n\n```topology\n{}\n```").join(" ")).toContain(NOTES.diagram);

    // While a sheet is still arriving none of it is spoken; once finished it is named, never read.
    const chunker = new SpeechChunker();
    const arriving = "Look at this sheet, it covers the timers well.\n\n:::cheatsheet[OSPF]\n## Hello\nHello every ten seconds. Dead after forty.";
    const early = chunker.next(arriving).map((chunk) => chunk.text);
    expect(early.join(" ")).not.toContain("seconds");
    const late = chunker.finish(`${arriving}\n:::`).map((chunk) => chunk.text);
    expect(late.join(" ")).toContain(NOTES.cheatsheet);
    expect([...early, ...late].join(" ")).not.toContain("forty");
  });
});

/** The value of the first `name="…"` attribute in `html`. */
function aria(name: string, html: string): string {
  return new RegExp(`${name}="([^"]+)"`).exec(html)?.[1] ?? "missing";
}
