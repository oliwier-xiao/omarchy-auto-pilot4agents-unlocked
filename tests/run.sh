#!/bin/bash
# tests/run.sh: every suite, in order, with the exit status of the whole run.
#
#   tests/run.sh
#
# 1. every Python file compiles (in memory: nothing is written next to the sources)
# 2. python3 -m unittest discover -s tests -p 'test_*.py'   (core, runner, scan, policy)
# 3. tests/canary.sh                                         (prompt only on stdin, end to end)
# 4. tests/*.test.sh                                         (QML engine cases, qmllint, panel smoke)
# 5. tests/preflight.sh --no-qmllint                         (marketplace and reviewer rules)
# 6. tests/baseline.sh "$AP4A_MKT_SCRIPTS"                   (only when that variable names the scanner)
#
# Every suite gets a private TMPDIR that is removed afterwards, and any __pycache__ that appears in
# the repository is deleted, so a run leaves the tree exactly as it found it.
#
# New suites need no wiring: every tests/test_*.py and tests/*.test.sh is picked up (q1, q2, q4, q5 ...).
# AP4A_REAL_CLI=1 is passed through to the suites and enables the one case that starts the installed
# OpenCode against a 127.0.0.1 stand-in server (no model); it is unset unless you set it.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY=/usr/bin/python3
if [ "${AP4A_REAL_CLI:-}" = "1" ]; then
  export AP4A_REAL_CLI=1
  echo "AP4A_REAL_CLI=1: the real OpenCode case runs"
else
  unset AP4A_REAL_CLI
fi

export PYTHONDONTWRITEBYTECODE=1
export QML_DISABLE_DISK_CACHE=1
WORK="$(mktemp -d "${TMPDIR:-/tmp}/ap4a-tests.XXXXXX")" || exit 2
export TMPDIR="$WORK"

clean_pycache() {
  find "$REPO" -path "$REPO/.git" -prune -o \( -name __pycache__ -type d -prune -exec rm -rf {} + \) 2>/dev/null
  find "$REPO" -path "$REPO/.git" -prune -o \( -name '*.pyc' -type f -delete \) 2>/dev/null
}
trap 'clean_pycache; rm -rf "$WORK"' EXIT INT TERM

rc=0
SUMMARY=""
step() { # <name> <command...>
  local name="$1"; shift
  echo
  echo "########## $name"
  local started=$SECONDS
  timeout 1800 "$@"
  local r=$?
  local took=$((SECONDS - started))
  if [ "$r" -eq 0 ]; then
    SUMMARY="${SUMMARY}  ok     ${name} (${took}s)"$'\n'
  else
    SUMMARY="${SUMMARY}  FAIL   ${name} (exit ${r}, ${took}s)"$'\n'
    rc=1
  fi
}

step "python compiles" "$PY" -I -S -B - "$REPO" <<'PY'
import os
import sys

root = sys.argv[1]
paths = []
for base in ("bin", "tests"):
    for dirpath, dirnames, files in os.walk(os.path.join(root, base)):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in files:
            path = os.path.join(dirpath, name)
            if name.endswith(".py"):
                paths.append(path)
            elif "." not in name:
                with open(path, "rb") as handle:
                    if handle.readline().startswith(b"#!/usr/bin/python3"):
                        paths.append(path)
bad = 0
for path in sorted(paths):
    with open(path, "rb") as handle:
        source = handle.read()
    try:
        compile(source, path, "exec", dont_inherit=True)
    except SyntaxError as exc:
        bad += 1
        print("  FAIL %s: %s" % (os.path.relpath(path, root), exc))
print("  %d file(s) compiled, %d failed" % (len(paths), bad))
sys.exit(1 if bad else 0)
PY

step "unittest" bash -c 'cd "$1" && exec /usr/bin/python3 -B -m unittest discover -s tests -p "test_*.py"' _ "$REPO"
step "canary" bash "$REPO/tests/canary.sh"
for suite in "$REPO"/tests/*.test.sh; do
  [ -f "$suite" ] || continue
  step "$(basename "$suite")" bash "$suite"
done
step "preflight" bash "$REPO/tests/preflight.sh" "$REPO" --no-qmllint
if [ -n "${AP4A_MKT_SCRIPTS:-}" ]; then
  step "marketplace baseline" bash "$REPO/tests/baseline.sh" "$AP4A_MKT_SCRIPTS"
fi

echo
echo "########## summary"
printf '%s' "$SUMMARY"
[ "$rc" -eq 0 ] && echo "all suites passed" || echo "some suites FAILED"
exit "$rc"
