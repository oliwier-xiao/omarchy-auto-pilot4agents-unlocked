#!/bin/bash
# tests/q4_qml.test.sh: limits, bar states and timelines (owner Q4, CONTRACT-V2-DELTA section 10).
#
#   Service.qml verb table (static), lib/Model.js and lib/Timeline.js in the Qt 6 engine, then the
#   Q4 components offscreen with the repository's lint stand-ins for qs.Commons, qs.Ui and
#   Quickshell: BarWidget, LimitStrip, LimitsSheet, TimelineRibbon, DayTimeline, HistoryView,
#   QueueView, JobRow and JobCard, Panel's sheet routing, and Service.qml itself with a stand-in
#   BoundedProcess that records each helper call and answers from a table.
#
# Each case is its own engine process; its exit code is the verdict (0 passed, n = first failing
# check, 250 = it threw), and any QML warning while it ran fails it too. No helper, no process, no
# state folder: nothing here touches the machine. Local time is pinned to Europe/Warsaw so every
# clock string below is a fact.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QML=/usr/lib/qt6/bin/qml
PY=/usr/bin/python3

pass=0; fail=0
ok() { printf '  ok   %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL %s\n         %s\n' "$1" "$2"; fail=$((fail + 1)); }
finish() { printf '\n%d passed, %d failed\n' "$pass" "$fail"; [ "$fail" -eq 0 ]; exit; }

T="$(mktemp -d "${TMPDIR:-/tmp}/ap4a-q4.XXXXXX")" || exit 2
trap 'rm -rf "$T"' EXIT INT TERM

export TZ=Europe/Warsaw
export QT_QPA_PLATFORM=offscreen
export QT_FORCE_STDERR_LOGGING=1
export QML_DISABLE_DISK_CACHE=1
export PYTHONDONTWRITEBYTECODE=1

# ---------------------------------------------------------------- static

echo "=== Service.qml verb table (CONTRACT-V2 4, 9.1) ==="
if out="$("$PY" -I -S -B - "$REPO/Service.qml" 2>&1 <<'PY'
import re
import sys

src = open(sys.argv[1], encoding="utf-8").read()
pattern = r'"([a-z-]+)":\s*\{\s*ms:\s*(\d+),\s*cap:\s*(\d+),\s*stdin:\s*(true|false),\s*lane:\s*"(read|write)"'
rows = {m.group(1): (int(m.group(2)), int(m.group(3)), m.group(4) == "true", m.group(5))
        for m in re.finditer(pattern, src)}
R, W = "read", "write"
expected = {
    "edition": (7000, 65536, False, R), "list": (9000, 1048576, False, R), "job-get": (9000, 524288, False, R),
    "job-create": (13000, 65536, True, W), "job-update": (30000, 65536, True, W), "job-delete": (11000, 65536, False, W),
    "preview": (17000, 65536, True, R), "arm": (45000, 65536, False, W), "run-now": (45000, 65536, False, W),
    "disarm": (30000, 65536, False, W), "cancel-all": (95000, 65536, False, W), "reschedule": (30000, 65536, False, W),
    "swap": (45000, 65536, False, W), "shift": (90000, 65536, False, W), "reconcile": (65000, 65536, False, W),
    "settings-get": (7000, 16384, False, R), "settings-set": (9000, 16384, True, W), "copy-resume": (8000, 16384, False, W),
    "sessions": (13000, 921600, False, R), "usage": (8000, 131072, False, R), "agents": (35000, 65536, False, R),
    "models": (30000, 262144, False, R), "timeline": (9000, 262144, False, R),
}
bad = sorted(set(rows) ^ set(expected)) + sorted(k for k in rows if k in expected and rows[k] != expected[k])
if bad:
    print("differs: " + ", ".join(bad))
    raise SystemExit(1)
PY
)"; then ok "service_verbs_v2_table: every verb's deadline, cap, stdin and lane, models and timeline added"
else no "service_verbs_v2_table" "$out"; fi

if [ ! -x "$QML" ]; then
  echo "  skip the engine cases (no Qt 6 qml runtime at $QML)"
  finish
fi

# ---------------------------------------------------------------- tree

