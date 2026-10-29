#!/usr/bin/env bash
#
# The local security gate: bun run security
#
# GitHub Actions do not run for this repository, so this is the enforcement. It scans the
# checkout and, on PASS of a clean tree, writes a receipt bound to the commit
# (.git/netsec-security-gate, never committed). deploy-netsec/deploy.sh refuses a commit without
# a PASS receipt unless NETSEC_SECURITY_GATE=skip:<reason> is given (logged).
#
#   a. semgrep     registry packs p/python p/typescript p/javascript p/secrets p/security-audit
#                  (downloaded at run time, internal use only: never vendor them) + .semgrep/netsec.yml
#                  and its self-test; any finding fails (--error)
#   b. gitleaks    the working tree (tracked + untracked files, never git-ignored ones) and the
#                  full history (every ref); a finding in released history (branches, tags,
#                  remotes) fails, one only in local refs (refs/agents/...) is a warning
#   c. pip-audit   backend/requirements.lock (hashes required, no pip); any vulnerability fails
#   d. bun audit   frontend/ (bun.lock); high or critical fails, moderate and low are listed
#   e. zizmor      .github/workflows/ (offline, default persona); medium or above fails
#   f. tools       exact versions (below); any other version fails
#
# Mode: a clean tree at a commit is "receipt" mode; uncommitted or untracked changes are
# "check only" (same scans, no receipt). The steps run concurrently; each step's output is
# buffered and printed in order. Secrets are never printed: gitleaks runs with --redact and
# every finding is shown as rule id + path:line only (Semgrep's matched lines are never shown).
#
# Runs from Git Bash on Windows. semgrep, gitleaks, pip-audit and zizmor run in WSL
# (Ubuntu-24.04, /root/sec-tools) on a copy staged from Windows git: WSL's git cannot read this
# checkout's .git quickly or reliably over /mnt/c, so the files git would track are tarred into a
# WSL temp dir and the history goes in as a git bundle of every ref. bun audit runs on Windows.
set -uo pipefail

DISTRO=Ubuntu-24.04
EXPECT_SEMGREP=1.182.1
EXPECT_GITLEAKS=8.30.1
EXPECT_PIP_AUDIT=2.10.1
EXPECT_ZIZMOR=1.31.1
EXPECT_BUN=1.3.14

cd "$(git rev-parse --show-toplevel)" || exit 2
START=$(date +%s)
OUT=$(mktemp -d) # Windows-side buffers (outside the repository)
D=""             # WSL-side work dir (outside the repository)

# `--exec` runs bash directly: `wsl -- cmd` would re-parse the command line in a login shell.
wsl_run() { MSYS_NO_PATHCONV=1 wsl.exe -d "$DISTRO" -u root --exec bash -c "$@"; }
# A step's script arrives on stdin and is saved before it runs, so no tool can read the rest of it.
wsl_step() { wsl_run 'cat > "$1/$2.sh" && exec bash "$1/$2.sh" "$1" < /dev/null' _ "$D" "$1"; }

cleanup() {
  trap - EXIT INT TERM
  jobs -p | xargs -r kill 2> /dev/null
  if [ -n "$D" ]; then
    wsl_run 'case "$1" in /tmp/netsec-gate.??????) pgrep -f -- "$1" | grep -vx "$$" | xargs -r kill 2> /dev/null; rm -rf -- "$1" ;; esac' _ "$D" > /dev/null 2>&1
  fi
  case "$OUT" in /tmp/tmp.*) rm -rf -- "$OUT" ;; esac
}
trap cleanup EXIT
trap 'echo; echo "interrupted"; exit 130' INT TERM

# ---------------------------------------------------------------- mode
HEAD_SHA=$(git rev-parse --verify --quiet HEAD) || HEAD_SHA=""
clean() { [ -z "$(git status --porcelain --untracked-files=normal)" ]; }
if [ -n "$HEAD_SHA" ] && clean; then
  MODE=receipt
  echo "==> security gate: receipt mode (clean tree at $HEAD_SHA; a PASS writes the deploy receipt)"
else
  MODE=check
  echo "==> security gate: check-only mode (uncommitted or untracked changes; no receipt is written)"
