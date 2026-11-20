import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import { NhprepMarkdown, isNhprepHost } from "./nhprep-markdown";

const html = (text: string) => renderToStaticMarkup(<NhprepMarkdown text={text} />);
const hrefs = (markup: string) => [...markup.matchAll(/href="([^"]*)"/g)].map((match) => match[1]);

describe("NhprepMarkdown", () => {
  test("nhprep.com and every subdomain are hosts; lookalikes are not", () => {
    expect(isNhprepHost("https://nhprep.com/course/x")).toBe(true);
    expect(isNhprepHost("https://www.NHprep.com/x")).toBe(true);
    expect(isNhprepHost("https://videos.nhprep.com./a.mp4")).toBe(true);
    expect(isNhprepHost("https://nhprep.com.evil.tld/")).toBe(false);
    expect(isNhprepHost("https://evilnhprep.com/")).toBe(false);
    expect(isNhprepHost("not a url")).toBe(false);
  });

  test("never links to NHprep: Markdown links, bare URLs, www. and reference links all stay text", () => {
    const markup = html(
      [
        "See [the course](https://nhprep.com/course/x).",
        "Watch it at https://www.nhprep.com/course/y or www.nhprep.com/course/z.",
        "Also [ref][1].",
        "",
        "[1]: https://learn.nhprep.com/a",
      ].join("\n"),
    );
    expect(hrefs(markup).filter((href) => href?.includes("nhprep.com"))).toEqual([]);
    expect(markup).toContain("the course");
    expect(markup).toContain("ref");
  });

  test("other http links still open in a new tab; javascript: links are dropped", () => {
    const markup = html("[RFC 2328](https://www.rfc-editor.org/rfc/rfc2328) and [x](javascript:alert(1)) and [y](https://nhprep.com.evil.tld/)");
    expect(hrefs(markup)).toEqual(["https://www.rfc-editor.org/rfc/rfc2328", "https://nhprep.com.evil.tld/"]);
    expect(markup).toContain('rel="noopener noreferrer nofollow"');
    expect(markup).not.toContain("javascript:");
  });

  test("images are never loaded or linked", () => {
    const markup = html("![topology](https://nhprep.com/img.png) ![other](https://example.com/a.png)");
    expect(markup).not.toContain("<img");
    expect(hrefs(markup)).toEqual([]);
    expect(markup).toContain("[Image: topology]");
  });

  test("raw HTML is text, and a widget fence stays plain code (no tutor syntax, no widgets)", () => {
    const markup = html(
      '<script>alert(1)</script>\n\n:::note[Hi]\nbody\n:::\n\n```video\n{"youtube":"dQw4w9WgXcQ","title":"x"}\n```\n\n$x$',
    );
    expect(markup).not.toContain("<script");
    expect(markup).toContain("&lt;script&gt;");
    expect(markup).not.toContain("data-card");
    expect(markup).not.toContain("Preparing");
    expect(markup).toContain("dQw4w9WgXcQ"); // the fence's text, shown as code
    expect(markup).not.toContain("katex");
  });
});
