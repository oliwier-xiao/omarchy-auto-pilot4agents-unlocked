#!/bin/bash
# tests/preflight.sh: every machine-checkable marketplace and reviewer rule, offline.
#
#   tests/preflight.sh [DIR] [--strict] [--no-qmllint] [--submission=BODY.md]
#
# Adapted from the preflight of agent-skills-manager (same author, MIT). It mirrors:
#   (a) scripts/build-catalog.mjs          repository layout, manifest contract, optional root preview
#   (b) scripts/security-baseline-*.mjs    scan scope, scan limits, the findings and the capabilities.
#                                          Expected here: review-required with service-management only,
#                                          because transient systemd user units are what the plugin is.
#   (c) HANCORE-linux's manual review      helper I/O and bounds, unit hardening, the repository policy
#                                          (tests/test_policy.py --report), qmllint, omarchy-plugin-validate
#   (d) README and packaging
#   (e) scripts/submission.mjs             the submission issue body, when --submission is given
#
# Out of scope offline: whether the repository or plugin id is already listed or retired. Check
# site/catalog.json and registry.json in omacom/omarchy-plugin-marketplace by hand before submitting.
# The real scanner can be run with tests/baseline.sh.
#
# WARN never fails the run unless --strict. FAIL always does. No network calls.
set -uo pipefail

DIR="."; SUBMISSION=""; STRICT=0; QMLLINT=1
for arg in "$@"; do
  case "$arg" in
    --submission=*) SUBMISSION="${arg#*=}" ;;
    --strict)       STRICT=1 ;;
    --no-qmllint)   QMLLINT=0 ;;
    -h|--help)      sed -n '2,23p' "$0"; exit 0 ;;
    -*) printf 'preflight: unknown option %s\n' "$arg" >&2; exit 2 ;;
    *)  DIR="$arg" ;;
  esac
done
cd "$DIR" || { printf 'preflight: cannot enter %s\n' "$DIR" >&2; exit 2; }
PY=/usr/bin/python3
[ -x "$PY" ] || { echo "preflight: /usr/bin/python3 is required" >&2; exit 2; }
for t in find grep sed awk; do
  command -v "$t" >/dev/null 2>&1 || { printf 'preflight: %s is required\n' "$t" >&2; exit 2; }
done
export PYTHONDONTWRITEBYTECODE=1

WORK="$(mktemp -d "${TMPDIR:-/tmp}/ap4a-preflight.XXXXXX")" || exit 2
trap 'rm -rf "$WORK"' EXIT
FILES="$WORK/files"; LINKS="$WORK/links"; MODES="$WORK/modes"; TALLY="$WORK/tally"
: >"$TALLY"; : >"$MODES"

if [ -t 1 ]; then G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; Z=$'\033[0m'
else G=""; R=""; Y=""; Z=""; fi
pass() { printf '%sPASS%s  %-44s %s\n' "$G" "$Z" "$1" "${2-}"; }
fail() { printf '%sFAIL%s  %-44s %s\n' "$R" "$Z" "$1" "${2-}"; printf 'FAIL\n' >>"$TALLY"; }
warn() { printf '%sWARN%s  %-44s %s\n' "$Y" "$Z" "$1" "${2-}"; printf 'WARN\n' >>"$TALLY"; }
soft() { if [ "$STRICT" -eq 1 ]; then fail "$@"; else warn "$@"; fi; }

# The marketplace reads the pushed git TREE, never the worktree.
if git rev-parse --git-dir >/dev/null 2>&1; then
  git ls-files >"$FILES"
  git ls-files -s | awk -F'\t' '$1 ~ /^120000/ { print $2 }' >"$LINKS"
  git ls-files -s | awk -F'\t' '{ split($1, m, " "); print m[1] "\t" $2 }' >"$MODES"
  SRC="git tree"
else
  find . -path ./.git -prune -o -name __pycache__ -prune -o -name '*.pyc' -prune -o -type f -print 2>/dev/null \
    | sed 's|^\./||' | LC_ALL=C sort >"$FILES"
  find . -path ./.git -prune -o -type l -print 2>/dev/null | sed 's|^\./||' >"$LINKS"
  SRC="worktree"
fi
printf 'preflight: omarchy marketplace and reviewer rules  (%s, %s files)\n\n' "$SRC" "$(wc -l <"$FILES" | tr -d ' ')"

# ------------------------------------------------ (a) repository layout
echo "-- (a) marketplace validation: repository layout --"
ORIGIN="$(git config --get remote.origin.url 2>/dev/null || true)"
NORM="$(printf '%s' "$ORIGIN" | sed -E 's|^git@github\.com:|https://github.com/|; s|\.git$||; s|/$||')"
if printf '%s' "$NORM" | grep -qE '^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$'; then
  pass "submission-repository-invalid" "$NORM"
else
  soft "submission-repository-invalid" "origin must be https://github.com/owner/repo before submitting (got '${ORIGIN:-none}')"
fi

if grep -qiE '^readme(\.[^/]+)?$' "$FILES"; then pass "readme-missing" "root README present"
else fail "readme-missing" "A README file is required in the repository root."; fi

if grep -qiE '^(licen[cs]e|copying)(\.[^/]+)?$' "$FILES"; then pass "license-missing" "root license present"
else fail "license-missing" "A license file is required in the repository root."; fi

N_ANY="$(grep -icE '^([^/]+/)?manifest\.json$' "$FILES" || true)"
N_ROOT="$(grep -cE '^manifest\.json$' "$FILES" || true)"
if [ "${N_ANY:-0}" = "1" ] && [ "${N_ROOT:-0}" = "1" ]; then
  pass "unsupported-repository-layout" "exactly one manifest.json, at the root"
else
  fail "unsupported-repository-layout" "one plugin with manifest.json in the repository root (any=${N_ANY:-0} root=${N_ROOT:-0})"
fi

if [ -s "$LINKS" ]; then
  fail "manifest-invalid/symlinks" "symlinks are not allowed in plugin folders: $(tr '\n' ' ' <"$LINKS")"
else
  pass "manifest-invalid/symlinks" "no symlink anywhere in the tree"
fi

