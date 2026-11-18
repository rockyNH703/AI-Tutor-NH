import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dir, "..", "..");
const read = (path) => readFileSync(join(ROOT, path), "utf8");

const deploy = read("deploy-netsec/deploy.sh");
const gate = read("scripts/security.sh");

describe("deploy refuses a commit without a PASS security receipt", () => {
  test("the receipt check: this exact commit, result=PASS, a STOP that says what to run", () => {
    expect(deploy).toContain("security_gate() {");
    expect(deploy).toContain("receipt=$(git rev-parse --git-path netsec-security-gate)");
    expect(deploy).toContain('grep -qx "commit=$sha" "$receipt"');
    expect(deploy).toContain('grep -qx "result=PASS" "$receipt"');
    expect(deploy).toMatch(/\|\| stop "no PASS security receipt for \$sha .*run 'bun run security'/);
  });

  test("a deploy checks the receipt before any remote action", () => {
    const call = deploy.indexOf('[ -n "$TARGET" ] || security_gate "$(git rev-parse HEAD)"');
    expect(call).toBeGreaterThan(0);
    for (const remote of ["git fetch", "ssh ", "git push", "git archive"]) {
      const first = deploy.indexOf(remote, deploy.indexOf("set -euo pipefail"));
      expect(first, remote).toBeGreaterThan(call);
    }
  });

  test("the override needs a non-empty reason and prints a warning with it", () => {
    expect(deploy).toContain('case "${NETSEC_SECURITY_GATE:-}" in');
    expect(deploy).toContain("skip:*[![:space:]]*)");
    expect(deploy).toContain('*) stop "NETSEC_SECURITY_GATE must be skip:<reason> with a non-empty reason" ;;');
    expect(deploy).toContain("!!! SECURITY GATE SKIPPED: $sha is released WITHOUT a PASS receipt");
    expect(deploy).toContain('"!!! reason: $reason"');
    expect(deploy).toContain("netsec-security-gate.overrides");
    expect(deploy).not.toMatch(/^[^#\n]*NETSEC_SECURITY_GATE(:-skip|=skip)/m); // no built-in default override
  });

  test("only a rollback to an earlier commit of GitHub main skips the gate", () => {
    expect(deploy).toContain('if [ "$SHA" != "$MAIN" ] && git merge-base --is-ancestor "$SHA" "$MAIN"; then');
    const rollback = deploy.slice(deploy.indexOf("MODE=rollback"), deploy.indexOf('echo "==> $MODE $SHA'));
    expect(rollback).toContain('security_gate "$SHA"');
    expect(deploy.indexOf("MODE=rollback")).toBeLessThan(deploy.indexOf("# ---------------------------------------------------------------- 2. production Git"));
  });
});

describe("the gate (scripts/security.sh)", () => {
  test("bun run security runs it", () => {
    expect(JSON.parse(read("package.json")).scripts.security).toBe("bash scripts/security.sh");
  });

  test("the receipt is written only on PASS of a clean tree, and removed on FAIL", () => {
    expect(gate).toContain('if [ -n "$HEAD_SHA" ] && clean; then\n  MODE=receipt');
    expect(gate).toContain('clean() { [ -z "$(git status --porcelain --untracked-files=normal)" ]; }');
    expect(gate).toContain("RECEIPT=$(git rev-parse --git-path netsec-security-gate)");
    const write = gate.indexOf('} > "$RECEIPT.tmp" && mv -f "$RECEIPT.tmp" "$RECEIPT"');
    const pass = gate.indexOf('if [ "$FAILED" = 0 ] && [ "$MODE" = receipt ]; then');
    const unchanged = gate.indexOf('if [ "$(git rev-parse HEAD)" = "$HEAD_SHA" ] && clean; then');
    expect(pass).toBeGreaterThan(0);
    expect(unchanged).toBeGreaterThan(pass);
    expect(write).toBeGreaterThan(unchanged);
    expect(gate.match(/> "\$RECEIPT(\.tmp)?"/g)).toHaveLength(1);
    expect(gate).toContain('grep -qx "commit=$HEAD_SHA" "$RECEIPT"; then\n    rm -f -- "$RECEIPT"');
    expect(gate).toContain('echo "result=PASS"');
    expect(gate).toMatch(/\[ "\$FAILED" = 0 \]\s*$/);
  });

  test("gitleaks runs with --redact and no secret value is printed", () => {
    expect(gate).toMatch(/common=\(-c "\$D\/src\/\.gitleaks\.toml" --redact /);
    expect(gate.match(/"\$GL" (dir|git) /g)).toHaveLength(2);
    expect(gate.match(/"\$GL" (dir|git) [^\n]*"\$\{common\[@\]\}"/g)).toHaveLength(2);
    expect(gate).toContain("--ignore-gitleaks-allow");
    expect(gate).not.toMatch(/\\\(\.(Secret|Match)\)|\\\(\.extra\.lines\)/);
  });

  test("tool versions are pinned", () => {
    for (const pin of ["EXPECT_SEMGREP=1.182.1", "EXPECT_GITLEAKS=8.30.1", "EXPECT_PIP_AUDIT=2.10.1", "EXPECT_ZIZMOR=1.31.1", "EXPECT_BUN=1.3.14"]) {
      expect(gate).toContain(pin);
    }
  });

  test("the scans are the agreed ones", () => {
    for (const config of ["p/python", "p/typescript", "p/javascript", "p/secrets", "p/security-audit", ".semgrep/netsec.yml"]) {
      expect(gate).toContain(`--config ${config}`);
    }
    expect(gate).toContain("--metrics=off --error --json");
    expect(gate).toContain("pip-audit -r backend/requirements.lock --require-hashes --disable-pip");
    expect(gate).toContain("(cd frontend && bun audit --json)");
    expect(gate).toContain(".github/workflows/");
  });

  test("no bypass: no --no-verify, no skip switch, no swallowed scanner failure", () => {
    for (const source of [gate, deploy]) expect(source).not.toContain("--no-verify");
    expect(gate).not.toMatch(/\$\{?NETSEC_SECURITY_GATE|SKIP_SECURITY|--disable-nosem|--exit-code 0/);
    expect(gate).not.toMatch(/(semgrep|gitleaks|pip-audit|zizmor|bun audit)[^\n]*\|\| true/);
  });
});

describe("scanner configuration", () => {
  test("gitleaks: default rules; each allowlist is one file AND one rule AND one exact value", () => {
    const toml = read(".gitleaks.toml");
    expect(toml).toContain("[extend]\nuseDefault = true");
    expect(toml).not.toMatch(/^\[allowlist\]|disabledRules|^paths = .*\.\*|stopwords/m);
    const blocks = toml.split("[[allowlists]]").slice(1);
    expect(blocks.length).toBeGreaterThan(0);
    for (const block of blocks) {
      expect(block).toContain('condition = "AND"');
      expect(block).toMatch(/^targetRules = \["[a-z-]+"\]$/m);
      expect(block).toMatch(/^paths = \['''\^[^*\n]+\$'''\]$/m);
      expect(block).toMatch(/^regexes = \['''\^[^*\n]+\$'''\]$/m);
    }
  });

  test("bun audit: reviewed exceptions are exact (advisory + package), dated and short-lived", () => {
    const block = gate.slice(gate.indexOf("const ACCEPTED = {"), gate.indexOf("const accepted ="));
    const ids = [...block.matchAll(/"(GHSA-[a-z0-9-]+)": \{/g)].map((m) => m[1]);
    expect(ids).toEqual(["GHSA-fc5q-39xc-vjpf"]);
    expect(block).toContain('pkg: "picomatch"');
    for (const [, until] of block.matchAll(/until: "(\d{4}-\d{2}-\d{2})"/g)) {
      expect(until <= "2026-12-31").toBe(true);
    }
    expect(gate).toContain("ACCEPTED[r.id]?.pkg === r.pkg && new Date().toISOString().slice(0, 10) <= ACCEPTED[r.id].until");
    expect(gate).toContain('["critical", "high"].includes(r.sev) && !accepted(r)');
  });

  test("semgrepignore lists only generated or vendored paths, never source directories", () => {
    const entries = read(".semgrepignore").split("\n").filter((line) => line && !line.startsWith("#"));
    for (const source of ["backend/", "frontend/", "tests/", "test/", "scripts/", "deploy-netsec/", "docs/", "app/", "src/", "qbank/"]) {
      expect(entries).not.toContain(source);
    }
    for (const generated of ["node_modules/", "qbank/content/", "docs/openapi.json", "frontend/apps/app/src/lib/api/contract.gen.ts"]) {
      expect(entries).toContain(generated);
    }
  });

  test("the project's own Semgrep rules exist, with self-test fixtures", () => {
    const rules = read(".semgrep/netsec.yml");
    for (const id of ["netsec-sql-text-built-string", "netsec-log-sensitive-value", "netsec-react-dangerously-set-inner-html", "netsec-ts-eval"]) {
      expect(rules).toContain(`- id: ${id}`);
      expect(read(".semgrep/tests/netsec.py") + read(".semgrep/tests/netsec.tsx")).toContain(`ruleid: ${id}`);
    }
    expect(rules).toContain("- /frontend/apps/app/src/app/layout.tsx");
  });
});
