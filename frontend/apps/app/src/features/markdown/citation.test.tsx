import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import { TooltipProvider } from "@v1/ui/tooltip";

import type { Citation } from "@/lib/api/types";
import { CitationMark, Sources } from "./citation";
import { CitationsContext } from "./context";

const book: Citation = {
  ref: "1",
  kind: "source",
  title: "CCNA 200-301 Official Cert Guide Volume 1 (2nd edition)",
  printedPageLabel: "35",
  physicalPage: 57,
};

describe("a book citation names its page", () => {
  test("the Sources line shows the printed page after the book, edition and volume", () => {
    const html = renderToStaticMarkup(<Sources citations={[book]} content="OSPF elects a DR [1]." />);
    expect(html).toContain("CCNA 200-301 Official Cert Guide Volume 1 (2nd edition)");
    expect(html).toContain("p. 35");
  });

  test("without a printed label the physical page is shown; a file citation has no page", () => {
    const unlabelled: Citation = { ...book, printedPageLabel: null };
    const file: Citation = { ref: "F1", kind: "file", title: "router.cfg" };
    const html = renderToStaticMarkup(<Sources citations={[unlabelled, file]} content="See [1] and [F1]." />);
    expect(html).toContain("p. 57");
    expect(html).toContain("Your file: router.cfg");
    expect(html.match(/p\. /g)).toHaveLength(1);
  });

  test("the chip's accessible name carries the page too", () => {
    const html = renderToStaticMarkup(
      <TooltipProvider>
        <CitationsContext.Provider value={[book]}>
          <CitationMark cite="1" />
        </CitationsContext.Provider>
      </TooltipProvider>,
    );
    expect(html).toContain(
      'aria-label="1. Source: CCNA 200-301 Official Cert Guide Volume 1 (2nd edition), p. 35"',
    );
  });
});