if grep -qE '[[:cntrl:]]' "$FILES"; then fail "repository/paths" "a tracked path contains a control character"
else pass "repository/paths" "every tracked path is plain"; fi
echo

# ------------------------------------------------ (a) manifest contract
echo "-- (a) marketplace validation: manifest contract (community=true) --"
if [ ! -f manifest.json ]; then
  fail "manifest-invalid" "manifest.json is missing"
else
"$PY" -I -S -B - manifest.json "$FILES" "$TALLY" <<'PY'
import ast, json, re, sys
mpath, flist, tally = sys.argv[1], sys.argv[2], sys.argv[3]
files = set(l for l in open(flist, encoding="utf-8").read().split("\n") if l)
T = open(tally, "a", encoding="utf-8")
def ok(r, n=""): print("PASS  %-44s %s" % (r, n))
def no(r, m):    print("FAIL  %-44s %s" % (r, m)); T.write("FAIL\n")
def wa(r, m):    print("WARN  %-44s %s" % (r, m)); T.write("WARN\n")

try:
    m = json.loads(open(mpath, encoding="utf-8").read())
except Exception as e:
    no("manifest-invalid", "%s: invalid JSON (%s)" % (mpath, e)); T.close(); sys.exit(0)
if not isinstance(m, dict):
    no("manifest-invalid", "manifest must be a JSON object"); T.close(); sys.exit(0)
ok("manifest-invalid/json", "manifest.json parses as an object")

sv = m.get("schemaVersion")
(ok if sv == 1 and not isinstance(sv, bool) else no)("manifest-invalid/schemaVersion",
    "schemaVersion is exactly 1" if sv == 1 else 'manifest field "schemaVersion" must be exactly 1')

CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
LIM = [("id", 128), ("name", 120), ("version", 64), ("author", 120), ("description", 500), ("license", 120)]
probs = []
for f in ("id", "name", "version", "author", "description"):
    v = m.get(f)
    if not isinstance(v, str) or not v.strip():
        probs.append('manifest field "%s" is required' % f); continue
    if f == "id" and v != v.strip():
        probs.append('manifest field "id" must not contain leading or trailing whitespace')
    if CTRL.search(v.strip()):
        probs.append('manifest field "%s" contains control characters' % f)
if probs: no("manifest-invalid/required-fields", probs[0])
else:     ok("manifest-invalid/required-fields", "id, name, version, author, description")

lic = m.get("license")
if lic == "MIT": ok("manifest/license", "MIT, matching LICENSE")
elif lic is None: wa("manifest/license", "absent: the reviewer reads the license from the manifest too")
else: wa("manifest/license", "license %r: LICENSE in this repository is MIT" % lic)

over = ["%s=%d>%d" % (f, len(m[f].strip()), n) for f, n in LIM if isinstance(m.get(f), str) and len(m[f].strip()) > n]
sizes = " ".join("%s=%d/%d" % (f, len(m[f].strip()), n) for f, n in LIM if isinstance(m.get(f), str))
if over: no("manifest-invalid/field-limits", "must not exceed the community limit: " + "; ".join(over))
else:    ok("manifest-invalid/field-limits", sizes)
d = m.get("description")
if isinstance(d, str) and 460 <= len(d.strip()) <= 500:
    wa("manifest/description-headroom", "description is %d/500, under 40 chars of headroom" % len(d.strip()))

pid = m.get("id") if isinstance(m.get("id"), str) else ""
if not re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", pid) or ".." in pid:
    no("manifest-invalid/id-charset", "manifest id contains unsupported characters")
elif pid != pid.lower():
    no("manifest-invalid/id-lowercase", "community manifest ids must use lowercase characters")
else:
    ok("manifest-invalid/id-charset", pid)
(no if pid.lower().startswith("omarchy.") else ok)("reserved-plugin-id",
    "the omarchy.* namespace is reserved" if pid.lower().startswith("omarchy.") else "outside the reserved omarchy.* namespace")

try:
    tree = ast.parse(open("bin/autopilot/edition.py", encoding="utf-8").read())
    edition_id = next(n.value.value for n in tree.body if isinstance(n, ast.Assign)
                      and any(getattr(t, "id", "") == "PLUGIN_ID" for t in n.targets))
except Exception:
    edition_id = None
(ok if edition_id == pid else no)("manifest/id-matches-edition",
    "manifest id == edition.PLUGIN_ID" if edition_id == pid else "manifest id %r, edition.PLUGIN_ID %r" % (pid, edition_id))