W="$T/w"
mkdir -p "$W/imports"
cp "$REPO"/*.qml "$W/"
cp -r "$REPO/lib" "$REPO/components" "$W/"
cp -r "$REPO/lint/qs" "$REPO/lint/Quickshell" "$W/imports/"

# Q2's sign-in sheet, when a parallel build has not added it yet: the common sheet surface.
[ -f "$W/components/SignInSheet.qml" ] || cat > "$W/components/SignInSheet.qml" <<'QML'
import QtQuick
Item {
  id: sheet
  required property var theme
  required property var service
  property bool active: false
  property string harness: ""
  readonly property string hints: "Enter check again  ·  Esc back"
  signal closed()
  signal noticeRequested(string text, string kind, var undo)
  function handleKey(event) { if (event.key === Qt.Key_Escape) { sheet.close(); return true } return false }
  function recheck() {}
  function open(args) {}
  function close() { sheet.closed() }
}
QML

# Q5's model sheet, when a parallel build has not added it yet: the common sheet surface.
[ -f "$W/components/ModelSheet.qml" ] || cat > "$W/components/ModelSheet.qml" <<'QML'
import QtQuick
Item {
  id: sheet
  required property var theme
  required property var service
  property bool active: false
  readonly property string hints: "Esc back"
  signal closed()
  signal picked(var selection)
  signal noticeRequested(string text, string kind, var undo)
  function handleKey(event) { if (event.key === Qt.Key_Escape) { sheet.close(); return true } return false }
  function open(args) {}
  function close() { sheet.closed() }
}
QML

cat > "$W/Harness.qml" <<'QML'
import QtQuick
import "lib/Timeline.js" as Timeline

Item {
  id: h
  width: 1100
  height: 800
  property int n: 0
  property int firstFail: 0

  function check(c, what) {
    h.n++
    if (!c) {
      console.warn("check " + h.n + " failed: " + (what === undefined ? "" : what))
      if (h.firstFail === 0) h.firstFail = h.n
    }
  }
  function eq(a, b, what) {
    var same = JSON.stringify(a) === JSON.stringify(b)
    h.check(same, (what === undefined ? "" : what + ": ") + "got " + JSON.stringify(a) + " want " + JSON.stringify(b))
  }
  function at(y, mo, d, hh, mi, s) { return new Date(y, mo - 1, d, hh, mi, s || 0).getTime() }
  function done() { Qt.exit(h.firstFail) }
  function threw(e) {
    console.warn("threw: " + e + (e && e.stack ? " " + e.stack : ""))
    h.firstFail = 250
    Qt.exit(250)
  }
  function find(item, pred, depth) {
    var d = depth || 0
    if (!item || d > 80) return null
    if (pred(item)) return item
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) {
      var hit = h.find(kids[i], pred, d + 1)
      if (hit) return hit
    }
    return null
  }
  function collect(item, pred, out, depth) {
    var d = depth || 0
    if (!item || d > 80) return out
    if (pred(item)) out.push(item)
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) h.collect(kids[i], pred, out, d + 1)
    return out
  }
  // Every visible Text's string under `item`.
  function texts(item) {
    return h.collect(item, function (it) { return it.visible === true && typeof it.text === "string" && it.font !== undefined
      && it.horizontalAlignment !== undefined }, [], 0).map(function (t) { return t.text })
  }
  // First pair of boxes on the same row that overlap, or null.
  function overlapping(rects) {
    for (var i = 0; i < rects.length; i++)
      for (var j = i + 1; j < rects.length; j++)
        if (Math.abs(rects[i].y - rects[j].y) < 0.5 && Timeline.intersects(rects[i], rects[j])) return [rects[i], rects[j]]
    return null
  }
}
QML

cat > "$W/TestTheme.qml" <<'QML'
import QtQuick
import "lib/Model.js" as Model
import "lib/Tint.js" as Tint

// Panel's theme object on the tokyo-night surface.
QtObject {
  id: theme
  readonly property color surface: "#1a1b26"
  readonly property color fg: "#a9b1d6"
  readonly property color accent: "#7aa2f7"
  readonly property color accentInk: Tint.ink("#7aa2f7", "#1a1b26", 4.5)
  readonly property color strong: Qt.rgba(theme.fg.r, theme.fg.g, theme.fg.b, 0.88)
  readonly property color readable: Qt.rgba(theme.fg.r, theme.fg.g, theme.fg.b, 0.77)
  readonly property color soft: Qt.rgba(theme.fg.r, theme.fg.g, theme.fg.b, 0.69)
  readonly property color faint: Qt.rgba(theme.fg.r, theme.fg.g, theme.fg.b, 0.14)
  readonly property real textTarget: 4.5
  readonly property var inks: {
    var out = {}
    var ids = Model.harnessOrder()
    for (var i = 0; i < ids.length; i++) out[ids[i]] = Tint.harnessInk(ids[i], "#1a1b26", 4.5)
    return out
  }
  readonly property var marks: {
    var out = {}
    var ids = Model.harnessOrder()
    for (var i = 0; i < ids.length; i++) out[ids[i]] = Tint.harnessInk(ids[i], "#1a1b26", 3.0)
    return out
  }
  readonly property var geminiStops: Tint.geminiStops("#1a1b26", 3.0)
  readonly property color okInk: Tint.solved(150, 60, "#1a1b26", 4.5)
  readonly property color warnInk: Tint.solved(38, 85, "#1a1b26", 4.5)
  readonly property color badInk: Tint.ink("#f7768e", "#1a1b26", 4.5)
  property bool reduceMotion: false
  readonly property bool animate: true
  readonly property string fontFamily: "monospace"
  readonly property QtObject type: QtObject {
    readonly property int readout: 24
    readonly property int title: 14
    readonly property int body: 12
    readonly property int label: 11
    readonly property int data: 11
    readonly property int meta: 10
    readonly property int glyph: 11
    readonly property int glyphLarge: 14
    readonly property int brandGlyph: 16
    readonly property real tracking: 0.4
    readonly property real proseLeading: 1.45
    readonly property real measure: 560
    readonly property var digits: ({ "tnum": 1 })
  }
  function harnessInk(id) { return theme.inks[id] || theme.soft }
  function harnessMark(id) { return theme.marks[id] || theme.soft }
  function harnessFill(id) { return theme.inks[id] || theme.soft }
  function moveMs(ms) { return theme.reduceMotion ? 0 : ms }
  function fadeMs(ms) { return theme.reduceMotion ? Math.min(ms, 120) : ms }
}
QML

run_case() { # <dir> <file> <name: description>
  local dir="$1" file="$2" name="$3" out rc warn
  out="$(cd "$dir" && timeout 90 "$QML" -I "$W/imports" "$file" 2>&1)"; rc=$?
  warn="$(printf '%s\n' "$out" | grep -E 'TypeError|ReferenceError|Unable to assign|Cannot read|is not a function|Binding loop|is not a type|failed to load|\.qml:[0-9]+|\.js:[0-9]+' \
    | grep -v 'check [0-9]* failed' || true)"
  if [ "$rc" -eq 0 ] && [ -z "$warn" ]; then ok "$name"
  else no "$name" "rc=$rc $(printf '%s\n' "$out" | grep -E 'check|threw|Warning|Error|qml:' | head -6 | tr '\n' ' ' | cut -c1-900)"; fi
}

# ---------------------------------------------------------------- lib

echo "=== lib/Model.js and lib/Timeline.js ==="

cat > "$W/BarNear.qml" <<'QML'
import QtQuick
import "lib/Model.js" as Model
Harness {
  id: root
  Component.onCompleted: {
    try {
      var now = root.at(2026, 9, 15, 12, 0)
      function st(sec) { return Model.barState({ ready: true, nowMs: now, nextFireAtMs: now + sec * 1000 }) }
      root.eq([Model.NEAR_SEC, Model.SOON_SEC], [3600, 21600], "thresholds")
      root.eq([st(3599).state, st(3600).state, st(21599).state, st(21600).state], ["near", "soon", "soon", "later"])
      root.eq([st(-30).state, st(0).text, st(-30).text], ["near", "due", "due"])
      root.eq([st(45).text, st(92).text, st(42 * 60).text, st(3 * 3600 + 10 * 60).text, st(22 * 3600 + 42 * 60).text,
               st(2 * 86400 + 3 * 3600).text], ["45s", "1m 32s", "42m", "3h10", "22h42", "2d03h"])
      root.eq([st(600).tone, st(7200).tone, st(80000).tone], ["ok", "warn", "readable"])
      root.eq([st(600).glyph, st(7200).glyph, st(80000).glyph], [Model.GLYPH.clock, Model.GLYPH.clock, Model.GLYPH.calendar])
      root.eq(st(42 * 60).sentence, "The next job fires at 12:42, in 42m.")
      root.eq(st(30 * 3600).sentence, "The next job fires Wed 16 Sep 18:00, in 1d 6h.")
      root.check(st(600).pulse === false && st(0).sentence === "The next job is due now.")
    } catch (e) { root.threw(e) }
    root.done()
  }
}
QML
run_case "$W" BarNear.qml "bar_near_soon_thresholds: near under 1 h, soon under 6 h, later after; 42m, 1m 32s, 3h10, 22h42"

cat > "$W/BarColour.qml" <<'QML'
import QtQuick
import "lib/Model.js" as Model
Harness {
  id: root
  Component.onCompleted: {
    try {
      var now = root.at(2026, 9, 15, 12, 0)
      var inputs = [
        { setupProblem: "The helper did not answer.", ready: false },
        { ready: true, nowMs: now, problems: 1, failures: 1 },
        { ready: true, nowMs: now, problems: 2, failures: 1 },
        { ready: true, nowMs: now, authProblems: 1, nextFireAtMs: now + 600000 },
        { ready: true, nowMs: now, running: 1, runningSinceMs: now - 190000 },
        { ready: true, nowMs: now, nextFireAtMs: now + 60000 },
        { ready: true, nowMs: now, nextFireAtMs: now + 7200000 },
        { ready: true, nowMs: now, nextFireAtMs: now + 80000000 },
        { ready: true, nowMs: now, doneSinceSeen: 2 },
        { ready: true, nowMs: now, drafts: 3 },
        { ready: true, nowMs: now }
      ]
      var coloured = ["bad", "ok", "warn", "accent"]
      var seen = {}
      for (var i = 0; i < inputs.length; i++) {
        var s = Model.barState(inputs[i])
        seen[s.state] = s
        root.check(typeof s.glyph === "string" && s.glyph !== "", "glyph for " + s.state)
        root.check(typeof s.sentence === "string" && s.sentence !== "", "sentence for " + s.state)
        if (s.state !== "idle") root.check(s.text !== "", "a word for " + s.state)
        if (coloured.indexOf(s.tone) >= 0) root.check(s.text !== "", "coloured state " + s.state + " has a word")
      }
      root.eq(Object.keys(seen).sort(), ["attention", "drafts", "idle", "later", "near", "running", "soon", "success"])
      // Near (green clock) and success (green check) differ by glyph and wording.
      root.check(seen.near.tone === "ok" && seen.success.tone === "ok")
      root.check(seen.near.glyph !== seen.success.glyph && seen.success.text === "2 done" && seen.near.text === "1m 00s")
      root.check(seen.attention.tone === "bad" && seen.running.tone === "accent" && seen.soon.tone === "warn")
      // Red only for real problems: no other state is bad.
      for (var k in seen) if (k !== "attention") root.check(seen[k].tone !== "bad", k + " is not red")
    } catch (e) { root.threw(e) }
    root.done()
  }
}
QML
run_case "$W" BarColour.qml "bar_glyph_and_text_never_colour_alone: every state has a glyph and a word, red only for attention"

cat > "$W/Chips.qml" <<'QML'
import QtQuick
import "lib/Model.js" as Model
Harness {
  id: root
  Component.onCompleted: {
    try {
      var now = root.at(2026, 9, 15, 12, 0)
      var s = now / 1000
      function win(key, label, kind, pct, resets, extra) {
        var w = { key: key, label: "", title: null, shortLabel: label, kind: kind, percent: pct, over: pct !== null && pct > 1,
                  resetsAt: resets, sliding: false, source: "record", bindable: true }
        for (var k in (extra || {})) w[k] = extra[k]
        return w
      }
      var claude = { id: "claude", name: "Claude", tier: "Max 5x", source: "record", readable: true, relevant: true, stale: false,
                     ageSec: 120, harnesses: ["claude"], headlineKey: "claude:weekly",
                     windows: [win("claude:5-hour", "5-hour", "session", 0.14, s + 3600), win("claude:weekly", "Weekly", "weekly", 0.31, s + 4 * 86400)] }
      var c = Model.chipFor(claude, now)
      root.eq([c.id, c.harness, c.shortLabel, c.text, c.tone, c.computed, c.resetText], ["claude", "claude", "Weekly", "31%", "harness", false, "in 4d 0h"])
      var cursor = { id: "cursor", name: "Cursor", source: "record", readable: true, harnesses: ["cursor"], headlineKey: "cursor:included",
                     windows: [win("cursor:cursor-models", "Cursor models", "billing_pool", 0.96, s + 25 * 86400),
                               win("cursor:included", "Included", "billing_total", 1.05, s + 25 * 86400)] }
      var cc = Model.chipFor(cursor, now)
      root.eq([cc.shortLabel, cc.text, cc.over, cc.tone], ["Included", "100%+", true, "bad"])
      root.eq([Model.meterTone(0.79), Model.meterTone(0.8), Model.meterTone(0.949), Model.meterTone(0.95), Model.meterTone(0.2, true)],
              ["harness", "warn", "warn", "bad", "bad"])
      var zen = { id: "zen-free", name: "OpenCode Zen free", source: "computed", readable: true, harnesses: ["opencode"], headlineKey: "zen-free:daily",
                  windows: [win("zen-free:daily", "", "daily", null, Date.UTC(2026, 8, 16) / 1000, { source: "computed" })] }
      var cz = Model.chipFor(zen, now)
      root.eq([cz.computed, cz.percent, cz.text, cz.shortLabel, cz.harness], [true, null, "resets 02:00", "OpenCode Zen free", "opencode"])
      var fireworks = { id: "fireworks", name: "Fireworks", readable: true, headlineKey: null, windows: [], harnesses: [] }
      root.eq([Model.chipFor(fireworks, now).text, Model.chipFor(fireworks, now).harness], ["No limits reported", ""])
      var collector = { id: "opencode", name: "OpenCode", readable: true, headlineKey: null, windows: [], harnesses: [] }
      root.eq(Model.providerHarness(collector), "opencode")
      var stale = JSON.parse(JSON.stringify(claude)); stale.stale = true; stale.ageSec = 2520
      root.eq([Model.chipFor(stale, now).stale, Model.chipFor(stale, now).ageSec], [true, 2520])
      root.eq([Model.percentText(1.0), Model.percentText(null), Model.percentText(0.004)], ["100%", "-", "0%"])
      root.eq(["five_hour", "seven_day", "usage", "unknown", "model_weekly", "billing_total"].map(Model.legacyLimitKind),
              ["session", "weekly", "other", "other", "model_weekly", "billing_total"])
    } catch (e) { root.threw(e) }
    root.done()
  }
}
QML
run_case "$W" Chips.qml "limit_chip_headline_selection: headline window, Cursor Included over as 100%+, computed resets, windowless"

cat > "$W/Shown.qml" <<'QML'
import QtQuick
import "lib/Model.js" as Model
Harness {
  id: root
  Component.onCompleted: {
    try {
      function p(id, over) {
        var o = { id: id, name: id.toUpperCase(), readable: true, relevant: true, stale: false, headlineKey: id + ":w", harnesses: [] }
        for (var k in over) o[k] = over[k]
        return o
      }
      var providers = [
        p("codex", { harnesses: ["codex", "pi"] }),
        p("cursor", { harnesses: ["cursor"] }),
        p("fireworks", {}),
        p("claude", { harnesses: ["claude"] }),
        p("opencode-go", { harnesses: ["opencode"], relevant: false }),
        p("gemini", { harnesses: ["gemini"], stale: true }),
        p("broken", { readable: false, headlineKey: null }),
        p("zen-free", { harnesses: ["opencode"], source: "computed" }),
        p("gemini-daily", { harnesses: ["gemini"], source: "computed" }),
        p("pi", { headlineKey: null })
      ]
      root.eq(Model.shownSources(providers, "auto"), ["claude", "zen-free", "codex", "gemini-daily", "cursor", "fireworks"])
      root.eq(Model.shownSources(providers, undefined), Model.shownSources(providers, "auto"))
      root.eq(Model.shownSources(providers, ["cursor", "nope", "claude", "cursor", "broken", 7]), ["cursor", "claude", "broken"])
      root.eq(Model.shownSources(null, "auto"), [])
      root.eq(Model.shownSources(providers, []), [])
      root.eq(Model.limitChoices(providers).map(function (c) { return c.id }),
              ["claude", "opencode-go", "zen-free", "codex", "gemini", "gemini-daily", "cursor", "fireworks"])
      root.eq(Model.limitChoices(providers)[0], { id: "claude", name: "CLAUDE", harness: "claude" })
    } catch (e) { root.threw(e) }
    root.done()
  }
}
QML
run_case "$W" Shown.qml "limits_shown_auto_and_custom_order: Auto filters and edition order, Custom keeps listed present ids"

cat > "$W/Lanes.qml" <<'QML'
import QtQuick
import "lib/Model.js" as Model
import "lib/Timeline.js" as Timeline
Harness {
  id: root
  Component.onCompleted: {
    try {
      var dayStart = root.at(2026, 9, 14, 0, 0)
      var dayEnd = Timeline.nextDayMs(dayStart)
      root.eq(dayEnd, root.at(2026, 9, 15, 0, 0))
      var now = root.at(2026, 9, 14, 15, 0)
      function sec(hh, mi) { return root.at(2026, 9, 14, hh, mi) / 1000 }
      var timeline = {
        ok: true,
        resets: [
          { source: "claude", name: "Claude", kind: "session", shortLabel: "5-hour", at: sec(13, 0), origin: "record", harnesses: ["claude"] },
          { source: "claude", name: "Claude", kind: "session", shortLabel: "5-hour", at: sec(13, 0), origin: "current", harnesses: ["claude"] },
          { source: "codex", name: "Codex", kind: "session", shortLabel: "5-hour", at: sec(9, 30), origin: "observed", harnesses: ["codex", "pi"] },
          { source: "cursor", name: "Cursor", kind: "billing_total", shortLabel: "Cursor cycle", at: root.at(2026, 9, 15, 0, 0) / 1000, origin: "record", harnesses: ["cursor"] },
          { source: "gemini-daily", name: "Gemini CLI daily", kind: "daily", shortLabel: "Daily", at: sec(9, 0), origin: "computed", harnesses: ["gemini"] }
        ],
        runs: [
          { runId: "1111111111111111-g1", jobId: "1111111111111111", harness: "claude", label: "a", startedAt: sec(12, 0), endedAt: sec(12, 20), outcome: "done", status: null, limitSource: "claude" },
          { runId: "2222222222222222-g1", jobId: "2222222222222222", harness: "pi", label: "b", startedAt: sec(9, 0), endedAt: sec(9, 5), outcome: "limit", status: null, limitSource: "codex" },
          { runId: "3333333333333333-g1", jobId: "3333333333333333", harness: "opencode", label: "c", startedAt: root.at(2026, 9, 13, 23, 30) / 1000, endedAt: sec(0, 15), outcome: "failed", status: null, limitSource: null },
          { runId: "4444444444444444-g1", jobId: "4444444444444444", harness: "cursor", label: "d", startedAt: sec(14, 50), endedAt: null, outcome: null, status: "running", limitSource: null }
        ],
        armed: [ { jobId: "5555555555555555", harness: "gemini", fireAt: sec(20, 0), limitSource: "gemini-daily" } ]
      }
      var lanes = Timeline.dayLanes(timeline, dayStart, dayEnd, now)
      root.eq(lanes.map(function (l) { return l.source }), ["claude", "codex", "gemini-daily", null])
      root.eq(lanes.map(function (l) { return l.name }), ["Claude", "Codex", "Gemini CLI daily", "No limit data"])
      root.eq(lanes.map(function (l) { return l.harness }), ["claude", "codex", "gemini", ""])
      root.eq([lanes[0].markers.length, lanes[0].markers[0].hollow, lanes[0].markers[0].shortLabel], [1, false, "5-hour"])
      root.eq([lanes[1].markers[0].hollow, lanes[1].capsules[0].outcome, lanes[1].capsules[0].harness], [true, "limit", "pi"])
      root.eq([lanes[2].capsules[0].outcome, lanes[2].capsules[0].startMs === lanes[2].capsules[0].endMs], ["armed", true])
      var none = lanes[3].capsules
      root.eq(none.map(function (c) { return c.jobId }), ["3333333333333333", "4444444444444444"])
      root.eq([none[0].startMs, none[0].endMs], [dayStart, sec(0, 15) * 1000], "a run from the day before is clipped")
      root.eq([none[1].endMs, none[1].outcome], [now, "running"], "a running run ends at now")
      root.eq(Timeline.dayLanes(null, dayStart, dayEnd, now), [])
      root.eq(Timeline.dayLanes({ resets: [], runs: [], armed: [] }, dayStart, dayEnd, now), [])
      root.eq(Timeline.ticks(dayStart, dayEnd, 3).map(function (t) { return t.label }), ["00", "03", "06", "09", "12", "15", "18", "21"])
      var dst = root.at(2026, 10, 25, 0, 0)
      root.eq([Timeline.nextDayMs(dst) - dst, Timeline.ticks(dst, Timeline.nextDayMs(dst), 3).length], [25 * 3600000, 8], "25-hour DST day")
      root.eq(Timeline.dayStartFor(root.at(2026, 9, 15, 13, 0), -2), root.at(2026, 9, 13, 0, 0))

      // layoutLabels: no two visible boxes on a row touch, whatever is thrown at it.
      var seed = 7
      function rnd() { seed = (seed * 1103515245 + 12345) % 2147483648; return seed / 2147483648 }
      for (var round = 0; round < 150; round++) {
        var width = 300 + Math.floor(rnd() * 700)
        var specs = []
        var count = 2 + Math.floor(rnd() * 14)
        for (var i = 0; i < count; i++) {
          var pr = Math.floor(rnd() * 4)
          var w = 10 + Math.floor(rnd() * 70)
          var x = rnd() * width
          specs.push({ id: "l" + i, x: x, altX: x - w - 3, width: w, priority: pr, row: rnd() < 0.3 ? 1 : 0 })
        }
        var laid = Timeline.layoutLabels(specs, width, 40, 4)
        var boxes = []
        for (var j = 0; j < laid.length; j++) {
          if (!laid[j].visible) continue
          var bw = laid[j].id === "count" ? 40 : specs[j].width
          boxes.push({ x: laid[j].x, y: laid[j].row * 20, w: bw, h: 12 })
          root.check(laid[j].x >= 0 && laid[j].x + bw <= width + 0.5, "inside the width")
        }
        var clash = root.overlapping(boxes)
        root.check(clash === null, "round " + round + " overlap " + JSON.stringify(clash))
        root.eq(laid.length >= specs.length && laid.slice(0, specs.length).map(function (l) { return l.id }).join() === specs.map(function (s) { return s.id }).join(), true, "order kept")
      }
      // The later label owns the right edge; the now label beats a reset at the same place.
      var edge = Timeline.layoutLabels([
        { id: "tick", x: 950, width: 14, priority: 0, row: 0 },
        { id: "later", x: 952, width: 48, priority: 1, row: 0 },
        { id: "reset", x: 100, altX: 20, width: 70, priority: 2, row: 0 },
        { id: "now", x: 110, width: 20, priority: 3, row: 0 }
      ], 1000, 40, 6)
      root.eq(edge.map(function (l) { return [l.id, l.visible] }), [["tick", false], ["later", true], ["reset", true], ["now", true]])
      root.eq(edge[2].x, 20, "the reset label flipped left of its marker")
      // A count that lands away from the reset it stands for knows that reset's marker, for its leader.
      var crowded = Timeline.layoutLabels([
        { id: "a", x: 103, altX: 37, anchorX: 100, width: 60, priority: 2, row: 0 },
        { id: "b", x: 113, altX: 47, anchorX: 110, width: 60, priority: 2, row: 0 },
        { id: "c", x: 123, altX: 57, anchorX: 120, width: 60, priority: 2, row: 0 }
      ], 1000, 40, 4)
      var countEntry = crowded.filter(function (l) { return l.id === "count" })[0]
      root.check(!!countEntry && countEntry.visible && countEntry.collapsed === 2 && countEntry.anchorX === 110,
                 "count anchors to its collapsed marker: " + JSON.stringify(countEntry))
      root.check(Timeline.layoutLabels([{ id: "x", x: 5, width: 990, priority: 2, row: 0 }, { id: "y", x: 6, width: 990, priority: 2, row: 0 }], 1000, 40, 4)
                   .filter(function (l) { return l.id === "count" })[0].anchorX === 6, "no marker given: the label's own x")
    } catch (e) { root.threw(e) }
    root.done()
  }
}
QML
run_case "$W" Lanes.qml "day_timeline_lanes_by_source: lanes per source in agent order, hollow observed resets, clipped and running capsules"

# ---------------------------------------------------------------- components

echo "=== Q4 components (offscreen, lint stand-ins) ==="

cat > "$W/BarStates.qml" <<'QML'
import QtQuick
import "lib/Model.js" as Model
import "lib/Tint.js" as Tint
import "lib/Edition.js" as Edition
Harness {
  id: root
  readonly property double now: root.at(2026, 9, 15, 12, 0)

  QtObject { id: shellObj; function serviceFor(id) { return id === Edition.PLUGIN_ID ? svc : null } }
  QtObject {
    id: fakeBar
    property var shell: shellObj
    property bool vertical: false
    property int barSize: 30
    property string fontFamily: "monospace"
    property color barForeground: "#cacccc"
    property color urgent: "#f7768e"
    property bool foregroundAnimationEnabled: false
    function showTooltip(target, text) {}
    function hideTooltip(target) {}
    function registerClickTarget(target) {}
    function unregisterClickTarget(target) {}
  }
  QtObject {
    id: svc
    property bool ready: true
    property bool anyRunning: false
    property int armedCount: 1
    property int runningCount: 0
    property int attentionCount: 0
    property int draftCount: 3
    property double nowMs: root.now
    property var nextJob: null
    property string setupProblem: ""
    property string barLabel: ""
    property var settings: ({ motion: "full" })
    property var barState: Model.barState({ ready: true, nowMs: root.now })
  }

  BarWidget { id: w; bar: fakeBar; settings: ({ barLabel: "Next run" }) }

  Component.onCompleted: {
    try {
      var button = root.find(w, function (it) { return it.activeColor !== undefined && it.dimmed !== undefined && it.fixedWidth !== undefined })
      root.check(button !== null, "WidgetButton found")
      var surface = "#101315"
      var inks = { bad: Tint.ink("#f7768e", surface, 4.5), ok: Tint.solved(150, 60, surface, 4.5),
                   warn: Tint.solved(38, 85, surface, 4.5), accent: Tint.ink("#cacccc", surface, 4.5) }
      var t = root.now
      var rows = [
        // input, state, text, active tone or "", dimmed
        [{ ready: false, setupProblem: "The helper did not answer.", problems: 3, running: 1 }, "attention", "error", "bad", false],
        [{ ready: true, nowMs: t, problems: 1, failures: 1, authProblems: 1, running: 2, nextFireAtMs: t + 60000, doneSinceSeen: 1, drafts: 2 }, "attention", "1 failed", "bad", false],
        [{ ready: true, nowMs: t, problems: 2, failures: 1 }, "attention", "2 need you", "bad", false],
        [{ ready: true, nowMs: t, authProblems: 1, running: 1, nextFireAtMs: t + 60000 }, "attention", "auth", "bad", false],
        [{ ready: true, nowMs: t, running: 1, runningSinceMs: t - 190000, nextFireAtMs: t + 60000, doneSinceSeen: 4 }, "running", "running 3m", "accent", false],
        [{ ready: true, nowMs: t, nextFireAtMs: t + 42 * 60000, doneSinceSeen: 4, drafts: 1 }, "near", "42m", "ok", false],
        [{ ready: true, nowMs: t, nextFireAtMs: t + 190 * 60000, doneSinceSeen: 4 }, "soon", "3h10", "warn", false],
        [{ ready: true, nowMs: t, nextFireAtMs: t + (22 * 60 + 42) * 60000, doneSinceSeen: 4 }, "later", "22h42", "", false],
        [{ ready: true, nowMs: t, doneSinceSeen: 2, drafts: 3 }, "success", "2 done", "ok", false],
        [{ ready: true, nowMs: t, drafts: 3 }, "drafts", "3 drafted", "soft", false],
        [{ ready: true, nowMs: t }, "idle", "", "soft", false]
      ]
      // Drafts and idle: the bar foreground at the lowest alpha that reads at 4.5:1, never the kit's opacity dim.
      var softAlpha = Tint.alphaFor("#cacccc", surface, 4.5, 0.6)
      inks.soft = Qt.rgba(0xca / 255, 0xcc / 255, 0xcc / 255, softAlpha)
      var fgRgb = Tint.hexRgb("#cacccc"), bgRgb = Tint.hexRgb(surface)
      var composite = { r: fgRgb.r * softAlpha + bgRgb.r * (1 - softAlpha), g: fgRgb.g * softAlpha + bgRgb.g * (1 - softAlpha),
                        b: fgRgb.b * softAlpha + bgRgb.b * (1 - softAlpha) }
      root.check(Tint.contrast(composite, surface) >= 4.5, "drafts ink reads at 4.5:1 on the dark bar")
      var light = "#fafafa", lightAlpha = Tint.alphaFor("#1f1f1f", light, 4.5, 0.6), lf = Tint.hexRgb("#1f1f1f"), lb = Tint.hexRgb(light)
      root.check(Tint.contrast({ r: lf.r * lightAlpha + lb.r * (1 - lightAlpha), g: lf.g * lightAlpha + lb.g * (1 - lightAlpha),
                                 b: lf.b * lightAlpha + lb.b * (1 - lightAlpha) }, light) >= 4.5, "and on a light bar")
      for (var i = 0; i < rows.length; i++) {
        var r = rows[i]
        svc.barState = Model.barState(r[0])
        root.eq([w.barState.state, w.labelText], [r[1], r[2]], "row " + i)
        root.check(button.active === (r[3] !== ""), "row " + i + " active")
        if (r[3] !== "") root.check(Qt.colorEqual(button.activeColor, inks[r[3]]), "row " + i + " ink " + button.activeColor + " want " + inks[r[3]])
        root.check(button.dimmed === r[4] && button.opacity === 1, "row " + i + " never dimmed by opacity")
        root.check(w.glyph === svc.barState.glyph && w.glyph !== "", "row " + i + " glyph")
        root.eq(w.tooltipText.split("\n")[0], r[1] === "attention" && i === 0 ? "The helper did not answer." : svc.barState.sentence, "row " + i + " tooltip")
      }
      svc.barState = Model.barState(rows[4][0])
      root.check(w.pulsing === true, "running pulses")
      svc.settings = { motion: "reduced" }
      root.check(w.pulsing === false, "reduced motion stops the pulse")
      root.eq(w.labelTemplate, "running 8m")
      w.settings = { barLabel: "Queued count" }
      root.eq(w.labelText, "1")
      w.settings = { barLabel: "Nothing" }
      root.eq(w.labelText, "")
      root.eq(w.glyphIdle, "󰃰")
    } catch (e) { root.threw(e) }
    root.done()
  }
}
QML
run_case "$W" BarStates.qml "bar_states_priority_table: attention > running > near > soon > later > success > drafts > idle, inks solved on the bar"

cat > "$W/Strip.qml" <<'QML'
import QtQuick
import "components"
import "lib/Model.js" as Model
Harness {
  id: root
  readonly property double now: root.at(2026, 9, 15, 12, 0)
  function provider(id, harness, label, pct, extra) {
    var s = root.now / 1000
    var p = { id: id, name: id, readable: true, relevant: true, stale: false, ageSec: 60, source: "record", harnesses: [harness],
              headlineKey: id + ":main",
              windows: [{ key: id + ":main", label: "", title: null, shortLabel: label, kind: "session", percent: pct,
                          over: pct !== null && pct > 1, resetsAt: s + 9000, sliding: false, source: "record", bindable: true }] }
    for (var k in (extra || {})) p[k] = extra[k]
    return p
  }
  readonly property var providers: [
    root.provider("claude", "claude", "5-hour", 0.14),
    root.provider("codex", "codex", "30-day", 0.01),
    root.provider("cursor", "cursor", "Included", 1.05),
    root.provider("zen-free", "opencode", "", null, { source: "computed" }),
    root.provider("gemini-daily", "gemini", "", null, { source: "computed", stale: false })
  ]
  TestTheme { id: theme }
  property int clicks: 0

  LimitStrip { id: strip; y: 10; width: 520; theme: theme; providers: root.providers; nowMs: root.now
    shownIds: ["claude", "codex", "cursor", "zen-free", "gemini-daily"]; onSheetRequested: root.clicks++ }
  LimitStrip { id: wide; y: 60; width: implicitWidth; theme: theme; providers: root.providers; nowMs: root.now; shownIds: ["claude", "codex"] }
  LimitStrip { id: nothing; y: 110; width: 300; theme: theme; providers: root.providers; nowMs: root.now; shownIds: [] }
  LimitStrip { id: failed; y: 150; width: 300; theme: theme; providers: []; shownIds: []; nowMs: root.now; errorText: "The helper did not answer." }
  LimitStrip { id: tight; y: 200; width: 90; theme: theme; providers: root.providers; nowMs: root.now; shownIds: ["claude", "codex"] }

  Timer {
    interval: 300
    running: true
    onTriggered: {
      try {
        root.eq(strip.chips.length, 5)
        root.check(strip.fitCount >= 1 && strip.fitCount < 5, "some chips fit, not all: " + strip.fitCount)
        root.eq([strip.hiddenCount, strip.moreText], [5 - strip.fitCount, "+" + (5 - strip.fitCount)])
        var rects = strip.chipRects()
        root.eq(rects.length, strip.fitCount + 1)
        root.check(root.overlapping(rects) === null, "chips overlap: " + JSON.stringify(root.overlapping(rects)))
        for (var i = 0; i < rects.length; i++) root.check(rects[i].x >= -0.5 && rects[i].x + rects[i].w <= strip.width + 0.5, "chip inside the strip " + JSON.stringify(rects[i]))
        var widths = rects.filter(function (r) { return r.id !== "more" }).map(function (r) { return r.w })
        root.check(widths.every(function (x) { return x === widths[0] }), "one chip width")
        var words = root.texts(strip)
        root.check(words.indexOf("5-hour") >= 0 && words.indexOf("14%") >= 0 && words.indexOf("in 2h 30m") >= 0, JSON.stringify(words))
        root.eq([wide.fitCount, wide.hiddenCount, wide.chipRects().length], [2, 0, 2])
        root.eq([nothing.chips.length, nothing.moreText], [0, "Limits"])
        root.check(root.texts(failed).indexOf("Limits could not be read.") >= 0, "fail note")
        root.eq([tight.fitCount, tight.moreText], [0, "+2"])
        var more = root.find(strip, function (it) { return it.containsMouse !== undefined && it.parent && it.parent.width === strip.moreWidth })
        root.check(more !== null, "more chip mouse area")
        if (more) more.clicked(null)
        root.eq(root.clicks, 1)
        var cursorChip = root.texts(wide)
        root.check(cursorChip.indexOf("100%+") < 0, "cursor not shown in wide")
      } catch (e) { root.threw(e) }
      root.done()
    }
  }
}
QML
run_case "$W" Strip.qml "limit_strip_overflow_no_overlap: fixed-width chips, +N overflow, nothing overlaps or leaves the strip"

cat > "$W/Sheet.qml" <<'QML'
import QtQuick
import "components"
import "lib/Model.js" as Model
Harness {
  id: root
  readonly property double now: root.at(2026, 9, 15, 12, 0)
  readonly property double s: root.now / 1000
  property double gotFire: 0
  property string gotHarness: ""
  property int closes: 0
  TestTheme { id: theme }
  QtObject {
    id: svc
    property double nowMs: root.now
    property bool loadingUsage: false
    property int refreshes: 0
    property var settings: ({ resetMarginSec: 180 })
    function refreshUsage() { svc.refreshes++ }
    property var providers: [
      { id: "claude", name: "Claude", tier: "Max 5x", source: "record", readable: true, relevant: true, stale: false, ageSec: 120,
        harnesses: ["claude"], headlineKey: "claude:5-hour", tokens: 987654, totalTokens: 123456789, updatedAtMs: root.now - 120000,
        windows: [
          { key: "claude:5-hour", shortLabel: "5-hour", kind: "session", percent: 0.14, over: false, resetsAt: root.s + 3600, sliding: false, source: "record", bindable: true, tokensUsed: 55555 },
          { key: "claude:weekly", shortLabel: "Weekly", kind: "weekly", percent: 0.83, over: false, resetsAt: root.s + 4 * 86400, sliding: false, source: "record", bindable: true },
          { key: "claude:fable-weekly", shortLabel: "Fable weekly", kind: "model_weekly", percent: 0, over: false, resetsAt: root.s + 9 * 86400, sliding: false, source: "record", bindable: false } ] },
      { id: "codex", name: "Codex", tier: "free", source: "record", readable: true, relevant: true, stale: true, ageSec: 5400, harnesses: ["codex", "pi"],
        headlineKey: "codex:30-day", windows: [
          { key: "codex:30-day", shortLabel: "30-day", kind: "monthly", percent: 0, over: false, resetsAt: root.s + 30 * 86400, sliding: true, source: "record", bindable: false } ] },
      { id: "fireworks", name: "Fireworks", tier: "Prepaid", statusText: "Fireworks unavailable", source: "record", readable: true, relevant: false,
        stale: false, ageSec: 30, harnesses: [], headlineKey: null, windows: [] },
      { id: "broken", name: "broken", readable: false, unreadableReason: "too_large", stale: true, harnesses: [], headlineKey: null, windows: [] },
      { id: "gemini-daily", name: "Gemini CLI daily", source: "computed", readable: true, relevant: true, stale: false, ageSec: null, harnesses: ["gemini"],
        headlineKey: "gemini-daily:daily", windows: [
          { key: "gemini-daily:daily", shortLabel: "Daily", kind: "daily", percent: null, over: false, resetsAt: root.s + 50000, sliding: false, source: "computed", bindable: true } ] }
    ]
  }
  LimitsSheet {
    id: sheet
    anchors.fill: parent
    theme: theme
    service: svc
    active: true
    onRunAtRequested: function (fireAtSec, harness) { root.gotFire = fireAtSec; root.gotHarness = harness }
    onClosed: root.closes++
  }
  Timer {
    interval: 300
    running: true
    onTriggered: {
      try {
        sheet.open({})
        root.eq([svc.refreshes, sheet._cursor], [1, 0])
        var words = root.texts(sheet)
        function has(t) { return words.indexOf(t) >= 0 }
        root.check(has("Limits"), "title")
        root.check(has("No limits reported. Fireworks unavailable"), "windowless: " + JSON.stringify(words))
        root.check(has("Could not be read: the record is larger than 64 KiB."), "unreadable")
        root.check(has("computed from the clock") && has("usage 1h 30m old") && has("updated 2m ago"), "status lines")
        root.check(has("starts when first used") && has("83%") && has("resets Sat 19 Sep 12:00, in 4d 0h"), "window lines")
        var joined = words.join("\n")
        root.check(joined.indexOf("987654") < 0 && joined.indexOf("123456789") < 0 && joined.indexOf("55555") < 0, "no token numbers")
        root.check(!/token/i.test(joined), "no token words")
        root.eq(sheet.actions.map(function (a) { return a.key }), ["claude:5-hour", "claude:weekly", "gemini-daily:daily"])
        root.eq(words.filter(function (t) { return t === "Run at reset" }).length, 3)
        root.check(sheet.handleKey({ key: Qt.Key_Down, modifiers: Qt.NoModifier, text: "" }) === true && sheet._cursor === 1)
        root.check(sheet.handleKey({ key: Qt.Key_Return, modifiers: Qt.NoModifier, text: "" }) === true)
        root.eq([root.gotFire, root.gotHarness], [root.s + 4 * 86400 + 180, "claude"])
        root.check(sheet.handleKey({ key: Qt.Key_Escape, modifiers: Qt.NoModifier, text: "" }) === true && root.closes === 1)
        root.check(sheet.hints.indexOf("Enter run at reset") >= 0)
      } catch (e) { root.threw(e) }
      root.done()
    }
  }
}
QML
run_case "$W" Sheet.qml "limits_sheet_windowless_unreadable_no_tokens: every source listed, reasons in words, run at reset within 8 days"

cat > "$W/Ribbon.qml" <<'QML'
import QtQuick
import "components"
import "lib/Model.js" as Model
import "lib/Timeline.js" as Timeline
Harness {
  id: root
  readonly property double now: root.at(2026, 9, 15, 10, 40)
  TestTheme { id: theme }
  function win(key, label, kind, resetsMs, extra) {
    var w = { key: key, shortLabel: label, kind: kind, percent: 0.4, over: false, resetsAt: resetsMs / 1000, sliding: false, source: "record", bindable: true }
    for (var k in (extra || {})) w[k] = extra[k]
    return w
  }
  // A reset ten minutes after the 12 tick, and two jobs past the strip's end.
  readonly property var providers: [{ id: "claude", name: "Claude", readable: true, relevant: true, stale: false, harnesses: ["claude"],
    headlineKey: "claude:5-hour", windows: [root.win("claude:5-hour", "5-hour", "session", root.at(2026, 9, 15, 12, 10))] }]
  readonly property var items: [
    { id: "a1a1a1a1a1a1a1a1", harness: "claude", fireAtMs: root.at(2026, 9, 15, 14, 0), running: false, pending: false },
    { id: "b1b1b1b1b1b1b1b1", harness: "codex", fireAtMs: root.at(2026, 9, 17, 9, 0), running: false, pending: false },
    { id: "c1c1c1c1c1c1c1c1", harness: "gemini", fireAtMs: root.at(2026, 9, 18, 9, 0), running: false, pending: false }
  ]
  readonly property var crowded: [
    { id: "claude", readable: true, relevant: true, harnesses: ["claude"], windows: [root.win("c", "5-hour", "session", root.at(2026, 9, 15, 16, 0))] },
    { id: "codex", readable: true, relevant: true, harnesses: ["codex"], windows: [root.win("x", "30-day", "monthly", root.at(2026, 9, 15, 16, 2))] },
    { id: "opencode-go", readable: true, relevant: true, harnesses: ["opencode"], windows: [root.win("g", "Weekly", "weekly", root.at(2026, 9, 15, 16, 3))] }
  ]
  TimelineRibbon { id: ribbon; y: 20; width: 1000; height: 40; theme: theme; items: root.items; nowMs: root.now; providers: root.providers }
  TimelineRibbon { id: busy; y: 100; width: 1000; height: 40; theme: theme; items: []; nowMs: root.now; providers: root.crowded }
  Timer {
    interval: 300
    running: true
    onTriggered: {
      try {
        var rects = ribbon.labelRects()
        var clash = root.overlapping(rects)
        root.check(clash === null, "labels overlap: " + JSON.stringify(clash))
        function kind(k) { return rects.filter(function (r) { return r.kind === k }) }
        root.eq(kind("reset").map(function (r) { return r.text }), ["5-hour 12:10"])
        root.eq(kind("now").length, 1)
        var later = kind("later")
        root.eq(later.map(function (r) { return r.text }), ["+2 later"])
        root.check(later.length === 1 && later[0].x + later[0].w <= ribbon.width + 0.5 && later[0].x + later[0].w >= ribbon.width - 2, "later at the right edge " + JSON.stringify(later) + " width " + ribbon.width)
        root.check(kind("tick").every(function (r) { return r.text !== "12" && r.text !== "09" }), "the 12 and 09 ticks give way: " + JSON.stringify(kind("tick")))
        root.check(kind("tick").length >= 4, "other ticks stay")
        for (var i = 0; i < rects.length; i++) root.check(rects[i].x >= 0 && rects[i].x + rects[i].w <= ribbon.width + 0.5, "inside " + JSON.stringify(rects[i]))

        var crowd = busy.labelRects()
        root.check(root.overlapping(crowd) === null, "crowded labels overlap: " + JSON.stringify(root.overlapping(crowd)))
        var counts = busy.labels.filter(function (l) { return l.kind === "count" })
        var resets = busy.labels.filter(function (l) { return l.kind === "reset" })
        root.check(counts.length === 1 && counts[0].collapsed >= 1, "one count label: " + JSON.stringify(busy.labels))
        root.eq(resets.length + (counts.length ? counts[0].collapsed : 0), 3, "every reset is a label or counted")
        root.check(/^\+[0-9]+ resets?$/.test(counts.length ? counts[0].text : ""), "count text")
      } catch (e) { root.threw(e) }
      root.done()
    }
  }
}
QML
run_case "$W" Ribbon.qml "ribbon_label_collisions: reset beside an hour label and +N later at the edge never overlap; crowded resets collapse"

cat > "$W/Markers.qml" <<'QML'
import QtQuick
import "components"
import "lib/Model.js" as Model
Harness {
  id: root
  readonly property double now: root.at(2026, 9, 15, 10, 0)
  readonly property double h: 3600000
  TestTheme { id: theme }
  function win(key, label, kind, ms, extra) {
    var w = { key: key, shortLabel: label, kind: kind, percent: 0.3, over: false, resetsAt: ms / 1000, sliding: false, source: "record", bindable: true }
    for (var k in (extra || {})) w[k] = extra[k]
    return w
  }
  readonly property var providers: [
    { id: "claude", readable: true, relevant: true, stale: false, harnesses: ["claude"], windows: [
      root.win("claude:5-hour", "5-hour", "session", root.now + 2 * root.h), root.win("claude:weekly", "Weekly", "weekly", root.now + 72 * root.h)] },
    { id: "codex", readable: true, relevant: true, stale: false, harnesses: ["codex", "pi"], windows: [
      root.win("codex:5-hour", "5-hour", "session", root.now + 5 * root.h, { sliding: true, bindable: false })] },
    { id: "opencode-go", readable: true, relevant: false, stale: false, harnesses: ["opencode"], windows: [
      root.win("opencode-go:5-hour", "5-hour", "session", root.now + 4 * root.h)] },
    { id: "cursor", readable: true, relevant: true, stale: false, harnesses: ["cursor"], windows: [
      root.win("cursor:cursor-models", "Cursor models", "billing_pool", root.now + 6 * root.h),
      root.win("cursor:included", "Included", "billing_total", root.now + 6 * root.h)] },
    { id: "gemini-daily", readable: true, relevant: true, stale: false, source: "computed", harnesses: ["gemini"], windows: [
      root.win("gemini-daily:daily", "Daily", "daily", root.now + 10 * root.h, { source: "computed", percent: null })] },
    { id: "broken", readable: false, relevant: true, harnesses: [], windows: [root.win("b", "5-hour", "session", root.now + 3 * root.h)] }
  ]
  TimelineRibbon { id: ribbon; width: 1000; height: 40; theme: theme; items: []; nowMs: root.now; providers: root.providers }
  Timer {
    interval: 300
    running: true
    onTriggered: {
      try {
        root.eq(ribbon.markers.map(function (m) { return m.source + ":" + m.shortLabel }), ["claude:5-hour", "cursor:Included", "gemini-daily:Daily"])
        root.eq([ribbon.band !== null, ribbon.band ? ribbon.band.harness : "", ribbon.band ? ribbon.band.fromMs : 0], [true, "claude", ribbon.startMs])
        var texts = ribbon.labels.map(function (l) { return l.text })
        root.check(texts.indexOf("5-hour 12:00") >= 0 && texts.indexOf("Included 16:00") >= 0 && texts.indexOf("Daily 20:00") >= 0, JSON.stringify(texts))
        root.check(texts.every(function (t) { return t.indexOf("Cursor models") < 0 }), "one Cursor marker")
        ribbon.providers = []
        root.eq([ribbon.markers.length, ribbon.band], [0, null])
      } catch (e) { root.threw(e) }
      root.done()
    }
  }
}
QML
run_case "$W" Markers.qml "ribbon_generic_markers_sliding_hidden: fixed resets of every relevant source; sliding, unreadable and pool markers left out"

cat > "$W/DayCrowd.qml" <<'QML'
import QtQuick
import "components"
import "lib/Model.js" as Model
import "lib/Timeline.js" as Timeline
Harness {
  id: root
  readonly property double dayStart: root.at(2026, 9, 14, 0, 0)
  readonly property double now: root.at(2026, 9, 14, 13, 2)
  function sec(hh, mi) { return root.at(2026, 9, 14, hh, mi) / 1000 }
  TestTheme { id: theme }
  DayTimeline {
    id: day
    y: 20
    width: 1000
    height: implicitHeight
    theme: theme
    dayStartMs: root.dayStart
    nowMs: root.now
    timeline: ({
      ok: true,
      resets: [
        { source: "claude", name: "Claude", kind: "session", shortLabel: "5-hour", at: root.sec(13, 0), origin: "record", harnesses: ["claude"] },
        { source: "codex", name: "Codex", kind: "monthly", shortLabel: "30-day", at: root.sec(13, 3), origin: "observed", harnesses: ["codex"] },
        { source: "opencode-go", name: "OpenCode Go", kind: "weekly", shortLabel: "Weekly", at: root.sec(13, 6), origin: "record", harnesses: ["opencode"] },
        { source: "claude", name: "Claude", kind: "session", shortLabel: "5-hour", at: root.sec(18, 0), origin: "current", harnesses: ["claude"] }
      ],
      runs: [ { runId: "1111111111111111-g1", jobId: "1111111111111111", harness: "claude", label: "x", startedAt: root.sec(12, 30), endedAt: root.sec(12, 58), outcome: "limit", status: null, limitSource: "claude" } ],
      armed: []
    })
  }
  Timer {
    interval: 300
    running: true
    onTriggered: {
      try {
        root.eq(day.laneCount, 3)
        var rects = day.labelRects()
        var clash = root.overlapping(rects)
        root.check(clash === null, "labels overlap: " + JSON.stringify(clash))
        var resets = rects.filter(function (r) { return r.kind === "reset" })
        var counts = rects.filter(function (r) { return r.kind === "count" })
        root.check(counts.length === 1 && counts[0].collapsed >= 1, "count chip: " + JSON.stringify(rects))
        root.eq(resets.length + (counts.length ? counts[0].collapsed : 0), 4, "every marker is a label or counted")
        root.eq(rects.filter(function (r) { return r.kind === "now" }).length, 1)
        var ticks = rects.filter(function (r) { return r.kind === "tick" })
        root.eq(ticks.map(function (r) { return r.text }), ["00", "03", "06", "09", "12", "15", "18", "21"])
        var laneTop = day.lanesTop, laneBottom = day.lanesTop + day.lanesHeight
        root.check(rects.every(function (r) { return r.kind === "tick" ? r.y >= laneBottom : r.y + r.h <= laneTop + 0.5 }), "marker labels above the lanes, hours below")
        root.check(rects.every(function (r) { return r.x >= day.trackX - 0.5 && r.x + r.w <= day.trackX + day.trackWidth + 0.5 }), "inside the track")
        root.check(!day.empty && day.loaded)
      } catch (e) { root.threw(e) }
      root.done()
    }
  }
}
QML
run_case "$W" DayCrowd.qml "day_timeline_label_collision_count_chip: crowded markers collapse to one count, marker and hour rows never overlap"

cat > "$W/DayEmpty.qml" <<'QML'
import QtQuick
import "components"
Harness {
  id: root
  TestTheme { id: theme }
  DayTimeline { id: empty; width: 1000; height: implicitHeight; theme: theme; dayStartMs: root.at(2026, 9, 14, 0, 0); nowMs: root.at(2026, 9, 15, 9, 0)
    timeline: ({ ok: true, resets: [ { source: "gemini-daily", name: "Gemini CLI daily", kind: "daily", shortLabel: "Daily", at: root.at(2026, 9, 15, 9, 0) / 1000, origin: "computed", harnesses: ["gemini"] } ], runs: [], armed: [] }) }
  DayTimeline { id: loading; y: 60; width: 1000; height: implicitHeight; theme: theme; dayStartMs: root.at(2026, 9, 14, 0, 0); nowMs: root.at(2026, 9, 15, 9, 0); timeline: null }
  Timer {
    interval: 200
    running: true
    onTriggered: {
      try {
        root.eq([empty.loaded, empty.empty, empty.laneCount], [true, true, 0])
        root.eq(empty.emptyText, "No runs or resets recorded on Mon 14 Sep.")
        root.check(root.texts(empty).indexOf("No runs or resets recorded on Mon 14 Sep.") >= 0, JSON.stringify(root.texts(empty)))
        root.check(root.texts(loading).indexOf("Reading the day…") >= 0 && loading.loaded === false)
        root.eq(empty.labelRects(), [])
      } catch (e) { root.threw(e) }
      root.done()
    }
  }
}
QML
run_case "$W" DayEmpty.qml "day_timeline_empty_copy: \"No runs or resets recorded on Mon 14 Sep.\" and a reading line"

cat > "$W/HistoryNav.qml" <<'QML'
import QtQuick
import "components"
import "lib/Model.js" as Model
Harness {
  id: root
  TestTheme { id: theme }
  QtObject {
    id: svc
    property bool ready: true
    property var jobs: []
    property var jobsById: ({})
    property double nowMs: Date.now()
    property var timeline: ({})
    property bool loadingTimeline: false
    property var models: ({})
    property var loads: []
    function loadTimeline(ms) { svc.loads = svc.loads.concat([ms]) }
    function getJob(id, cb) {}
    function refresh() {}
    function runNow(id, digest, cb) {}
    function copyResume(id, cb) {}
  }
  HistoryView { id: hv; anchors.fill: parent; theme: theme; service: svc; active: false }
  function key(k) { return hv.handleKey({ key: k, modifiers: Qt.ControlModifier, text: "" }) }
  Timer {
    interval: 200
    running: true
    onTriggered: {
      try {
        var today = Model.localMidnight(Date.now())
        function day(n) { var d = new Date(today); d.setDate(d.getDate() + n); return d.getTime() }
        root.eq([hv.dayWord, hv.dayDate, hv.dayStartMs], ["Today", Model.dateText(today), today])
        hv.active = true
        root.eq(svc.loads[svc.loads.length - 1], today, "activation reads today")
        root.check(root.key(Qt.Key_Left) === true)
        root.eq([hv.dayWord, hv.dayDate, svc.loads[svc.loads.length - 1]], ["Yesterday", Model.dateText(day(-1)), day(-1)])
        root.key(Qt.Key_Left)
        root.eq([hv.dayWord, hv.dayDate], [Model.dateText(day(-2)), ""])
        root.check(/^[A-Z][a-z]{2} [0-9]{1,2} [A-Z][a-z]{2}$/.test(hv.dayWord), "date form " + hv.dayWord)
        root.key(Qt.Key_Home)
        root.eq(hv.dayWord, "Today")
        root.key(Qt.Key_Right)
        root.eq([hv.dayWord, hv.dayDate], ["Tomorrow", Model.dateText(day(1))])
        for (var i = 0; i < 30; i++) root.key(Qt.Key_Left)
        root.eq([hv._dayOffset, hv.dayStartMs], [-14, day(-14)])
        for (var j = 0; j < 30; j++) root.key(Qt.Key_Right)
        root.eq([hv._dayOffset, hv.dayStartMs], [7, day(7)])
        root.key(Qt.Key_Home)
        root.check(hv.hints.indexOf("Ctrl+←/→ day") >= 0, hv.hints)
        var t = {}
        t[Model.dayKey(today)] = { ok: true, resets: [ { source: "claude", name: "Claude", kind: "session", shortLabel: "5-hour", at: today / 1000 + 13 * 3600, origin: "record", harnesses: ["claude"] } ], runs: [], armed: [] }
        svc.timeline = t
        var dt = root.find(hv, function (it) { return it.laneCount !== undefined && it.labelRects !== undefined })
        root.check(dt !== null && dt.laneCount === 1 && dt.dayStartMs === today, "the day timeline shows the day")
      } catch (e) { root.threw(e) }
      root.done()
    }
  }
}
QML
run_case "$W" HistoryNav.qml "history_day_navigation_labels: Ctrl+Left/Right steps days (Today, Yesterday, Mon 14 Sep), Ctrl+Home, bounds, reads each day"

sed "s|@SHOT@|$T/stress-queue-running.png|" > "$W/NowGroup.qml" <<'QML'
import QtQuick
import "components"
import "lib/Model.js" as Model
Harness {
  id: root
  readonly property double now: Date.now()
  property string shotResult: ""
  TestTheme { id: theme }
  function job(id, status, fireAtSec, label) {
    return {
      id: id, label: label, harness: "claude", createdAt: 1789400000, updatedAt: 1789400000,
      cli: { link: "/usr/bin/claude", version: null },
      target: { mode: "new", sessionId: null, newSessionId: null, cwd: "/home/u/proj/api", title: "api", allowNonGit: false },
      level: "plan", limits: { maxTurns: 15, budgetUsd: 5, runtimeSec: 5400 }, model: null,
      trigger: { kind: "at", fireAt: fireAtSec, delaySec: null, marginSec: 120, weeklyPolicy: "defer", graceSec: 900 },
      promptSha256: "", promptBytes: 10, promptAvailable: true, commandDigest: "", digest: "",
      state: { status: status, wait: null, reason: null, gen: 1, fireAt: fireAtSec, unit: null, armedAt: null, pluginDir: null,
               basis: null, runSessionId: null, defers: 0, limitRetries: 0, transientRetries: 0, busyDefers: 0, unknownRetries: 0,
               lastRun: status === "running" ? { runId: id + "-g1", startedAt: fireAtSec, endedAt: null, outcome: null } : null,
               lastEvent: null },
      fireAtMs: fireAtSec * 1000, canRunNow: status !== "running", canDisarm: status === "armed", canEdit: status !== "running",
      canDelete: false, queue: true, allowPaid: false, provider: null
    }
  }
  readonly property var armedJobs: [
    root.job("b1b1b1b1b1b1b1b1", "armed", Math.floor(root.now / 1000) + 3 * 3600, "Nightly dependency audit"),
    root.job("b2b2b2b2b2b2b2b2", "armed", Math.floor(root.now / 1000) + 26 * 3600, "Draft release notes")
  ]
  QtObject {
    id: svc
    signal notice(string text, string kind)
    signal jobsUpdated()
    property bool ready: true
    property var jobs: root.armedJobs
    property var jobsById: ({})
    property var providers: []
    property var models: ({})
    property var settings: ({ motion: "full", resetMarginSec: 120, eveningTime: "23:00", morningTime: "07:00" })
    property var agents: ({})
    property double nowMs: root.now
    function getJob(id, cb) {}
  }
  Rectangle { anchors.fill: parent; color: theme.surface }
  QueueView { id: qv; anchors.fill: parent; theme: theme; service: svc; active: true; home: "/home/u" }

  function slots() {
    var lv = root.find(qv, function (it) { return it.contentItem !== undefined && it.delegate !== undefined && it.model !== undefined })
    var out = []
    var kids = lv ? lv.contentItem.children : []
    for (var i = 0; i < kids.length; i++) {
      var d = kids[i]
      if (d.uid === undefined || !d.visible) continue
      out.push({ uid: d.uid, kind: d.kind, y: d.y, h: d.height, shift: d.enterShift })
    }
    return out.sort(function (a, b) { return a.y - b.y })
  }

  property int step: 0
  Timer {
    interval: 600
    repeat: true
    running: true
    onTriggered: {
      try {
        var s = root.step++
        if (s === 0) {
          var run = root.job("aaaaaaaaaaaaaaaa", "running", Math.floor(root.now / 1000) - 3584, "Zażółć gęślą jaźń: przegląd zmian")
          svc.jobsById = { aaaaaaaaaaaaaaaa: run, b1b1b1b1b1b1b1b1: root.armedJobs[0], b2b2b2b2b2b2b2b2: root.armedJobs[1] }
          svc.jobs = [run].concat(root.armedJobs)
        } else if (s === 2) {
          var rows = root.slots()
          root.eq(rows.map(function (r) { return r.uid }), ["h:now", "j:aaaaaaaaaaaaaaaa", "h:" + Model.dayKey(root.now + 3 * 3600000),
            "j:b1b1b1b1b1b1b1b1", "h:" + Model.dayKey(root.now + 26 * 3600000), "j:b2b2b2b2b2b2b2b2"].filter(function (u, i, a) { return a.indexOf(u) === i }))
          var headers = rows.filter(function (r) { return r.kind === "header" })
          root.check(headers.every(function (r) { return r.h === headers[0].h }), "the Now header is as tall as a day header")
          for (var i = 1; i < rows.length; i++)
            root.check(rows[i - 1].y + rows[i - 1].h <= rows[i].y + 0.5, "row " + rows[i].uid + " starts below " + rows[i - 1].uid + ": " + JSON.stringify(rows))
          root.check(rows[1].y === rows[0].y + rows[0].h, "the running row sits right under Now")
          root.check(rows.every(function (r) { return r.shift === 0 }), "entrance offsets settled")
          qv.grabToImage(function (r) { root.shotResult = r.saveToFile("@SHOT@") ? "saved" : "not saved" })
        } else if (s === 3) {
          root.eq(root.shotResult, "saved", "stress-queue-running shot")
          root.done()
        }
      } catch (e) { root.threw(e) }
    }
  }
}
QML
run_case "$W" NowGroup.qml "queue_now_group_header_offset: the Now group appearing with its running job keeps header height and row offset"
if [ -s "$T/stress-queue-running.png" ]; then ok "stress-queue-running: the offscreen render of that queue was written"
else no "stress-queue-running" "no image at $T/stress-queue-running.png"; fi

cat > "$W/RowPaid.qml" <<'QML'
import QtQuick
import "components"
import "lib/Model.js" as Model
Harness {
  id: root
  readonly property double now: root.at(2026, 9, 15, 12, 0)
  TestTheme { id: theme }
  function job(allowPaid, model, kind, harness, provider) {
    return {
      id: "a1a1a1a1a1a1a1a1", label: "Nightly", harness: harness || "claude", createdAt: 1789400000, updatedAt: 1789400000,
      target: { mode: "new", sessionId: null, cwd: "/home/u/proj", title: "proj", allowNonGit: false, sessionPath: null },
      level: "plan", limits: { maxTurns: 15, budgetUsd: 5, runtimeSec: 5400 }, model: model, allowPaid: allowPaid, provider: provider || null,
      trigger: { kind: kind, fireAt: root.now / 1000 + 3600, delaySec: null, marginSec: 120 },
      state: { status: "armed", wait: null, reason: null, fireAt: root.now / 1000 + 3600, lastRun: null, lastEvent: null },
      canRunNow: true, canDisarm: true, canEdit: true, canDelete: false, queue: true
    }
  }
  JobRow { id: paidRow; width: 1000; theme: theme; job: root.job(true, "sonnet", "at"); mode: "queue"; nowMs: root.now; modelLabel: "Sonnet 4.6" }
  JobRow { id: freeRow; y: 60; width: 1000; theme: theme; job: root.job(false, null, "zen_free_reset", "opencode"); mode: "queue"; nowMs: root.now }
  JobCard { id: card; y: 120; width: 1000; theme: theme; job: root.job(true, "sonnet", "at"); mode: "queue"; nowMs: root.now; modelLabel: "Sonnet 4.6" }
  JobCard { id: freeCard; y: 400; width: 1000; theme: theme; job: root.job(false, null, "at"); mode: "queue"; nowMs: root.now }
  Timer {
    interval: 200
    running: true
    onTriggered: {
      try {
        root.check(paidRow.paid === true && root.texts(paidRow).indexOf("paid") >= 0, "paid pill")
        root.eq(paidRow.metaLine, "new session, Sonnet 4.6, 15 turns, $5.00, 1h 30m max")
        root.check(freeRow.paid === false && root.texts(freeRow).indexOf("paid") < 0, "no pill when off")
        root.check(freeRow.metaLine.indexOf("$") < 0, "no dollar amount when off: " + freeRow.metaLine)
        root.eq(freeRow.triggerLine, "follows Zen free reset +2m")
        var words = root.texts(card)
        root.check(words.indexOf("Model Sonnet 4.6") >= 0 && words.indexOf("Paid usage is on for this job. It may spend usage credits or API dollars.") >= 0, JSON.stringify(words))
        root.check(root.texts(freeCard).every(function (t) { return t.indexOf("Paid usage") < 0 && t.indexOf("Model ") !== 0 }))
        var models = {
          opencode: { models: [ { id: "opencode/big-pickle", label: "Big Pickle", provider: null, modelId: null } ] },
          pi: { models: [ { id: "openai-codex/gpt-5.3-codex", label: "GPT-5.3 Codex", provider: "openai-codex", modelId: "gpt-5.3-codex" } ] }
        }
        root.eq([
          Model.modelLabel({ harness: "opencode", model: "opencode/big-pickle" }, models),
          Model.modelLabel({ harness: "pi", provider: "openai-codex", model: "gpt-5.3-codex" }, models),
          Model.modelLabel({ harness: "codex", model: "gpt-5.4" }, models),
          Model.modelLabel({ harness: "claude", model: null }, models),
          Model.modelLabel({ harness: "opencode", model: "opencode/big-pickle" }, null)
        ], ["Big Pickle", "GPT-5.3 Codex", "gpt-5.4", "", "opencode/big-pickle"])
        var legacy = root.job(true, null, "claude_5h_reset", "opencode")
        legacy.target.sessionPath = "/home/u/.pi/agent/sessions/x.jsonl"
        var d = Model.draftFromJob(legacy, "p", "edit")
        root.eq([d.trigger, d.allowPaid, d.provider, d.target.sessionPath], [{ kind: "now" }, true, null, "/home/u/.pi/agent/sessions/x.jsonl"])
        root.eq(Model.draftFromJob(root.job(false, null, "zen_free_reset", "opencode"), "", "duplicate").trigger.kind, "zen_free_reset")
        var fresh = Model.draftFromSettings({ defaultAllowPaid: true }, {})
        root.eq([fresh.allowPaid, fresh.provider, fresh.target.sessionPath], [true, null, null])
        root.eq(Model.draftFromSettings({}, {}).allowPaid, false)
        root.eq([Model.RESET_TRIGGER.opencode, Model.RESET_TRIGGER.cursor, Model.HARNESS_NAMES.cursor, Model.CLI_NAMES.cursor],
                ["zen_free_reset", "", "Cursor Agent", "cursor-agent"])
        root.eq([Model.reasonSentence("paid_blocked"), Model.reasonSentence("paid_exhausted")],
                ["It would have used paid usage, which is off for this job.", "Included usage is used up until after the 8-day limit, so it was skipped."])
        root.eq([Model.followsLine("claude", 120), Model.followsLine("go_window_reset", 300), Model.followsLine("cursor", 60)],
                ["follows Claude reset +2m", "follows Go reset +5m", ""])
      } catch (e) { root.threw(e) }
      root.done()
    }
  }
}
QML
run_case "$W" RowPaid.qml "job_row_paid_chip_and_model_label: paid pill and budget only when allowed, model label, Zen reset line, drafts v2"

# ---------------------------------------------------------------- Panel routing

P="$T/panel"
mkdir -p "$P/components"
cp "$W"/*.qml "$P/"
cp -r "$W/lib" "$P/"
cp "$W"/components/*.qml "$P/components/"
# The Compose side belongs to Q5; here it is a recorder with the surface Panel uses.
cat > "$P/components/ComposeView.qml" <<'QML'
import QtQuick
Item {
  id: c
  required property var theme
  required property var service
  property bool active: false
  property string home: ""
  property Item focusReturn: null
  readonly property bool editorFocused: false
  readonly property bool dirty: false
  readonly property string hints: "Compose"
  property var calls: []
  signal noticeRequested(string text, string kind, var undo)
  signal viewRequested(string view, string jobId)
  signal armed(string jobId)
  signal sheetRequested(string sheet, var args)
  signal globalKey(var event)
  function rec(name, args) { c.calls = c.calls.concat([{ name: name, args: args }]) }
  function activate() {}
  function newDraft() { c.rec("newDraft", []) }
  function loadDraft(draft, prompt, notice) { c.rec("loadDraft", [draft]) }
  function applySession(selection) { c.rec("applySession", [selection]) }
  function applyModel(selection) { c.rec("applyModel", [selection]) }
  function setHarness(h) { c.rec("setHarness", [h]) }
  function presetTrigger(trigger) { c.rec("presetTrigger", [trigger]) }
  function handleKey(event) { return false }
}
QML
cat > "$P/components/ModelSheet.qml" <<'QML'
import QtQuick
Item {
  id: sheet
  required property var theme
  required property var service
  property bool active: false
  property var openedWith: null
  readonly property string hints: "Type to filter  ·  Enter pick  ·  Ctrl+R refresh list  ·  Esc back"
  signal closed()
  signal picked(var selection)
  signal noticeRequested(string text, string kind, var undo)
  function handleKey(event) { if (event.key === Qt.Key_Escape) { sheet.close(); return true } return false }
  function open(args) { sheet.openedWith = args }
  function close() { sheet.closed() }
}
QML
for s in SessionSheet SettingsSheet; do
cat > "$P/components/$s.qml" <<'QML'
import QtQuick
Item {
  id: sheet
  required property var theme
  required property var service
  property bool active: false
  property string home: ""
  readonly property string hints: "Esc back"
  signal closed()
  signal picked(var selection)
  signal noticeRequested(string text, string kind, var undo)
  function handleKey(event) { return false }
  function open(args) {}
  function close() { sheet.closed() }
}
QML
done

cat > "$P/Route.qml" <<'QML'
import QtQuick
import "lib/Model.js" as Model
Harness {
  id: root
  width: 1400
  height: 1000
  readonly property double now: Date.now()
  QtObject {
    id: stubBar
    property string fontFamily: "monospace"
    property color barForeground: "#cacccc"
    property color urgent: "#a55555"
    property bool vertical: false
    property int barSize: 26
    property string position: "top"
    property bool foregroundAnimationEnabled: true
    property var shell: null
    function switchPanelFrom(owner, direction) { return false }
    function showTooltip(item, text) {}
    function hideTooltip(item) {}
    function registerClickTarget(item) {}
    function unregisterClickTarget(item) {}
    function moduleWidgets(name) { return [] }
  }
  Item { id: anchor; width: 24; height: 24 }
  QtObject {
    id: svc
    signal notice(string text, string kind)
    signal jobsUpdated()
    property bool ready: true
    property string setupProblem: ""
    property var edition: ({})
    property var levels: []
    property var harnesses: []
    property var caps: ({})
    property var jobs: []
    property var jobsById: ({})
    property var listMeta: ({ killSwitch: false, enabledInShell: true, linger: false, reconciledAt: null, nowMs: root.now })
    property var usage: null
    property string usageError: ""
    property var providers: [ { id: "claude", name: "Claude", readable: true, relevant: true, stale: false, ageSec: 30, source: "record",
      harnesses: ["claude"], headlineKey: "claude:5-hour", windows: [ { key: "claude:5-hour", shortLabel: "5-hour", kind: "session",
      percent: 0.4, over: false, resetsAt: Math.floor(root.now / 1000) + 7200, sliding: false, source: "record", bindable: true } ] } ]
    property var models: ({})
    property var timeline: ({})
    property var agents: ({})
    property var sessions: ({})
    property var settings: ({ schemaVersion: 1, defaultHarness: "claude", defaultLevel: "plan", resetMarginSec: 120, motion: "reduced", limitsShown: "auto" })
    property double nowMs: root.now
    property bool loadingJobs: false
    property bool loadingUsage: false
    property bool loadingAgents: false
    property bool loadingSessions: false
    property bool loadingSettings: false
    property bool loadingModels: false
    property bool loadingTimeline: false
    property bool busy: false
    property var lastError: null
    property var nextJob: null
    property int armedCount: 0
    property int runningCount: 0
    property int attentionCount: 0
    property double attentionSeenAt: 0
    property bool anyRunning: false
    property int viewers: 0
    property string barLabel: "Next run"
    property int usageReads: 0
    function refresh() {}
    function refreshUsage() { svc.usageReads++ }
    function refreshAgents(checkLogin) {}
    function loadSessions(harness, cwd) {}
    function loadModels(harness, refresh) {}
    function loadTimeline(ms) {}
    function getJob(id, cb) {}
    function preview(draft, cb) {}
    function createAndArm(draft, cb) {}
    function saveDraft(draft, cb) {}
    function arm(id, digest, cb) {}
    function runNow(id, digest, cb) {}
    function disarm(id, cb) {}
    function deleteJob(id, cb) {}
    function cancelAll(cb) {}
    function reschedule(id, epochSec, cb) {}
    function swap(a, b, cb) {}
    function shift(ids, deltaSec, cb) {}
    function setSettings(obj, cb) {}
    function setLimitsShown(value, cb) {}
    function markSeen() { return false }
    function copyResume(id, cb) {}
    function reconcileNow(cb) {}
    function viewerOpened() { svc.viewers++ }
    function viewerClosed() { svc.viewers = Math.max(0, svc.viewers - 1) }
    function levelFor(id) { return null }
    function agentFor(harness) { return null }
  }
  Loader {
    id: loader
    source: "Panel.qml"
    onLoaded: { item.bar = stubBar; item.anchorItem = anchor; item.service = svc }
  }
  function key(k, mods) { var e = { key: k, modifiers: mods, text: "", accepted: false }; loader.item.routeKey(e); return e.accepted }
  property int step: 0
  Timer {
    interval: 120
    repeat: true
    running: loader.status === Loader.Ready
    onTriggered: {
      try {
        var p = loader.item
        var s = root.step++
        var compose = root.find(p, function (it) { return it.calls !== undefined && it.presetTrigger !== undefined })
        var modelSheet = root.find(p, function (it) { return it.openedWith !== undefined })
        var limits = root.find(p, function (it) { return it.runAtRequested !== undefined && it.actions !== undefined })
        var strip = root.find(p, function (it) { return it.chipRects !== undefined })
        if (s === 0) p.open()
        else if (s === 1) {
          root.check(compose !== null && modelSheet !== null && limits !== null && strip !== null, "pieces found")
          var ids = Model.harnessOrder()
          root.check(ids.length === 6 && ids.every(function (id) { return typeof p.theme.inks[id] !== "undefined" && typeof p.theme.marks[id] !== "undefined" }), "an ink and a mark for every agent")
          p.openSheet("limits", null)
          root.eq([p.sheet, limits.visible, svc.usageReads], ["limits", true, 1])
          root.eq(p.footerText, limits.hints)
        } else if (s === 2) {
          limits.runAtRequested(1789500000, "codex")
          root.eq([p.view, p.sheet], ["compose", ""])
          var names = compose.calls.map(function (c) { return c.name })
          root.eq(names.slice(-2), ["presetTrigger", "setHarness"])
          root.eq(compose.calls[compose.calls.length - 2].args[0], { kind: "at", fireAt: 1789500000 })
          root.eq(compose.calls[compose.calls.length - 1].args[0], "codex")
        } else if (s === 3) {
          compose.sheetRequested("model", { harness: "opencode", provider: "", model: null, allowPaid: false })
          root.eq([p.sheet, modelSheet.visible, modelSheet.openedWith.harness], ["model", true, "opencode"])
          root.eq(p.footerText, modelSheet.hints)
        } else if (s === 4) {
          modelSheet.picked({ harness: "opencode", provider: "", model: "opencode/big-pickle", label: "Big Pickle" })
          root.eq(p.sheet, "")
          root.eq(compose.calls[compose.calls.length - 1], { name: "applyModel", args: [{ harness: "opencode", provider: "", model: "opencode/big-pickle", label: "Big Pickle" }] })
        } else if (s === 5) {
          root.check(root.key(Qt.Key_L, Qt.ControlModifier) === true && p.sheet === "limits", "Ctrl+L opens the limits sheet")
          root.check(root.key(Qt.Key_Escape, Qt.NoModifier) === true && p.sheet === "", "Esc closes it")
          compose.sheetRequested("model", { harness: "pi" })
          root.check(root.key(Qt.Key_Escape, Qt.NoModifier) === true && p.sheet === "", "Esc closes the model sheet")
        } else if (s === 6) {
          root.eq(strip.chips.length, 1)
          strip.sheetRequested()
          root.eq(p.sheet, "limits")
          p.closeSheet()
          root.eq(p.sheet, "")
          root.done()
        }
      } catch (e) { root.threw(e) }
    }
  }
}
QML
run_case "$P" Route.qml "panel_model_and_limits_sheets_route: limits and model sheets open, route their picks to Compose, Ctrl+L and Esc"

# ---------------------------------------------------------------- Service.qml (plain engine, recorded helper)

S="$T/svc"
mkdir -p "$S/lib"
cp "$REPO/Service.qml" "$W/Harness.qml" "$S/"
cp "$REPO"/lib/*.js "$S/lib/"
cat > "$S/lib/Recorder.js" <<'JS'
.pragma library
// What the stand-in BoundedProcess was asked to run, and what it answers.
var calls = []
var answers = {}
function reply(verb, args, stdin) {
  if (answers.hasOwnProperty(verb)) {
    var a = answers[verb]
    return typeof a === "function" ? a(args, stdin) : a
  }
  if (verb === "settings-set") {
    var patch = {}
    try { patch = JSON.parse(stdin) } catch (e) { patch = {} }
    var s = { schemaVersion: 1 }
    for (var k in patch) s[k] = patch[k]
    return { ok: true, settings: s }
  }
  return { ok: false, code: "stub", message: "Stub answer." }
}
function of(verb) { return calls.filter(function (c) { return c.verb === verb }) }
JS
cat > "$S/BoundedProcess.qml" <<'QML'
import QtQuick
import "lib/Recorder.js" as Recorder
// Records the call and answers on a later turn, like the real one.
Item {
  id: proc
  property var command: []
  property var environment: ({})
  property string workingDirectory: ""
  property string stdinText: ""
  property int maxStdoutBytes: 65536
  property int maxStderrBytes: 16384
  property int deadlineMs: 10000
  signal finished(int exitCode, bool overflowed, bool timedOut, string stdoutText, string stderrText)
  function start() {
    var argv = []
    for (var i = 0; i < proc.command.length; i++) argv.push(String(proc.command[i]))
    var verb = argv.length > 5 ? argv[5] : ""
    var args = argv.slice(6)
    Recorder.calls.push({ verb: verb, args: args, stdin: proc.stdinText, deadlineMs: proc.deadlineMs, cap: proc.maxStdoutBytes })
    var answer = JSON.stringify(Recorder.reply(verb, args, proc.stdinText))
    Qt.callLater(function () { proc.finished(0, false, false, answer, "") })
    return true
  }
}
QML

cat > "$S/Seen.qml" <<'QML'
import QtQuick
import "lib/Model.js" as Model
import "lib/Recorder.js" as Recorder
Harness {
  id: root
  Service { id: svc }
  readonly property int nowSec: Math.floor(Date.now() / 1000)
  property int before: 0
  function job(id, status, endedAt, extra) {
    var j = { id: id, label: id, harness: "claude", updatedAt: endedAt, createdAt: endedAt - 100, queue: status === "armed" || status === "draft",
              trigger: { kind: "at" }, target: {}, state: { status: status, fireAt: status === "armed" ? root.nowSec + 7200 : null, wait: null,
              lastRun: status === "armed" || status === "draft" ? null : { runId: id + "-g1", startedAt: endedAt - 60, endedAt: endedAt, outcome: status } } }
    for (var k in (extra || {})) j[k] = extra[k]
    return j
  }
  property int step: 0
  Timer {
    interval: 60
    repeat: true
    running: true
    onTriggered: {
      try {
        var s = root.step++
        if (s === 0) {
          svc._editionLoaded = true
          svc._applyList({ ok: true, jobs: [
            root.job("aaaaaaaaaaaaaaaa", "failed", root.nowSec - 600),
            root.job("bbbbbbbbbbbbbbbb", "done", root.nowSec - 300),
            root.job("dddddddddddddddd", "draft", root.nowSec - 900)
          ] })
          root.check(svc.ready === true, "ready")
          root.check(svc.lastSeenAt <= root.nowSec - 86399 && svc.attentionSeenAt === svc.lastSeenAt, "boot fallback: a day ago")
          root.eq([svc.problemCount, svc.doneSinceSeen, svc.draftCount, svc.barState.state, svc.barState.text], [1, 1, 1, "attention", "1 failed"])
          svc._settings = { schemaVersion: 1, lastSeenAt: root.nowSec - 450 }
          root.eq([svc.lastSeenAt, svc.problemCount, svc.doneSinceSeen, svc.barState.state, svc.barState.text], [root.nowSec - 450, 0, 1, "success", "1 done"])
          svc._settings = { schemaVersion: 1, lastSeenAt: root.nowSec - 10 }
          root.eq([svc.problemCount, svc.doneSinceSeen, svc.barState.state, svc.barState.text], [0, 0, "drafts", "1 drafted"])
          // An armed job whose agent reports signed out is attention even when nothing failed.
          svc._applyList({ ok: true, jobs: [ root.job("cccccccccccccccc", "armed", root.nowSec - 5, { harness: "codex" }) ] })
          svc._agents = { codex: { harness: "codex", loggedIn: false } }
          root.eq([svc.authProblemCount, svc.barState.state, svc.barState.text], [1, "attention", "auth"])
          svc._agents = { codex: { harness: "codex", loggedIn: true } }
          root.eq([svc.authProblemCount, svc.barState.state], [0, "soon"])
          svc._applyList({ ok: true, jobs: [ root.job("eeeeeeeeeeeeeeee", "gave_up", root.nowSec - 5) ] })
          root.eq([svc.problemCount, svc.barState.text], [1, "1 failed"])
          svc.viewerClosed()
          root.check(svc.lastSeenAt >= root.nowSec, "closing the panel marks what it showed as seen")
          root.eq(svc.problemCount, 0)
          // A day later prune deletes the seen job's prompt: a housekeeping event, not a new problem.
          var pruned = root.job("ffffffffffffffff", "interrupted", root.nowSec - 600)
          pruned.state.statusAt = root.nowSec - 600
          pruned.state.lastEvent = { event: "prompt_deleted", at: root.nowSec + 30, detail: null }
          svc._applyList({ ok: true, jobs: [ pruned ] })
          root.eq([svc.problemCount, svc.barState.state], [0, "idle"], "prompt_deleted after lastSeenAt stays calm")
          delete pruned.state.statusAt
          svc._applyList({ ok: true, jobs: [ pruned ] })
          root.eq(svc.problemCount, 0, "an older helper without statusAt ignores housekeeping events too")
          pruned.state.statusAt = root.nowSec + 30
          pruned.state.lastEvent = { event: "interrupted", at: root.nowSec + 30, detail: "interrupted" }
          svc._applyList({ ok: true, jobs: [ pruned ] })
          root.eq(svc.problemCount, 1, "a new interruption after the look is news")
          svc._applyList({ ok: true, jobs: [ root.job("eeeeeeeeeeeeeeee", "gave_up", root.nowSec - 5) ] })
        } else if (s === 1) {
          svc._settings = { schemaVersion: 1 }
          svc._seenLocal = 0
          root.eq(svc.problemCount, 1)
          root.before = Recorder.of("settings-set").length
          svc.viewerOpened()
          root.eq(svc.problemCount, 1, "the opening that is choosing its view still sees the failure")
        } else if (s === 3) {
          var sets = Recorder.of("settings-set")
          root.eq(sets.length, root.before + 1, "opening writes lastSeenAt (after the reconcile ahead of it)")
          var payload = sets.length ? JSON.parse(sets[sets.length - 1].stdin) : {}
          root.check(Object.keys(payload).join() === "lastSeenAt" && Math.abs(payload.lastSeenAt - root.nowSec) <= 3, JSON.stringify(payload))
        } else if (s === 5) {
          root.check(svc.settings.lastSeenAt >= root.nowSec && svc.lastSeenAt >= root.nowSec, "the write's answer moves lastSeenAt")
          root.eq([svc.problemCount, svc.barState.state], [0, "idle"])
          root.done()
        }
      } catch (e) { root.threw(e) }
    }
  }
}
QML
run_case "$S" Seen.qml "bar_last_seen_clears_attention_and_success: lastSeenAt (settings, boot fallback, close) clears failures and done runs"

cat > "$S/Debounce.qml" <<'QML'
import QtQuick
import "lib/Model.js" as Model
import "lib/Recorder.js" as Recorder
Harness {
  id: root
  Service { id: svc }
  property int step: 0
  property int base: 0
  Timer {
    interval: 60
    repeat: true
    running: true
    onTriggered: {
      try {
        var s = root.step++
        if (s === 0) {
          root.check(svc.markSeen() === false && Recorder.of("settings-set").length === 0, "not ready: no write, no debounce used")
          svc._editionLoaded = true
          svc._applyList({ ok: true, jobs: [] })
          root.base = Recorder.of("settings-set").length
          root.check(svc.markSeen() === true, "first mark writes")
          root.eq(Recorder.of("settings-set").length, root.base + 1)
          root.check(svc.markSeen() === false, "a second mark within a minute does nothing")
          root.eq(Recorder.of("settings-set").length, root.base + 1)
        } else if (s === 2) {
          var sec = JSON.parse(Recorder.of("settings-set")[root.base].stdin).lastSeenAt
          root.eq([svc.settings.lastSeenAt, svc.lastSeenAt], [sec, sec])
          svc.viewerOpened()
          root.eq(Recorder.of("settings-set").length, root.base + 1, "opening again within a minute: no second write")
          svc._markSeenAtMs = Date.now() - 61000
          root.check(svc.markSeen() === true, "after a minute it writes again")
        } else if (s === 4) {
          root.eq(Recorder.of("settings-set").length, root.base + 2)
          root.done()
        }
      } catch (e) { root.threw(e) }
    }
  }
}
QML
run_case "$S" Debounce.qml "service_mark_seen_debounce: one lastSeenAt write a minute, none before ready, the answer moves the mark"

cat > "$S/Payload.qml" <<'QML'
import QtQuick
import "lib/Model.js" as Model
import "lib/Recorder.js" as Recorder
Harness {
  id: root
  Service { id: svc }
  readonly property double today: Model.localMidnight(Date.now())
  property int step: 0
  function dayAt(n) { var d = new Date(root.today); d.setDate(d.getDate() + n); return d.getTime() }
  function last(verb) { var l = Recorder.of(verb); return l.length ? l[l.length - 1] : null }
  Timer {
    interval: 60
    repeat: true
    running: true
    onTriggered: {
      try {
        var s = root.step++
        if (s === 0) {
          svc._editionLoaded = true
          svc._applyList({ ok: true, jobs: [] })
          root.eq([svc._defaultSettings.defaultAllowPaid, svc._defaultSettings.limitsShown, svc._defaultSettings.lastSeenAt], [false, "auto", null])
          var d = Model.draftFromSettings({ defaultAllowPaid: true }, {})
          d.harness = "pi"
          d.provider = "openai-codex"
          d.model = "gpt-5.3-codex"
          d.target = { mode: "resume", sessionId: "3f2a0c19-1111-4222-8333-444455556666", cwd: "/home/u/proj", allowNonGit: false,
                       sessionPath: "/home/u/.pi/agent/sessions/--home-u-proj--/s.jsonl" }
          d.prompt = "Q4 canary 51c2"
          var pv = svc._draftPayload(d, true)
          root.eq([pv.allowPaid, pv.provider, pv.target.sessionPath, pv.prompt], [true, "openai-codex", "/home/u/.pi/agent/sessions/--home-u-proj--/s.jsonl", undefined])
          root.eq(svc._draftPayload(d, false).prompt, "Q4 canary 51c2")
          var v1 = { harness: "claude", target: { mode: "new", sessionId: null, cwd: null, allowNonGit: false }, level: "plan", limits: {}, model: null, trigger: { kind: "now" } }
          var p1 = svc._draftPayload(v1, false)
          root.check(!("allowPaid" in p1) && !("provider" in p1) && !("sessionPath" in p1.target), "a v1 draft stays v1: " + JSON.stringify(p1))
          var fresh = svc._draftPayload(Model.draftFromSettings({}, {}), false)
          root.eq([fresh.allowPaid, fresh.provider, fresh.target.sessionPath], [false, null, null])
          svc.preview(d, function (res) {})
          var sent = JSON.parse(root.last("preview").stdin)
          root.eq([sent.allowPaid, sent.provider, sent.target.sessionPath, "prompt" in sent], [true, "openai-codex", d.target.sessionPath, false])
          root.eq(root.last("preview").deadlineMs, 17000)

          svc.loadSessions("pi", "/home/u/proj")
          root.eq(root.last("sessions").args, ["--harness", "pi", "--cwd", "/home/u/proj"])
          svc.loadSessions("claude")
          root.eq(root.last("sessions").args, ["--harness", "claude"])
          svc.loadSessions("gemini", "relative/dir")
          root.eq(root.last("sessions").args, ["--harness", "gemini"])

          Recorder.answers["models"] = function (args) { return { ok: true, harness: args[1], models: [ { id: "opencode/big-pickle", label: "Big Pickle" } ], reason: null } }
          svc.loadModels("opencode", true)
          root.eq(root.last("models").args, ["--harness", "opencode", "--refresh"])
          root.eq(root.last("models").deadlineMs, 30000)
          var before = Recorder.of("models").length
          svc.loadModels("nope", false)
          root.eq(Recorder.of("models").length, before)
          svc.loadModels("cursor", false)
          root.eq(root.last("models").args, ["--harness", "cursor"])

          Recorder.answers["timeline"] = function (args) { return { ok: true, from: Number(args[1]), to: Number(args[3]), resets: [], runs: [], armed: [], truncated: false } }
          root.check(svc.loadTimeline(root.today) === true)
          var tl = root.last("timeline").args
          root.eq([tl[0], tl[1], tl[2]], ["--from", String(root.today / 1000), "--to"])
          root.check([82800, 86400, 90000].indexOf(Number(tl[3]) - Number(tl[1])) >= 0, "one local day " + tl.join(" "))
          var count = Recorder.of("timeline").length
          root.check(svc.loadTimeline(Date.now() - 20 * 86400000) === false && svc.loadTimeline(Date.now() + 12 * 86400000) === false)
          root.eq(Recorder.of("timeline").length, count, "days outside the helper's window are not asked for")
          for (var i = -8; i <= -1; i++) svc.loadTimeline(root.dayAt(i))

          Recorder.answers["settings-set"] = function (args, stdin) { return { ok: true, settings: JSON.parse(stdin) } }
          svc.setLimitsShown(["claude", "codex"], function (res) {})
          root.eq(JSON.parse(root.last("settings-set").stdin), { limitsShown: ["claude", "codex"] })
          var sets = Recorder.of("settings-set").length
          svc.setLimitsShown(["Bad Id"], function (res) {})
          svc.setLimitsShown(["claude", "claude"], function (res) {})
          root.eq(Recorder.of("settings-set").length, sets)
          svc.setLimitsShown("auto", null)
        } else if (s === 3) {
          root.eq(JSON.parse(root.last("settings-set").stdin), { limitsShown: "auto" })
          root.eq(svc.models.opencode.models[0].label, "Big Pickle")
          root.eq([svc.models.cursor.ok, svc.models.cursor.reason], [true, null])
          var keys = Object.keys(svc.timeline)
          root.eq(keys.length, 7, "at most seven days kept: " + keys.join())
          root.check(keys.indexOf(Model.dayKey(root.today)) < 0 && keys.indexOf(Model.dayKey(root.dayAt(-8))) < 0, "the oldest reads were dropped")
          root.check(keys.indexOf(Model.dayKey(root.dayAt(-1))) >= 0)
          root.eq(svc.providers, [])
          svc._usage = { ok: true, providers: [ { id: "claude" } ] }
          root.eq(svc.providers.length, 1)
          root.check(svc.loadingModels === false && svc.loadingTimeline === false)
          root.done()
        }
      } catch (e) { root.threw(e) }
    }
  }
}
QML
run_case "$S" Payload.qml "service_payload_v2_fields: allowPaid, provider and sessionPath reach the helper, sessions --cwd, models, timeline days"

finish
