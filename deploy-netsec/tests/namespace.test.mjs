import { describe, expect, test } from "bun:test";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dir, "..", "..");
const read = (path) => readFileSync(join(ROOT, path), "utf8");

const normalize = (path) => path.replaceAll("\\", "/");

function files(dir) {
  return readdirSync(join(ROOT, dir), { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory() ? files(join(dir, entry.name)) : [normalize(join(dir, entry.name))],
  );
}

const all = files("deploy-netsec")
  .filter((file) => !file.endsWith("safety.sh") && !file.includes("tests"))
  .map((path) => [path, read(path)]);
const protectedHostPorts = [3000, 4100, 5432, 4200];
const protectedPortAllowed = new Map([
  ["deploy-netsec/compose/docker-compose.yml", new Set([5432])],
  ["deploy-netsec/server/lib.sh", new Set([3000])],
]);

function containsPort(source, port) {
  return new RegExp(`(^|[^0-9])${port}([^0-9]|$)`, "m").test(source);
}

describe("NetSec deployment namespace", () => {
  test("canonical constants", () => {
    const lib = read("deploy-netsec/server/lib.sh");
    for (const value of ["/opt/netsecai", "/etc/netsecai", "API_PORT=4400", "WEB_PORT=4300"]) expect(lib).toContain(value);
    const compose = Bun.YAML.parse(read("deploy-netsec/compose/docker-compose.yml"));
    expect(compose.name).toBe("netsecai-postgres");
    expect(compose.volumes.pgdata.name).toBe("netsecai_postgres_data");
    expect(compose.networks.postgres.name).toBe("netsecai_postgres_network");
  });

  test("denylist is explicit and fail-closed", () => {
    const safety = read("deploy-netsec/server/safety.sh");
    for (const value of ["/opt/aichat", "/etc/aichat", "/var/backups/aichat", "aichat-", "3000 4100 5432 4200", "compose", "ayur.namahos.com", "assert_safe_target", "safety_preflight"]) expect(safety).toContain(value);
  });

  test("isolated resources and dedicated tunnel", () => {
    const tunnel = read("deploy-netsec/cloudflared/config.yml.template");
    expect(tunnel).toContain("chat.netsecai.com");
    expect(tunnel).toContain("/etc/netsecai/cloudflared");
    expect(statSync(join(ROOT, "deploy-netsec/systemd/netsecai-cloudflared.service")).isFile()).toBe(true);
    const unit = read("deploy-netsec/systemd/netsecai-cloudflared.service");
    expect(unit).toContain("cloudflared tunnel --no-autoupdate --config /dev/null --url http://127.0.0.1:4300");
    expect(unit).toContain("Wants=network-online.target netsecai-web.service");
    expect(unit).not.toContain("Requires=netsecai-web.service");
    expect(unit).not.toContain("/etc/cloudflared");
  });

  test("no protected mutation targets or protected host ports", () => {
    for (const [file, source] of all) {
      for (const value of ["/opt/aichat", "/etc/aichat", "/var/backups/aichat", " systemctl start aichat-"]) expect(source, file).not.toContain(value);
      for (const port of protectedHostPorts) {
        if (protectedPortAllowed.get(file)?.has(port)) continue;
        expect(containsPort(source, port), `${file} contains protected host port ${port}`).toBe(false);
      }
    }
  });

  test("API binds 4400 and deploy archives deploy-netsec", () => {
    const api = read("deploy-netsec/systemd/netsecai-api.service");
    expect(api).toContain("--host 127.0.0.1 --port 4400");
    const deploy = read("deploy-netsec/deploy.sh");
    expect(deploy).toContain("git archive --format=tar HEAD deploy-netsec");
    expect(deploy).not.toMatch(/git archive --format=tar HEAD deploy(?:\s|\|)/);
  });

  test("web unit bypasses the package's protected port and binds 4300", () => {
    const web = read("deploy-netsec/systemd/netsecai-web.service");
    expect(web).toContain("next start -p 4300 -H 127.0.0.1");
    expect(web).not.toContain("bun run start");
  });

  test("install checks every proposed host port before package or filesystem mutation", () => {
    const install = read("deploy-netsec/install.sh");
    const checks = install.indexOf('for binding in "$WEB_PORT:netsecai-web.service" "$API_PORT:netsecai-api.service" "$POSTGRES_HOST_PORT:netsecai-postgres.service"');
    const apt = install.indexOf("apt-get update");
    const directories = install.indexOf('install -d -m 755 "$STATE"');
    expect(checks).toBeGreaterThan(0);
    expect(checks).toBeLessThan(apt);
    expect(checks).toBeLessThan(directories);
  });

  test("database operations prove immutable isolated identity", () => {
    const env = read("deploy-netsec/env/postgres.env.example");
    expect(env).toContain("NETSECAI_DB_IDENTITY=");
    const lib = read("deploy-netsec/server/lib.sh");
    for (const value of ["assert_database_identity", "assert_netsec_database_urls", "netsecai-postgres-postgres-1", "netsecai_postgres_data", "netsecai_postgres_network", 'configured_db" = "$DB_NAME', "POSTGRES_USER postgres.env)\" = netsecai_migrator", "POSTGRES_HOST_PORT postgres.env)\" = 55432", "BACKUP_DIR postgres.env)\" = /var/backups/netsecai", "NETSECAI_DB_IDENTITY"]) expect(lib).toContain(value);
    for (const file of ["deploy-netsec/server/update.sh", "deploy-netsec/database-grants.sh", "deploy-netsec/backup.sh", "deploy-netsec/server/status.sh", "deploy-netsec/infra-check.sh"]) {
      expect(read(file), file).toContain("assert_database_identity");
    }
    expect(read("deploy-netsec/install.sh")).toContain("initialize_database_identity");
    const backup = read("deploy-netsec/backup.sh");
    expect(backup).toContain('BACKUP_DIR=/var/backups/netsecai');
    expect(backup).toContain('must not be a symlink');
  });

  test("canonical backup path cannot be redirected", () => {
    const backup = read("deploy-netsec/backup.sh");
    expect(backup).toContain("BACKUP_DIR=/var/backups/netsecai");
    expect(backup).toContain("assert_netsec_backup_dir");
    expect(backup).toContain("env_value POSTGRES_USER postgres.env");
    expect(backup).not.toContain('BACKUP_DIR="${BACKUP_DIR:-');
  });

  test("clean database bootstrap provisions final least-privilege roles", () => {
    const grants = read("deploy-netsec/database-grants.sh");
    for (const value of ["netsecai_runtime", "netsecai_auth", "env_value POSTGRES_USER postgres.env", "env_value POSTGRES_RUNTIME_PASSWORD postgres.env", "CREATE ROLE %I LOGIN PASSWORD", "GRANT SELECT, INSERT, UPDATE ON TABLE users", "auth_sessions", "auth_accounts", "auth_verifications", "auth_two_factors", "GRANT SELECT, INSERT ON TABLE auth_attempts", "DROP ROLE aichat_auth"]) expect(grants).toContain(value);
    const update = read("deploy-netsec/server/update.sh");
    expect(update).toContain("CREATE ROLE aichat_auth NOLOGIN");
    expect(update).toContain('"$SHARED/postgres/database-grants.sh"');
  });

  test("chain smoke uses the application's actual local session cookie", () => {
    const lib = read("deploy-netsec/server/lib.sh");
    expect(lib).toContain("Cookie: padhai_session=$signed");
    expect(lib).not.toContain("netsecai_session=");
  });

  test("server scripts source sibling safety helper", () => {
    for (const file of ["deploy-netsec/server/install-units.sh", "deploy-netsec/server/update.sh"]) {
      const source = read(file);
      expect(source, file).toContain('pwd)/safety.sh');
      expect(source, file).not.toContain('pwd)/server/safety.sh');
    }
  });

  test("workstation status composes a runnable host script", () => {
    const status = read("deploy-netsec/status.sh");
    expect(status).toContain("ssh \"$HOST\" 'bash /opt/netsecai/app/deploy-netsec/server/status.sh'");
    expect(status).not.toContain("cat deploy-netsec/server/lib.sh deploy-netsec/server/status.sh | ssh");
  });

  test("script hygiene defects stay fixed", () => {
    expect(read("deploy-netsec/server/safety.sh").startsWith("#!/usr/bin/env bash\n")).toBe(true);
    expect(read("deploy-netsec/status.sh")).toContain("STOP: set NETSEC_DEPLOY_HOST");
    expect(read("deploy-netsec/status.sh")).not.toContain("STOR:");
  });
});