home = m.get("homepage")
if isinstance(home, str) and re.match(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", home):
    ok("manifest/homepage", home)
else:
    wa("manifest/homepage", "homepage should be the https://github.com/owner/repo of the submission")

for stale in ("keepLoaded", "activation"):
    if stale in m or stale in (m.get("barWidget") or {}):
        no("manifest/no-%s" % stale, '"%s" is not used by this plugin (R0 D1)' % stale)
    else:
        ok("manifest/no-%s" % stale, "absent")

KINDS = {"bar", "bar-widget", "menu", "overlay", "panel", "service"}
kinds = m.get("kinds")
if not isinstance(kinds, list) or not kinds or any(not isinstance(k, str) or k not in KINDS for k in kinds):
    no("manifest-invalid/kinds", 'manifest "kinds" contains unsupported values (allowed: %s)' % sorted(KINDS)); kinds = []
else:
    ok("manifest-invalid/kinds", ", ".join(kinds))

eps = m.get("entryPoints")
if not isinstance(eps, dict):
    no("manifest-invalid/entryPoints", 'manifest "entryPoints" must be an object'); eps = {}
else:
    ok("manifest-invalid/entryPoints", "entryPoints is an object")

bw = m.get("barWidget")
if isinstance(bw, dict) and "defaultSection" in bw and bw["defaultSection"] not in ("left", "center", "right"):
    no("manifest-invalid/defaultSection", '"barWidget.defaultSection" must be left, center, or right')
else:
    ok("manifest-invalid/defaultSection", bw.get("defaultSection", "absent (optional)") if isinstance(bw, dict) else "no barWidget block")

key = lambda k: "barWidget" if k == "bar-widget" else k
missing = [k for k in kinds if key(k) not in eps]
if missing: no("entry-point-missing/declared", 'entry point for "%s" is missing' % missing[0])
else:       ok("entry-point-missing/declared", "every kind declares its entry point")

vals = list(eps.values())
unsafe = [str(p) for p in vals if not isinstance(p, str) or not p.strip() or p.startswith("/") or ".." in p
          or re.search(r"[\\:\r\n\x00]", p)]
if not vals:  no("manifest-invalid/entry-point-paths", "entry points must be safe relative paths (none declared)")
elif unsafe:  no("manifest-invalid/entry-point-paths", "entry points must be safe relative paths (%s)" % unsafe[0])
else:         ok("manifest-invalid/entry-point-paths", ", ".join(vals))

absent = [p for p in vals if isinstance(p, str) and p not in files]
if absent: no("entry-point-missing/file", "declared entry point is missing: %s" % absent[0])
else:      ok("entry-point-missing/file", "every declared entry point is a file in the tree")

EXCL = {".github", "coverage", "docs", "fixtures", "node_modules", "spec", "specs", "test", "tests"}
forced = [p for p in vals if isinstance(p, str) and any(s in EXCL for s in p.split("/")[:-1])]
if forced: no("scan-scope/entry-point-forced", "%s sits under a scan-excluded directory" % forced[0])
else:      ok("scan-scope/entry-point-forced", "no entry point under docs/ tests/ .github/")

if isinstance(bw, dict):
    for f in ("displayName", "description", "category"):
        (ok if isinstance(bw.get(f), str) and bw[f].strip() else no)("manifest/barWidget-%s" % f,
            bw.get(f) if isinstance(bw.get(f), str) and bw[f].strip() else 'barWidget "%s" is required' % f)
    schema = bw.get("schema") or []
    defaults = bw.get("defaults") or {}
    sk = sorted(e["key"] for e in schema if isinstance(e, dict) and isinstance(e.get("key"), str))
    (ok if sk == sorted(defaults.keys()) else no)("manifest/schema-defaults",
        "%d setting(s), keys and defaults agree" % len(sk) if sk == sorted(defaults.keys())
        else "schema keys and barWidget.defaults disagree")
    for e in schema:
        if not isinstance(e, dict):
            no("manifest/schema-entry", "a schema entry is not an object"); continue
        k = e.get("key", "?")
        need = [f for f in ("key", "type", "label", "defaultValue", "description") if f not in e]
        if need:
            no("manifest/schema-complete %s" % k, "lacks %s" % ", ".join(need)); continue
        if not all(isinstance(e[f], str) and e[f].strip() for f in ("key", "type", "label", "description")):
            no("manifest/schema-complete %s" % k, "key, type, label and description must be non-empty strings"); continue
        if e["type"] == "enum":
            opts = e.get("options")
            if not isinstance(opts, list) or not opts or not all(isinstance(o, str) and o for o in opts) or len(set(opts)) != len(opts):
                no("manifest/schema-options %s" % k, "an enum needs a non-empty list of distinct string options"); continue
            if e["defaultValue"] not in opts:
                no("manifest/schema-options %s" % k, "defaultValue %r is not one of the options" % e["defaultValue"]); continue
        if defaults.get(k) != e["defaultValue"]:
            no("manifest/schema-defaultValue %s" % k, "barWidget.defaults[%r] != defaultValue" % k); continue
        ok("manifest/schema-complete %s" % k, "%s with label, description, options and defaultValue" % e["type"])
T.close()
PY
fi
echo

# ------------------------------------------------ (a) optional preview
echo "-- (a) marketplace validation: optional root preview --"
PREVIEW=""
for ext in png webp jpg jpeg avif; do
  [ -n "$PREVIEW" ] && continue
  PREVIEW="$(grep -iE "^preview\.$ext\$" "$FILES" | LC_ALL=C sort | head -1 || true)"
done
if [ -z "$PREVIEW" ]; then
  soft "preview-absent" "no root preview.png yet: the listing shows a fallback"
else
"$PY" -I -S -B - "$PREVIEW" "$TALLY" <<'PY'
import os, struct, sys
p, tally = sys.argv[1], sys.argv[2]
T = open(tally, "a", encoding="utf-8")
size = os.path.getsize(p); BYTE, PIX = 50 * 1024 * 1024, 40000000
d = open(p, "rb").read(65536); w = h = 0
if d[:8] == b"\x89PNG\r\n\x1a\n" and d[12:16] == b"IHDR":
    w, h = struct.unpack(">II", d[16:24])
elif d[:4] == b"RIFF" and d[8:12] == b"WEBP":
    if d[12:16] == b"VP8X":
        w = int.from_bytes(d[24:27], "little") + 1; h = int.from_bytes(d[27:30], "little") + 1
    elif d[12:16] == b"VP8 " and d[23:26] == b"\x9d\x01\x2a":
        w = int.from_bytes(d[26:28], "little") & 0x3fff; h = int.from_bytes(d[28:30], "little") & 0x3fff
    elif d[12:16] == b"VP8L" and d[20:21] == b"\x2f":
        bits = int.from_bytes(d[21:25], "little"); w = (bits & 0x3fff) + 1; h = ((bits >> 14) & 0x3fff) + 1
elif d[:2] == b"\xff\xd8":
    f = open(p, "rb"); f.seek(2)
    while True:
        b = f.read(1)
        if not b: break
        if b != b"\xff": continue
        mk = f.read(1)
        if mk in (b"\xc0", b"\xc1", b"\xc2", b"\xc3", b"\xc5", b"\xc6", b"\xc7", b"\xc9", b"\xca", b"\xcb"):
            f.read(3); h, w = struct.unpack(">HH", f.read(4)); break
        ln = f.read(2)
        if len(ln) < 2: break
        f.seek(struct.unpack(">H", ln)[0] - 2, 1)
note = "%s %dB %dx%d" % (p, size, w, h)
if size < 1 or size > BYTE or (w and h and w * h > PIX):
    print("FAIL  %-44s %s exceeds the 50MB / 40MP limit" % ("preview-invalid", note)); T.write("FAIL\n")
elif not (w and h):
    print("WARN  %-44s %s header unread; confirm the 40MP limit by hand" % ("preview-invalid", note)); T.write("WARN\n")
else:
    print("PASS  %-44s %s within 50MB / 40MP" % ("preview-invalid", note))
    if w < 1600:
        print("WARN  %-44s long edge %d < 1600" % ("preview-small", w)); T.write("WARN\n")
T.close()
PY
fi
echo

# ------------------------------------------------ (b) security baseline mirror
echo "-- (b) automated security baseline mirror (scope, limits, findings, capabilities) --"
"$PY" -I -S -B - "$FILES" "$MODES" "$TALLY" "$STRICT" <<'PY'
import json, os, re, sys
flist, mlist, tally, strict = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] == "1"
files = [l for l in open(flist, encoding="utf-8").read().split("\n") if l]
MODES = dict(l.split("\t", 1)[::-1] for l in open(mlist, encoding="utf-8").read().split("\n") if "\t" in l)
def mode_of(p): return MODES.get(p) or ("100755" if os.access(p, os.X_OK) else "100644")
T = open(tally, "a", encoding="utf-8")
def ok(r, n=""): print("PASS  %-44s %s" % (r, n))
def no(r, m):    print("FAIL  %-44s %s" % (r, m)); T.write("FAIL\n")
def wa(r, m):    print("WARN  %-44s %s" % (r, m)); T.write("WARN\n")
def soft(r, m):  (no if strict else wa)(r, m)

