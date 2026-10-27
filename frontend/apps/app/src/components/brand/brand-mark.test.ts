import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { BRAND } from "@/lib/brand";

/** The logo (also the favicon) changes colour with the palette; its shape never changes. */
const SVG = readFileSync(join(import.meta.dir, "..", "..", "..", "public", BRAND.markSrc), "utf-8");

describe("the brand mark", () => {
  test("keeps its geometry, size and name", () => {
    for (const part of [
      'viewBox="0 0 32 32"',
      'role="img" aria-label="NetSec AI"',
      'fill="none" stroke="currentColor" stroke-width="1.6"',
      'd="M16 2.8 29.1 26H2.9L16 2.8Z"',
      'd="m16 2.8-4.35 15.3L2.9 26"',
      'd="m16 2.8 4.35 15.3L29.1 26"',
      'd="m11.65 18.1 8.7 0L16 26l-4.35-7.9Z"',
    ]) {
      expect(SVG).toContain(part);
    }
  });

  test("uses a single neutral outline without the previous coloured tile", () => {
    expect(SVG).toContain('color="#F5F5F6"');
    expect(SVG).not.toContain("linearGradient");
    expect(SVG).not.toContain("<rect");
    expect(SVG).not.toMatch(/#(4F6F8F|6B8CAB|4F46E5|7C3AED)/i);
  });
});
