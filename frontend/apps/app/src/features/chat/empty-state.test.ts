import { afterEach, beforeEach, expect, test } from "bun:test";

import { fitOneLine } from "./empty-state";

const DEFAULT_PX = 40;
const realComputedStyle = globalThis.getComputedStyle;

beforeEach(() => {
  globalThis.getComputedStyle = (() => ({ fontSize: `${DEFAULT_PX}px` })) as unknown as typeof getComputedStyle;
});
afterEach(() => {
  globalThis.getComputedStyle = realComputedStyle;
});

/**
 * A heading `room` px wide holding a sentence `natural` px wide at the default size. Once shrunk, its text is `extra` px
 * wider than in proportion (hinting and rounding, as on Linux), so a single proportional pass leaves it over the edge.
 */
function heading(room: number, natural: number, extra = 6) {
  const style = { fontSize: "", whiteSpace: "" };
  const element = {
    style,
    clientWidth: room,
    get scrollWidth() {
      const size = style.fontSize ? Number.parseFloat(style.fontSize) : DEFAULT_PX;
      return Math.ceil((natural * size) / DEFAULT_PX + (size < DEFAULT_PX ? extra : 0));
    },
  };
  return element as unknown as HTMLElement & { style: typeof style };
}

test("a greeting whose glyphs don't shrink in proportion still ends on one line", () => {
  const element = heading(300, 330);
  fitOneLine(element);
  expect(element.scrollWidth).toBeLessThanOrEqual(element.clientWidth);
  expect(element.style.whiteSpace).toBe(""); // wrapping is allowed again once it fits
});

test("a greeting that fits keeps its size", () => {
  const element = heading(400, 330);
  fitOneLine(element);
  expect(element.style.fontSize).toBe("");
});

test("never smaller than 12px: a sentence too long for that wraps instead", () => {
  const element = heading(50, 2000);
  fitOneLine(element);
  expect(Number.parseFloat(element.style.fontSize)).toBe(12);
});