EXCL = {".github", "coverage", "docs", "fixtures", "node_modules", "spec", "specs", "test", "tests"}
SCANNED = {".bash", ".cjs", ".desktop", ".fish", ".js", ".lua", ".mjs", ".pl", ".py", ".qml", ".rb",
           ".service", ".sh", ".sudoers", ".toml", ".yaml", ".yml", ".zsh"}
ASSET = {".apng", ".avif", ".bmp", ".gif", ".heic", ".heif", ".ico", ".jfif", ".jpe", ".jpeg",
         ".jpg", ".jxl", ".png", ".tif", ".tiff", ".webp"}
SETUP = re.compile(r"(?:^|[-_])(install|installer|setup|uninstall)(?:[-_.]|$)", re.I)
SETUP_LOOSE = re.compile(r"install|installer|setup|uninstall", re.I)

def is_root_readme(p): return "/" not in p and re.match(r"^readme(\.[^/]+)?$", p, re.I) is not None
def extof(b):
    i = b.rfind(".")
    return b[i:].lower() if i > 0 else ""
def is_scan_path(p):
    if is_root_readme(p): return True
    parts = p.lower().split("/")
    if any(x in EXCL for x in parts[:-1]): return False
    b = parts[-1]; e = extof(b)
    if mode_of(p) == "100755": return True
    if "." not in b: return True
    if e in SCANNED: return True
    if e in ASSET: return False
    return SETUP_LOOSE.search(b) is not None

FORCED = set()
try:
    FORCED = {v for v in (json.load(open("manifest.json", encoding="utf-8")).get("entryPoints") or {}).values() if isinstance(v, str)}
except Exception:
    pass
scanned = [p for p in files if p in FORCED or is_scan_path(p)]
ok("scan-scope", "%d scanned: %s" % (len(scanned), ", ".join(sorted(scanned)[:6]) + (" ..." if len(scanned) > 6 else "")))

FILE_LIMIT, TOTAL_LIMIT, PER_FILE, PROBE = 1000, 8 * 1024 * 1024, 512 * 1024, 4096
sizes = dict((p, os.path.getsize(p) if os.path.exists(p) else 0) for p in scanned)
charged = dict((p, PROBE if (s > PER_FILE and mode_of(p) == "100755") else s) for p, s in sizes.items())
big = [p for p, s in sizes.items() if s > PER_FILE and mode_of(p) != "100755"]
(ok if len(scanned) <= FILE_LIMIT else no)("security-baseline-scan-limit/files", "%d relevant files (limit %d)" % (len(scanned), FILE_LIMIT))
(ok if sum(charged.values()) <= TOTAL_LIMIT else no)("security-baseline-scan-limit/bytes", "%d B relevant text (limit %d)" % (sum(charged.values()), TOTAL_LIMIT))
(ok if not big else no)("security-baseline-scan-limit/per-file", "no scanned file over 512 KiB" if not big else "%s exceeds 512 KiB" % big[0])

INTERP = r"(?:bash|sh|zsh|dash|ash|ksh|fish|python(?:[23](?:\.[0-9]+)?)?|node|ruby|perl|php|java|deno|dotnet)"
FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})\s*(?:(?:ba|z|fi|da|a|k)?sh|shell)\s*$", re.I)
FENCE_SKIP = re.compile(r"\b(development|contributing|contributors?|testing|tests?)\b", re.I)
def read(p):
    try: return open(p, encoding="utf-8", errors="replace").read()
    except Exception: return ""
def is_shell_runtime(p):
    b = p.split("/")[-1]
    if re.search(r"\.(ba|z|fi)?sh$", b, re.I): return True
    if "." not in b and re.match(r"^(bin|scripts)/", p, re.I): return True
    return bool(SETUP.search(b) and not re.search(r"\.(md|json)$", b, re.I))
def units(p):
    text = read(p)
    execish = os.access(p, os.X_OK) or bool(re.search(r"^#!.*\b(sh|bash|zsh|dash|ash|ksh|fish)\b", text, re.M))
    out = [(p, text, is_shell_runtime(p) or execish or p in FORCED)]
    if not is_root_readme(p): return out
    lines = text.split("\n"); sec = ""; para = []; prev = []; i = 0
    while i < len(lines):
        hd = re.match(r"^ {0,3}#{1,6}\s+(.+)$", lines[i])
        if hd:
            sec = hd.group(1).strip().lower(); para = []; prev = []; i += 1; continue
        opn = FENCE_OPEN.match(lines[i])
        if not opn:
            if lines[i].strip(): para.append(lines[i])
            elif para: prev, para = para, []
            else: prev = []
            i += 1; continue
        mark, n, body = opn.group(1)[0], len(opn.group(1)), []
        i += 1
        while i < len(lines):
            cl = re.match(r"^ {0,3}(`{3,}|~{3,})\s*$", lines[i])
            if cl and cl.group(1)[0] == mark and len(cl.group(1)) >= n: break
            body.append(lines[i]); i += 1
        if not FENCE_SKIP.search(sec + "\n" + "\n".join(para or prev)):
            out.append((p + " (tagged shell fence)", "\n".join(body), True))
        para, prev = [], []; i += 1
    return out