fi

# ---------------------------------------------------------------- stage the code and the history into WSL
D=$(wsl_run 'mktemp -d /tmp/netsec-gate.XXXXXX' | tr -d '\r')
case "$D" in /tmp/netsec-gate.??????) ;; *) echo "STOP: cannot create a work directory in WSL ($DISTRO)" >&2; exit 2 ;; esac
# What git would commit: tracked files plus untracked ones that are not ignored (never the
# ignored root *.txt key files or .env files); a tracked file deleted in the tree is skipped.
git ls-files -z --cached --others --exclude-standard \
  | while IFS= read -r -d '' f; do { [ -e "$f" ] || [ -L "$f" ]; } && printf '%s\0' "$f"; done > "$OUT/files"
(tar --null -T "$OUT/files" -cf - | wsl_run 'mkdir "$1/src" "$1/empty" && tar -xf - -C "$1/src"' _ "$D") > "$OUT/stage.log" 2>&1 &
STAGE=$!
(git bundle create --quiet - --all | wsl_run 'cat > "$1/repo.bundle" && git clone -q --mirror "$1/repo.bundle" "$1/history.git"' _ "$D") > "$OUT/bundle.log" 2>&1 &
BUNDLE=$!

# ---------------------------------------------------------------- f. tools (exact versions)
TOOLS_START=$(date +%s)
VERSIONS=$(wsl_step tools << 'EOF' 2> /dev/null | tr -d '\r'
S=/root/sec-tools/venv/bin
echo "semgrep=$(SEMGREP_ENABLE_VERSION_CHECK=0 "$S/semgrep" --version 2> /dev/null)"
echo "gitleaks=$(/root/sec-tools/gitleaks version 2> /dev/null)"
echo "pip-audit=$("$S/pip-audit" --version 2> /dev/null | awk '{print $2}')"
echo "zizmor=$("$S/zizmor" --version 2> /dev/null | awk '{print $2}')"
echo "jq=$(jq --version 2> /dev/null | sed 's/^jq-//')"
echo "git=$(git --version 2> /dev/null | awk '{print $3}')"
EOF
)
VERSIONS+=$'\n'"bun=$(bun --version 2> /dev/null | tr -d '\r')"
version() { sed -n "s/^$1=//p" <<< "$VERSIONS"; }
{
  bad=""
  for pair in "semgrep $EXPECT_SEMGREP" "gitleaks $EXPECT_GITLEAKS" "pip-audit $EXPECT_PIP_AUDIT" "zizmor $EXPECT_ZIZMOR" "bun $EXPECT_BUN"; do
    set -- $pair
    got=$(version "$1")
    [ "$got" = "$2" ] || { echo "$1 is '${got:-missing}', the gate pins $2"; bad=1; }
  done
  for tool in jq git; do [ -n "$(version $tool)" ] || { echo "$tool is missing in WSL"; bad=1; }; done
  echo "SUMMARY semgrep $(version semgrep), gitleaks $(version gitleaks), pip-audit $(version pip-audit), zizmor $(version zizmor), bun $(version bun); WSL jq $(version jq), git $(version git)"
  [ -z "$bad" ]
} > "$OUT/tools.log" 2>&1
echo "$? $(($(date +%s) - TOOLS_START))" > "$OUT/tools.rc"

wait "$STAGE" || { echo "STOP: could not stage the files into WSL:"; tail -5 "$OUT/stage.log"; exit 2; }
wait "$BUNDLE" || { echo "STOP: could not stage the git history into WSL:"; tail -5 "$OUT/bundle.log"; exit 2; }
echo "    staged $(tr -cd '\0' < "$OUT/files" | wc -c | tr -d ' ') files and the history of $(git for-each-ref | wc -l | tr -d ' ') refs ($(git rev-list --all | wc -l | tr -d ' ') commits) into WSL in $(($(date +%s) - START))s"

