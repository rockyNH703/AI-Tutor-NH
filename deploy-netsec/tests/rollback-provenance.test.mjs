import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dir, "..", "..");
const read = (path) => readFileSync(join(ROOT, path), "utf8");
const rollback = read("deploy-netsec/rollback.sh");

const toolingResolution = rollback.indexOf('TOOL_SHA=$(git ls-remote --exit-code "$SOURCE" refs/heads/main');
const toolingFetch = rollback.indexOf('git -C "$TOOLING" fetch -q --no-tags "$SOURCE" "$TOOL_SHA"');
const toolingVerify = rollback.indexOf('[ "$(git -C "$TOOLING" rev-parse HEAD)" = "$TOOL_SHA" ]');
const targetVerify = rollback.indexOf('TARGET_SHA=$(git -C "$TOOLING" rev-parse --verify --quiet "$TARGET^{commit}")');
const targetHistory = rollback.indexOf('git -C "$TOOLING" merge-base --is-ancestor "$TARGET_SHA" "$TOOL_SHA"');
const deployCall = rollback.indexOf('(cd "$TOOLING" && bash deploy-netsec/deploy.sh --host "$HOST" --rollback-to "$TARGET_SHA")');

describe("NetSec rollback source provenance", () => {
  test("production tooling is bound to canonical main by immutable commit", () => {
    expect(rollback).toContain("CANONICAL=https://github.com/rockyNH703/AI-Tutor-NH.git");
    expect(toolingResolution).toBeGreaterThan(0);
    expect(toolingFetch).toBeGreaterThan(toolingResolution);
    expect(toolingVerify).toBeGreaterThan(toolingFetch);
    expect(rollback).toContain('git -C "$TOOLING" checkout -q --detach FETCH_HEAD');
  });

  test("target is resolved and constrained inside the verified tooling repository", () => {
    expect(targetVerify).toBeGreaterThan(toolingVerify);
    expect(targetHistory).toBeGreaterThan(targetVerify);
    expect(deployCall).toBeGreaterThan(targetHistory);
    expect(rollback).toContain('|| ns_die "$TARGET is not a commit in $SOURCE main history"');
    expect(rollback).toContain('|| ns_die "$TARGET_SHA is not an earlier commit of $SOURCE main"');
  });

  test("host execution cannot use arbitrary workstation HEAD tooling", () => {
    expect(rollback).not.toContain("exec bash deploy-netsec/deploy.sh");
    expect(rollback).not.toMatch(/git archive --format=tar HEAD/);
    expect(rollback.slice(deployCall)).toContain('cd "$TOOLING"');
    expect(rollback.slice(deployCall)).not.toContain('cd "$(git rev-parse --show-toplevel)"');
  });

  test("explicit host authorization and namespace safety remain before remote actions", () => {
    const hostRequired = rollback.indexOf('[ -n "$HOST" ] || ns_die "set NETSEC_DEPLOY_HOST=user@host or pass --host"');
    const safety = rollback.indexOf("safety_preflight");
    const firstRemote = rollback.indexOf('TARGET=$(ssh "$HOST"');
    expect(safety).toBeGreaterThan(0);
    expect(hostRequired).toBeGreaterThan(safety);
    expect(toolingResolution).toBeGreaterThan(hostRequired);
    expect(firstRemote).toBeGreaterThan(toolingVerify);
    expect(firstRemote).toBeLessThan(targetVerify);
  });

  test("rehearsal source stays explicit and cannot target production", () => {
    expect(rollback).toContain('SOURCE=${DEPLOY_REHEARSAL_SOURCE:?DEPLOY_REHEARSAL needs DEPLOY_REHEARSAL_SOURCE (a local repository)}');
    expect(rollback).toContain('case "$HOST" in *"$PRODUCTION"*) ns_die "rehearsal mode never rolls back production ($HOST)" ;; esac');
    expect(rollback).toContain('TOOL_SHA=$(git -C "$SOURCE" rev-parse --verify main^{commit})');
  });

  test("security gate remains delegated to the verified deploy implementation", () => {
    const deploy = read("deploy-netsec/deploy.sh");
    expect(deploy).toContain('if [ "$SHA" != "$MAIN" ] && git merge-base --is-ancestor "$SHA" "$MAIN"; then');
    expect(deploy).toContain('security_gate "$SHA"');
    expect(rollback).not.toContain("NETSEC_SECURITY_GATE=");
  });
});
