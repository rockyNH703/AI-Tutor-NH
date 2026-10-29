import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dir, "..", "..");
const read = (path) => readFileSync(join(ROOT, path), "utf8");

const processing = read("backend/app/files/processing.py");
const lib = read("deploy-netsec/server/lib.sh");

// The API's file extractor (backend/app/files/processing.py) and the deploy scripts must agree on
// NetSec's layout: a mismatch leaves secrets visible to the sandbox, or the worker unaware of a deploy.
describe("file extractor sandbox matches the NetSec deployment", () => {
  test("it hides this deployment's root and secrets", () => {
    const root = lib.match(/^ROOT=(\S+)$/m)?.[1];
    const secrets = lib.match(/^SECRETS=(\S+)$/m)?.[1];
    expect(root).toBe("/opt/netsecai");
    expect(secrets).toBe("/etc/netsecai");
    expect(processing).toContain(`DEPLOY_ROOT = "${root}"`);
    expect(processing).toContain(`SECRETS_DIR = "${secrets}"`);
    expect(processing).toContain('f"TemporaryFileSystem={DEPLOY_ROOT}:ro"');
    expect(processing).toContain('f"InaccessiblePaths=-{SECRETS_DIR} ');
  });

  test("it pauses while update.sh deploys", () => {
    const marker = read("deploy-netsec/server/update.sh").match(/touch (\/run\/\S+) && MARKER_OWNED=1/)?.[1];
    expect(marker).toBe("/run/netsecai-deploying");
    expect(processing).toContain(`DEPLOY_MARKER = Path("${marker}")`);
  });

  test("its units are the ones status.sh counts", () => {
    expect(read("deploy-netsec/server/status.sh")).toContain("'netsecai-extract-*'");
    expect(processing).toContain('UNIT_PREFIX = "netsecai-extract"');
    expect(processing).not.toContain("aichat-extract");
  });
});

describe("secrets never travel in process arguments", () => {
  test("the deploy smoke signs its cookie with the secret in the environment", () => {
    // Any local user can read another process's arguments (ps, /proc/*/cmdline).
    expect(lib).not.toMatch(/-hmac\s+"\$secret"/);
    expect(lib).toContain('NETSEC_COOKIE_SECRET="$secret" python3.12');
    expect(lib).toContain('os.environ["NETSEC_COOKIE_SECRET"]');
  });

  test("the database roles' passwords reach psql through environments, never as -v arguments", () => {
    const grants = read("deploy-netsec/database-grants.sh");
    expect(grants).not.toMatch(/-v\s+(runtime|auth)_password=/);
    expect(grants).toContain("db_compose exec -T -e NETSEC_RUNTIME_PW -e NETSEC_AUTH_PW postgres psql");
    expect(grants).toContain("\\getenv runtime_password NETSEC_RUNTIME_PW");
    expect(grants).toContain("\\getenv auth_password NETSEC_AUTH_PW");
  });
});

describe("frontend dependencies", () => {
  test("a deploy that changes them reinstalls with --force, so no stale nested copy survives", () => {
    expect(read("deploy-netsec/server/update.sh")).toContain('"$BUN" install --frozen-lockfile --force');
  });
});