CURL_PIPE = re.compile(r"\b(?:curl|wget)\b[^\n|]*\|\s*(?:sudo\s+|env\s+\S+=\S+\s+)*(?:/\S*/)?" + INTERP + r"\b", re.I)
PROC_SUB = re.compile(r"(?:(?:ba|z|fi|da|a|k)?sh|source|\.)\s+(?:<\s*)?<\(\s*(?:curl|wget)\b", re.I)
CMD_SUB = re.compile(r"(?:eval\s+|(?:ba|z|fi|da|a|k)?sh\s+-c\s+)[\"']?\$\(\s*(?:curl|wget)\b", re.I)
CARGO_GIT = re.compile(r"\bcargo\s+(?:\+\S+\s+)?install\b[^\n]*\s--git(?:\s|=)", re.I)
REV_PIN = re.compile(r"--rev(?:\s+|=)[\"']?[a-f0-9]{40}[\"']?", re.I)
GIT_ACQ = re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?(?:clone|fetch|pull)\b", re.I)
EXEC_SINK = re.compile(r"\b(?:make|gmake|cmake|ninja|meson|gradle|gradlew|mvn|go|cargo|npm|pnpm|yarn|bun|" + INTERP + r")\b|(?:^|\s)\./\S+", re.I)
SHA_PIN = re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?(?:checkout|reset|switch)\b[^\n]*\b[a-f0-9]{40}\b", re.I)
NOPASSWD = re.compile(r"\bNOPASSWD\s*:", re.I)
DANGEROUS = re.compile(r"\bNOPASSWD\s*:\s*(?:ALL|/[^\s,]*(?:\*|\?|\[)|/[^\s,]*/(?:kill|pkill|systemctl|systemd-run|rm|mv|cp|install|tee|chmod"
                       r"|chown|mount|umount|wg-quick|sudo|su|env|busybox|toybox|sh|bash|zsh|fish|dash|ash|ksh|perl|ruby|node|php|deno|java|dotnet|python[0-9.]*)\s*$)", re.I)
TMP_PID = re.compile(r"/tmp/[^\s\"']*\.?pid\b", re.I)
PRIV_KILL = re.compile(r"\b(?:sudo|pkexec)\b[^\n]*\b(?:kill|pkill|renice|systemctl)\b", re.I)
SUDOERS_F = re.compile(r"(?:^|/)(?:sudoers(?:\.d)?(?:/|$)|[^/]+\.sudoers$)", re.I)

hits = dict((k, []) for k in ("curl-pipe-shell", "cargo-git-unpinned", "remote-git-execution-unpinned",
                              "sudoers-dangerous-passwordless-command", "privileged-process-control-from-shared-temp"))
for p in scanned:
    for label, text, runtime in units(p):
        lines = text.split("\n")
        if SUDOERS_F.search(p) or NOPASSWD.search(text):
            for i, ln in enumerate(lines, 1):
                if DANGEROUS.search(ln): hits["sudoers-dangerous-passwordless-command"].append("%s:%d" % (label, i))
        if not runtime: continue
        pending = None
        for i, ln in enumerate(lines, 1):
            s = re.sub(r"\s+#.*$", "", ln).strip()
            if not s: continue
            if CURL_PIPE.search(s) or PROC_SUB.search(s) or CMD_SUB.search(s): hits["curl-pipe-shell"].append("%s:%d" % (label, i))
            if CARGO_GIT.search(s) and not REV_PIN.search(s): hits["cargo-git-unpinned"].append("%s:%d" % (label, i))
            if GIT_ACQ.search(s): pending = (i, s)
            elif pending and SHA_PIN.search(s): pending = None
            elif pending and EXEC_SINK.search(s):
                hits["remote-git-execution-unpinned"].append("%s:%d -> :%d" % (label, pending[0], i)); pending = None
            if TMP_PID.search(s):
                for j in range(i - 1, min(i + 11, len(lines))):
                    if PRIV_KILL.search(lines[j]): hits["privileged-process-control-from-shared-temp"].append("%s:%d" % (label, i)); break
BLOCKING = {"sudoers-dangerous-passwordless-command", "privileged-process-control-from-shared-temp"}
for rule in sorted(hits):
    ev = hits[rule]
    if not ev: ok("finding/" + rule, "no match")
    elif rule in BLOCKING: no("finding/" + rule, "%d hit(s); first: %s" % (len(ev), ev[0]))
    else: no("finding/" + rule, "%d hit(s); first: %s (this plugin expects none)" % (len(ev), ev[0]))

PKG = re.compile(r"\bomarchy\s+pkg\s+(?:add|drop|remove|update)\b|\b(?:pacman|paru|yay|apt|apt-get|dnf|zypper|apk)\s+"
                 r"(?:-[A-Za-z]*[SRU]|install|remove|upgrade|add|del)\b|(?:^|[\s/'\"])(?:pip|pip3|pipx)[\"']?\s+install\b"
                 r"|\bpython[23]?(?:\.[0-9]+)?\s+-m\s+pip\s+install\b|\b(?:npm|pnpm|yarn|bun)\s+(?:install|add)\b"
                 r"|\bcargo\s+install\b|\bgo\s+install\b|\bgem\s+install\b|\bbrew\s+(?:install|uninstall|upgrade)\b", re.I)
PRIV = re.compile(r"\b(?:sudo|pkexec)\b")
NEG = re.compile(r"\b(?:does\s+not|doesn't|do\s+not|don't|never)\s+(?:use|run|invoke|require|need)\s+"
                 r"|\bno\s+sudo\b(?:\s+or\s+pkexec)?\s+is\s+(?:required|needed)\b|\bwithout\s+sudo\b|\bsudo\s+is\s+not\s+(?:used|required)\b", re.I)
