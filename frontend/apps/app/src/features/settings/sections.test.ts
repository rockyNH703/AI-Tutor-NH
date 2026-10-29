import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { SECTION_REDIRECTS, SETTINGS_SECTIONS, findSection, matchSections } from "./sections";

const slugs = (query: string) => matchSections(query).map((section) => section.slug);

describe("Settings search", () => {
  test("an empty or blank query lists every section, in order", () => {
    const all = SETTINGS_SECTIONS.map((section) => section.slug);
    expect(all).toHaveLength(14);
    expect(slugs("")).toEqual(all);
    expect(slugs("   ")).toEqual(all);
  });

  test("finds a section by what's in it, not only by its name", () => {
    expect(slugs("password")).toEqual(["privacy"]);
    expect(slugs("dark")).toEqual(["appearance"]);
    expect(slugs("hindi")).toEqual(["learning"]);
    expect(slugs("streak")).toContain("streak");
    expect(slugs("calendar")).toEqual(["streak"]);
    expect(slugs("remember")).toEqual(["memory"]);
    expect(slugs("delete account")).toEqual(["privacy"]);
  });

  test("engineering words find the Engineering Profile, time goals find Practice & Lab Time", () => {
    for (const word of ["role", "vendor", "platform", "ccna", "CCNP", "palo alto", "focus areas", "seniority"]) {
      expect(slugs(word)).toEqual(["engineering"]);
    }
    expect(slugs("certification")).toEqual(["engineering", "goals"]);
    expect(slugs("exam date")).toEqual(["goals"]);
    expect(slugs("weekly goal")).toEqual(["subjects"]);
    expect(slugs("preferred time")).toEqual(["subjects"]);
  });

  test("partial words, any case, extra spaces", () => {
    expect(slugs("pass")).toEqual(["privacy"]);
    expect(slugs("PassWord")).toEqual(["privacy"]);
    expect(slugs("  NOTIF  ")).toEqual(["notifications"]);
  });

  test("every word must match", () => {
    expect(slugs("dark contrast")).toEqual(["appearance"]);
    expect(slugs("dark password")).toEqual([]);
  });

  test("nothing matches gibberish, and school words find nothing", () => {
    expect(slugs("zzzz")).toEqual([]);
    for (const word of ["cbse", "school", "grade", "board", "syllabus"]) expect(slugs(word)).toEqual([]);
  });

  test("every section is found by its own title", () => {
    for (const section of SETTINGS_SECTIONS) {
      expect(slugs(section.title)).toContain(section.slug);
    }
  });
});

describe("section addresses", () => {
  test("the learning sections, renamed for engineers", () => {
    const titles = Object.fromEntries(SETTINGS_SECTIONS.map((section) => [section.slug, section.title]));
    expect(titles.engineering).toBe("Engineering Profile");
    expect(titles.goals).toBe("Skills & Certification Goals");
    expect(titles.subjects).toBe("Practice & Lab Time");
    expect(SETTINGS_SECTIONS.map((section) => section.slug).slice(0, 3)).toEqual(["profile", "engineering", "goals"]);
  });

  test("an old address redirects to a section that exists, in next.config.mjs too", () => {
    const config = readFileSync(join(dirname(fileURLToPath(import.meta.url)), "../../../next.config.mjs"), "utf8");
    expect(Object.keys(SECTION_REDIRECTS)).toEqual(["academic"]);
    for (const [from, to] of Object.entries(SECTION_REDIRECTS)) {
      expect(findSection(from)).toBeUndefined();
      expect(findSection(to)?.slug).toBe(to);
      expect(config).toContain(`{ source: "/settings/${from}", destination: "/settings/${to}", permanent: false }`);
    }
  });
});
