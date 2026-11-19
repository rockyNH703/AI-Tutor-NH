import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import Video, { VideoBlock, embedUrl, formatTime, sandboxFor, watchUrl, youtubeId } from "./video";

const ID = "dQw4w9WgXcQ";
const BLOCK = {
  youtube: `https://youtu.be/${ID}`,
  title: "OSPF in ten minutes",
  channel: "Lab Notes",
  start: 30,
  chapters: [
    { at: 0, title: "Neighbours" },
    { at: 135, title: "OSPF areas" },
  ],
  captions: "en",
};

describe("youtubeId", () => {
  test("takes a bare id and the YouTube link forms", () => {
    for (const value of [
      ID,
      `https://www.youtube.com/watch?v=${ID}&t=30s`,
      `https://youtube.com/watch?v=${ID}`,
      `https://m.youtube.com/watch?v=${ID}`,
      `http://www.youtube.com/watch?v=${ID}`,
      `https://youtu.be/${ID}?si=abc`,
      `https://www.youtube.com/embed/${ID}`,
      `https://www.youtube.com/shorts/${ID}`,
      `https://www.youtube.com/live/${ID}`,
      `https://www.youtube-nocookie.com/embed/${ID}`,
    ]) {
      expect(youtubeId(value)).toBe(ID);
    }
  });

  test("refuses bad ids, other hosts and other schemes", () => {
    for (const value of [
      "",
      "dQw4w9WgXc", // 10 characters
      "dQw4w9WgXcQQ", // 12
      "dQw4w9WgX/Q",
      "dQw4w9WgX Q",
      `https://evil.example/watch?v=${ID}`,
      `https://youtube.com.evil.example/watch?v=${ID}`,
      `https://www.youtube.com/watch?v=${ID}x`,
      "https://www.youtube.com/watch?v=../../x",
      `https://www.youtube.com/playlist?list=${ID}`,
      `https://www.youtube.com/embed/${ID}/../x`,
      `javascript:alert(1)//youtu.be/${ID}`,
      `ftp://youtu.be/${ID}`,
      "https://youtu.be/",
    ]) {
      expect(youtubeId(value)).toBeNull();
    }
  });
});

describe("the ```video validator", () => {
  test("takes a full block and gives the id", () => {
    const parsed = VideoBlock.safeParse(BLOCK);
    expect(parsed.success).toBe(true);
    expect(parsed.data?.youtube).toBe(ID);
    expect(VideoBlock.safeParse({ youtube: ` ${ID} `, title: "T" }).data?.youtube).toBe(ID);
  });

  test("refuses bad ids, captions that smuggle parameters, and out-of-range times", () => {
    const bad = [
      { ...BLOCK, youtube: "not-an-id" },
      { ...BLOCK, youtube: `https://vimeo.com/${ID}` },
      { ...BLOCK, captions: "en&list=x" },
      { ...BLOCK, captions: "EN" },
      { ...BLOCK, captions: "english" },
      { ...BLOCK, captions: "en-" },
      { ...BLOCK, start: -1 },
      { ...BLOCK, start: 1.5 },
      { ...BLOCK, start: 86_401 },
      { ...BLOCK, chapters: [{ at: -5, title: "x" }] },
      { ...BLOCK, chapters: [{ at: 5, title: "" }] },
      { ...BLOCK, title: "" },
      { youtube: ID },
    ];
    for (const block of bad) expect(VideoBlock.safeParse(block).success).toBe(false);
    for (const captions of ["en", "pt-BR", "zh-Hant", "fil"]) {
      expect(VideoBlock.safeParse({ ...BLOCK, captions }).success).toBe(true);
    }
  });
});

describe("the URLs", () => {
  test("the embed is privacy-enhanced, autoplaying inline, with start and captions only when given", () => {
    expect(embedUrl({ youtube: ID })).toBe(`https://www.youtube-nocookie.com/embed/${ID}?autoplay=1&playsinline=1&rel=0`);
    expect(embedUrl({ youtube: ID, captions: "pt-BR" }, 135)).toBe(
      `https://www.youtube-nocookie.com/embed/${ID}?autoplay=1&playsinline=1&rel=0&start=135&cc_load_policy=1&cc_lang_pref=pt-BR`,
    );
  });

  test("a value that got past validation is still encoded, never pasted in", () => {
    const url = new URL(embedUrl({ youtube: ID, captions: "en&list=x" }));
    expect(url.searchParams.get("cc_lang_pref")).toBe("en&list=x");
    expect(url.searchParams.has("list")).toBe(false);
  });

  test("Open on YouTube", () => {
    expect(watchUrl(ID)).toBe(`https://www.youtube.com/watch?v=${ID}`);
    expect(watchUrl(ID, 30)).toBe(`https://www.youtube.com/watch?v=${ID}&t=30s`);
  });

  test("times read like a player's", () => {
    expect(formatTime(0)).toBe("0:00");
    expect(formatTime(135)).toBe("2:15");
    expect(formatTime(3723)).toBe("1:02:03");
  });
});

describe("the frame's sandbox", () => {
  test("is exactly the plan's list where every token is known", () => {
    expect(sandboxFor(() => true)).toBe(
      "allow-scripts allow-same-origin allow-presentation allow-popups allow-popups-to-escape-sandbox",
    );
  });

  test("leaves out a token the browser doesn't know (WebKit: allow-presentation), never adds one", () => {
    expect(sandboxFor((token) => token !== "allow-presentation")).toBe(
      "allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox",
    );
  });
});

describe("the first render", () => {
  const html = renderToStaticMarkup(<Video source={JSON.stringify(BLOCK)} onError={() => {}} />);

  test("loads nothing from YouTube before Play", () => {
    expect(html).not.toContain("<iframe");
    expect(html).not.toContain("<img");
    expect(html).not.toContain("youtube-nocookie");
  });

  test("shows the cover, the jump list and Open on YouTube", () => {
    expect(html).toContain("OSPF in ten minutes");
    expect(html).toContain("Lab Notes");
    expect(html).toContain("Loads YouTube in privacy-enhanced mode when you press Play");
    expect(html).toMatch(/<button[^>]*>(<svg[\s\S]*?<\/svg>)?Play<\/button>/);
    expect(html).toContain("Jump to");
    expect(html).toMatch(/2:15<\/span><span[^>]*>OSPF areas/);
    expect(html).toContain(`href="https://www.youtube.com/watch?v=${ID}&amp;t=30s"`);
    expect(html).toContain('rel="noopener noreferrer nofollow"');
    expect(html).toContain('target="_blank"');
  });

  test("a block that is not valid draws nothing", () => {
    expect(renderToStaticMarkup(<Video source={JSON.stringify({ ...BLOCK, youtube: "x" })} onError={() => {}} />)).toBe("");
  });
});
