#!/bin/bash
# tests/baseline.sh: the marketplace's own security baseline scanner, run locally on a clean export.
#
#   tests/baseline.sh <folder holding the scanner modules>
#
# The scanner is not vendored here. Fetch the seven modules once, pinned to the commit the research
# was done against, into any folder outside the repository:
#
#   REF=a8cf631265b39e3daa76141f5523d89bd291221b
#   for f in security-baseline-analysis.mjs security-baseline-policy.mjs security-baseline-scope.mjs \
#            security-baseline-limits.mjs security-baseline-error.mjs security-github-snapshot.mjs \
#            github-repository.mjs; do
#     gh api -H "Accept: application/vnd.github.raw" \
#       "repos/omacom/omarchy-plugin-marketplace/contents/scripts/$f?ref=$REF" > "$DIR/$f"
#   done
#
# The harness approximates resolveSecuritySnapshot's file selection (excluded folders, scanned
# extensions, extensionless and executable files, forced entry points) and prints the outcome, each
# finding and each capability with its evidence. Expected for this plugin: outcome review-required,
# one capability (service-management: the transient systemd user units are the product), no findings.
# Exit 0 when that holds, 1 when it does not, 2 when the scanner or node is missing.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCANNER="${1:-}"
MODULES="security-baseline-analysis.mjs security-baseline-policy.mjs security-baseline-scope.mjs
security-baseline-limits.mjs security-baseline-error.mjs security-github-snapshot.mjs github-repository.mjs"

if [ -z "$SCANNER" ] || [ ! -d "$SCANNER" ]; then
  echo "baseline: pass the folder holding the scanner modules (see the header of this script)"
  exit 2
fi
for m in $MODULES; do
  if [ ! -s "$SCANNER/$m" ]; then
    echo "baseline: $SCANNER/$m is missing (see the header of this script)"
    exit 2
  fi
done
NODE="$(command -v node || true)"
if [ -z "$NODE" ]; then
  echo "baseline: node is required to run the scanner"
  exit 2
fi

W="$(mktemp -d "${TMPDIR:-/tmp}/ap4a-baseline.XXXXXX")" || exit 2
trap 'rm -rf "$W"' EXIT INT TERM
mkdir -p "$W/scripts" "$W/export"
for m in $MODULES; do cp "$SCANNER/$m" "$W/scripts/$m"; done

# The marketplace reads the pushed tree. Export what git tracks when this is a checkout, otherwise
# the working tree without caches.
if git -C "$REPO" rev-parse --git-dir >/dev/null 2>&1; then
  git -C "$REPO" archive --format=tar HEAD | tar -x -C "$W/export"
  SRC="git HEAD"
else
  (cd "$REPO" && find . -path ./.git -prune -o -name __pycache__ -prune -o -name '*.pyc' -prune -o -type f -print) |
    while IFS= read -r f; do
      mkdir -p "$W/export/$(dirname "$f")"
      cp -p "$REPO/$f" "$W/export/$f"
    done
  SRC="working tree"
fi

REPO_NAME="$(/usr/bin/python3 -I -S -B -c '
import json, sys
home = json.load(open(sys.argv[1])).get("homepage", "")
print("/".join(home.rstrip("/").split("/")[-2:]) if home.startswith("https://github.com/") else "owner/repo")
' "$REPO/manifest.json")"

cat >"$W/harness.mjs" <<'EOF'
import fs from "node:fs";
import path from "node:path";
import { detectElevatedCapabilities, detectUnsafeRemoteExecution } from "./scripts/security-baseline-analysis.mjs";
import { isSecurityScanPath } from "./scripts/security-baseline-scope.mjs";
import { isBinaryAssetPath } from "./scripts/security-github-snapshot.mjs";
const root = process.argv[2], repo = process.argv[3];
const excluded = new Set([".github", "coverage", "docs", "fixtures", "node_modules", "spec", "specs", "test", "tests"]);
const walk = (d, r = "") => fs.readdirSync(d, { withFileTypes: true }).flatMap((e) => e.name === ".git" ? [] :
  e.isDirectory() ? walk(path.join(d, e.name), r ? `${r}/${e.name}` : e.name) :
  e.isFile() ? [{ abs: path.join(d, e.name), path: r ? `${r}/${e.name}` : e.name }] : []);
let manifest = {};
try { manifest = JSON.parse(fs.readFileSync(path.join(root, "manifest.json"), "utf8")); } catch {}
const forced = new Set(Object.values(manifest.entryPoints || {}));
const files = walk(root).flatMap((f) => {
  const mode = (fs.statSync(f.abs).mode & 0o111) ? "100755" : "100644";
  const parts = f.path.toLowerCase().split("/");
  if (parts.slice(0, -1).some((x) => excluded.has(x)) && !forced.has(f.path)) return [];
  if (!(isSecurityScanPath(f.path) || mode === "100755" || !parts.at(-1).includes(".") || forced.has(f.path))) return [];
  if (isBinaryAssetPath(f.path) && mode !== "100755") return [];
  return [{ path: f.path, content: fs.readFileSync(f.abs, "utf8"), mode, binary: false }];
});
const findings = detectUnsafeRemoteExecution(files, repo), caps = detectElevatedCapabilities(files, repo);
console.log("SCANNED:", files.length, "files");
console.log("OUTCOME:", findings.length ? "needs-fixes" : caps.length ? "review-required" : "passed");
for (const x of findings) console.log("FINDING", x.ruleId, JSON.stringify(x.evidence));
for (const c of caps) console.log("CAP", c.id, JSON.stringify(c.evidence.map((e) => `${e.path}:${e.line} ${String(e.snippet).slice(0, 100)}`)));
EOF

echo "=== marketplace security baseline ($SRC, scanner in $SCANNER) ==="
OUT="$(cd "$W" && "$NODE" harness.mjs "$W/export" "$REPO_NAME" 2>&1)"; NRC=$?
printf '%s\n' "$OUT"
if [ "$NRC" -ne 0 ]; then
  echo "FAIL  the scanner did not run (exit $NRC)"
  exit 1
fi
fail=0
printf '%s\n' "$OUT" | grep -qx 'OUTCOME: review-required' || { echo "FAIL  outcome is not review-required"; fail=1; }
if printf '%s\n' "$OUT" | grep -q '^FINDING'; then echo "FAIL  the scanner reports findings"; fail=1; fi
OTHER="$(printf '%s\n' "$OUT" | grep '^CAP' | grep -v '^CAP service-management ' || true)"
if [ -n "$OTHER" ]; then echo "FAIL  capabilities beyond service-management: $(printf '%s' "$OTHER" | cut -c1-120)"; fail=1; fi
printf '%s\n' "$OUT" | grep -q '^CAP service-management ' || { echo "FAIL  service-management is not declared by the scan"; fail=1; }
if [ "$fail" -eq 0 ]; then
  echo "PASS  review-required, service-management only, no findings"
  exit 0
fi
exit 1
