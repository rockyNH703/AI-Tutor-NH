import { describe, expect, test } from "bun:test";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dir, "..", "..");
const scriptPath = join(ROOT, "deploy-netsec", "restore-drill.sh");
const source = readFileSync(scriptPath, "utf8");

describe("local restore drill safety", () => {
  test("shell source remains valid", () => {
    const check = spawnSync("bash", ["-n", "deploy-netsec/restore-drill.sh"], { cwd: ROOT, encoding: "utf8" });
    expect(check.status, check.stderr).toBe(0);
  });

  test("requires explicit archive, URL, loopback host, explicit port, and a non-production *_test name", () => {
    expect(source).toContain(': "${TEST_DATABASE_URL:?TEST_DATABASE_URL must be set explicitly}"');
    expect(source).toContain('[ -f "$ARCHIVE" ] && [ -r "$ARCHIVE" ]');
    expect(source).toContain('[ ! -L "$ARCHIVE" ]');
    expect(source).toContain('*_test)');
    expect(source).toContain('netsecai*|aichat*|padhai*|*production*|*prod*|*staging*|*stage*|*live*');
    expect(source).toContain('TEST_DATABASE_URL host must be loopback');
    expect(source).toContain('TEST_DATABASE_URL must include an explicit port');
    expect(source).toContain('do not put a password in TEST_DATABASE_URL');
    expect(source).not.toMatch(/\.\s+[^\n]*\.env|source\s+[^\n]*\.env/);
    expect(source).not.toContain("10.1.50.94");
    expect(source).not.toContain("NETSEC_DEPLOY_HOST");
  });
  test("uses a password file or inherited password, never URL credentials or command echoing", () => {
    expect(source).toContain("PGPASSWORD_FILE");
    expect(source).toContain('export PGPASSWORD');
    expect(source).not.toContain("set -x");
    expect(source).not.toMatch(/--password=|-W\b/);
    expect(source).toContain('hide_parameters=True');
  });

  test("required sequence is validate, recreate, restore, migrate, readiness, cleanup", () => {
    const runtime = source.slice(source.indexOf("printf 'restore-drill: validating custom-format archive"));
    const validate = runtime.indexOf('pg_restore --list "$ARCHIVE"');
    const drop = runtime.indexOf('dropdb --if-exists --force --maintenance-db="$ADMIN_URL" "$DB_NAME"');
    const create = runtime.indexOf('createdb --maintenance-db="$ADMIN_URL" --template=template0 "$DB_NAME"');
    const restore = runtime.indexOf('pg_restore --exit-on-error --no-owner --no-privileges');
    const migrate = runtime.indexOf('"$PYTHON" -m alembic upgrade head');
    const readiness = runtime.indexOf('RESTORE_DRILL_DATABASE_URL="$TEST_DATABASE_URL"');
    const cleanup = source.indexOf('trap cleanup EXIT');

    expect(validate).toBeGreaterThan(0);
    expect(drop).toBeGreaterThan(validate);
    expect(create).toBeGreaterThan(drop);
    expect(restore).toBeGreaterThan(create);
    expect(migrate).toBeGreaterThan(restore);
    expect(readiness).toBeGreaterThan(migrate);
    expect(cleanup).toBeGreaterThan(0);
    expect(source.indexOf("printf 'restore-drill: validating custom-format archive")).toBeGreaterThan(cleanup);
    expect(source).toContain("LLM_PROVIDER=fake");
    expect(source).toContain("EMBEDDING_PROVIDER=fake");
    expect(source).toContain("EMAIL_BACKEND=memory");
    expect(source).toContain('dropdb --if-exists --force');
  });

  test("mock contract includes fail-closed cleanup and exact offline checks", () => {
    expect(source).toContain('trap cleanup EXIT');
    expect(source).toContain('DISPOSABLE_CREATED=1');
    expect(source).toContain('dropdb --if-exists --force --maintenance-db="$ADMIN_URL" "$DB_NAME"');
    expect(source).toContain('pg_restore --exit-on-error --no-owner --no-privileges');
    expect(source).toContain('revision != heads[0]');
    expect(source).toContain('vector != "vector"');
    expect(source).toContain('AsyncClient(transport=ASGITransport(app=app)');
    expect(source).toContain('await client.get("/health")');
    expect(source).toContain('await client.get("/ready")');
  });
});
