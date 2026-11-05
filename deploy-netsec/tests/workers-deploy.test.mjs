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

// Deep audit (low): every deploy re-enabled the knowledge timer, so the owner could not switch it off for good.
describe("the owner's off switch for the knowledge worker survives deploys", () => {
  test("the timer is enabled only while /etc/netsecai/knowledge.disabled is absent, and stopped while it exists", () => {
    const install = read("server/install-units.sh");
    const enables = install.split("\n").filter((line) => /systemctl enable/.test(line) && line.includes("netsecai-knowledge.timer"));
    expect(enables).toHaveLength(1);
    expect(install).toMatch(
      /if \[ -e "\$SECRETS\/knowledge\.disabled" \]; then systemctl disable --now --quiet netsecai-knowledge\.timer; else systemctl enable --quiet netsecai-knowledge\.timer; fi/,
    );
  });
});

// Plan E: book processing shares the VM with the live services (and PadhAI), so it runs below them.
describe("the knowledge worker and its extractors run in a low-priority slice with a memory ceiling", () => {
  const slice = read("systemd/netsecai-knowledge.slice");
  const service = read("systemd/netsecai-knowledge.service");

  test("the slice weighs CPU and IO below the default 100 and caps memory under the host's 3.9 GB", () => {
    expect(slice).toMatch(/^CPUWeight=20$/m);
    expect(slice).toMatch(/^IOWeight=20$/m);
    expect(slice).toMatch(/^MemoryMax=1200M$/m);
    expect(slice).toMatch(/^MemorySwapMax=0$/m);
  });

  test("the worker runs in the slice and long enough for an hour of OCR", () => {
    expect(service).toMatch(/^Slice=netsecai-knowledge\.slice$/m);
    const timeout = Number(service.match(/^TimeoutStartSec=(\d+)$/m)?.[1]);
    expect(timeout).toBeGreaterThan(3600);
    expect(service).not.toMatch(/^CPUQuota=200%$/m);
  });

  test("the extractors join the same slice and every deploy installs it", () => {
    expect(readFileSync(new URL("../../backend/app/knowledge/processing.py", import.meta.url), "utf8")).toContain(
      'KNOWLEDGE_SLICE = "netsecai-knowledge.slice"',
    );
    expect(read("server/install-units.sh")).toContain('"$SRC"/deploy-netsec/systemd/netsecai-*.slice');
  });
});
