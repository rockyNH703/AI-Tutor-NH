import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dir, "..", "..");
const read = (path) => readFileSync(join(ROOT, path), "utf8");

const compose = Bun.YAML.parse(read("deploy/compose/docker-compose.yml"));

describe("production database isolation", () => {
  test("Compose uses dedicated PadhAI resources and a configurable loopback port", () => {
    expect(compose.name).toBe("padhai-postgres");
    expect(compose.services.postgres.ports).toEqual(["127.0.0.1:${POSTGRES_HOST_PORT:-55432}:5432"]);
    expect(compose.volumes.pgdata.name).toBe("padhai_postgres_data");
    expect(compose.networks.postgres.name).toBe("padhai_postgres_network");
    expect(compose.services.postgres.networks).toEqual(["postgres"]);
  });

  test("installer collision checks precede package mutation", () => {
    const install = read("deploy/install.sh");
    expect(install.indexOf("POSTGRES_HOST_PORT=$(env_value")).toBeGreaterThan(-1);
    expect(install.indexOf("POSTGRES_HOST_PORT=$(env_value")).toBeLessThan(install.indexOf("apt-get update"));
    expect(install).toContain("padhai_postgres_network");
    expect(install).toContain("padhai_postgres_data");
  });

  test("backup has no shared-schema exception and uses a dedicated target", () => {
    const backup = read("deploy/backup.sh");
    expect(backup).not.toContain("ncert_knowledge");
    expect(backup).not.toContain("--exclude-schema");
    expect(backup).toContain("/var/backups/padhai");
    expect(backup).toContain("padhai-$(date");
    expect(backup).toContain("unexpected non-system schemas");
    expect(backup).toContain("information_schema.schemata");
  });

  test("infra checks fail when an unrelated schema is present", () => {
    const check = read("deploy/infra-check.sh");
    expect(check).toContain("information_schema.schemata");
    expect(check).toContain("unexpected database schemas");
  });

  test("migration owner is optional for compatibility and runtime grants are installed", () => {
    expect(read("backend/alembic/env.py")).toContain('os.environ.get("MIGRATION_DATABASE_URL")');
    expect(read("backend/scripts/migration_status.py")).toContain('os.environ.get("MIGRATION_DATABASE_URL")');
    expect(read("deploy/server/update.sh")).toContain('EnvironmentFile="$SECRETS/migration.env"');
    expect(read("deploy/server/update.sh")).toContain('database-grants.sh');
    const grants = read("deploy/database-grants.sh");
    expect(grants).toContain("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES");
    expect(grants).not.toContain("CREATE ROLE");
  });
});
