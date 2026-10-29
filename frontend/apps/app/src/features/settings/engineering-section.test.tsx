import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import type { EngineeringCatalog, EngineeringProfileField } from "@/lib/api/types";
import { EngineeringFields } from "./engineering-section";
import type { EngineeringFormDraft } from "./profile-form";

const entry = (code: string, name: string, deprecated = false) => ({ code, name, deprecated });

/** A catalog shaped like GET /v1/catalog/engineering: ten focus areas (one deprecated), three platforms. */
const CATALOG: EngineeringCatalog = {
  version: 1,
  roles: [entry("network_engineer", "Network Engineer"), entry("soc_analyst", "SOC Analyst")],
  experienceLevels: [entry("junior", "Junior (0–2 years)"), entry("senior", "Senior (6–10 years)")],
  focusAreas: [
    ...["a", "b", "c", "d", "e", "f", "g", "h", "i"].map((code) => entry(`area_${code}`, `Area ${code.toUpperCase()}`)),
    entry("old_area", "Old Area", true),
  ],
  platforms: [entry("cisco_ios", "Cisco IOS / IOS XE"), entry("juniper", "Juniper Junos"), entry("aws", "Amazon Web Services (AWS)")],
  certifications: [entry("ccna", "CCNA"), entry("oscp", "OffSec OSCP")],
};

function render(draft: EngineeringFormDraft, options: { base?: EngineeringFormDraft; invalid?: EngineeringProfileField[] } = {}) {
  return renderToStaticMarkup(
    <EngineeringFields
      catalog={CATALOG}
      base={options.base ?? null}
      draft={draft}
      change={() => {}}
      invalid={(field) => options.invalid?.includes(field) ?? false}
      notice={null}
    />,
  );
}

const checkboxes = (html: string) => html.match(/<input type="checkbox"[^>]*>/g) ?? [];

describe("the Engineering Profile form", () => {
  test("role, experience and certification goal are labelled selects with Not set", () => {
    const html = render({ role: "soc_analyst" });
    for (const label of ["Role", "Experience", "Certification goal"]) expect(html).toContain(`>${label}</label>`);
    expect(html.match(/<select/g)).toHaveLength(3);
    expect(html).toContain('<option value="">Not set</option>');
    expect(html).toContain('<option value="soc_analyst" selected="">SOC Analyst</option>');
  });

  test("focus areas and platforms are named groups of labelled checkboxes, with how many are chosen", () => {
    const html = render({ focusAreas: ["area_a", "area_b"], platforms: ["juniper"] });
    expect(html.match(/<fieldset aria-labelledby=/g)).toHaveLength(2);
    expect(html).toContain(">Focus areas</p>");
    expect(html).toContain(">Platforms</p>");
    expect(html).toContain("2 of 8 chosen");
    expect(html).toContain("1 of 12 chosen");
    expect(html).toContain('aria-live="polite"');
    // Nine offered focus areas and three platforms; the deprecated one is not offered anew.
    expect(checkboxes(html)).toHaveLength(12);
    expect(html).not.toContain("Old Area");
    expect(checkboxes(html).filter((box) => box.includes('checked=""'))).toHaveLength(3);
    expect(checkboxes(html).filter((box) => box.includes('disabled=""'))).toHaveLength(0);
  });

  test("at the limit, every unchecked focus area is disabled and the count says so", () => {
    const full = ["a", "b", "c", "d", "e", "f", "g", "h"].map((code) => `area_${code}`);
    const html = render({ focusAreas: full, platforms: [] });
    expect(html).toContain("8 of 8 chosen, the most you can choose");
    const boxes = checkboxes(html);
    const disabled = boxes.filter((box) => box.includes('disabled=""'));
    expect(disabled).toHaveLength(1);
    expect(disabled[0]).toContain('value="area_i"');
  });

  test("a deprecated code is shown while saved or chosen, marked as no longer offered", () => {
    const html = render({ focusAreas: [] }, { base: { focusAreas: ["old_area"] } });
    expect(html).toContain("Old Area (no longer offered)");
    expect(checkboxes(html)).toHaveLength(13);
  });

  test("the API's field errors show next to their fields", () => {
    const html = render({ role: "network_engineer", platforms: ["aws"] }, { invalid: ["role", "platforms"] });
    expect(html).toContain("Choose one from the list.");
    expect(html).toContain("One of these can&#x27;t be saved");
    expect(html).toContain('aria-invalid="true"');
  });
});