# ---------------------------------------------------------------- the steps, concurrently
# Each step prints its details, then one "SUMMARY <text>" line; exit 0 = PASS, otherwise FAIL.
step() { # name wsl|fn script|function
  (
    s=$(date +%s)
    if [ "$2" = wsl ]; then printf '%s\n' "$3" | wsl_step "$1"; else "$3"; fi > "$OUT/$1.log" 2>&1
    echo "$? $(($(date +%s) - s))" > "$OUT/$1.rc"
  ) &
}

# a. semgrep ------------------------------------------------------
SEMGREP_SH=$(cat << 'EOF'
set -uo pipefail
D=$1 S=/root/sec-tools/venv/bin
cd "$D/src" || exit 2
export SEMGREP_ENABLE_VERSION_CHECK=0 SEMGREP_SEND_METRICS=off
selftest() {
  [ -f .semgrep/tests/netsec.py ] && [ -f .semgrep/tests/netsec.tsx ] || { echo "fixtures missing"; return 1; }
  for t in .semgrep/tests/netsec.py .semgrep/tests/netsec.tsx; do
    "$S/semgrep" --metrics=off --test --config .semgrep/netsec.yml "$t" > "$D/selftest.out" 2>&1 || { echo "$t: $(grep -E 'did not pass|missed lines' "$D/selftest.out" | tr -s '\t ' ' ' | tr '\n' ' ')"; return 1; }
  done
}
selftest > "$D/selftest.result" 2>&1 &
# avoid-sqlalchemy-text flags every text() call, static SQL included; netsec-sql-text-built-string
# (.semgrep/netsec.yml) flags the dangerous form exactly: SQL built from strings in shipped code.
"$S/semgrep" scan --exclude-rule python.sqlalchemy.security.audit.avoid-sqlalchemy-text.avoid-sqlalchemy-text --config p/python --config p/typescript --config p/javascript --config p/secrets \
  --config p/security-audit --config .semgrep/netsec.yml --metrics=off --error --json \
  --output "$D/semgrep.json" --jobs "$(nproc)" --timeout 30 --timeout-threshold 0 --quiet . 2> "$D/semgrep.err"
rc=$?
wait $! && selftest="custom rules self-test passed" || selftest="custom rules self-test FAILED: $(cat "$D/selftest.result")"
J=$D/semgrep.json
if [ "$rc" -gt 1 ] || ! jq -e '.results' "$J" > /dev/null 2>&1; then
  echo "semgrep did not complete (exit $rc):"; tail -n 5 "$D/semgrep.err"
  echo "SUMMARY tool error (exit $rc); $selftest"; exit 2
fi
# rule id + path:line + severity only: never .extra.lines (Semgrep's secrets rules match secrets)
jq -r 'def pad(n): . + (" " * ([n - length, 0] | max));
  .results | sort_by(.extra.severity, .check_id, .path, .start.line)[]
  | "\(.extra.severity | pad(8)) \(.check_id)  \(.path):\(.start.line)"' "$J"
jq -r '[.errors[] | {t: (.type | if type == "array" then .[0] else . end), p: (.path // "-")}]
  | group_by(.t)[] | "coverage (not gating): \(length) \(.[0].t) in \(map(.p) | unique | join(", "))"' "$J"
n=$(jq '.results | length' "$J")
by=$(jq -r '.results | group_by(.extra.severity) | map("\(.[0].extra.severity) \(length)") | join(", ")' "$J")
echo "SUMMARY $n findings${by:+ ($by)} in $(jq '.paths.scanned | length' "$J") files; $selftest"
[ "$n" = 0 ] && [ "${selftest#*FAILED}" = "$selftest" ]
EOF
)

