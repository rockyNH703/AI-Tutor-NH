import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const read = (path) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8");

// K-49a: a timer-started worker could run the new checkout against the old schema mid-deploy.
describe("the background workers never run while a deploy is in progress", () => {
  for (const unit of ["netsecai-jobs.service", "netsecai-knowledge.service"]) {
    test(`${unit} is skipped while /run/netsecai-deploying exists`, () => {
      expect(read(`systemd/${unit}`)).toMatch(/^ConditionPathExists=!\/run\/netsecai-deploying$/m);
    });
  }

  test("the deploy writes that marker before it touches the checkout and removes it on exit", () => {
    const update = read("server/update.sh");
    expect(update.indexOf("touch /run/netsecai-deploying")).toBeLessThan(update.indexOf("production checkout ->"));
    expect(update).toContain("rm -f /run/netsecai-deploying");
  });
});
