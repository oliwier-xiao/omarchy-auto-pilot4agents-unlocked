#!/bin/bash
# tests/q5_qml.test.sh: Compose v2 (owner Q5). Allow paid usage, the model picker, Cursor and
# Pi in Send to, level reasons, the per-agent reset chip, Will run wrapping, the two new
# settings and the session sheet's Cursor and Pi filters (CONTRACT-V2-DELTA 9.4, 9.5, 9.9, 10).
#
# 1. qmllint (Qt 6) on every Q5 file, zero warnings ("missing-property" off, as in q2).
# 2. One offscreen Qt 6 engine run: ComposeView, ModelSheet, SettingsSheet, SessionSheet and a
#    lone ArgvLine against a stand-in service with the v2 surface (providers, models, gate in
#    the preview), plus lib/Compose.js and lib/Tint.js. Every case prints one CASE line; the
#    suite needs all of them, and an engine warning in a Q5 file fails it.
#
# Stand-ins answer synchronously and record every call. No helper, no process, no state
# folder, no model: nothing here touches the machine. Denylisted spellings in fixtures are
# built by concatenation (CONTRACT-V2-DELTA 0.5).
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QML=/usr/lib/qt6/bin/qml
QMLLINT=/usr/lib/qt6/bin/qmllint
SHELL_DIR=/usr/share/omarchy/shell

Q5_FILES="components/ComposeView.qml components/PromptEditor.qml components/SendToSection.qml components/WhenControl.qml
components/OptionsSection.qml components/ArgvLine.qml components/SessionSheet.qml components/SettingsSheet.qml
components/ModelSheet.qml components/CheckRow.qml components/AgentMark.qml components/SignInSheet.qml"

CASES="paid_checkbox_space_and_click paid_notes_per_harness_both_states budget_row_hidden_when_off
will_run_line_matches_preview_off_on model_sheet_filter_groups_default_row model_sheet_badges_free_zen_go
model_sheet_failure_reason_keeps_agent_default pi_requires_model_no_default_row model_picker_row_will_run_model_flag
sendto_cursor_pi_marks_and_notes level_unattended_disabled_reasons when_reset_chip_per_harness
argv_line_wraps_at_token_boundaries settings_default_allow_paid settings_limits_shown_custom_toggle_reorder
session_sheet_cursor_pi_filters_and_pi_path tint_cursor_pi_contrast_and_hue_distance compose_gated_cursor_arm_disabled
compose_pi_slash_prompt_error_shown"

export QT_QPA_PLATFORM=offscreen
export QT_FORCE_STDERR_LOGGING=1
export QML_DISABLE_DISK_CACHE=1

