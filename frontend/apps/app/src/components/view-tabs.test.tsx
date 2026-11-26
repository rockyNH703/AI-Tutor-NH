import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import { TabPanel, ViewTabs } from "./view-tabs";

const ids = { tab: (value: string) => `t-${value}`, panel: (value: string) => `p-${value}` };
const TABS = [
  { value: "plan", label: "Today" },
  { value: "missed", label: "Missed questions", count: 3 },
] as const;

function render(open: "plan" | "missed") {
  return renderToStaticMarkup(
    <>
      <ViewTabs label="Views" ids={ids} value={open} onChange={() => {}} tabs={TABS} />
      <TabPanel ids={ids} value="plan" hidden={open !== "plan"}>
        plan
      </TabPanel>
      <TabPanel ids={ids} value="missed" hidden={open !== "missed"}>
        missed
      </TabPanel>
    </>,
  );
}

describe("view tabs", () => {
  test("a named tablist of tabs, each tied to its panel, only the open one selected and in the Tab order", () => {
    const html = render("missed");
    expect(html).toContain('role="tablist" aria-label="Views"');
    expect(html).toContain('id="t-plan" aria-selected="false" aria-controls="p-plan" tabindex="-1"');
    expect(html).toContain('id="t-missed" aria-selected="true" aria-controls="p-missed" tabindex="0"');
    expect(html).toContain('role="tabpanel" id="p-missed" aria-labelledby="t-missed"');
  });

  test("a count shows in its tab; a closed panel stays in the page, hidden", () => {
    const html = render("plan");
    expect(html).toMatch(/Missed questions<span[^>]*>3<\/span>/);
    expect(html).toMatch(/<div role="tabpanel" hidden="" id="p-missed"/);
    expect(html).not.toMatch(/<div role="tabpanel" hidden="" id="p-plan"/);
  });
});