# b. gitleaks -----------------------------------------------------
GITLEAKS_SH=$(cat << 'EOF'
set -uo pipefail
D=$1 GL=/root/sec-tools/gitleaks
cd "$D/src" || exit 2
[ -f .gitleaks.toml ] || { echo "SUMMARY .gitleaks.toml is missing"; exit 2; }
# --redact: values never reach logs or reports. gitleaks:allow comments and .gitleaksignore files
# are ignored: allowlisting happens only in .gitleaks.toml.
common=(-c "$D/src/.gitleaks.toml" --redact --no-banner --ignore-gitleaks-allow -i "$D/empty" --report-format json --exit-code 3)
("$GL" dir . "${common[@]}" --log-level warn --report-path "$D/gl-dir.json" > "$D/gl-dir.out" 2>&1; echo $? > "$D/gl-dir.rc") &
("$GL" git "$D/history.git" "${common[@]}" --log-level info --report-path "$D/gl-git.json" > "$D/gl-git.out" 2>&1; echo $? > "$D/gl-git.rc") &
files=$(find . -type f | wc -l)
ignored=$({ find . -maxdepth 1 -type f -name '*.txt'; find . -name '.env*' ! -name '.env.example'; } | sed 's#^\./##')
wait
fail=0
for m in dir git; do
  rc=$(cat "$D/gl-$m.rc")
  if [ "$rc" != 0 ] && [ "$rc" != 3 ] || ! jq -e 'type == "array"' "$D/gl-$m.json" > /dev/null 2>&1; then
    echo "gitleaks $m did not complete (exit $rc):"; tail -n 3 "$D/gl-$m.out"; fail=2
  fi
done
[ "$fail" = 0 ] || { echo "SUMMARY tool error"; exit 2; }
if [ -n "$ignored" ]; then
  echo "FAIL  a key or env file is part of the scanned tree (it must stay git-ignored): $(echo $ignored)"; fail=1
fi
nd=$(jq length "$D/gl-dir.json")
jq -r '.[] | "FAIL  \(.RuleID)  \(.File):\(.StartLine)"' "$D/gl-dir.json"
[ "$nd" = 0 ] || fail=1
G=$D/history.git
git -C "$G" rev-list --branches --tags --remotes > "$D/released"
released=0 localonly=0
while IFS=$'\t' read -r rule commit file line; do
  if grep -qx "$commit" "$D/released"; then
    echo "FAIL  $rule  commit ${commit:0:12}  $file:$line  (released history)"; released=$((released + 1))
  else
    refs=$(git -C "$G" for-each-ref --contains "$commit" --format='%(refname)' | awk -F/ '{print $1 "/" $2}' | sort | uniq -c | awk '{printf "%s%s (%s refs)", s, $2, $1; s=", "}')
    echo "WARN  $rule  commit ${commit:0:12}  $file:$line  (only in local refs: $refs; not released by a deploy)"; localonly=$((localonly + 1))
  fi
done < <(jq -r '.[] | [.RuleID, .Commit, .File, (.StartLine | tostring)] | @tsv' "$D/gl-git.json")
[ "$released" = 0 ] || fail=1
[ "$localonly" = 0 ] || echo "      a real credential in local refs must still be rotated; delete those refs to clear the warning"
scanned=$(sed 's/\x1b\[[0-9;]*m//g' "$D/gl-git.out" | grep -oE '[0-9]+ commits scanned' | grep -oE '^[0-9]+')
commits=$(git -C "$G" rev-list --all | wc -l)
echo "SUMMARY tree: $nd findings in $files files (tracked + untracked, none git-ignored); history: $released findings in released history, $localonly only in local refs ($commits commits on $(git -C "$G" for-each-ref | wc -l) refs, ${scanned:-?} with changes)"
exit "$fail"
EOF
)

