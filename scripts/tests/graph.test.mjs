import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

// Graphify as this repository uses it (docs/graphify.md, `bun run graph`), on a throwaway repository:
// the pinned version builds a code graph, keeps the relationships between modules, follows changes,
// forgets deleted code, never reads what is ignored, and recovers from a broken graph. These are the
// contract tests of a third-party tool: they check what we rely on, not graphify's internals.

const ROOT = join(import.meta.dir, "..", "..");
const runner = readFileSync(join(ROOT, "scripts", "run.mjs"), "utf8");
const PIN = runner.match(/const GRAPHIFY = "([^"]+)"/)?.[1];
const REVIEWED = runner.match(/const GRAPHIFY_REVIEWED = "([^"]+)"/)?.[1];
// The same environment `bun run graph` uses (scripts/run.mjs GRAPHIFY_ENV).
const ENV = { GRAPHIFY_NO_AUTO_REFRESH: "1", PYTHONHASHSEED: "0", PYTHONSAFEPATH: "1" };
const SECRET = "FAKE-SECRET-MARKER-7f3a9c";
const available = spawnSync("uv", ["--version"], { encoding: "utf8" }).status === 0;
if (!available) console.warn("graph tests SKIPPED: uv is not installed (NOT TESTED)");

let repo;

function write(path, text) {
  mkdirSync(join(repo, path, ".."), { recursive: true });
  writeFileSync(join(repo, path), text);
}

function graphify(...args) {
  const result = spawnSync("uv", ["tool", "run", "--exclude-newer", REVIEWED, "--from", PIN, "graphify", "update", repo, ...args], {
    cwd: repo,
    encoding: "utf8",
    timeout: 600_000,
    env: { ...process.env, ...ENV },
  });
  return { status: result.status, output: `${result.stdout}\n${result.stderr}` };
}

function graph() {
  const data = JSON.parse(readFileSync(join(repo, "graphify-out", "graph.json"), "utf8"));
  const nodes = new Map(data.nodes.map((node) => [node.id, node]));
  const file = (id) => nodes.get(id)?.source_file?.replaceAll("\\", "/");
  return {
    files: new Set(data.nodes.map((node) => node.source_file?.replaceAll("\\", "/"))),
    links: data.links.map((link) => ({ from: file(link.source), to: file(link.target), relation: link.relation })),
  };
}

const depends = (g, from, to) =>
  g.links.some(
    (link) =>
      link.from === from && link.to === to && ["calls", "imports", "imports_from", "indirect_call", "references"].includes(link.relation),
  );

function everyFile(dir) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    return statSync(path).isDirectory() ? everyFile(path) : [path];
  });
}

describe.skipIf(!available)("Graphify (the pinned version, as `bun run graph` runs it)", () => {
  beforeAll(() => {
    expect(PIN).toMatch(/^graphifyy==\d+\.\d+\.\d+$/);
    expect(REVIEWED).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    repo = mkdtempSync(join(tmpdir(), "graph-test-"));
    write("app/__init__.py", "");
    write("app/routes.py", "from app.service import create_order\n\n\ndef handle():\n    return create_order(1)\n");
    write("app/service.py", "from app.store import save\n\n\ndef create_order(n):\n    return save(n)\n");
    write("app/store.py", "def save(n):\n    return n\n");
    write("web/client.ts", "export function fetchOrders(): number {\n  return 1;\n}\n");
    write("web/api.ts", 'import { fetchOrders } from "./client";\n\nexport function load(): number {\n  return fetchOrders();\n}\n');
    // What must never reach the graph: git-ignored keys, and code only .graphifyignore excludes.
    write(".gitignore", "secrets.txt\n.env\ngraphify-out/\n");
    write("secrets.txt", `${SECRET}\n`);
    write(".env", `API_KEY=${SECRET}\n`);
    write(".graphifyignore", "generated/\n");
    write("generated/big.py", `def generated_only():\n    """${SECRET} must not be copied into the graph."""\n`);
    for (const args of [["init", "-q"], ["add", "."], ["-c", "user.email=t@example.com", "-c", "user.name=t", "commit", "-qm", "fixture"]]) {
      expect(spawnSync("git", args, { cwd: repo }).status).toBe(0);
    }
  });

  afterAll(() => {
    if (repo) rmSync(repo, { recursive: true, force: true });
  });

  test("builds the code graph with the relationships between modules", () => {
    const run = graphify();
    expect(run.status, run.output).toBe(0);
    const g = graph();
    for (const file of ["app/routes.py", "app/service.py", "app/store.py", "web/api.ts", "web/client.ts"]) expect(g.files).toContain(file);
    expect(depends(g, "app/routes.py", "app/service.py")).toBe(true);
    expect(depends(g, "app/service.py", "app/store.py")).toBe(true);
    expect(depends(g, "web/api.ts", "web/client.ts")).toBe(true);
  }, 600_000);

  test("never reads ignored files: no key, no excluded code, in any output", () => {
    const g = graph();
    for (const file of g.files) expect(file ?? "").not.toMatch(/(^|\/)(secrets\.txt|\.env|generated\/)/);
    for (const path of everyFile(join(repo, "graphify-out"))) expect(readFileSync(path, "utf8"), path).not.toContain(SECRET);
  });

  test("an update adds new code and its dependencies", () => {
    write("app/audit.py", "from app.service import create_order\n\n\ndef audit():\n    return create_order(2)\n");
    const run = graphify();
    expect(run.status, run.output).toBe(0);
    const g = graph();
    expect(g.files).toContain("app/audit.py");
    expect(depends(g, "app/audit.py", "app/service.py")).toBe(true);
  }, 600_000);

  test("an update forgets deleted code (no stale nodes or edges)", () => {
    rmSync(join(repo, "app", "store.py"));
    write("app/service.py", "def create_order(n):\n    return n\n");
    const run = graphify();
    expect(run.status, run.output).toBe(0);
    const g = graph();
    expect(g.files).not.toContain("app/store.py");
    expect(g.links.some((link) => link.from === "app/store.py" || link.to === "app/store.py")).toBe(false);
    expect(g.files).toContain("app/service.py");
  }, 600_000);

  test("a file that does not parse never stops the build", () => {
    write("app/broken.py", "def broken(:\n    pass\n");
    const run = graphify();
    expect(run.status, run.output).toBe(0);
    const g = graph();
    expect(g.files).toContain("app/routes.py");
    expect(depends(g, "app/routes.py", "app/service.py")).toBe(true);
  }, 600_000);

  test("a corrupt graph fails loudly, and a clean rebuild recovers (bun run graph -- --rebuild)", () => {
    writeFileSync(join(repo, "graphify-out", "graph.json"), "{ not json");
    const broken = graphify();
    expect(broken.status).not.toBe(0); // never builds on a graph it can't read
    expect(broken.output).toMatch(/full rebuild/i);
    rmSync(join(repo, "graphify-out"), { recursive: true, force: true }); // what --rebuild does
    const run = graphify();
    expect(run.status, run.output).toBe(0);
    const g = graph();
    expect(g.files).toContain("app/audit.py");
    expect(depends(g, "web/api.ts", "web/client.ts")).toBe(true);
  }, 600_000);
});