SVC = re.compile(r"\bsystemctl\b|\bsystemd-run\b")
SUDOERS = re.compile(r"/etc/sudoers(?:\.d)?(?:/|\b)|\bvisudo\b|\bSUDOERS(?:_FILE)?\s*=", re.I)
REMOTE = re.compile(r"\bgit\s+(?:clone|fetch|pull)\b|\bcurl\b|\bwget\b", re.I)
caps = {}
def add(c, w): caps.setdefault(c, []).append(w)
for p in files:
    if any(s in EXCL for s in p.lower().split("/")[:-1]): continue
    if SETUP.search(p.split("/")[-1]): add("installer", p)
    if p.lower().endswith(".service"): add("service-management", p)
for p in scanned:
    for label, text, _ in units(p):
        for i, ln in enumerate(text.split("\n"), 1):
            where = "%s:%d: %s" % (label, i, ln.strip()[:60])
            if PKG.search(ln): add("package-manager", where)
            if PRIV.search(ln) and not NEG.search(ln): add("privilege", where)
            if SVC.search(ln): add("service-management", where)
            if SUDOERS.search(ln): add("sudoers-modification", where)
            if REMOTE.search(ln): add("remote-build", where)
for p in scanned:
    if not os.access(p, os.X_OK): continue
    try: head = open(p, "rb").read(4)
    except Exception: continue
    if head[:4] == b"\x7fELF" or head[:2] == b"MZ" or head[:4] in (b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf"):
        add("bundled-executable-binary", p)

EXPECTED = {"service-management"}
extra = sorted(set(caps) - EXPECTED)
if extra:
    no("security-baseline-capabilities", "unexpected capabilities: " + ", ".join("%s(%d)" % (k, len(caps[k])) for k in extra))
    for k in extra: print("        %-28s %s" % (k, caps[k][0]))
elif "service-management" not in caps:
    wa("security-baseline-capabilities", "service-management not detected: the systemd use should be visible to the scanner")
else:
    ok("security-baseline-capabilities", "review-required, service-management only (%d evidence line(s))" % len(caps["service-management"]))

for p in files:
    if not is_root_readme(p): continue
    tagged = [i for i, l in enumerate(read(p).split("\n"), 1) if FENCE_OPEN.match(l)]
    if tagged: soft("readme/tagged-shell-fence", "line %d opens a shell-tagged fence; it is scanned as a script" % tagged[0])
    else: ok("readme/tagged-shell-fence", "all code fences untagged")
T.close()
PY
echo

# ------------------------------------------------ (c) reviewer bar
echo "-- (c) reviewer bar: helper I/O, bounds and units (HANCORE-linux) --"
HELPER_SRC="$WORK/helper.py"
cat bin/ap4a bin/autopilot/*.py >"$HELPER_SRC" 2>/dev/null || true
if [ ! -s "$HELPER_SRC" ]; then
  fail "helper/present" "bin/ap4a and bin/autopilot/*.py not found"
else
  need() { # <rule> <note> <extended regex>...
    local rule="$1" note="$2" missing=""; shift 2
    for re in "$@"; do grep -qE -- "$re" "$HELPER_SRC" || missing="$missing $re"; done
    if [ -z "$missing" ]; then pass "$rule" "$note"; else fail "$rule" "not found:$missing"; fi
  }
  deny() { # <rule> <note> <extended regex> [files...]
    local rule="$1" note="$2" re="$3" hit
    shift 3
    [ "$#" -gt 0 ] || set -- bin/ap4a bin/autopilot/*.py
    hit="$(grep -nE -- "$re" "$@" 2>/dev/null | grep -vE ':[0-9]+:\s*#' | head -1)"
    if [ -z "$hit" ]; then pass "$rule" "$note"; else fail "$rule" "$hit"; fi
  }
  need "helper/one-open-descriptor-decisions" "O_NOFOLLOW|O_NONBLOCK + fstat" 'O_NOFOLLOW' 'O_NONBLOCK' 'fstat'
  need "helper/single-link-leaf" "st_nlink checked" 'st_nlink'
  need "helper/group-world-writable-refusal" "g/o-writable refused" '0o022|0o002|S_IWOTH'
  need "helper/deep-json-guard" "RecursionError handled" 'RecursionError'
  need "helper/producer-side-output-cap" "emit() with a byte ceiling" 'def emit\('
  need "helper/atomic-writes" "O_EXCL temp, fsync, rename" 'O_EXCL' 'fsync' 'os\.replace'
  need "helper/flock" "jobs flock" 'flock'
  need "helper/process-groups" "own session, killpg" 'start_new_session=True' 'killpg'
  need "helper/runner-needs-systemd" "INVOCATION_ID guard" 'INVOCATION_ID'
  need "units/transient-hardened" "--collect, RuntimeMaxSec, KillMode, MemoryMax, TasksMax, UMask, NoNewPrivileges, StandardOutput=null" \
       '--collect' 'RuntimeMaxSec=' 'KillMode=control-group' 'MemoryMax=' 'TasksMax=' 'UMask=0077' 'NoNewPrivileges=yes' 'StandardOutput=null'
  deny "units/no-persistent-units" "no daemon-reload, linger, Persistent= or --on-active anywhere" \
       '"(daemon-reload|enable-linger)"|Persistent=|--on-active|loginctl'
  deny "units/no-enabled-units" "systemctl is never asked to enable, link, preset or mask" \
       '"(enable|reenable|link|preset|mask|edit)"' bin/autopilot/systemd.py
  deny "helper/no-credential-files" "no agent credential file named" '\.credentials\.json|auth\.json|oauth_creds'
  deny "helper/no-network" "no network client in the helper" 'api\.anthropic\.com|urllib\.request|http\.client|^\s*import socket|from socket import'
  deny "helper/no-usage-collector" "never runs the usage collector" 'omarchy-agent-usage'
fi
for t in mkfifo st_nlink RecursionError symlink canary; do
  if [ -d tests ] && grep -rqs -- "$t" tests; then pass "tests/adversarial-$t" "covered"
  else soft "tests/adversarial-$t" "no test mentions $t"; fi
done
echo

echo "-- (c) reviewer bar: repository policy (tests/test_policy.py --report) --"
if [ -f tests/test_policy.py ]; then
  "$PY" -I -S -B tests/test_policy.py --report >"$WORK/policy" 2>&1
  cat "$WORK/policy"
  NP="$(grep -c '^FAIL' "$WORK/policy" || true)"
  for _ in $(seq 1 "${NP:-0}"); do printf 'FAIL\n' >>"$TALLY"; done
  if ! grep -qE '^(PASS|FAIL)' "$WORK/policy"; then fail "policy/report" "tests/test_policy.py --report printed no verdicts"; fi
else
  fail "policy/present" "tests/test_policy.py is missing"
fi
echo

echo "-- (c) reviewer bar: QML --"
mapfile -t QFILES < <(grep -E '\.qml$' "$FILES" | grep -vE '^tests/' || true)
if [ "${#QFILES[@]}" -eq 0 ]; then
  warn "qml/present" "no .qml file in the tree yet"
elif [ "$QMLLINT" -eq 0 ]; then
  pass "qml/qmllint" "skipped here (--no-qmllint); tests/qml.test.sh runs it"
elif [ ! -x /usr/lib/qt6/bin/qmllint ]; then
  warn "qml/qmllint" "no Qt 6 qmllint at /usr/lib/qt6/bin/qmllint"
else
  for q in "${QFILES[@]}"; do
    out="$(timeout 180 /usr/lib/qt6/bin/qmllint -I lint "$q" 2>&1)"; rc=$?
    n="$(printf '%s\n' "$out" | grep -cE '^(Warning|Error):' || true)"
    if [ "$rc" -eq 0 ] && [ "${n:-0}" -eq 0 ]; then pass "qml/qmllint $q" "0 warnings"
    else fail "qml/qmllint $q" "exit $rc, ${n:-0} warning(s): $(printf '%s\n' "$out" | grep -m1 -E '^(Warning|Error):')"; fi
  done
fi
VALIDATE="$(command -v omarchy-plugin-validate || true)"
if [ -n "$VALIDATE" ]; then
  if "$VALIDATE" . >"$WORK/opv" 2>&1; then pass "omarchy-plugin-validate" "exit 0"
  else fail "omarchy-plugin-validate" "$(head -1 "$WORK/opv")"; fi
else
  soft "omarchy-plugin-validate" "not installed here; run omarchy plugin validate on an Omarchy machine"
fi
echo

# ------------------------------------------------ (d) README and packaging
echo "-- (d) README and packaging --"
RM="$(grep -iE '^readme(\.[^/]+)?$' "$FILES" | head -1 || true)"
if [ -z "$RM" ] || [ ! -f "$RM" ]; then
  warn "readme/present" "no root README to inspect"
else
  "$PY" -I -S -B - "$RM" "$TALLY" <<'PY'
import json, re, sys
path, tally = sys.argv[1], sys.argv[2]
T = open(tally, "a", encoding="utf-8")
def ok(r, n=""): print("PASS  %-44s %s" % (r, n))
def no(r, m):    print("FAIL  %-44s %s" % (r, m)); T.write("FAIL\n")
def wa(r, m):    print("WARN  %-44s %s" % (r, m)); T.write("WARN\n")
text = open(path, encoding="utf-8").read()
try:
    manifest = json.load(open("manifest.json", encoding="utf-8"))
except Exception:
    manifest = {}
headings = [h.strip().lower() for h in re.findall(r"(?m)^#{1,6}\s+(.+)$", text)]
def has_heading(*names):
    return any(any(h.startswith(n) for n in names) for h in headings)

prose = [l.strip() for l in text.split("\n") if l.strip() and not l.lstrip().startswith(("#", "!", "<", "---", "|"))]
first = prose[0] if prose else ""
desc = str(manifest.get("description", ""))
lead = desc.split(":")[0]
(ok if lead and first.startswith(lead) else no)("readme/purpose-first",
    "first sentence is the manifest purpose" if lead and first.startswith(lead) else "the first prose line must start with: %s" % lead[:70])
for label, names in (("install", ("install",)), ("dependencies", ("dependencies", "requirements")),
                     ("permission levels", ("permission levels",)), ("security model", ("security model",)),
                     ("limitations", ("limitations",)), ("removal", ("removal", "remove", "uninstall")),
                     ("license", ("license",))):
    (ok if has_heading(*names) else no)("readme/section-%s" % label.replace(" ", "-"),
        "present" if has_heading(*names) else "a section heading for %s is required" % label)
for label, sentence in (("no-sudo-wording", "No sudo or pkexec is required."),
                        ("no-bypass-wording", "No automatic-approval or permission-bypass flag is ever passed.")):
    (ok if sentence in text else no)("readme/" + label, sentence if sentence in text else "missing the sentence: " + sentence)
(ok if "cancel-all" in text else no)("readme/removal-cancels-jobs", "cancel-all documented" if "cancel-all" in text
    else "Removal must run ap4a cancel-all before removing the plugin")
(ok if "<stdin>" in text else no)("readme/exact-commands", "the command templates show <stdin>" if "<stdin>" in text
    else "the Security model must show the exact agent commands with <stdin>")
# v2: paid usage and limits each have their own section, and Cursor Agent and Pi have exact templates.
for label, names in (("paid usage", ("paid usage",)), ("limits", ("limits",))):
    (ok if has_heading(*names) else no)("readme/section-%s" % label.replace(" ", "-"),
        "present" if has_heading(*names) else "a section heading for %s is required" % label)
collector_ids = [i for i in ("mrlarsendk", "chispes", "hancengiz", "agent-collectors", "io.github.") if i in text.lower()]
(no if collector_ids else ok)("readme/no-collector-plugin-ids",
    "names usage collector plugins: %s" % ", ".join(collector_ids) if collector_ids
    else "limits are described as records other tools keep, no plugin named")
fences = re.findall(r"(?ms)^```[^\n]*\n(.*?)^```", text)
def template(prefix):
    for block in fences:
        for chunk in re.split(r"\n[ \t]*\n", block):
            if chunk.lstrip().startswith(prefix):
                return chunk
    return None
missing = []
for label, prefix in (("Cursor Agent", "cursor-agent -p --output-format stream-json"), ("Pi", "pi --mode json")):
    chunk = template(prefix)
    if chunk is None:
        missing.append("no %s template starting %r" % (label, prefix))
    elif "<stdin>" not in chunk:
        missing.append("the %s template does not end in <stdin>" % label)
flags = "\n".join(fences)
for label, needle in (("Cursor Agent plan flags", "--mode ask --sandbox enabled"),
                      ("Pi plan tools", "--tools read,grep,find,ls")):
    if needle not in flags:
        missing.append("the permission flags do not list %s (%s)" % (label, needle))
(no if missing else ok)("readme/cursor-pi-templates-stdin", "; ".join(missing) if missing
    else "cursor-agent and pi templates with <stdin> and their plan flags")
bait = re.search(r"not (yet )?(installable|built|usable)|nothing is installable|^#+\s*status", text, re.I | re.M)
(no if bait else ok)("readme/manual-setup-bait", "a 'not installable' line invites a decline" if bait else "nothing says the plugin does not install")
claims = re.search(r"\b(verified|audited|security[- ]reviewed)\b", text, re.I)
(wa if claims else ok)("readme/no-verification-claims", "the README claims verification or an audit" if claims else "no verification or audit claim")
name = str(manifest.get("name", ""))
(ok if name and name in text else wa)("readme/name-agrees-with-manifest", name or "n/a")
(wa if "—" in text else ok)("readme/no-em-dash", "em-dash found" if "—" in text else "none")
T.close()
PY
fi

# ------------------------------------------------ (e) submission issue body
if [ -n "$SUBMISSION" ]; then
  echo
  echo "-- (e) submission issue body (scripts/submission.mjs) --"
"$PY" -I -S -B - "$SUBMISSION" "$TALLY" <<'PY'
import re, sys
body = open(sys.argv[1], encoding="utf-8").read()
T = open(sys.argv[2], "a", encoding="utf-8")
def ok(r, n=""): print("PASS  %-44s %s" % (r, n))
def no(r, m):    print("FAIL  %-44s %s" % (r, m)); T.write("FAIL\n")
HEAD = ["Repository URL", "Category", "Tags", "Suggest a missing tag", "Maintainer notes", "Submission checklist"]
CATS = ["Appearance", "Desktop", "Developer Tools", "Hardware", "Kids", "Productivity", "System", "Widgets", "Other"]
TAGS = ["ai", "bar", "education", "games", "hyprland", "kids", "launcher", "media", "power-management", "quickshell",
        "security", "system", "workspaces"]
ALIAS = set(["autohide", "bar-widget", "battery", "command-palette", "coming-soon", "dell", "dev", "firmware", "hardware",
             "hardware-control", "laptop", "music", "ollama", "omarchy", "overlay", "overviews", "plugin", "power-profiles",
             "previews", "quickapps", "search", "screenshot", "shell-suite", "sidebar", "system-monitoring", "updates",
             "visualizer"])
DROPPED = set(["coming-soon", "omarchy", "plugin"])
CHECK = ["The repository is public and contains installation and removal instructions.",
         "I have documented the plugin license and any external dependencies.",
         "I confirm that I own or have permission to submit this plugin and its preview assets.",
         "The plugin does not overwrite user configuration without explicit consent.",
         "I understand that approval is for listing and is not a security review."]
first = body.split("\n", 1)[0].strip()
title = first[6:].strip() if first.lower().startswith("title:") else first
(ok if re.match(r"^\[Plugin\]:\s*\S", title) else no)("submission-title-invalid",
    title[:54] if re.match(r"^\[Plugin\]:\s*\S", title) else 'title must match /^\\[Plugin\\]:\\s*\\S/')
marks = [(m.group(1), m.start(), m.end()) for m in re.finditer(r"^###\s+(.+?)\s*$", body, re.M) if m.group(1) in HEAD]
names = [h for h, _, _ in marks]
dup = [h for h in names if names.count(h) > 1]
if dup: no("submission-field-repeated", 'Submission repeats the "%s" field' % dup[0])
elif names == HEAD: ok("submission-fields-invalid", "all six reserved headings, in order")
else: no("submission-fields-invalid", "headings must be exactly %s (got %s)" % (", ".join(HEAD), names))
sec = {}
for i, (h, s, e) in enumerate(marks):
    sec[h] = body[e: marks[i + 1][1] if i + 1 < len(marks) else len(body)].strip()
repo = sec.get("Repository URL", "")
(ok if re.match(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$", repo) else no)(
    "submission-repository-invalid", repo or "Repository URL must be a public GitHub repository root URL")
cat = sec.get("Category", "")
(ok if cat in CATS else no)("submission-category-invalid", cat if cat in CATS else 'unsupported category "%s"' % cat)
raw = [re.sub(r"\s+", "-", t.strip().lstrip("-*+ ").strip("`").lower()) for t in re.split(r"[,\n]", sec.get("Tags", "")) if t.strip()]
uniq = list(dict.fromkeys(raw))
bad = [t for t in uniq if t not in TAGS and t not in ALIAS]
if not uniq or len(uniq) > 3: no("submission-tag-count-invalid", "between one and three tags (got %d)" % len(uniq))
elif bad: no("submission-tags-invalid", "unsupported tags: %s" % ", ".join(bad))
elif not [t for t in uniq if t not in DROPPED]: no("submission-tag-count-invalid", "every tag maps to nothing once aliased")
else: ok("submission-tags-invalid", ", ".join(uniq))
cl = sec.get("Submission checklist", "")
miss = [s for s in CHECK if not re.search(r"^-\s*\[[xX]\]\s*" + re.escape(s) + r"\s*$", cl, re.M)]
(no if miss else ok)("submission-checklist-unconfirmed", "not confirmed, verbatim: %s" % miss[0] if miss else "all five items checked")
T.close()
PY
fi

echo
NF="$(grep -c '^FAIL$' "$TALLY" || true)"
NW="$(grep -c '^WARN$' "$TALLY" || true)"
if [ "${NF:-0}" -eq 0 ]; then
  printf '%sready%s: 0 failures, %s warning(s).\n' "$G" "$Z" "${NW:-0}"
  exit 0
fi
printf '%s%s rule(s) failed%s, %s warning(s). Fix these before opening the submission issue.\n' "$R" "${NF:-0}" "$Z" "${NW:-0}"
exit 1
