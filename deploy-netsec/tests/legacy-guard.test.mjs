import { describe, expect, test } from "bun:test";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dir, "..", "..");
const legacy = readFileSync(join(ROOT, "deploy", "deploy.sh"), "utf8");
// The commands only: the header comment describes `git push` and SSH in prose.
const code = legacy
  .split("\n")
  .filter((line) => !line.trimStart().startsWith("#"))
  .join("\n");

// deploy/ is PadhAI's production deploy, kept here as history. From this NetSec repository it must stop
// before it fetches, connects or deploys anything (`bun run deploy`, `bun run rollback` both go through it).
describe("PadhAI's legacy deploy never runs from the NetSec repository", () => {
  test("it stops at once, before any fetch or SSH", () => {
    const guard = code.indexOf('if [ -d deploy-netsec ] && [ "${DEPLOY_REHEARSAL:-0}" != 1 ]; then');
    expect(guard).toBeGreaterThan(0);
    for (const later of ["git fetch", "ssh ", "git push"]) {
      const at = code.indexOf(later);
      if (at >= 0) expect(at, later).toBeGreaterThan(guard);
    }
  });

  test("running it from this checkout stops with the NetSec instructions", () => {
    const run = spawnSync("bash", ["deploy/deploy.sh"], { cwd: ROOT, encoding: "utf8", env: { ...process.env, DEPLOY_REHEARSAL: "0" } });
    expect(run.status).toBe(1);
    expect(run.stderr).toContain("STOP: this is the NetSec AI repository");
    expect(run.stderr).toContain("bun run deploy:netsec");
  });
});
