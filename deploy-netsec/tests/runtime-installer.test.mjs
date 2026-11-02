import { describe, expect, test } from "bun:test";
import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { chmodSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";

const shellQuote = (value) => `'${value.replaceAll("'", `'"'"'`)}'`;
const WINDOWS = process.platform === "win32";
const WSL = WINDOWS ? execFileSync("where.exe", ["wsl.exe"], { encoding: "utf8" }).trim().split(/\r?\n/)[0] : null;
const BASH = WINDOWS
  ? execFileSync("where.exe", ["bash"], { encoding: "utf8" }).trim().split(/\r?\n/)[0]
  : execFileSync("which", ["bash"], { encoding: "utf8" }).trim();
const toBashPath = (path) => {
  if (!WINDOWS) return path;
  const absolute = path.replaceAll("\\", "/");
  return `/mnt/${absolute[0].toLowerCase()}${absolute.slice(2)}`;
};
const runBash = (input, options = {}) =>
  WINDOWS
    ? spawnSync(WSL, ["env", ...(options.envArgs ?? []), "bash", "-s"], { ...options, input })
    : spawnSync(BASH, ["-s"], { ...options, env: { ...process.env, ...(options.env ?? {}) }, input });
const ROOT = join(import.meta.dir, "..", "..");
const read = (path) => readFileSync(join(ROOT, path), "utf8");
const install = read("deploy-netsec/install.sh");
const lib = read("deploy-netsec/server/lib.sh");
const manifest = read("deploy-netsec/bun-sha256.manifest");
const ensureBunSource = lib.slice(lib.indexOf("bun_release_asset()"), lib.indexOf("# Run an install or build capped"));
const version = read(".bun-version").trim();
function command(root, name, source) {
  const path = join(root, "mock-bin", name);
  writeFileSync(path, `#!/usr/bin/env bash\n${source}\n`, "utf8");
  chmodSync(path, 0o755);
}

function runFixture({ alterArchive = false, digest = "fixture" } = {}) {
  const fixture = mkdtempSync(join(tmpdir(), "netsec-bun-installer-"));
  const fixtureBash = toBashPath(fixture);
  try {
    mkdirSync(join(fixture, "mock-bin"));
    mkdirSync(join(fixture, "repo", "deploy-netsec"), { recursive: true });
    mkdirSync(join(fixture, "root"), { recursive: true });
    writeFileSync(join(fixture, "repo", ".bun-version"), "1.3.14\n");
    writeFileSync(join(fixture, "fixture.zip"), alterArchive ? "altered" : "fixture");
    const hash = digest === "fixture"
      ? createHash("sha256").update(readFileSync(join(fixture, "fixture.zip"))).digest("hex")
      : digest;
    writeFileSync(join(fixture, "repo", "deploy-netsec", "bun-sha256.manifest"), `1.3.14 bun-linux-x64 ${hash}\n`);
    writeFileSync(join(fixture, "events"), "");

    command(fixture, "uname", 'printf "x86_64\\n"');
    command(fixture, "sha256sum", `if [ "$1" = -c ]; then read expected file; actual=$(tr -d '\\r\\n' < "${fixtureBash}/fixture-hash"); [ "$expected" = "$actual" ]; else exit 2; fi`);
    command(fixture, "curl", `printf "curl:%s\\n" "$*" >> "${fixtureBash}/events"; output=""; while [ "$#" -gt 0 ]; do if [ "$1" = -o ]; then output=$2; shift 2; else shift; fi; done; mkdir -p "$(dirname "$output")"; cp "${fixtureBash}/fixture.zip" "$output"; printf '%s\\n' '${createHash("sha256").update(readFileSync(join(fixture, "fixture.zip"))).digest("hex")}' > "${fixtureBash}/fixture-hash"`);
    command(fixture, "unzip", `printf "unzip\\n" >> "${fixtureBash}/events"; out=""; while [ "$#" -gt 0 ]; do if [ "$1" = -d ]; then out=$2; shift 2; else shift; fi; done; mkdir -p "$out/bun-linux-x64"; printf '#!/usr/bin/env bash\\nprintf "1.3.14\\n"\\n' > "$out/bun-linux-x64/bun"; chmod 755 "$out/bun-linux-x64/bun"`);

    const rootBash = `${fixtureBash}/root-${Date.now()}-${Math.random().toString(16).slice(2)}`;
    const bunBash = `${rootBash}/bun`;
    const shell = `set -euo pipefail
export PATH=${shellQuote(`${fixtureBash}/mock-bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`)}
hash -r
ROOT=${shellQuote(rootBash)}
BUN=${shellQuote(bunBash)}
log() { :; }
note() { :; }
die() { printf '%s\\n' "$*" >&2; exit 1; }
${ensureBunSource}
ensure_bun ${shellQuote(`${fixtureBash}/repo`)}`;
    const result = runBash(shell, {
      cwd: ROOT,
      encoding: "utf8",
      envArgs: [`PATH=${fixtureBash}/mock-bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`],
      env: { PATH: `${fixtureBash}/mock-bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin` },
    });
    const stdout = result.stdout?.toString() ?? "";
    const stderr = result.stderr?.toString() ?? "";
    return { result: { status: result.status, stdout, stderr, error: result.error ? String(result.error) : "" }, events: readFileSync(join(fixture, "events"), "utf8") };
  } finally {
    rmSync(fixture, { recursive: true, force: true });
  }
}

describe("pinned Bun runtime installer", () => {
  test("bootstrap and update share one reviewed installer without curl-pipe-shell", () => {
    expect(install).toContain('ensure_bun "$SRC"');
    expect(install).not.toContain("bun.sh/install");
    expect(lib.match(/^ensure_bun\(\)/gm)).toHaveLength(1);
    expect(lib).not.toContain("bun.sh/install");
    expect(`${install}\n${lib}`).not.toMatch(/curl[^\n]*\|[^\n]*(bash|sh)/);
  });

  test("the exact version and supported architecture select an immutable release URL", () => {
    expect(lib).toContain("x86_64|amd64) printf '%s\\n' bun-linux-x64");
    expect(lib).toContain('url="https://github.com/oven-sh/bun/releases/download/bun-v$want/$asset.zip"');
    expect(lib).toContain('*) die "unsupported Linux architecture for Bun: $1"');
  });

  test("unsupported architectures fail closed before download", () => {
    const start = lib.indexOf("bun_release_asset() {");
    const end = lib.indexOf("\n}\n\nensure_bun()", start) + 2;
    const result = runBash(
      `set -euo pipefail\ndie() { printf '%s\\n' "$*" >&2; exit 1; }\n${lib.slice(start, end)}\nbun_release_asset aarch64\n`,
      { encoding: "utf8" },
    );
    expect(result.status).not.toBe(0);
    expect(result.stderr.toString()).toContain("unsupported Linux architecture for Bun: aarch64");
  });

  test("the repository digest is version-pinned and verification precedes extraction", () => {
    expect(manifest).toContain(`${version} bun-linux-x64`);
    expect(lib.indexOf("sha256sum -c --status")).toBeLessThan(lib.indexOf('unzip -q "$archive"'));
    expect(lib).toContain("umask 077");
    expect(lib).toContain('[ ! -L "$tmp" ] && [ -d "$tmp" ]');
    expect(lib.indexOf('unzip -q "$archive"')).toBeLessThan(lib.indexOf('install -m 755 "$staged/$asset/bun" "$BUN"'));
    expect(lib).toContain('[ "$("$BUN" --version)" = "$want" ]');
  });

  test("a verified fixture installs and checks the pinned version", () => {
    const { result, events } = runFixture();
    expect(result.status, `${result.stderr}\n${result.error}`).toBe(0);
    expect(events).toContain("curl:-fsSL --proto =https -o");
    expect(events).toContain("unzip");
  });

  test("an altered archive is rejected before extraction", () => {
    const { result, events } = runFixture({ digest: "0".repeat(64) });
    expect(result.status).not.toBe(0);
    expect(result.stderr).toContain("failed its repository-controlled SHA-256 check");
    expect(events).toContain("curl:");
    expect(events).not.toContain("unzip");
  });

  test("the reviewed digest is a lowercase SHA-256 value", () => {
    const reviewed = manifest.match(new RegExp(`^${version} bun-linux-x64 ([0-9a-f]{64})$`, "m"))?.[1];
    expect(reviewed).toBe("951ee2aee855f08595aeec6225226a298d3fea83a3dcd6465c09cbccdf7e848f");
  });

  test("shell sources remain valid", () => {
    for (const path of ["deploy-netsec/install.sh", "deploy-netsec/server/lib.sh"]) {
      const result = spawnSync(BASH, ["-n", path], { cwd: ROOT, encoding: "utf8" });
      expect(result.status, `${path}: ${result.stderr}`).toBe(0);
    }
  });
});