pass=0; fail=0
ok() { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
no() { printf '  FAIL %s\n         %s\n' "$1" "$2"; fail=$((fail+1)); }

T="$(mktemp -d)" || exit 2
trap 'rm -rf "$T"' EXIT INT TERM

# ---------------------------------------------------------------- lint

echo "=== qmllint (Q5 files) ==="
if [ -x "$QMLLINT" ] && [ -d "$SHELL_DIR/Commons" ]; then
  mkdir -p "$T/lint/import" "$T/lint/plugin/components" "$T/lint/plugin/lib"
  ln -s "$SHELL_DIR" "$T/lint/import/qs"
  cp "$REPO"/*.qml "$T/lint/plugin/"
  cp "$REPO"/components/*.qml "$T/lint/plugin/components/"
  cp "$REPO"/lib/*.js "$T/lint/plugin/lib/"
  for f in $Q5_FILES; do
    out="$(cd "$T/lint/plugin" && "$QMLLINT" -I "$T/lint/import" --missing-property disable "$f" 2>&1)"
    warn="$(printf '%s\n' "$out" | grep -E '^Warning' || true)"
    if [ -z "$warn" ]; then ok "qmllint $f"
    else no "qmllint $f" "$(printf '%s' "$warn" | head -4 | tr '\n' ' ')"; fi
  done
else
  echo "  skip qmllint (no $QMLLINT or no $SHELL_DIR)"
fi

# ---------------------------------------------------------------- engine

echo "=== Compose v2 in a Qt 6 engine ==="
if [ ! -x "$QML" ]; then
  echo "  skip the engine run (no Qt 6 qml runtime at $QML)"
  printf '\n%d passed\n' "$pass"
  [ "$fail" -eq 0 ]; exit
fi

W="$T/engine"
mkdir -p "$W"
cp -r "$REPO/lib" "$REPO/components" "$W/"
# The repository's own stand-ins for the shell singletons (lint/qs), read-only here.
cp -r "$REPO/lint/qs" "$W/qs"

cat > "$W/Q5Smoke.qml" <<'QML'
import QtQuick
import "components"
import "lib/Edition.js" as Edition
import "lib/Model.js" as Model
import "lib/Tint.js" as Tint
import "lib/Compose.js" as Compose

Item {
  id: root
  width: 1200
  height: 1800

  readonly property double nowMs: Date.now()
  readonly property int nowSec: Math.floor(root.nowMs / 1000)
  readonly property string digestA: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  readonly property string piPath: "/home/tester/.pi/agent/sessions/--home-tester-proj-api--/2026-09-15T10-00-00-000Z_0199aaaa-1111-7222-8333-000000000001.jsonl"
  readonly property string cursorReason: "Cursor applies file edits headless only with " + "--fo" + "rce" + ", which Auto Pilot never passes."
  readonly property string piReason: "Pi has no approval prompts, so only Plan is offered."
  readonly property var caseNames: []

  property var results: ({})
  property var notices: []
  property var lastPick: null
  property var lastSession: null
  property var steps: []
  property int si: 0
  property int tries: 0

  // ---------------------------------------------------------------- harness

  function check(name, cond, label) {
    var r = root.results[name] || { n: 0, fail: "" }
    r.n++
    if (!cond && r.fail === "") r.fail = String(label)
    var next = {}
    for (var k in root.results) next[k] = root.results[k]
    next[name] = r
    root.results = next
  }
  function ev(k, mods, text) { return { key: k, modifiers: mods || 0, text: text || "", accepted: false } }
  function key(target, k, mods, text) { return target.handleKey(root.ev(k, mods, text)) }
  function typeInto(target, s) { for (var i = 0; i < s.length; i++) target.handleKey(root.ev(0, 0, s[i])) }
  function find(item, pred) {
    if (!item) return null
    if (pred(item)) return item
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) {
      var hit = root.find(kids[i], pred)
      if (hit) return hit
    }
    return null
  }
  function findAll(item, pred, out) {
    out = out || []
    if (!item) return out
    if (pred(item)) out.push(item)
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) root.findAll(kids[i], pred, out)
    return out
  }
  function calls(name) { return svc.calls.filter(function (c) { return c.name === name }) }
  function last(name) { var l = root.calls(name); return l.length ? l[l.length - 1] : null }
  function lastNotice() { return root.notices.length ? root.notices[root.notices.length - 1] : { text: "", kind: "" } }
  function codes(notes) { return JSON.stringify(notes.map(function (n) { return n.code })) }
  function plain(text) { return String(text).split("⁠").join("") }
  function hueDistance(a, b) { var d = Math.abs(a - b) % 360; return d > 180 ? 360 - d : d }

  // A step runs once `until` holds (checked every tick, 80 ticks at most).
  function step(name, run, until) { root.steps.push({ name: name, run: run, until: until || null }) }
  function waitPreview(name) { root.step(name, function () {}, function () { return compose.previewCurrent }) }
  function freshCompose(harness, cwd) {
    compose.reset()
    root.notices = []
    compose.applySession({ harness: harness, mode: "new", sessionId: null, cwd: cwd || "/home/tester/proj/api", title: "api" })
  }

  readonly property var compose: composeView
  readonly property var options: root.find(composeView, function (i) { return i.valueRows !== undefined })
  readonly property var when: root.find(composeView, function (i) { return i.plusChips !== undefined })
  readonly property var sendTo: root.find(composeView, function (i) { return i.modeKeys !== undefined })
  readonly property var editor: root.find(composeView, function (i) { return i.counterText !== undefined })
  readonly property var argv: root.find(composeView, function (i) { return i.fitted !== undefined })
  readonly property var armButton: root.find(composeView, function (i) { return i.widthTemplate === "Run now" })
  readonly property var saveButton: root.find(composeView, function (i) { return i.shortcut === "Ctrl+S" && i.text === "Save draft" })
  readonly property var paidRow: root.options ? root.find(root.options, function (i) { return i.captionTone !== undefined && i.badge !== undefined }) : null

  Timer {
    interval: 40
    repeat: true
    running: true
    onTriggered: root.tick()
  }

  function tick() {
    if (root.si >= root.steps.length) { root.finish(); return }
    var st = root.steps[root.si]
    try {
      if (st.until && !st.until()) {
        root.tries++
        if (root.tries < 80) return
        root.check(st.name, false, "timed out waiting at step " + root.si)
      }
      root.tries = 0
      st.run(st.name)
    } catch (err) {
      root.check(st.name, false, "threw at step " + root.si + ": " + err + " " + err.stack)
    }
    root.si++
  }

  function finish() {
    var names = __CASES__
    for (var i = 0; i < names.length; i++) {
      var r = root.results[names[i]]
      if (!r || r.n === 0) console.log("CASE " + names[i] + " FAIL no checks ran")
      else if (r.fail !== "") console.log("CASE " + names[i] + " FAIL " + r.fail)
      else console.log("CASE " + names[i] + " ok " + r.n)
    }
    Qt.exit(0)
  }

  // ---------------------------------------------------------------- theme (tokyo-night)

  readonly property QtObject theme: QtObject {
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
      for (var i = 0; i < Edition.HARNESS_IDS.length; i++) out[Edition.HARNESS_IDS[i]] = Tint.harnessInk(Edition.HARNESS_IDS[i], "#1a1b26", 4.5)
      return out
    }
    readonly property var marks: {
      var out = {}
      for (var i = 0; i < Edition.HARNESS_IDS.length; i++) out[Edition.HARNESS_IDS[i]] = Tint.harnessMark(Edition.HARNESS_IDS[i], "#1a1b26", 3.0)
      return out
    }
    readonly property var geminiStops: Tint.geminiStops("#1a1b26", 3.0)
    readonly property color okInk: Tint.solved(150, 60, "#1a1b26", 4.5)
    readonly property color warnInk: Tint.solved(38, 85, "#1a1b26", 4.5)
    readonly property color badInk: Tint.ink("#f7768e", "#1a1b26", 4.5)
    property bool reduceMotion: false
    readonly property bool animate: false
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
    function harnessFill(id) { return Tint.harnessInk(id, "#1a1b26", 3.0) }
    function moveMs(ms) { return theme.reduceMotion ? 0 : ms }
    function fadeMs(ms) { return theme.reduceMotion ? Math.min(ms, 120) : ms }
  }

  // ---------------------------------------------------------------- stand-in service

  readonly property var modelFixtures: ({
    opencode: { ok: true, harness: "opencode", source: "mixed", fetchedAt: root.nowSec - 300, cliVersion: "1.18.31", cached: true,
      truncated: false, reason: null, models: [
        { id: "opencode/big-pickle", label: "Big Pickle", group: "opencode", "default": false, billing: "zen_free", provider: null, modelId: null },
        { id: "opencode/gpt-5-zen", label: "GPT-5 Zen", group: "opencode", "default": false, billing: "zen_paid", provider: null, modelId: null },
        { id: "opencode-go/kimi-k2", label: "Kimi K2", group: "opencode-go", "default": false, billing: "go", provider: null, modelId: null },
        { id: "anthropic/claude-sonnet-4", label: "Claude Sonnet 4", group: "anthropic", "default": true, billing: "anthropic", provider: null, modelId: null }
      ] },
    claude: { ok: true, harness: "claude", source: "static", fetchedAt: root.nowSec, cliVersion: null, cached: false, truncated: false,
      reason: null, models: [
        { id: "fable", label: "fable", group: "", "default": false, billing: null },
        { id: "opus", label: "opus", group: "", "default": false, billing: null },
        { id: "opus[1m]", label: "opus[1m]", group: "", "default": true, billing: null },
        { id: "sonnet", label: "sonnet", group: "", "default": false, billing: null }
      ] },
    cursor: { ok: true, harness: "cursor", source: "cli", fetchedAt: root.nowSec, cliVersion: null, cached: false, truncated: false,
      reason: "cursor_no_list", models: [] },
    pi: { ok: true, harness: "pi", source: "cli", fetchedAt: root.nowSec, cliVersion: "0.85.1", cached: false, truncated: false,
      reason: null, models: [
        { id: "openai-codex/gpt-5.5", label: "gpt-5.5", group: "openai-codex", "default": false, billing: null, provider: "openai-codex", modelId: "gpt-5.5" },
        { id: "github-copilot/gpt-4.1", label: "gpt-4.1", group: "github-copilot", "default": false, billing: null, provider: "github-copilot", modelId: "gpt-4.1" },
        { id: "openrouter/deepseek-v3", label: "deepseek-v3", group: "openrouter", "default": false, billing: null, provider: "openrouter", modelId: "deepseek-v3" }
      ] }
  })

  QtObject {
    id: svc
    property bool ready: true
    property var levels: [
      { id: "plan", label: "Plan", "default": true, summary: "Reads and plans only.", defaultMaxTurns: 15, unavailable: {},
        harness: { claude: { caption: "PLAN-CLAUDE" }, opencode: { caption: "PLAN-OPENCODE" }, codex: { caption: "PLAN-CODEX" },
                   gemini: { caption: "PLAN-GEMINI" }, cursor: { caption: "PLAN-CURSOR" }, pi: { caption: "PLAN-PI" } } },
      { id: "unattended", label: "Unattended", "default": false, summary: "Runs without you.", defaultMaxTurns: 30,
        unavailable: { cursor: root.cursorReason, pi: root.piReason },
        harness: { claude: { caption: "UNATTENDED-CLAUDE" }, opencode: { caption: "UNATTENDED-OPENCODE" },
                   codex: { caption: "UNATTENDED-CODEX" }, gemini: { caption: "UNATTENDED-GEMINI" } } }
    ]
    property var caps: ({ promptBytes: 65536, labelChars: 40, maxTurns: [1, 200], budgetUsd: [0.1, 100.0],
                          runtimeSec: [300, 14400], marginSec: [60, 540], horizonSec: 691200, uiMinLeadSec: 60 })
    property var jobs: []
    property var listMeta: ({ linger: true })
    property var agents: ({
      claude: { harness: "claude", available: true, enabled: true, armable: true, gated: false, loggedIn: null, reason: null },
      opencode: { harness: "opencode", available: true, enabled: true, armable: true, gated: false, loggedIn: null, reason: null },
      codex: { harness: "codex", available: true, enabled: true, armable: true, gated: false, loggedIn: true, reason: null },
      gemini: { harness: "gemini", available: true, enabled: true, armable: true, gated: false, loggedIn: null, reason: null },
      cursor: { harness: "cursor", available: true, enabled: true, armable: false, gated: true, loggedIn: null, reason: "gated" },
      pi: { harness: "pi", available: true, enabled: true, armable: true, gated: false, loggedIn: null, reason: null }
    })
    property var providers: [
      { id: "claude", name: "Claude", readable: true, relevant: true, stale: false, source: "record", headlineKey: "claude:5-hour",
        harnesses: ["claude"], windows: [
          { key: "claude:5-hour", kind: "session", shortLabel: "5-hour", percent: 0.42, resetsAt: root.nowSec + 7200, sliding: false, bindable: true, source: "record" },
          { key: "claude:weekly", kind: "weekly", shortLabel: "Weekly", percent: 0.3, resetsAt: root.nowSec + 3 * 86400, sliding: false, bindable: true, source: "record" } ] },
      { id: "codex", name: "Codex", readable: true, relevant: true, stale: false, source: "record", headlineKey: "codex:5-hour",
        harnesses: ["codex", "pi"], windows: [
          { key: "codex:5-hour", kind: "session", shortLabel: "5-hour", percent: 0.2, resetsAt: root.nowSec + 3600, sliding: false, bindable: true, source: "record" },
          { key: "codex:monthly", kind: "monthly", shortLabel: "Monthly", percent: 0.01, resetsAt: root.nowSec + 30 * 86400, sliding: false, bindable: true, source: "record" } ] },
      { id: "gemini", name: "Gemini", readable: true, relevant: true, stale: false, source: "record", headlineKey: "gemini:daily",
        harnesses: ["gemini"], windows: [
          { key: "gemini:daily", kind: "daily", shortLabel: "Daily", percent: 0.1, resetsAt: root.nowSec + 5000, sliding: false, bindable: true, source: "record" } ] },
      { id: "opencode-go", name: "OpenCode Go", readable: true, relevant: true, stale: false, source: "record", headlineKey: "opencode-go:weekly",
        harnesses: ["opencode"], windows: [
          { key: "opencode-go:5-hour", kind: "session", shortLabel: "5-hour", percent: 0, resetsAt: root.nowSec + 18000, sliding: true, bindable: false, source: "record" },
          { key: "opencode-go:weekly", kind: "weekly", shortLabel: "Weekly", percent: 0.5, resetsAt: root.nowSec + 2 * 86400, sliding: false, bindable: true, source: "record" } ] },
      { id: "cursor", name: "Cursor", readable: true, relevant: true, stale: false, source: "record", headlineKey: "cursor:included",
        harnesses: ["cursor"], windows: [
          { key: "cursor:included", kind: "billing_total", shortLabel: "Included", percent: 0.37, resetsAt: root.nowSec + 25 * 86400, sliding: false, bindable: false, source: "record" } ] }
    ]
    property var models: ({})
    property bool loadingModels: false
    property bool holdModels: false
    property var sessions: ({})
    property bool loadingSessions: false
    property var settings: ({ schemaVersion: 1, defaultHarness: "claude", defaultLevel: "plan", resetMarginSec: 120,
                              eveningTime: "23:00", morningTime: "07:00", notify: "all", motion: "full", limitsShown: "auto" })
    property double nowMs: root.nowMs
    property var calls: []

    function rec(name, args) { svc.calls = svc.calls.concat([{ name: name, args: args }]) }
    function refreshAgents(checkLogin) { svc.rec("refreshAgents", [checkLogin]) }
    function agentFor(h) { return svc.agents[h] || null }

    function loadModels(harness, refresh) {
      svc.rec("loadModels", [harness, refresh === true])
      if (svc.holdModels) return
      var m = {}
      for (var k in svc.models) m[k] = svc.models[k]
      m[harness] = root.modelFixtures[harness] || { ok: true, harness: harness, models: [], reason: "failed" }
      svc.models = m
    }

    function loadSessions(harness, cwd) {
      svc.rec("loadSessions", [harness, cwd === undefined ? null : cwd])
      var c = typeof cwd === "string" && cwd !== "" ? cwd : null
      var now = root.nowMs
      var rows = [
        { harness: "claude", id: "3f2a0c19-0000-4000-8000-000000000001", title: "api-refactor", cwd: "/home/tester/proj/api",
          updatedAtMs: now - 7200000, messages: 148, canFork: true, path: null },
        { harness: "cursor", id: "c0ffee00-1111-4222-8333-000000000001", title: "Nightly audit", cwd: "/home/tester/proj/api",
          updatedAtMs: now - 600000, messages: null, canFork: false, path: null }
      ]
      if (c !== null) {
        rows.push({ harness: "pi", id: "0199aaaa-1111-7222-8333-000000000001", title: "pi review", cwd: c,
                    updatedAtMs: now - 120000, messages: 4, canFork: true, path: root.piPath })
        rows.push({ harness: "pi", id: "0199aaaa-1111-7222-8333-000000000002", title: "no file", cwd: c,
                    updatedAtMs: now - 900000, messages: 2, canFork: true, path: null })
      }
      svc.sessions = { all: { ok: true, harness: null, nowMs: now, cwd: c, needsCwd: c === null ? ["pi"] : [], sessions: rows,
        counts: { claude: 1, opencode: 0, codex: 0, gemini: 0, cursor: 1, pi: c === null ? 0 : 2 },
        truncated: { claude: false, opencode: false, codex: false, gemini: false, cursor: false, pi: false },
        errors: {}, limitDays: 90, perHarnessCap: 50 } }
    }

    function gateFor(d) {
      var on = d.allowPaid === true
      var h = d.harness
      var st = on ? "paid_on" : "subscription_only"
      var g = { ok: true, code: null, detail: null, notes: [st], billing: null, provider: null, resetAtMs: null, pending: false }
      var m = typeof d.model === "string" ? d.model : ""
      if (h === "cursor") {
        g.ok = false
        g.code = "harness_gated"
        g.notes = on ? ["paid_on", "cursor_on_demand"] : ["cursor_on_demand"]
      } else if (h === "claude") {
        g.notes = on ? ["paid_on", "budget_cap"] : ["subscription_only"]
      } else if (h === "codex") {
        g.notes = on ? ["paid_on"] : ["subscription_only", "codex_free_tier"]
      } else if (h === "opencode") {
        var b = m === "" ? "unknown" : (m === "opencode/big-pickle" ? "zen_free" : (m.indexOf("opencode-go/") === 0 ? "go"
          : (m.indexOf("anthropic/") === 0 ? "anthropic" : (m.indexOf("opencode/") === 0 ? "zen_paid" : "other"))))
        g.billing = b
        if (b === "zen_free") { g.notes.push("zen_free"); g.resetAtMs = (Math.floor(root.nowMs / 86400000) + 1) * 86400000 }
        else if (b === "go") g.notes.push("go_plan")
        else if (b !== "zen_paid" && b !== "anthropic") g.notes.push("opencode_provider")
        if (!on && b === "zen_paid") { g.ok = false; g.code = "paid_zen" }
        if (!on && b === "anthropic") { g.ok = false; g.code = "paid_opencode_claude" }
      } else if (h === "pi") {
        g.provider = d.provider
        if (d.provider === "openrouter" && !on) { g.ok = false; g.code = "paid_pi_key"; g.detail = { provider: "openrouter" } }
        else if (d.provider !== "openai-codex" && !on) g.notes.push("pi_subscription")
      }
      return g
    }

    function preview(draft, cb) {
      svc.rec("preview", [draft])
      if (draft.prompt !== undefined && draft.prompt !== "") console.warn("preview saw prompt")
      var h = draft.harness
      var m = typeof draft.model === "string" ? draft.model : ""
      var parts
      if (h === "claude") {
        parts = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--permission-mode", "plan", "--max-turns", "15"]
        if (draft.allowPaid === true) parts = parts.concat(["--max-budget-usd", String(draft.limits && draft.limits.budgetUsd ? draft.limits.budgetUsd : 5)])
        if (m !== "") parts = parts.concat(["--model", m])
      } else if (h === "pi") {
        parts = ["pi", "--mode", "json", "--offline", "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes",
                 "--no-approve", "--tools", "read,grep,find,ls", "--provider", String(draft.provider), "--model", m,
                 "--session-id", "<new-session-id>", "--name", "autopilot-<job>"]
      } else if (h === "cursor") {
        parts = ["cursor-agent", "-p", "--output-format", "stream-json", "--mode", "ask", "--sandbox", "enabled", "--workspace", String(draft.target.cwd)]
      } else {
        parts = [h, "run"].concat(m !== "" ? ["--model", m] : [])
      }
      parts.push("<stdin>")
      cb({ ok: true, preview: {
        harness: h, level: draft.level, mode: draft.target.mode, display: parts.join(" "), argv: [], env: {},
        cwd: draft.target.cwd || "/home/tester/proj", binary: "/usr/bin/" + h, link: "", version: null,
        levelCaption: "PREVIEW-" + draft.level, commandDigest: root.digestA, fireAt: null,
        immediate: draft.trigger.kind === "now", fireAtHint: null, fireAtError: null, warnings: [], gate: svc.gateFor(draft) } })
    }
    function createAndArm(draft, cb) {
      svc.rec("createAndArm", [draft])
      cb({ ok: true, id: "0123456789abcdef", status: "armed", fireAt: root.nowSec + 3600, immediate: false, hint: null, job: {} })
    }
    function saveDraft(draft, cb) { svc.rec("saveDraft", [draft]); cb({ ok: true, id: "fedcba9876543210", job: {} }) }
    function disarm(id, cb) { svc.rec("disarm", [id]); if (cb) cb({ ok: true }) }
    function cancelAll(cb) { svc.rec("cancelAll", []); if (cb) cb({ ok: true, disarmed: [], verified: true }) }
    function setSettings(obj, cb) {
      svc.rec("setSettings", [obj])
      var s = {}
      for (var k in svc.settings) s[k] = svc.settings[k]
      for (var j in obj) s[j] = obj[j]
      svc.settings = s
      if (cb) cb({ ok: true, settings: s })
    }
    function setLimitsShown(value, cb) {
      svc.rec("setLimitsShown", [value])
      svc.setSettings({ limitsShown: value }, cb)
    }
  }

  // ---------------------------------------------------------------- the components

  ComposeView {
    id: composeView
    x: 0
    y: 0
    width: 1100
    height: 640
    theme: root.theme
    service: svc
    home: "/home/tester"
    active: true
    onNoticeRequested: function (text, kind, undo) { root.notices = root.notices.concat([{ text: text, kind: kind }]) }
    onSheetRequested: function (sheet, args) {
      if (sheet === "model") modelSheet.open(args)
      if (sheet === "session") sessionSheet.open(args)
    }
  }

  ModelSheet {
    id: modelSheet
    x: 0
    y: 660
    width: 1100
    height: 420
    theme: root.theme
    service: svc
    active: true
    onPicked: function (selection) {
      root.lastPick = selection
      composeView.applyModel(selection)
    }
    onNoticeRequested: function (text, kind, undo) { root.notices = root.notices.concat([{ text: text, kind: kind }]) }
  }

  SettingsSheet {
    id: settingsSheet
    x: 0
    y: 1100
    width: 1100
    height: 360
    theme: root.theme
    service: svc
    active: true
  }

  SessionSheet {
    id: sessionSheet
    x: 0
    y: 1480
    width: 1100
    height: 300
    theme: root.theme
    service: svc
    home: "/home/tester"
    active: true
    onPicked: function (selection) {
      root.lastSession = selection
      composeView.applySession(selection)
    }
    onNoticeRequested: function (text, kind, undo) { root.notices = root.notices.concat([{ text: text, kind: kind }]) }
  }

  ArgvLine {
    id: argvTest
    x: 0
    y: 1790
    width: 480
    theme: root.theme
    maxLines: 3
    display: "pi --mode json --offline --no-extensions --no-skills --no-prompt-templates --no-themes --no-approve --tools read,grep,find,ls --provider openai-codex --model gpt-5.5 --session-id <new-session-id> --name autopilot-<job> <stdin>"
  }

  TextEdit {
    id: wrapProbe
    x: 600
    y: 1790
    readOnly: true
    textFormat: TextEdit.PlainText
    wrapMode: TextEdit.Wrap
    font.family: root.theme.fontFamily
    font.pixelSize: root.theme.type.data
  }

  TextMetrics { id: plainMetrics; font.family: root.theme.fontFamily; font.pixelSize: root.theme.type.data; text: "--mode json --tools read,grep" }
  TextMetrics { id: joinedMetrics; font.family: root.theme.fontFamily; font.pixelSize: root.theme.type.data; text: Compose.argvWrapText("--mode json --tools read,grep") }

  // ---------------------------------------------------------------- cases

  Component.onCompleted: {
    var C

    // tint_cursor_pi_contrast_and_hue_distance
    C = "tint_cursor_pi_contrast_and_hue_distance"
    step(C, function (C) {
      var themes = [["catppuccin-latte", "#eff1f5"], ["tokyo-night", "#1a1b26"], ["gruvbox", "#282828"], ["nord", "#2e3440"],
                    ["rose-pine", "#faf4ed"], ["matte-black", "#121212"], ["ristretto", "#2c2525"]]
      var ids = Edition.HARNESS_IDS
      check(C, ids.length === 6 && ids.indexOf("cursor") === 4 && ids.indexOf("pi") === 5, "six agents in edition order")
      check(C, String(Tint.BRAND.cursor).toUpperCase() === "#BB64D8" && String(Tint.BRAND.pi).toUpperCase() === "#97C639", "brand inks")
      // Red only for real problems: no agent ink may sit near the reds themes use for urgent.
      var urgents = ["#f7768e", "#f38ba8", "#fb4934", "#bf616a", "#eb6f92", "#e67e80", "#c34043", "#fd6883", "#d20f39"]
      for (var u = 0; u < urgents.length; u++) for (var x = 0; x < ids.length; x++) {
        var near = hueDistance(Tint.toHsl(Tint.BRAND[ids[x]]).h, Tint.toHsl(urgents[u]).h)
        check(C, ids[x] === "claude" || near >= 40, ids[x] + " is " + Math.round(near) + " degrees from urgent " + urgents[u])
      }
      check(C, String(Tint.MARK_BRAND.cursor).toUpperCase() === "#8A8A8A" && String(Tint.MARK_BRAND.pi).toUpperCase() === "#C9C9C9", "mark colours")
      for (var t = 0; t < themes.length; t++) {
        var bg = themes[t][1]
        var hues = {}
        for (var i = 0; i < ids.length; i++) {
          var inkHex = Tint.harnessInk(ids[i], bg, 4.5)
          check(C, Tint.contrast(inkHex, bg) >= 4.5, themes[t][0] + " " + ids[i] + " ink " + inkHex + " below 4.5")
          hues[ids[i]] = Tint.toHsl(inkHex).h
          var markHex = Tint.harnessMark(ids[i], bg, 3.0)
          check(C, Tint.contrast(markHex, bg) >= 3.0, themes[t][0] + " " + ids[i] + " mark below 3")
        }
        for (var a = 0; a < ids.length; a++) for (var b = a + 1; b < ids.length; b++) {
          var dist = hueDistance(hues[ids[a]], hues[ids[b]])
          check(C, dist >= 25, themes[t][0] + " " + ids[a] + "/" + ids[b] + " hues " + Math.round(dist) + " degrees apart")
        }
        check(C, Tint.toHsl(Tint.harnessMark("pi", bg, 3.0)).s < 1 && Tint.toHsl(Tint.harnessMark("cursor", bg, 3.0)).s < 1, "cursor and pi marks stay grey")
      }
      check(C, Tint.harnessMark("claude", "#1a1b26", 3.0) === Tint.harnessInk("claude", "#1a1b26", 3.0), "agents with a brand hue draw their mark in it")
      check(C, String(theme.harnessInk("cursor")) !== String(theme.harnessMark("cursor")), "cursor words and mark differ")
    })

    // paid_checkbox_space_and_click
    C = "paid_checkbox_space_and_click"
    step(C, function (C) {
      freshCompose("claude")
      check(C, compose.allowPaid === false && compose.draft.allowPaid === false, "a new draft starts with paid usage off")
      compose.setSection(3)
      options.focusRow("paid")
      check(C, options.rowId === "paid" && options.hints.indexOf("Space allow paid usage") === 0, "paid row hints: " + options.hints)
      check(C, key(compose, Qt.Key_Space, 0, " ") === true && compose.draft.allowPaid === true, "Space checks the box")
      check(C, paidRow !== null && paidRow.checked === true && paidRow.hasCursor === true, "the box shows checked with the cursor")
      var glyph = find(paidRow, function (i) { return i.text === "󰄬" })
      check(C, glyph !== null && glyph.visible, "a check glyph inside the box")
      var box = find(paidRow, function (i) { return i.borderSpec !== undefined && i.width === i.height })
      check(C, box !== null && box.radius <= 3, "a box, not a pill")
      check(C, options.hints.indexOf("Space turn paid usage off") === 0, "hints follow the state")
      key(compose, Qt.Key_Space, Qt.ControlModifier, "")
      check(C, compose.draft.allowPaid === true, "Ctrl+Space does not toggle")
      key(compose, Qt.Key_Return, 0, "")
      check(C, compose.draft.allowPaid === true, "Enter does not toggle paid usage")
      key(compose, Qt.Key_Space, 0, " ")
      check(C, compose.draft.allowPaid === false, "Space again unchecks")
      var mouse = find(paidRow, function (i) { return i.cursorShape === Qt.PointingHandCursor && i.hoverEnabled === true })
      check(C, mouse !== null && mouse.enabled, "the row takes clicks")
      paidRow.toggle()
      check(C, compose.draft.allowPaid === true, "a click (the row's toggle) checks it")
      paidRow.toggle()
      check(C, compose.draft.allowPaid === false, "a second click unchecks it")
    })

    // budget_row_hidden_when_off
    C = "budget_row_hidden_when_off"
    step(C, function (C) {
      freshCompose("claude")
      check(C, JSON.stringify(options.rows) === JSON.stringify(["level", "paid", "turns", "runtime", "model"]), "off: no budget row " + JSON.stringify(options.rows))
      check(C, options.valueRows.indexOf("budget") < 0, "off: no budget value row")
      compose.applyPatch({ allowPaid: true })
      check(C, JSON.stringify(options.rows) === JSON.stringify(["level", "paid", "budget", "turns", "runtime", "model"]), "on: budget under the box")
      var budgetLabel = find(options, function (i) { return i.text === "Budget" && i.visible })
      check(C, budgetLabel !== null, "the budget row is drawn")
      compose.setHarness("opencode")
      check(C, compose.allowPaid === true && options.rows.indexOf("budget") < 0 && options.rows.indexOf("turns") < 0, "OpenCode on: no budget or turns")
      compose.setHarness("claude")
      compose.applyPatch({ allowPaid: false })
      check(C, options.rows.indexOf("budget") < 0 && find(options, function (i) { return i.text === "Budget" && i.visible }) === null, "off again: hidden")
    })

    // will_run_line_matches_preview_off_on
    C = "will_run_line_matches_preview_off_on"
    step(C, function (C) { freshCompose("claude") })
    waitPreview(C)
    step(C, function (C) {
      var p = last("preview")
      check(C, p !== null && p.args[0].allowPaid === false && p.args[0].prompt === undefined, "the preview gets allowPaid false and no prompt")
      check(C, compose.argvDisplay.indexOf("--max-budget-usd") < 0, "off: no budget flag: " + compose.argvDisplay)
      check(C, plain(argv.fitted) === compose.argvDisplay || argv.fitted.indexOf("<stdin>") > 0, "Will run shows the helper's display")
      var command = find(argv, function (i) { return i.maximumLineCount !== undefined && i.wrapMode === Text.Wrap })
      check(C, command !== null && plain(command.text) === argv.fitted, "the drawn command is the fitted display")
      compose.setSection(3)
      options.focusRow("paid")
      key(compose, Qt.Key_Space, 0, " ")
    })
    waitPreview(C)
    step(C, function (C) {
      var p = last("preview")
      check(C, p !== null && p.args[0].allowPaid === true, "the preview gets allowPaid true")
      check(C, compose.argvDisplay.indexOf("--max-budget-usd 5") > 0, "on: the budget flag: " + compose.argvDisplay)
      check(C, plain(argv.fitted).indexOf("--max-budget-usd") > 0, "Will run carries it")
    })

    // paid_notes_per_harness_both_states
    C = "paid_notes_per_harness_both_states"
    step(C, function (C) {
      var S1 = Compose.NOTE_SENTENCES
      check(C, S1.subscription_only === "Runs on your subscription only. If the limit is hit, it waits for the reset.", "off sentence")
      check(C, S1.paid_on === "This job may spend usage credits or API dollars.", "on sentence")
      check(C, S1.opencode_provider === "OpenCode bills through the provider in your OpenCode config.", "OpenCode sentence")
      check(C, S1.codex_free_tier === "Runs draw from a small monthly allowance.", "Codex free-tier sentence")
      var cases = [
        [{ harness: "claude" }, '["subscription_only"]', '["paid_on","budget_cap"]'],
        [{ harness: "codex" }, '["subscription_only","codex_free_tier"]', '["paid_on"]'],
        [{ harness: "gemini" }, '["subscription_only"]', '["paid_on"]'],
        [{ harness: "opencode", model: "opencode/big-pickle" }, '["zen_free"]', '["paid_on","zen_free"]'],
        [{ harness: "opencode", model: "opencode-go/kimi-k2" }, '["go_plan"]', '["paid_on","go_plan"]'],
        [{ harness: "opencode", model: null }, '["subscription_only","opencode_provider"]', '["paid_on","opencode_provider"]'],
        [{ harness: "cursor" }, '["cursor_on_demand"]', '["paid_on","cursor_on_demand"]'],
        [{ harness: "pi", provider: "openai-codex", model: "gpt-5.5" }, '["subscription_only"]', '["paid_on"]'],
        [{ harness: "pi", provider: "github-copilot", model: "gpt-4.1" }, '["subscription_only","pi_subscription"]', '["paid_on"]']
      ]
      for (var i = 0; i < cases.length; i++) {
        var d = cases[i][0]
        var off = { harness: d.harness, model: d.model || null, provider: d.provider || null, allowPaid: false }
        var on = { harness: d.harness, model: d.model || null, provider: d.provider || null, allowPaid: true }
        var gOff = svc.gateFor(off), gOn = svc.gateFor(on)
        check(C, codes(Compose.gateNotes(gOff, d.harness, false, 5, root.nowMs)) === cases[i][1], JSON.stringify(d) + " off " + codes(Compose.gateNotes(gOff, d.harness, false, 5, root.nowMs)))
        check(C, codes(Compose.gateNotes(gOn, d.harness, true, 5, root.nowMs)) === cases[i][2], JSON.stringify(d) + " on " + codes(Compose.gateNotes(gOn, d.harness, true, 5, root.nowMs)))
        // Right after the box flips, before the next preview: the other state's gate still gives the right notes.
        check(C, codes(Compose.gateNotes(gOff, d.harness, true, 5, root.nowMs)) === cases[i][2], JSON.stringify(d) + " flipped on " + codes(Compose.gateNotes(gOff, d.harness, true, 5, root.nowMs)))
        check(C, codes(Compose.gateNotes(gOn, d.harness, false, 5, root.nowMs)) === cases[i][1] || d.harness === "codex" || (d.harness === "pi" && d.provider !== "openai-codex"),
              JSON.stringify(d) + " flipped off " + codes(Compose.gateNotes(gOn, d.harness, false, 5, root.nowMs)))
      }
      var zen = Compose.gateNotes(svc.gateFor({ harness: "opencode", model: "opencode/big-pickle", allowPaid: false }), "opencode", false, 5, root.nowMs)
      check(C, zen.length === 1 && zen[0].text.indexOf("Free OpenCode Zen model. Free use has a daily limit that resets at ") === 0
               && zen[0].text.indexOf(Model.formatClock((Math.floor(root.nowMs / 86400000) + 1) * 86400000)) > 0, "zen free caption replaces the generic one: " + zen[0].text)
      var copilot = Compose.gateNotes(svc.gateFor({ harness: "pi", provider: "github-copilot", model: "gpt-4.1", allowPaid: false }), "pi", false, 5, root.nowMs)
      check(C, copilot[1].text === "Draws from your GitHub Copilot subscription. Extra usage may bill if that account allows it.", copilot[1].text)
      check(C, Compose.gateNotes(null, "claude", true, 7.5, root.nowMs)[1].text === "Budget cap $7.50", "budget cap amount")
      check(C, Compose.paidErrorText("paid_pi_key", { provider: "openrouter" }) === "Pi uses an API key for OpenRouter. Allow paid usage to run it.", "Pi key sentence")
      check(C, Compose.paidErrorText("paid_pi_key", { provider: "Bad Name" }) === "Pi uses an API key for this provider. Allow paid usage to run it.", "unchecked provider name not shown")
      freshCompose("claude")
    })
    waitPreview(C)
    step(C, function (C) {
      check(C, paidRow.caption === Compose.NOTE_SENTENCES.subscription_only && paidRow.captionTone === "soft", "claude off caption, soft")
      check(C, String(find(paidRow, function (i) { return i.text === paidRow.caption && i.visible }).color) === String(theme.soft), "soft ink")
      compose.applyPatch({ allowPaid: true })
      check(C, paidRow.caption === Compose.NOTE_SENTENCES.paid_on && paidRow.captionTone === "warn", "on caption at once, warn")
      check(C, options.extraNotes.length === 1 && options.extraNotes[0].text === "Budget cap $5.00", "budget cap note")
      check(C, String(find(paidRow, function (i) { return i.text === paidRow.caption && i.visible }).color) === String(theme.warnInk), "warn ink, not red")
      compose.applyPatch({ allowPaid: false })
      compose.setHarness("opencode")
      compose.applyModel({ harness: "opencode", model: "opencode/big-pickle" })
    })
    waitPreview(C)
    step(C, function (C) {
      check(C, codes(options.notes) === '["zen_free"]' && paidRow.caption === options.notes[0].text, "OpenCode free model: the class caption alone " + codes(options.notes))
      // The billing badge sits on the model it describes, not on the checkbox.
      var modelBadge = find(options, function (i) { return i.objectName === "modelBadge" })
      check(C, options.badge.text === "free" && paidRow.badge === "" && modelBadge !== null && modelBadge.text === "free"
               && String(modelBadge.color) === String(theme.okInk),
            "free badge on the Model row: " + JSON.stringify(options.badge))
      compose.applyModel({ harness: "opencode", model: "anthropic/claude-sonnet-4" })
      // Before the next preview answers, the free model's note must not linger.
      // The badge follows the draft's own model list entry at once (anthropic: "paid"), never the free one before it.
      check(C, codes(options.notes) === '["subscription_only"]' && options.badge.text === "paid" && options.badge.tone === "warn",
            "no stale notes or badge while the preview is on its way: " + codes(options.notes) + " " + JSON.stringify(options.badge))
    })
    waitPreview(C)
    step(C, function (C) {
      check(C, options.paidError === "Claude models in OpenCode bill API or extra usage, not your Claude plan.", "Claude in OpenCode refused under the box: " + options.paidError)
      check(C, compose.gateLine === "" && armButton.enabled === false, "the paid refusal is not repeated by the buttons; Arm off")
      compose.applyPatch({ allowPaid: true })
    })
    waitPreview(C)
    step(C, function (C) {
      check(C, options.paidError === "" && compose.gateCode === "" && armButton.enabled === true, "allowed once paid usage is on")
      compose.applyPatch({ allowPaid: false })
      compose.setHarness("codex")
    })
    waitPreview(C)
    step(C, function (C) {
      check(C, codes(options.notes) === '["subscription_only","codex_free_tier"]' && options.extraNotes[0].text === Compose.NOTE_SENTENCES.codex_free_tier, "Codex free-tier caption")
    })

    // model_sheet_filter_groups_default_row
    C = "model_sheet_filter_groups_default_row"
    step(C, function (C) {
      svc.models = ({})
      freshCompose("opencode")
      compose.setSection(3)
      options.focusRow("model")
      check(C, options.valueText("model") === "Agent default", "the row starts at Agent default")
      key(compose, Qt.Key_Return, 0, "")
      var lm = last("loadModels")
      check(C, modelSheet.harness === "opencode" && lm !== null && lm.args[0] === "opencode" && lm.args[1] === false, "Enter opens the sheet, which reads OpenCode's models")
      check(C, modelSheet.rows.length > 0 && modelSheet.rows[0].kind === "default" && modelSheet.rows[0].label === "Agent default", "Agent default first")
      check(C, modelSheet.cursor === 0, "the cursor starts on the current choice")
      var groups = modelSheet.rows.filter(function (r) { return r.kind === "group" }).map(function (r) { return r.label })
      check(C, JSON.stringify(groups) === JSON.stringify(["OpenCode Zen", "OpenCode Go", "Anthropic"]), "grouped by provider: " + JSON.stringify(groups))
      var def = modelSheet.rows.filter(function (r) { return r.isDefault })
      check(C, def.length === 1 && def[0].id === "anthropic/claude-sonnet-4", "the configured default is marked")
      check(C, modelSheet.hints === "Type to filter  ·  Enter pick  ·  Ctrl+R refresh list  ·  Esc back", "footer: " + modelSheet.hints)
      typeInto(modelSheet, "pickle")
      check(C, modelSheet.rows.length === 2 && modelSheet.rows[0].kind === "group" && modelSheet.rows[1].id === "opencode/big-pickle", "type to filter")
      check(C, modelSheet.cursor === 1, "the cursor skips the group row")
      check(C, key(modelSheet, Qt.Key_Escape) === true && modelSheet.query === "", "Esc clears the filter first")
      check(C, key(modelSheet, Qt.Key_Escape) === false, "then Esc is the sheet's way back")
      key(modelSheet, Qt.Key_R, Qt.ControlModifier, "")
      lm = last("loadModels")
      check(C, lm.args[0] === "opencode" && lm.args[1] === true, "Ctrl+R refreshes the list")
      key(modelSheet, Qt.Key_Down)
      check(C, modelSheet.rows[modelSheet.cursor].kind === "model", "Down lands on a model, never a heading")
      typeInto(modelSheet, "pickle")
      key(modelSheet, Qt.Key_Return)
      check(C, lastPick && lastPick.model === "opencode/big-pickle" && lastPick.provider === null && lastPick.label === "Big Pickle", "Enter picks")
      check(C, compose.model === "opencode/big-pickle" && options.valueText("model") === "Big Pickle", "the draft and the row take it")
      // Loading: no answer yet.
      svc.holdModels = true
      svc.loadingModels = true
      modelSheet.open({ harness: "codex", model: "" })
      check(C, modelSheet.waiting === true, "waiting for the list")
      var reading = find(modelSheet, function (i) { return i.text === "Reading models…" && i.visible && i.font && i.font.pixelSize === theme.type.body })
      check(C, reading !== null, "Reading models… line")
      svc.holdModels = false
      svc.loadingModels = false
    })

    // model_sheet_badges_free_zen_go
    C = "model_sheet_badges_free_zen_go"
    step(C, function (C) {
      var res = root.modelFixtures.opencode
      var off = Compose.modelRows(res, "opencode", "", false)
      var byId = {}
      for (var i = 0; i < off.length; i++) byId[off[i].id] = off[i]
      check(C, byId["opencode/big-pickle"].badge === "free" && byId["opencode/big-pickle"].badgeTone === "ok", "free badge")
      check(C, byId["opencode/gpt-5-zen"].badge === "Zen" && byId["opencode/gpt-5-zen"].badgeTone === "warn", "Zen badge amber while off")
      check(C, byId["opencode-go/kimi-k2"].badge === "Go", "Go badge")
      // Claude through OpenCode is refused while paid usage is off: the picker says so before the pick.
      check(C, byId["anthropic/claude-sonnet-4"].badge === "paid" && byId["anthropic/claude-sonnet-4"].badgeTone === "warn",
            "paid badge, amber, on anthropic models while off")
      var onAll = Compose.modelRows(res, "opencode", "claude", true)
      check(C, onAll.some(function (r) { return r.id === "anthropic/claude-sonnet-4" && r.badge === "paid" && r.badgeTone === "readable" }),
            "paid badge calm while on")
      check(C, Compose.badgeFor("other", false).text === "" && Compose.badgeFor("unknown", false).text === "", "no badge for other classes")
      var on = Compose.modelRows(res, "opencode", "zen", true)
      check(C, on.length === 2 && on[1].badge === "Zen" && on[1].badgeTone === "readable", "Zen badge calm while on")
      check(C, Compose.modelRows(root.modelFixtures.claude, "claude", "", false).every(function (r) { return r.badge === "" }), "no badges outside OpenCode")
      // Claude: newest-of-family aliases, then pinned versions, each under its heading; a
      // version number finds the alias through its note.
      var claudeList = { models: [
        { id: "opus", label: "Opus (newest)", group: "claude-latest", note: "newest: Claude Opus 5" },
        { id: "claude-opus-5", label: "Claude Opus 5", group: "claude-pinned", note: null },
        { id: "claude-sonnet-5", label: "Claude Sonnet 5", group: "claude-pinned", note: null } ] }
      var claudeRows = Compose.modelRows(claudeList, "claude", "", false)
      check(C, JSON.stringify(claudeRows.map(function (r) { return r.kind + ":" + r.label })) === JSON.stringify(
              ["default:Agent default", "group:Newest of each family", "model:Opus (newest)", "group:Pinned versions",
               "model:Claude Opus 5", "model:Claude Sonnet 5"]), "Claude rows grouped: " + JSON.stringify(claudeRows.map(function (r) { return r.label })))
      check(C, JSON.stringify(Compose.modelRows(claudeList, "claude", "opus 5", false).filter(function (r) { return r.kind === "model" }).map(function (r) { return r.id }))
               === JSON.stringify(["opus", "claude-opus-5"]), "opus 5 finds the alias by its note and the pinned version")
      check(C, Compose.modelRows({ models: [{ id: "opencode/fake-free", label: "x", group: "opencode" }] }, "opencode", "", false)[2].badge === "",
            "never free from the name alone")
      modelSheet.open({ harness: "opencode", model: "", allowPaid: false })
      var pills = findAll(modelSheet, function (i) { return i.radius !== undefined && i.visible && i.children.length === 1 && i.children[0].text === "Zen" })
      check(C, pills.length === 1 && String(pills[0].children[0].color) === String(theme.warnInk), "the Zen pill is drawn in the warning ink")
    })

    // model_sheet_failure_reason_keeps_agent_default
    C = "model_sheet_failure_reason_keeps_agent_default"
    step(C, function (C) {
      freshCompose("cursor")
      compose.openModelSheet("")
      check(C, modelSheet.harness === "cursor" && modelSheet.reasonText === "Cursor did not list models. Run cursor-agent login.", "fixed reason: " + modelSheet.reasonText)
      check(C, modelSheet.rows.length === 1 && modelSheet.rows[0].kind === "default", "Agent default stays")
      var status = find(modelSheet, function (i) { return i.visible && typeof i.text === "string" && i.text.indexOf("Cursor did not list models.") === 0 })
      check(C, status !== null && status.text.indexOf("Agent default still works.") > 0 && String(status.color) === String(theme.readable), "reason line, not red")
      key(modelSheet, Qt.Key_Return)
      check(C, lastPick && lastPick.harness === "cursor" && lastPick.model === null, "Agent default can be picked")
      var cursorRows = Compose.modelRows({ models: [{ id: "auto", label: "Auto" }, { id: "claude-opus-4-8[context=1m,effort=high,fast=false]", label: "Opus 4.8 1M" }] }, "cursor", "", false)
      check(C, JSON.stringify(cursorRows.map(function (r) { return r.id })) === JSON.stringify(["", "claude-opus-4-8[context=1m,effort=high,fast=false]"]), "Cursor never offers auto; bracket ids stay")
      check(C, Compose.modelOk("claude-opus-4-8[context=1m,effort=high,fast=false]") && Compose.modelOk("opus[1m]") && !Compose.modelOk("bad name")
               && !Compose.modelOk("x".repeat(129)) && !Compose.modelOk(""), "model grammar mirror")
      check(C, Compose.MODEL_REASONS.pi_no_provider === "Pi has no signed-in provider." && Compose.MODEL_REASONS.timeout === "Reading models took too long.", "reason table")
    })

    // pi_requires_model_no_default_row
    C = "pi_requires_model_no_default_row"
    step(C, function (C) {
      svc.calls = []
      freshCompose("pi")
      check(C, compose.piNeedsModel === true && armButton.enabled === false, "Pi without a model: Arm off")
      check(C, compose.argvDisplay === "Pick a Pi model first. The exact command shows here.", "Will run says what is missing")
      check(C, options.valueText("model") === "Pick a model" && options.noteText("model") === "required", "the row asks for a model")
      check(C, lastNotice().text.indexOf("Pi needs a provider and a model. Pick one from the list.") >= 0, "switching to Pi says so: " + lastNotice().text)
    })
    step(C, function (C) {
      check(C, calls("preview").filter(function (c) { return c.args[0].harness === "pi" }).length === 0, "no preview for Pi without a model")
      compose.setSection(3)
      options.focusRow("model")
      key(compose, Qt.Key_Delete)
      key(compose, Qt.Key_Return)
      check(C, modelSheet.harness === "pi" && modelSheet.rows.length > 0 && modelSheet.rows.every(function (r) { return r.kind !== "default" }), "no Agent default row for Pi")
      var groups = modelSheet.rows.filter(function (r) { return r.kind === "group" }).map(function (r) { return r.label })
      check(C, JSON.stringify(groups) === JSON.stringify(["ChatGPT", "GitHub Copilot", "OpenRouter"]), "grouped by provider: " + JSON.stringify(groups))
      key(modelSheet, Qt.Key_Return)
      check(C, lastPick && lastPick.provider === "openai-codex" && lastPick.model === "gpt-5.5", "a pick carries provider and model id")
      check(C, compose.provider === "openai-codex" && compose.model === "gpt-5.5" && !compose.piNeedsModel, "the draft has both")
    }, function () { return true })
    waitPreview(C)
    step(C, function (C) {
      var p = last("preview")
      check(C, p.args[0].harness === "pi" && p.args[0].provider === "openai-codex" && p.args[0].model === "gpt-5.5", "the preview gets provider and model")
      check(C, armButton.enabled === true && options.valueText("model") === "gpt-5.5", "Arm on, the row shows the label")
      options.focusRow("model")
      key(compose, Qt.Key_Delete)
      check(C, compose.model === "gpt-5.5", "Delete does not clear a Pi model")
      var m = {}
      for (var k in svc.models) m[k] = svc.models[k]
      m.pi = { ok: true, harness: "pi", models: [], reason: "pi_no_provider" }
      svc.models = m
      modelSheet.open({ harness: "pi", provider: "", model: "" })
      check(C, modelSheet.rows.length === 0 && modelSheet.reasonText === "Pi has no signed-in provider.", "empty list reason")
      var status = find(modelSheet, function (i) { return i.visible && i.text === "Pi has no signed-in provider." })
      check(C, status !== null, "no Agent default promise for Pi")
      m = {}
      for (var k2 in svc.models) m[k2] = svc.models[k2]
      m.pi = root.modelFixtures.pi
      svc.models = m
    })

    // model_picker_row_will_run_model_flag
    C = "model_picker_row_will_run_model_flag"
    step(C, function (C) { freshCompose("claude") })
    waitPreview(C)
    step(C, function (C) {
      check(C, compose.argvDisplay.indexOf("--model") < 0 && options.valueText("model") === "Agent default", "Agent default: no --model")
      compose.openModelSheet("op")
      check(C, modelSheet.query === "op" && modelSheet.rows[modelSheet.cursor].id === "opus", "a typed letter starts the filter")
      key(modelSheet, Qt.Key_Return)
    })
    waitPreview(C)
    step(C, function (C) {
      var p = last("preview")
      check(C, p.args[0].model === "opus" && compose.argvDisplay.indexOf("--model opus <stdin>") > 0, "picked: --model in Will run: " + compose.argvDisplay)
      check(C, options.noteText("model") === "", "not the configured default")
      compose.setSection(3)
      options.focusRow("model")
      check(C, options.hints.indexOf("Enter pick a model  ·  Delete agent default") === 0, "model row hints: " + options.hints)
      key(compose, Qt.Key_Delete)
      check(C, compose.model === "" && compose.draft.model === null, "Delete goes back to Agent default")
    })
    waitPreview(C)
    step(C, function (C) {
      check(C, compose.argvDisplay.indexOf("--model") < 0 && last("preview").args[0].model === null, "no --model again")
    })

    // sendto_cursor_pi_marks_and_notes
    C = "sendto_cursor_pi_marks_and_notes"
    step(C, function (C) {
      freshCompose("claude")
      var st = sendTo.agentState("cursor")
      check(C, st.enabled === true && st.reasonKey === "gated" && st.warn === true && st.ready === false, "Cursor gated state: " + JSON.stringify(st))
      check(C, sendTo.agentState("pi").enabled === true && sendTo.agentState("pi").reasonKey === "" && sendTo.agentState("pi").ready === true, "Pi plain")
      var chips = findAll(sendTo, function (i) { return i.pill === true && i.noteColor !== undefined && (i.harness === "cursor" || i.harness === "pi") })
      check(C, chips.length === 2, "cursor and pi chips")
      for (var i = 0; i < chips.length; i++) {
        var mark = find(chips[i], function (x) { return x.pathData !== undefined })
        check(C, mark !== null && mark.pathData !== "" && mark.visible, chips[i].harness + " mark drawn")
        check(C, String(mark.color) === String(theme.harnessMark(chips[i].harness)), chips[i].harness + " mark in its grey")
        check(C, String(chips[i].textColor) === String(theme.harnessInk(chips[i].harness)), chips[i].harness + " words in its own hue")
        check(C, chips[i].text === sendTo.chipLabel(chips[i].harness), "chip name")
        // No words on a chip: a warning is one glyph, so all six fit on one line.
        check(C, chips[i].note === (chips[i].harness === "cursor" ? Model.GLYPH.alert : ""), chips[i].harness + " note: " + chips[i].note)
        if (chips[i].harness === "cursor") check(C, String(chips[i].noteColor) === String(theme.warnInk), "the glyph in the warning ink")
      }
      check(C, sendTo.chipLabel("cursor") === "Cursor" && sendTo.chipLabel("claude") === "Claude" && sendTo.chipLabel("pi") === "Pi",
            "short chip labels for the widest agents")
      check(C, Model.harnessName("cursor") === "Cursor Agent" && Model.harnessName("claude") === "Claude Code", "the agents are not renamed elsewhere")
      var cursorMark = find(sendTo, function (x) { return x.pathData !== undefined && x.agent === "cursor" })
      check(C, cursorMark && cursorMark.paths.cursor.indexOf("M 22.106 5.68") === 0 && cursorMark.paths.pi.indexOf("M 1 1 h 16.5") === 0, "agent-skills-manager paths")
      compose.setHarness("cursor")
      check(C, JSON.stringify(sendTo.modes) === JSON.stringify(["resume", "new"]) && sendTo.hints.indexOf("f fork") < 0, "Cursor: resume and new only")
      compose.setSection(1)
      key(compose, Qt.Key_F, 0, "f")
      check(C, compose.target.mode === "new", "f does not fork for Cursor")
      compose.setHarness("pi")
      check(C, JSON.stringify(sendTo.modes) === JSON.stringify(["resume", "fork", "new"]) && sendTo.hints.indexOf("f fork") > 0, "Pi: resume, fork and new")
    })

    // level_unattended_disabled_reasons
    C = "level_unattended_disabled_reasons"
    step(C, function (C) {
      freshCompose("claude")
      check(C, JSON.stringify(Compose.levelState(svc.levels, "unattended", "claude")) === JSON.stringify({ available: true, reason: "" }), "Claude offers Unattended")
      check(C, JSON.stringify(Compose.levelState(svc.levels, "unattended", "pi")) === JSON.stringify({ available: false, reason: root.piReason }), "Pi reason from the table")
      check(C, Compose.levelState(svc.levels, "unattended", "cursor").reason === root.cursorReason, "Cursor reason from the table")
      check(C, Compose.levelState([{ id: "unattended", harness: { claude: {} } }], "unattended", "gemini").reason === "That permission level is not offered for this agent.", "absent from the table: not offered")
      check(C, Compose.levelState([], "unattended", "pi").available === true, "no table yet: the helper decides")
      compose.applyPatch({ level: "unattended" })
      compose.setHarness("pi")
      check(C, compose.level === "plan", "switching to Pi falls back to Plan")
      check(C, lastNotice().text.indexOf(root.piReason) === 0, "and says why: " + lastNotice().text)
      check(C, options.unavailableLine === root.piReason, "the reason under the chips")
      var chip = find(options, function (i) { return i.text === "Unattended" && i.noteColor !== undefined })
      check(C, chip !== null && chip.enabled === false && chip.note === "not offered", "Unattended chip dimmed")
      var reasonText = find(options, function (i) { return i.text === root.piReason && i.visible })
      check(C, reasonText !== null && String(reasonText.color) === String(theme.soft), "reason drawn in the soft ink")
      compose.setSection(3)
      options.focusRow("level")
      key(compose, Qt.Key_Right)
      check(C, compose.level === "plan" && options._note === root.piReason, "Right cannot pick it and says why")
      compose.setHarness("cursor")
      check(C, options.unavailableLine === root.cursorReason, "Cursor's own reason")
      compose.setHarness("codex")
      check(C, options.unavailableLine === "" && Compose.levelState(svc.levels, "unattended", "codex").available, "Codex offers both")
    })

    // when_reset_chip_per_harness
    C = "when_reset_chip_per_harness"
    step(C, function (C) {
      function clock(sec) { return Model.formatClock(sec * 1000) }
      freshCompose("claude")
      check(C, when.reset.kind === "claude_5h_reset" && when.reset.available && when.reset.label === "At reset " + clock(root.nowSec + 7200), "Claude: " + JSON.stringify(when.reset))
      compose.setSection(2)
      key(compose, Qt.Key_R, 0, "r")
      check(C, compose.trigger.kind === "claude_5h_reset" && when.captionText === "follows Claude reset +2m", "r follows Claude: " + when.captionText)
      compose.setHarness("codex")
      check(C, compose.trigger.kind === "codex_window_reset", "the reset follows the agent")
      check(C, when.reset.label === "At reset " + clock(root.nowSec + 3600) && when.reset.available, "Codex soonest open window: " + JSON.stringify(when.reset))
      compose.setHarness("gemini")
      check(C, compose.trigger.kind === "gemini_daily_reset" && when.reset.label === "At reset " + clock(root.nowSec + 5000), "Gemini record daily window: " + when.reset.label)
      compose.setHarness("opencode")
      check(C, compose.trigger.kind === "now" && lastNotice().text === "No reset applies to OpenCode, so the time is set to Now.", "OpenCode default model: no reset: " + lastNotice().text)
      check(C, when.resetOffered === false && when.stops.indexOf("reset") < 0 && when.hints.indexOf("r reset") < 0, "chip, stop and hint gone")
      var hiddenChip = find(when, function (i) { return i.meterValue !== undefined && i.text !== undefined && String(i.text).indexOf("reset") >= 0 })
      check(C, hiddenChip === null || !hiddenChip.parent.visible, "no reset chip on screen")
      key(compose, Qt.Key_R, 0, "r")
      check(C, when.captionText === "No reset applies to this agent. Pick a time instead.", "r says why")
      compose.applyModel({ harness: "opencode", model: "opencode/big-pickle" })
      check(C, compose.billing === "zen_free" && when.reset.kind === "zen_free_reset" && when.reset.available, "free Zen model: the Zen free reset: " + JSON.stringify(when.reset))
      check(C, when.reset.label === "At reset " + Model.formatClock((Math.floor(root.nowMs / 86400000) + 1) * 86400000), "00:00 UTC in local time: " + when.reset.label)
      compose.applyModel({ harness: "opencode", model: "opencode-go/kimi-k2" })
      check(C, when.reset.kind === "go_window_reset" && when.reset.label === "At reset " + clock(root.nowSec + 2 * 86400), "Go model: its open non-sliding window: " + when.reset.label)
      compose.applyModel({ harness: "opencode", model: "opencode/gpt-5-zen" })
      check(C, when.resetOffered === false, "paid Zen model: none")
      compose.setHarness("cursor")
      check(C, when.resetOffered === false, "Cursor: none")
      compose.setHarness("pi")
      compose.applyModel({ harness: "pi", provider: "openai-codex", model: "gpt-5.5" })
      check(C, when.reset.kind === "codex_window_reset" && when.reset.available, "Pi with ChatGPT follows Codex")
      compose.applyModel({ harness: "pi", provider: "github-copilot", model: "gpt-4.1" })
      check(C, when.resetOffered === false, "Pi with another provider: none")
      check(C, JSON.stringify(Compose.resetKindsFor("opencode", "", "opencode/x", "unknown")) === "[]" && JSON.stringify(Compose.resetKindsFor("opencode", "", "opencode/x", "")) === '["zen_free_reset"]', "unknown billing class refused, not yet known offered")
      var far = Compose.resetChipFor([{ id: "codex", readable: true, windows: [{ kind: "monthly", percent: 1.0, resetsAt: root.nowSec + 20 * 86400, bindable: true, sliding: false }] }], "codex", "", "", "", root.nowMs, 120)
      check(C, far.available === false && far.reason === "too_far", "a reset past 8 days is not offered: " + JSON.stringify(far))
    })

    // argv_line_wraps_at_token_boundaries
    C = "argv_line_wraps_at_token_boundaries"
    step(C, function (C) {
      check(C, Math.abs(plainMetrics.advanceWidth - joinedMetrics.advanceWidth) < 0.5, "word joiners take no width: " + plainMetrics.advanceWidth + " vs " + joinedMetrics.advanceWidth)
      check(C, argvTest.columns > 20, "columns measured: " + argvTest.columns)
      check(C, argvTest.fitted.slice(-8) === " <stdin>" && argvTest.fitted.indexOf(" … ") > 0, "a long command keeps its <stdin> tail: " + argvTest.fitted)
      check(C, argvTest.lineCount > 1 && argvTest.lineCount <= 3, "within maxLines: " + argvTest.lineCount)
      check(C, Compose.lineCount(argvTest.fitted.split(" "), argvTest.columns) <= 3, "the fitted text fits by count")
      var command = find(argvTest, function (i) { return i.maximumLineCount !== undefined && i.wrapMode === Text.Wrap })
      check(C, command !== null && command.truncated === false, "nothing elided by the Text itself")
      wrapProbe.width = command.width
      wrapProbe.text = Compose.argvWrapText(argvTest.fitted)
      check(C, Compose.fitArgv("claude -p <stdin>", argvTest.columns, 3) === "claude -p <stdin>", "a short command is left whole")
      // A 120-character folder is shortened before any flag is cut, and the model is never cut.
      var longDir = "/tmp/claude-1000/" + new Array(11).join("scratch-folder-") + "/project"
      var oc = "opencode run --dir " + longDir + " --pure --format json --agent plan -m anthropic/claude-fable-5 <stdin>"
      var ocFit = Compose.fitArgv(oc, 40, 3)
      check(C, ocFit.indexOf("-m anthropic/claude-fable-5 <stdin>") > 0 && ocFit.indexOf("--pure") > 0 && ocFit.indexOf("…") > 0
               && Compose.lineCount(ocFit.split(" "), 40) <= 3, "path shortened first, model kept: " + ocFit)
      var cramped = Compose.fitArgv(oc, 24, 2)
      check(C, cramped.slice(-8) === " <stdin>" && cramped.indexOf("-m anthropic/claude-fable-5") > 0, "model survives even a cut: " + cramped)
      var budget = Compose.fitArgv("claude -p --output-format stream-json --verbose --max-turns 15 --permission-mode plan --max-budget-usd 5.00 --model claude-sonnet-4-6 <stdin>", 30, 2)
      check(C, budget.indexOf("--max-budget-usd 5.00") > 0 && budget.indexOf("--model claude-sonnet-4-6") > 0, "budget and model kept: " + budget)
      check(C, Compose.argvWrapText("a-b c/d") === "a⁠-⁠b c⁠/⁠d", "joiners inside tokens only")
    })
    step(C, function (C) {
      var text = wrapProbe.text
      var token = 0, tokenY = null, broken = [], ys = {}
      for (var i = 0; i < text.length; i++) {
        var ch = text.charAt(i)
        if (ch === " ") { token++; tokenY = null; continue }
        if (ch === "⁠") continue
        var y = Math.round(wrapProbe.positionToRectangle(i).y)
        ys[y] = true
        if (tokenY === null) tokenY = y
        else if (y !== tokenY && broken.indexOf(token) < 0) broken.push(token)
      }
      check(C, Object.keys(ys).length > 1, "the probe wrapped at all")
      check(C, broken.length === 0, "no token split across lines: " + JSON.stringify(broken.map(function (t) { return argvTest.fitted.split(" ")[t] })))
    })

    // settings_default_allow_paid
    C = "settings_default_allow_paid"
    step(C, function (C) {
      settingsSheet.open()
      check(C, settingsSheet.rowIds.indexOf("defaultAllowPaid") === 2 && settingsSheet.labelFor("defaultAllowPaid") === "Paid usage for new jobs", "row and label")
      check(C, JSON.stringify(settingsSheet.options("defaultAllowPaid").map(function (o) { return o.label })) === '["Off","On"]', "Off and On")
      check(C, settingsSheet.current("defaultAllowPaid") === false, "a missing key reads Off")
      for (var i = 0; i < 2; i++) key(settingsSheet, Qt.Key_Down)
      check(C, settingsSheet.rowId === "defaultAllowPaid", "cursor on the row")
      key(settingsSheet, Qt.Key_Right)
      var ss = last("setSettings")
      check(C, ss !== null && ss.args[0].defaultAllowPaid === true && svc.settings.defaultAllowPaid === true, "Right writes On")
      check(C, settingsSheet.noteFor("defaultAllowPaid") === "new jobs may spend credits or API dollars", "note says what On means")
      compose.reset()
      check(C, compose.allowPaid === true, "a new draft takes the default")
      key(settingsSheet, Qt.Key_Left)
      check(C, last("setSettings").args[0].defaultAllowPaid === false, "Left writes Off")
      compose.reset()
      check(C, compose.allowPaid === false, "and new drafts are off again")
    })

    // settings_limits_shown_custom_toggle_reorder
    C = "settings_limits_shown_custom_toggle_reorder"
    step(C, function (C) {
      settingsSheet.open()
      check(C, settingsSheet.labelFor("limitsShown") === "Limits in the header" && settingsSheet.limitsCustom === false, "Auto at first")
      check(C, settingsSheet.rowIds.filter(function (r) { return r.indexOf("limit:") === 0 }).length === 0, "no source rows while Auto")
      settingsSheet.cursor = settingsSheet.rowIds.indexOf("limitsShown")
      key(settingsSheet, Qt.Key_Right)
      var auto = Model.shownSources(svc.providers, "auto")
      var lc = last("setLimitsShown")
      check(C, lc !== null && JSON.stringify(lc.args[0]) === JSON.stringify(auto) && auto.length === 5, "Custom starts from what Auto shows: " + JSON.stringify(lc ? lc.args[0] : null))
      check(C, settingsSheet.limitsCustom === true && settingsSheet.limitRows.length === 5, "one row per source")
      settingsSheet.cursor = settingsSheet.rowIds.indexOf("limit:codex")
      check(C, settingsSheet.hints === "Space show or hide  ·  Alt+↑/↓ move  ·  ↑/↓ setting  ·  Esc back", "hints: " + settingsSheet.hints)
      key(settingsSheet, Qt.Key_Space, 0, " ")
      check(C, JSON.stringify(svc.settings.limitsShown) === JSON.stringify(auto.filter(function (id) { return id !== "codex" })), "Space hides it")
      var row = settingsSheet.limitRow("codex")
      check(C, row && row.shown === false && settingsSheet.limitRows[settingsSheet.limitRows.length - 1].id === "codex", "a hidden source sorts after the shown ones")
      check(C, settingsSheet.rowId === "limit:codex", "the cursor follows it")
      key(settingsSheet, Qt.Key_Space, 0, " ")
      var list = svc.settings.limitsShown
      check(C, list[list.length - 1] === "codex", "Space shows it again, last")
      key(settingsSheet, Qt.Key_Up, Qt.AltModifier, "")
      list = svc.settings.limitsShown
      check(C, list[list.length - 2] === "codex" && settingsSheet.rowId === "limit:codex", "Alt+Up moves it up: " + JSON.stringify(list))
      key(settingsSheet, Qt.Key_Down, Qt.AltModifier, "")
      check(C, svc.settings.limitsShown[svc.settings.limitsShown.length - 1] === "codex", "Alt+Down moves it back")
      var check0 = findAll(settingsSheet, function (i) { return i.captionTone !== undefined && i.visible && i.text === "Codex" })
      check(C, check0.length === 1 && check0[0].checked === true, "a box per source")
      settingsSheet.cursor = settingsSheet.rowIds.indexOf("limitsShown")
      key(settingsSheet, Qt.Key_Left)
      check(C, svc.settings.limitsShown === "auto" && settingsSheet.limitsCustom === false, "Left goes back to Auto")
    })

    // session_sheet_cursor_pi_filters_and_pi_path
    C = "session_sheet_cursor_pi_filters_and_pi_path"
    step(C, function (C) {
      root.notices = []
      sessionSheet.open({ harness: "pi", cwd: "/home/tester/proj/api" })
      var ls = last("loadSessions")
      check(C, ls !== null && ls.args[0] === "" && ls.args[1] === "/home/tester/proj/api", "reads sessions for the draft's folder: " + JSON.stringify(ls ? ls.args : null))
      check(C, sessionSheet.filters.indexOf("cursor") > 0 && sessionSheet.filters.indexOf("pi") > 0, "Cursor and Pi filters")
      check(C, sessionSheet.filter === "pi" && sessionSheet.rows.length === 2 && sessionSheet.rows.every(function (r) { return r.harness === "pi" }), "Pi rows only")
      check(C, sessionSheet.piLine === "Pi sessions are listed for ~/proj/api only. Type a folder path to list another.", "Pi folder line: " + sessionSheet.piLine)
      check(C, sessionSheet.cursor === 1, "cursor on the newest")
      key(sessionSheet, Qt.Key_Return)
      check(C, lastSession && lastSession.harness === "pi" && lastSession.mode === "resume" && lastSession.sessionPath === root.piPath, "the pick carries the session file")
      check(C, compose.target.sessionPath === root.piPath && compose.harness === "pi", "the draft stores it")
      sessionSheet.open({ harness: "pi", cwd: "/home/tester/proj/api" })
      key(sessionSheet, Qt.Key_Down)
      var before = lastSession
      key(sessionSheet, Qt.Key_Return)
      check(C, lastSession === before && lastNotice().text === "That Pi session has no file to resume. Pick another, or start a new session.", "a Pi row without a file is refused")
      key(sessionSheet, Qt.Key_Left)
      check(C, sessionSheet.filter === "cursor" && sessionSheet.rows.length === 1 && sessionSheet.rows[0].canFork === false, "Cursor filter")
      check(C, sessionSheet.piLine === "", "no Pi line under the Cursor filter")
      key(sessionSheet, Qt.Key_Return)
      check(C, lastSession.harness === "cursor" && lastSession.sessionPath === null && compose.target.sessionPath === null, "Cursor picks carry no path")
      sessionSheet.open({ harness: "pi", cwd: "/home/tester/proj/api" })
      typeInto(sessionSheet, "~/proj/web")
    })
    step(C, function (C) {
      var ls = last("loadSessions")
      check(C, ls.args[1] === "/home/tester/proj/web", "a typed folder lists Pi sessions there: " + JSON.stringify(ls.args))
      sessionSheet.close()
    }, function () { var l = last("loadSessions"); return l && l.args[1] === "/home/tester/proj/web" })

    // compose_gated_cursor_arm_disabled
    C = "compose_gated_cursor_arm_disabled"
    step(C, function (C) {
      freshCompose("cursor")
      editor.text = "Audit the dependencies"
    })
    waitPreview(C)
    step(C, function (C) {
      check(C, compose.gateCode === "harness_gated", "gate code from the preview: " + compose.gateCode)
      check(C, compose.gateLine === "Cursor support is waiting for a one-time check.", "gate line: " + compose.gateLine)
      var line = find(composeView, function (i) { return i.text === compose.gateLine && i.visible })
      check(C, line !== null && String(line.color) === String(theme.warnInk), "shown in the warning ink")
      check(C, armButton.enabled === false && saveButton.enabled === true, "Arm off, Save draft on")
      check(C, compose.argvDisplay.indexOf("cursor-agent -p --output-format stream-json --mode ask --sandbox enabled --workspace") === 0, "Will run still shows the command")
      var arms = calls("createAndArm").length
      root.notices = []
      key(compose, Qt.Key_Return, Qt.ControlModifier, "")
      check(C, calls("createAndArm").length === arms && lastNotice().text === "Cursor support is waiting for a one-time check." && lastNotice().kind === "warn", "Ctrl+Enter says why and arms nothing")
      check(C, Compose.paidErrorText("cursor_untrusted") === "Cursor does not trust this folder yet. Open cursor-agent in this folder once and choose Trust this workspace.", "untrusted sentence")
      compose.saveDraft()
      check(C, last("saveDraft") !== null && last("saveDraft").args[0].harness === "cursor", "a Cursor draft can be saved")
    })

    // compose_pi_slash_prompt_error_shown
    C = "compose_pi_slash_prompt_error_shown"
    step(C, function (C) {
      freshCompose("pi")
      compose.applyModel({ harness: "pi", provider: "openai-codex", model: "gpt-5.5" })
      editor.text = "  /review the diff"
    })
    waitPreview(C)
    step(C, function (C) {
      var sentence = "Pi reads a prompt that starts with / as a command. Start it with a word."
      check(C, compose.piSlashPrompt === true && editor.problemText === sentence, "the editor says it: " + editor.problemText)
      var shown = find(editor, function (i) { return i.text === sentence && i.visible })
      check(C, shown !== null && String(shown.color) === String(theme.warnInk), "under the prompt, warning ink")
      check(C, armButton.enabled === false, "Arm off")
      var arms = calls("createAndArm").length
      var saves = calls("saveDraft").length
      compose.setSection(2)
      key(compose, Qt.Key_Return, Qt.ControlModifier, "")
      check(C, calls("createAndArm").length === arms && lastNotice().text === sentence && compose.section === 0, "Ctrl+Enter refuses and returns to the prompt")
      compose.saveDraft()
      check(C, calls("saveDraft").length === saves, "not saved either")
      editor.text = "Review the diff, then /summarise"
      check(C, compose.piSlashPrompt === false && editor.problemText === "" && armButton.enabled === true, "a prompt starting with a word is fine")
      compose.setHarness("claude")
      editor.text = "/review"
      check(C, compose.piSlashPrompt === false && editor.problemText === "", "only Pi reads a leading slash as a command")
    })
  }
}
QML

names_js="[$(printf '"%s",' $CASES | sed 's/,$//')]"
sed -i "s|__CASES__|$names_js|" "$W/Q5Smoke.qml"

out="$(cd "$W" && timeout 120 "$QML" -I "$W" Q5Smoke.qml 2>&1)"; rc=$?
[ "$rc" -eq 0 ] || no "engine run" "rc=$rc $(printf '%s' "$out" | grep -vE 'CASE ' | head -4 | tr '\n' ' ')"
for c in $CASES; do
  line="$(printf '%s\n' "$out" | grep -E "CASE $c (ok|FAIL)" | head -1)"
  if printf '%s' "$line" | grep -qE "CASE $c ok"; then ok "$c"
  elif [ -n "$line" ]; then no "$c" "$(printf '%s' "$line" | sed "s/^.*CASE $c FAIL //" | cut -c1-400)"
  else no "$c" "no result line"; fi
done

# An engine warning from a Q5 file or from this suite's own harness is a defect. Warnings
# from other owners' files are shown as a note only.
q5re='(ComposeView|PromptEditor|SendToSection|WhenControl|OptionsSection|ArgvLine|SessionSheet|SettingsSheet|ModelSheet|CheckRow|AgentMark|Compose\.js|Tint\.js|Q5Smoke)\.(qml|js)'
allwarn="$(printf '%s\n' "$out" | grep -E 'Warning|TypeError|ReferenceError|Unable to assign|Cannot read|is not a function|Binding loop|is not a type|preview saw prompt' || true)"
mine="$(printf '%s\n' "$allwarn" | grep -E "$q5re|preview saw prompt" || true)"
others="$(printf '%s\n' "$allwarn" | grep -vE "$q5re|preview saw prompt" | grep -v '^$' || true)"
[ -n "$others" ] && echo "  note warnings from other owners' files: $(printf '%s' "$others" | head -3 | tr '\n' ' ')"
if [ -z "$mine" ]; then ok "and the engine reported no warnings in Q5 files"
else no "and the engine reported no warnings in Q5 files" "$(printf '%s' "$mine" | head -6 | tr '\n' ' ')"; fi

printf '\n%d passed' "$pass"
[ "$fail" -gt 0 ] && printf ', %d FAILED' "$fail"
printf '\n'
[ "$fail" -eq 0 ]
