import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";

const status = readFileSync(new URL("../server/status.sh", import.meta.url), "utf8");

// K-10b: status:netsec stayed green while the public site was down or the backups had stopped.
describe("status.sh fails on what users and recovery depend on", () => {
  test("a public /login that does not answer 200 is a failure, not information", () => {
    expect(status).not.toContain("(information: Cloudflare/DNS)");
    expect(status).toMatch(/\[ "\$code" = 200 \] && ok "public[^\n]*\n\s*\|\| bad "public/);
    expect(status).toContain('bad "web.env NEXT_PUBLIC_SITE_URL is not set');
  });

  test("a stopped backup timer or no dump in 26 hours is a failure", () => {
    expect(status).toMatch(/is-enabled netsecai-pg-backup\.timer[^\n]*= enabled \] && ok[^\n]*\n\s*\|\| bad/);
    expect(status).toContain("-name 'netsecai-*.dump' -type f -mmin -1560");
    expect(status).toMatch(/-gt 0 \] && ok "a backup from the last 26 hours[^\n]*\n\s*\|\| bad "no backup/);
  });
});
