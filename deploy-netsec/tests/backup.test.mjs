import { describe, expect, test } from "bun:test";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dir, "..", "..");
const backup = readFileSync(join(ROOT, "deploy-netsec", "backup.sh"), "utf8");

describe("NetSec backup archive integrity", () => {
  test("shell source remains valid", () => {
    const check = spawnSync("bash", ["-n", "deploy-netsec/backup.sh"], {
      cwd: ROOT,
      encoding: "utf8",
    });
    expect(check.status, check.stderr).toBe(0);
  });

  test("an incomplete or unreadable archive is removed by the cleanup trap", () => {
    expect(backup).toContain("umask 077");
    expect(backup).toContain("backup_complete=0");
    expect(backup).toContain('trap cleanup_incomplete_backup EXIT');
    expect(backup).toContain("trap 'exit 1' HUP INT TERM");
    expect(backup).toMatch(/if \[ "\$backup_complete" != 1 \]; then\s+rm -f -- "\$out"/);

    const dump = backup.indexOf('postgres pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB"');
    const verify = backup.indexOf('postgres pg_restore --list < "$out" > /dev/null');
    const publish = backup.indexOf('mv -- "$out" "$final_out"');
    const complete = backup.indexOf("backup_complete=1");
    const success = backup.indexOf('echo "backup: wrote $final_out');
    const retention = backup.indexOf('find "$BACKUP_DIR"');

    expect(dump).toBeGreaterThan(0);
    expect(backup).toContain('out="$final_out.partial"');
    expect(verify).toBeGreaterThan(dump);
    expect(publish).toBeGreaterThan(verify);
    expect(complete).toBeGreaterThan(publish);
    expect(success).toBeGreaterThan(complete);
    expect(retention).toBeGreaterThan(success);
  });

  test("verification is fail-closed and does not expose database credentials in arguments", () => {
    const verification = backup.match(/if ! "\$\{COMPOSE\[@\]\}" exec -T postgres pg_restore[^\n]+; then[\s\S]*?\nfi/);
    expect(verification).not.toBeNull();
    expect(verification?.[0]).toContain('pg_restore --list < "$out" > /dev/null');
    expect(verification?.[0]).toContain("exit 1");
    expect(verification?.[0]).not.toMatch(/-U|-d|POSTGRES_USER|POSTGRES_DB|PASSWORD/);
    expect(backup).toContain('chmod 600 "$out"');
  });
});