# c. pip-audit ----------------------------------------------------
PIP_AUDIT_SH=$(cat << 'EOF'
set -uo pipefail
D=$1
cd "$D/src" || exit 2
/root/sec-tools/venv/bin/pip-audit -r backend/requirements.lock --require-hashes --disable-pip \
  --progress-spinner off --format json --output "$D/pip.json" > "$D/pip.out" 2>&1
rc=$?
if ! jq -e '.dependencies' "$D/pip.json" > /dev/null 2>&1; then
  echo "pip-audit did not complete (exit $rc):"; tail -n 5 "$D/pip.out"; echo "SUMMARY tool error (exit $rc)"; exit 2
fi
jq -r 'def chunks(n): [range(0; length; n) as $i | .[$i:$i + n]];
  .dependencies[] | select((.vulns // []) | length > 0)
  | ([.vulns[].fix_versions[]?] | unique | sort_by(split(".") | map(tonumber? // 0)) | last) as $fix
  | ([.vulns[].id] | unique) as $ids
  | "FAIL  \(.name) \(.version): \($ids | length) advisories\(if $fix then " (fixed in \($fix))" else " (no fix yet)" end)",
    ($ids | chunks(6)[] | "        " + join(" "))' "$D/pip.json"
jq -r '.dependencies[] | select(.skip_reason) | "FAIL  \(.name): not audited (\(.skip_reason))"' "$D/pip.json"
v=$(jq '[.dependencies[] | (.vulns // [])[] | .id] | unique | length' "$D/pip.json")
p=$(jq '[.dependencies[] | select((.vulns // []) | length > 0)] | length' "$D/pip.json")
s=$(jq '[.dependencies[] | select(.skip_reason)] | length' "$D/pip.json")
echo "SUMMARY $v vulnerabilities in $p of $(jq '.dependencies | length' "$D/pip.json") packages; $s not audited"
[ "$v" = 0 ] && [ "$s" = 0 ]
EOF
)

# g. the gate's own rules (Windows) -------------------------------
# The tests that constrain this gate (exact allowlists, no blanket excludes, the receipt, the redaction):
# a commit can't loosen its own scanners' exclusions without failing here.
gate_rules() {
  bun test deploy-netsec/tests/security-gate.test.mjs > "$OUT/gate-rules.out" 2>&1
  local rc=$?
  grep -E '^\(fail\)' "$OUT/gate-rules.out" | sed 's/^/FAIL  /'
  echo "SUMMARY $(grep -Eo '[0-9]+ pass' "$OUT/gate-rules.out" | tail -1), $(grep -Eo '[0-9]+ fail' "$OUT/gate-rules.out" | tail -1) (deploy-netsec/tests/security-gate.test.mjs)"
  return $rc
}

# d. bun audit (Windows) -------------------------------------------
bun_audit() {
  (cd frontend && bun audit --json) > "$OUT/bun.json" 2> "$OUT/bun.err"
  BUN_AUDIT_RC=$? bun -e '
    const rc = process.env.BUN_AUDIT_RC;
    let report;
    try { report = JSON.parse(await Bun.stdin.text()); } catch { report = null; }
    if (!report || typeof report !== "object" || Object.values(report).some((v) => !Array.isArray(v))) {
      console.log(`bun audit did not complete (exit ${rc})`);
      console.log(`SUMMARY tool error (exit ${rc})`);
      process.exit(2);
    }
    const order = ["critical", "high", "moderate", "low"];
    const rows = Object.entries(report)
      .flatMap(([pkg, list]) => list.map((a) => ({ pkg, sev: a.severity, id: String(a.url ?? a.id).split("/").pop(), range: a.vulnerable_versions })))
      .sort((a, b) => order.indexOf(a.sev) - order.indexOf(b.sev) || a.pkg.localeCompare(b.pkg));
    for (const r of rows) console.log(`${["critical", "high"].includes(r.sev) ? "FAIL" : "info"}  ${r.sev.padEnd(8)} ${r.pkg} ${r.range}  ${r.id}`);
    const count = (s) => rows.filter((r) => r.sev === s).length;
    console.log(`SUMMARY ${order.map((s) => `${s} ${count(s)}`).join(", ")} (high or critical fails)`);
    process.exit(count("critical") + count("high") > 0 ? 1 : 0);
  ' < "$OUT/bun.json"
}

# e. zizmor -------------------------------------------------------
ZIZMOR_SH=$(cat << 'EOF'
set -uo pipefail
D=$1 Z=/root/sec-tools/venv/bin/zizmor
cd "$D/src" || exit 2
[ -d .github/workflows ] || { echo "SUMMARY no .github/workflows: nothing to audit"; exit 0; }
# Offline: the online audits need a GitHub token and are not run.
"$Z" --offline --no-progress --no-exit-codes --format json .github/workflows/ > "$D/ziz.json" 2> "$D/ziz.err"
rc=$?
"$Z" --offline --no-progress --no-exit-codes --persona auditor --format json .github/workflows/ > "$D/ziz-auditor.json" 2> /dev/null
if [ "$rc" != 0 ] || ! jq -e 'type == "array"' "$D/ziz.json" > /dev/null 2>&1; then
  echo "zizmor did not complete (exit $rc):"; tail -n 3 "$D/ziz.err"; echo "SUMMARY tool error (exit $rc)"; exit 2
fi
row='.[] | (first(.locations[] | select(.symbolic.kind == "Primary")) // .locations[0]) as $l
  | "\(.determinations.severity) \(.ident)  \($l.symbolic.key.Local.verbatim_path // "?"):\(($l.concrete.location.start_point.row // -1) + 1)"'
jq -r "$row" "$D/ziz.json" | sort -u > "$D/ziz.rows"
sed -E 's/^(Medium|High) /FAIL  \1 /; t; s/^/info  /' "$D/ziz.rows"
jq -r "$row" "$D/ziz-auditor.json" 2> /dev/null | sort -u | grep -vxF -f "$D/ziz.rows" | sed 's/^/info  (auditor persona, not gating) /'
n=$(wc -l < "$D/ziz.rows") bad=$(grep -cE '^(Medium|High) ' "$D/ziz.rows")
echo "SUMMARY $n findings, $bad medium or high (default persona; medium or above fails); $(jq length "$D/ziz-auditor.json" 2> /dev/null || echo ?) with the auditor persona"
[ "$bad" = 0 ]
EOF
)

step semgrep wsl "$SEMGREP_SH"
step gitleaks wsl "$GITLEAKS_SH"
step pip-audit wsl "$PIP_AUDIT_SH"
step bun-audit fn bun_audit
step zizmor wsl "$ZIZMOR_SH"
step gate-rules fn gate_rules
wait

# ---------------------------------------------------------------- report, in order
echo
FAILED=0
for name in tools semgrep gitleaks pip-audit bun-audit zizmor gate-rules; do
  read -r rc secs < "$OUT/$name.rc" 2> /dev/null || { rc=2 secs=0; }
  log=$(tr -d '\r' < "$OUT/$name.log" 2> /dev/null)
  summary=$(sed -n 's/^SUMMARY //p' <<< "$log" | tail -1)
  if [ "$rc" = 0 ]; then verdict=PASS; else verdict=FAIL FAILED=$((FAILED + 1)); fi
  printf '%s %-9s %4ss  %s\n' "$verdict" "$name" "$secs" "${summary:-no summary (exit $rc)}"
  grep -v '^SUMMARY ' <<< "$log" | grep -v '^$' | sed 's/^/       /'
done
echo

# ---------------------------------------------------------------- receipt
RECEIPT=$(git rev-parse --git-path netsec-security-gate)
if [ "$FAILED" = 0 ] && [ "$MODE" = receipt ]; then
  if [ "$(git rev-parse HEAD)" = "$HEAD_SHA" ] && clean; then
    {
      echo "commit=$HEAD_SHA"
      echo "result=PASS"
      echo "tree=$(git rev-parse "$HEAD_SHA^{tree}")"
      echo "time=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      echo "operator=$(git config user.name 2> /dev/null)"
      for tool in semgrep gitleaks pip-audit zizmor bun; do echo "$tool=$(version $tool)"; done
    } > "$RECEIPT.tmp" && mv -f "$RECEIPT.tmp" "$RECEIPT"
    echo "RESULT: PASS. Receipt written for $HEAD_SHA ($RECEIPT): deploy-netsec/deploy.sh will release this commit."
  else
    echo "RESULT: PASS, but HEAD or the tree changed during the run: no receipt. Run bun run security again."
    FAILED=1
  fi
elif [ "$FAILED" = 0 ]; then
  echo "RESULT: PASS (check only). Commit, then run bun run security on the clean tree for a deploy receipt."
else
  if [ -n "$HEAD_SHA" ] && [ -f "$RECEIPT" ] && grep -qx "commit=$HEAD_SHA" "$RECEIPT"; then
    rm -f -- "$RECEIPT"
    echo "Removed the receipt for $HEAD_SHA."
  fi
  echo "RESULT: FAIL ($FAILED of 7 steps). No receipt: deploy-netsec/deploy.sh will refuse this commit."
fi
echo "Time: $(($(date +%s) - START))s"
[ "$FAILED" = 0 ]
