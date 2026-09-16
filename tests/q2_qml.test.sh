#!/bin/bash
# tests/q2_qml.test.sh: Panel.qml and the Compose view with its sheets (owner Q2).
#
# 1. qmllint (Qt 6) on every Q2 file, zero warnings. The shell's own qs.Commons and
#    qs.Ui are linked in; "missing-property" is off because the shell types several
#    runtime objects (Style.font, bar) as plain QtObject.
# 2. An offscreen smoke run in a real Qt 6 engine: Panel with a stand-in service that
#    has the whole public API (CONTRACT 5.1 and 5.2), stand-ins for the shell
#    singletons and for the Queue/History/Shift components, driven through
#    Panel.routeKey the way the key catcher drives it.
#
# Stand-ins answer synchronously and record every call. No helper, no process, no
# state folder: nothing here touches the machine.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QML=/usr/lib/qt6/bin/qml
QMLLINT=/usr/lib/qt6/bin/qmllint
SHELL_DIR=/usr/share/omarchy/shell

Q2_FILES="Panel.qml components/ComposeView.qml components/PromptEditor.qml components/SendToSection.qml
components/WhenControl.qml components/OptionsSection.qml components/SessionSheet.qml components/SettingsSheet.qml
components/ModelSheet.qml components/SignInSheet.qml"

# Offscreen, so no window reaches the desktop; console output on stderr, so the
# warning check below sees it.
export QT_QPA_PLATFORM=offscreen
export QT_FORCE_STDERR_LOGGING=1

pass=0; fail=0
ok() { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
no() { printf '  FAIL %s\n         %s\n' "$1" "$2"; fail=$((fail+1)); }

T="$(mktemp -d)" || exit 2
trap 'rm -rf "$T"' EXIT INT TERM

# ---------------------------------------------------------------- lint

echo "=== qmllint (Q2 files) ==="
if [ -x "$QMLLINT" ] && [ -d "$SHELL_DIR/Commons" ]; then
  mkdir -p "$T/lint/import" "$T/lint/plugin/components" "$T/lint/plugin/lib"
  ln -s "$SHELL_DIR" "$T/lint/import/qs"
  cp "$REPO"/*.qml "$T/lint/plugin/"
  cp "$REPO"/components/*.qml "$T/lint/plugin/components/"
  cp "$REPO"/lib/*.js "$T/lint/plugin/lib/"
  for v in QueueView HistoryView; do
    [ -f "$T/lint/plugin/components/$v.qml" ] || cat > "$T/lint/plugin/components/$v.qml" <<'QML'
import QtQuick
Item {
  required property var theme
  required property var service
  property bool active: false
  property string home: ""
  readonly property string hints: ""
  signal noticeRequested(string text, string kind, var undo)
  signal editRequested(string jobId, string mode)
  signal sheetRequested(string sheet, var args)
  signal viewRequested(string view, string jobId)
  signal composeRequested(var trigger)
  function handleKey(event) { return false }
  function activate() {}
  function focusJob(id) {}
  function flashJob(id) {}
}
QML
  done
  [ -f "$T/lint/plugin/components/ShiftSheet.qml" ] || cat > "$T/lint/plugin/components/ShiftSheet.qml" <<'QML'
import QtQuick
Item {
  required property var theme
  required property var service
  property bool active: false
  readonly property string hints: ""
  signal closed()
  signal shifted(var ids, int deltaSec)
  signal noticeRequested(string text, string kind, var undo)
  function handleKey(event) { return false }
  function open(args) {}
  function close() {}
}
QML
  for f in $Q2_FILES; do
    out="$(cd "$T/lint/plugin" && "$QMLLINT" -I "$T/lint/import" --missing-property disable "$f" 2>&1)"
    warn="$(printf '%s\n' "$out" | grep -E '^Warning' || true)"
    if [ -z "$warn" ]; then ok "qmllint $f"
    else no "qmllint $f" "$(printf '%s' "$warn" | head -4 | tr '\n' ' ')"; fi
  done
else
  echo "  skip qmllint (no $QMLLINT or no $SHELL_DIR)"
fi

# ---------------------------------------------------------------- smoke

echo "=== Panel and Compose in a Qt 6 engine ==="
if [ ! -x "$QML" ]; then
  echo "  skip the smoke run (no Qt 6 qml runtime at $QML)"
  printf '\n%d passed\n' "$pass"
  [ "$fail" -eq 0 ]; exit
fi

W="$T/smoke"
mkdir -p "$W/lib" "$W/components" "$W/qs/Commons" "$W/qs/Ui" "$W/Quickshell"
cp "$REPO"/lib/*.js "$W/lib/"
cp "$REPO"/components/*.qml "$W/components/"
cp "$REPO"/Panel.qml "$W/"

# The Queue, History and Shift components belong to another owner and are exercised
# by their own suite; here they are stand-ins with the CONTRACT 6.6 surface, so a
# half-built neighbour cannot fail this one.
for v in QueueView HistoryView; do
cat > "$W/components/$v.qml" <<'QML'
import QtQuick
Item {
  id: view
  required property var theme
  required property var service
  property bool active: false
  property string home: ""
  property string focused: ""
  property int activations: 0
  readonly property string hints: "Type to search  ·  Esc close"
  signal noticeRequested(string text, string kind, var undo)
  signal editRequested(string jobId, string mode)
  signal sheetRequested(string sheet, var args)
  signal viewRequested(string view, string jobId)
  signal composeRequested(var trigger)
  function handleKey(event) { return false }
  function activate() { view.activations++ }
  function focusJob(id) { view.focused = id }
  function flashJob(id) {}
  function undo() { return false }
}
QML
done
cat > "$W/components/ShiftSheet.qml" <<'QML'
import QtQuick
Item {
  id: sheet
  required property var theme
  required property var service
  property bool active: false
  readonly property string hints: "Enter apply  ·  Esc back"
  signal closed()
  signal shifted(var ids, int deltaSec)
  signal noticeRequested(string text, string kind, var undo)
  function handleKey(event) { return false }
  function open(args) {}
  function close() { sheet.closed() }
}
QML

cat > "$W/Quickshell/qmldir" <<'EOF'
module Quickshell
singleton Quickshell 1.0 Quickshell.qml
EOF
cat > "$W/Quickshell/Quickshell.qml" <<'EOF'
pragma Singleton
import QtQuick
QtObject {
  function env(name) { return name === "HOME" ? "/home/tester" : "" }
}
EOF

cat > "$W/qs/Commons/qmldir" <<'EOF'
module qs.Commons
singleton Style 1.0 Style.qml
singleton Util 1.0 Util.qml
singleton Border 1.0 Border.qml
singleton Color 1.0 Color.qml
EOF
cat > "$W/qs/Commons/Style.qml" <<'EOF'
pragma Singleton
import QtQuick
QtObject {
  property int cornerRadius: 0
  function space(px) { return Math.round(px) }
  function spaceReal(px) { return px }
  function hoverFillFor(fg, accent) { return Qt.rgba(0.5, 0.5, 0.5, 0.08) }
  readonly property QtObject spacing: QtObject {
    readonly property int sm: 4
    readonly property int lg: 8
    readonly property int controlHeight: 28
    readonly property int controlPaddingX: 10
    readonly property int rowPaddingX: 12
    readonly property int popupPadding: 14
  }
  readonly property QtObject font: QtObject {
    readonly property string family: "monospace"
    readonly property int caption: 10
    readonly property int bodySmall: 11
    readonly property int body: 12
    readonly property int subtitle: 13
    readonly property int title: 14
    readonly property int heading: 16
    readonly property int display: 24
    readonly property int iconSmall: 11
    readonly property int icon: 14
  }
}
EOF
cat > "$W/qs/Commons/Util.qml" <<'EOF'
pragma Singleton
import QtQuick
QtObject {
  function alpha(c, opacity) {
    if (!c) return Qt.rgba(0, 0, 0, opacity)
    if (typeof c === "string") c = Qt.color(c)
    return Qt.rgba(c.r, c.g, c.b, Math.max(0, Math.min(1, opacity)))
  }
  function wheelSteps(accumulator, delta) {
    var total = accumulator + Math.max(-120, Math.min(120, delta))
    var steps = total < 0 ? Math.ceil(total / 120) : Math.floor(total / 120)
    return { steps: steps, remainder: total - steps * 120 }
  }
  function editsFilter(event, text) {
    if (!text) return false
    if (event.modifiers & (Qt.AltModifier | Qt.MetaModifier)) return false
    if (event.key === Qt.Key_U) return event.modifiers === Qt.ControlModifier
    return event.key === Qt.Key_Backspace
  }
  function editedFilter(event, text) {
    if (event.key === Qt.Key_U) return ""
    if (event.modifiers & Qt.ControlModifier) return text.replace(/\s+$/, "").replace(/\S+$/, "")
    return text.slice(0, -1)
  }
}
EOF
cat > "$W/qs/Commons/Border.qml" <<'EOF'
pragma Singleton
import QtQuick
QtObject {
  function none() { return { color: "transparent", widths: { top: 0, right: 0, bottom: 0, left: 0 } } }
  function flat(color, width) { return { color: color, widths: { top: width, right: width, bottom: width, left: width } } }
  function controlSpec(state, foreground, accent) { return flat(state === "normal" ? foreground : accent, 1) }
}
EOF
cat > "$W/qs/Commons/Color.qml" <<'EOF'
pragma Singleton
import QtQuick
QtObject {
  readonly property color foreground: "#c0caf5"
  readonly property color background: "#1a1b26"
  readonly property color accent: "#7aa2f7"
  readonly property color urgent: "#f7768e"
  readonly property QtObject popups: QtObject {
    readonly property color background: "#1a1b26"
    readonly property color text: "#c0caf5"
    readonly property color border: "#7aa2f7"
  }
}
EOF
cat > "$W/qs/Ui/qmldir" <<'EOF'
module qs.Ui
BorderSurface 1.0 BorderSurface.qml
CursorSurface 1.0 CursorSurface.qml
Panel 1.0 Panel.qml
KeyboardPanel 1.0 KeyboardPanel.qml
EOF
cat > "$W/qs/Ui/BorderSurface.qml" <<'EOF'
import QtQuick
Rectangle {
  property var borderSpec: null
  border.width: borderSpec && borderSpec.widths ? borderSpec.widths.top : 0
  border.color: borderSpec ? borderSpec.color : "transparent"
}
EOF
cat > "$W/qs/Ui/CursorSurface.qml" <<'EOF'
import QtQuick
Rectangle {
  property bool hasCursor: false
  property bool current: false
  property color foreground: "white"
  property color accent: "blue"
  color: hasCursor ? Qt.rgba(accent.r, accent.g, accent.b, 0.1) : "transparent"
}
EOF
cat > "$W/qs/Ui/Panel.qml" <<'EOF'
import QtQuick
Item {
  id: base
  property QtObject bar: null
  property string moduleName: ""
  property var settings: ({})
  property string ipcTarget: ""
  property bool manageIpc: true
  property bool popoutSwitching: false
  property bool popoutSwitchClosing: false
  property bool _open: false
  readonly property bool opened: base._open
  function open() { base._open = true }
  function close() { base._open = false }
  function toggle() { base._open = !base._open }
  function closeForPopoutSwitch() { base.close() }
}
EOF
cat > "$W/qs/Ui/KeyboardPanel.qml" <<'EOF'
import QtQuick
Item {
  id: kp
  property var anchorItem: null
  property var owner: null
  property QtObject bar: null
  property bool open: false
  property Item focusTarget: null
  property int contentWidth: 280
  property int contentHeight: 200
  default property alias contentItem: holder.children
  function fittedContentWidth(w) { return Math.round(Number(w)) }
  function fittedContentHeight(h, cap) { return Math.round(Number(h)) }
  width: kp.contentWidth
  height: kp.contentHeight
  visible: kp.open
  onOpenChanged: if (kp.open && kp.focusTarget) Qt.callLater(function () { kp.focusTarget.forceActiveFocus() })
  Item { id: holder; anchors.fill: parent; anchors.margins: 14 }
}
EOF

cat > "$W/Q2Smoke.qml" <<'QML'
import QtQuick
import "lib/Edition.js" as Edition
import "lib/Model.js" as Model

Item {
  id: root
  width: 1200
  height: 900

  property int n: 0
  property int firstFail: 0
  property int stage: 0
  // Ticks a step has waited for a short panel timer (the default view uses 60 ms).
  property int waitTicks: 0
  property int waits: 0
  readonly property string digestA: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  readonly property string jobId: "0123456789abcdef"

  function check(c, label) {
    root.n++
    if (!c) {
      console.warn("check " + root.n + " failed: " + label)
      if (root.firstFail === 0) root.firstFail = root.n
    }
  }
  function ev(k, mods, text) { return { key: k, modifiers: mods || 0, text: text || "", accepted: false } }
  function press(k, mods, text) { var e = root.ev(k, mods, text); panel.routeKey(e); return e }
  function ctrl(k) { return root.press(k, Qt.ControlModifier, "") }
  function typeDigits(s) { for (var i = 0; i < s.length; i++) root.press(Qt.Key_0 + Number(s[i]), 0, s[i]) }
  function typeText(s) { for (var i = 0; i < s.length; i++) root.press(0, 0, s[i]) }
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
  function calls(name) { return svc.calls.filter(function (c) { return c.name === name }) }
  function last(name) { var l = root.calls(name); return l.length ? l[l.length - 1] : null }

  QtObject {
    id: svc
    property bool ready: true
    property string setupProblem: ""
    property var edition: ({ pluginId: Edition.PLUGIN_ID })
    property var levels: [
      { id: "plan", label: "Plan", "default": true, summary: "Reads and plans only.", defaultMaxTurns: 15,
        harness: { claude: { caption: "PLAN-CLAUDE" }, opencode: { caption: "PLAN-OPENCODE" },
                   codex: { caption: "PLAN-CODEX" }, gemini: { caption: "PLAN-GEMINI" } } },
      { id: "unattended", label: "Unattended", "default": false, summary: "Runs without you.", defaultMaxTurns: 30,
        harness: { claude: { caption: "UNATTENDED-CLAUDE" }, opencode: { caption: "UNATTENDED-OPENCODE" },
                   codex: { caption: "UNATTENDED-CODEX" }, gemini: { caption: "UNATTENDED-GEMINI" } } }
    ]
    property var harnesses: []
    property var caps: ({ promptBytes: 65536, labelChars: 40, maxTurns: [1, 200], budgetUsd: [0.1, 100.0],
                          runtimeSec: [300, 14400], marginSec: [60, 540], horizonSec: 691200, uiMinLeadSec: 60 })
    property var jobs: []
    property var jobsById: ({})
    property var listMeta: ({ killSwitch: false, enabledInShell: true, linger: null, reconciledAt: null, nowMs: Date.now() })
    property var usage: ({
      ok: true, nowMs: Date.now(),
      claude: { available: true, fetchedAtMs: Date.now(), ageSec: 5, stale: false, status: "", tierLabel: "",
                windows: [ { label: "Session (5-hour)", kind: "session", percent: 0.22,
                             resetsAt: Math.floor(Date.now() / 1000) + 7200, title: null } ] },
      codex: { available: false, updatedAtMs: null, stale: true, status: "", windows: [] }
    })
    // Usage v2 providers (the reset chip reads these) and the models verb's answers.
    property var providers: [
      { id: "claude", name: "Claude", readable: true, relevant: true, stale: false, source: "record", headlineKey: "claude:5-hour",
        harnesses: ["claude"], windows: [ { key: "claude:5-hour", kind: "session", shortLabel: "5-hour", percent: 0.22,
          resetsAt: Math.floor(Date.now() / 1000) + 7200, sliding: false, bindable: true, source: "record" } ] }
    ]
    property var models: ({})
    property bool loadingModels: false
    property var agents: ({
      claude: { harness: "claude", available: true, enabled: true, loggedIn: null, reason: null },
      opencode: { harness: "opencode", available: true, enabled: true, loggedIn: null, reason: null },
      codex: { harness: "codex", available: true, enabled: false, loggedIn: false, reason: "not_logged_in" },
      gemini: { harness: "gemini", available: true, enabled: true, loggedIn: null, reason: null }
    })
    property var sessions: ({})
    property var settings: ({ schemaVersion: 1, defaultHarness: "claude", defaultLevel: "plan", resetMarginSec: 120,
                              eveningTime: "23:00", morningTime: "07:00", notify: "all", motion: "full" })
    property double nowMs: Date.now()
    property bool loadingJobs: false
    property bool loadingUsage: false
    property bool loadingAgents: false
    property bool loadingSessions: false
    property bool loadingSettings: false
    property bool busy: false
    property var lastError: null
    property var nextJob: null
    property int armedCount: 0
    property int runningCount: 0
    property int attentionCount: 0
    property bool anyRunning: false
    property int viewers: 0
    property string barLabel: "Next run"
    property double attentionSeenAt: 0
    property var calls: []

    signal notice(string text, string kind)
    signal jobsUpdated()

    function rec(name, args) { svc.calls = svc.calls.concat([{ name: name, args: args }]) }
    function refresh() { svc.rec("refresh", []) }
    function refreshUsage() { svc.rec("refreshUsage", []) }
    function refreshAgents(checkLogin) { svc.rec("refreshAgents", [checkLogin]) }
    function loadModels(harness, refresh) {
      svc.rec("loadModels", [harness, refresh === true])
      var m = {}
      for (var k in svc.models) m[k] = svc.models[k]
      m[harness] = { ok: true, harness: harness, source: "cli", fetchedAt: Math.floor(Date.now() / 1000), reason: null, truncated: false,
        models: harness !== "opencode" ? [] : [
          { id: "opencode/big-pickle", label: "Big Pickle", group: "opencode", "default": false, billing: "zen_free" },
          { id: "anthropic/claude-sonnet-4", label: "Claude Sonnet 4", group: "anthropic", "default": true, billing: "anthropic" } ] }
      svc.models = m
    }
    function setLimitsShown(value, cb) { svc.setSettings({ limitsShown: value }, cb) }
    function loadSessions(harness) {
      svc.rec("loadSessions", [harness])
      var now = Date.now()
      svc.sessions = { all: {
        ok: true, harness: null, nowMs: now,
        sessions: [
          { harness: "claude", id: "3f2a0c19-0000-4000-8000-000000000001", title: "api-refactor", cwd: "/home/tester/proj/api",
            updatedAtMs: now - 7200000, messages: 148, canFork: true },
          { harness: "opencode", id: "ses_abcdefgh1234", title: "client regen", cwd: "/home/tester/proj/web",
            updatedAtMs: now - 3600000, messages: 36, canFork: true },
          { harness: "claude", id: "3f2a0c19-0000-4000-8000-000000000002", title: "docs pass", cwd: "/home/tester/proj/docs",
            updatedAtMs: now - 86400000 * 3, messages: 12, canFork: true }
        ],
        counts: { claude: 2, opencode: 1, codex: 0, gemini: 0 },
        truncated: { claude: false, opencode: true, codex: false, gemini: false },
        errors: { claude: null, opencode: null, codex: null, gemini: "too_large" },
        limitDays: 90, perHarnessCap: 50 } }
    }
    function getJob(id, cb) {
      svc.rec("getJob", [id])
      cb({ ok: true, prompt: "stored prompt", promptAvailable: true, preview: null, runs: [], lastLines: [],
           job: { id: id, label: "stored", harness: "opencode", level: "unattended", model: null,
                  target: { mode: "resume", sessionId: "ses_abcdefgh1234", newSessionId: null, cwd: "/home/tester/proj/web",
                            title: "client regen", allowNonGit: false },
                  limits: { maxTurns: 30, budgetUsd: 5, runtimeSec: 5400 },
                  trigger: { kind: "at", fireAt: Math.floor(Date.now() / 1000) + 7200, delaySec: null, marginSec: 120, weeklyPolicy: "defer" },
                  state: { status: "draft" } } })
    }
    function preview(draft, cb) {
      svc.rec("preview", [draft])
      if (draft.prompt !== undefined && draft.prompt !== "") console.warn("preview saw prompt")
      cb({ ok: true, preview: {
        harness: draft.harness, level: draft.level, mode: draft.target.mode,
        display: Model.cliName(draft.harness) + " --level " + draft.level + " <stdin>",
        argv: [], env: {}, cwd: draft.target.cwd || "/home/tester/proj", binary: "/home/tester/.local/bin/" + draft.harness,
        link: "", version: null, levelCaption: "PREVIEW-" + draft.level, commandDigest: root.digestA,
        fireAt: null, immediate: draft.trigger.kind === "now", fireAtHint: null, fireAtError: null, warnings: [] } })
    }
    function createAndArm(draft, cb) {
      svc.rec("createAndArm", [draft])
      cb({ ok: true, id: root.jobId, status: "armed", fireAt: Math.floor(Date.now() / 1000) + 3600, unit: "u",
           immediate: draft.trigger.kind === "now", hint: null, job: {} })
    }
    function saveDraft(draft, cb) {
      svc.rec("saveDraft", [draft])
      cb({ ok: true, id: "fedcba9876543210", job: {}, digest: root.digestA, commandDigest: root.digestA })
    }
    function arm(id, digest, cb) { svc.rec("arm", [id, digest]); if (cb) cb({ ok: true, id: id }) }
    function runNow(id, digest, cb) { svc.rec("runNow", [id, digest]); if (cb) cb({ ok: true, id: id }) }
    function disarm(id, cb) { svc.rec("disarm", [id]); if (cb) cb({ ok: true, id: id, verified: true, job: {} }) }
    function deleteJob(id, cb) { svc.rec("deleteJob", [id]); if (cb) cb({ ok: true, id: id }) }
    function cancelAll(cb) { svc.rec("cancelAll", []); if (cb) cb({ ok: true, disarmed: [root.jobId], stoppedUnits: [], verified: true }) }
    function reschedule(id, epochSec, cb) { svc.rec("reschedule", [id, epochSec]) }
    function swap(a, b, cb) { svc.rec("swap", [a, b]) }
    function shift(ids, deltaSec, cb) { svc.rec("shift", [ids, deltaSec]) }
    function setSettings(obj, cb) {
      svc.rec("setSettings", [obj])
      var s = {}
      for (var k in svc.settings) s[k] = svc.settings[k]
      for (var j in obj) s[j] = obj[j]
      svc.settings = s
      if (cb) cb({ ok: true, settings: s })
    }
    function copyResume(id, cb) { svc.rec("copyResume", [id]) }
    function reconcileNow(cb) { svc.rec("reconcileNow", []) }
    function viewerOpened() { svc.rec("viewerOpened", []); svc.viewers++ }
    function viewerClosed() { svc.rec("viewerClosed", []); svc.viewers-- }
    function levelFor(id) { for (var i = 0; i < svc.levels.length; i++) if (svc.levels[i].id === id) return svc.levels[i]; return null }
    function agentFor(h) { return svc.agents[h] || null }
  }

  Panel {
    id: panel
    anchorItem: root
    service: svc
  }

  readonly property var compose: root.find(panel, function (i) { return i.draftKey !== undefined && i.editorFocused !== undefined })
  readonly property var editor: root.find(panel, function (i) { return i.counterText !== undefined })
  readonly property var when: root.find(panel, function (i) { return i.plusChips !== undefined })
  readonly property var options: root.find(panel, function (i) { return i.valueRows !== undefined })
  readonly property var sendTo: root.find(panel, function (i) { return i.modeKeys !== undefined })
  readonly property var sessionSheet: root.find(panel, function (i) { return i.queryPath !== undefined })
  readonly property var settingsSheet: root.find(panel, function (i) { return i.rowIds !== undefined && i.cancelPress !== undefined })
  readonly property var signInSheet: root.find(panel, function (i) { return i.reasonKey !== undefined && i.recheck !== undefined })
  readonly property var noticeRow: root.find(panel, function (i) { return i.undoAvailable !== undefined && i.show !== undefined })
  readonly property var queue: root.find(panel, function (i) { return i.focused !== undefined && i.undo !== undefined && i.visible && i.hints.indexOf("search") >= 0 })

  // Keys go to the panel's catcher, not to the editor, so the scripted keys below
  // reach Panel.routeKey the way the catcher's own handler would call it.
  function unfocus() { if (root.compose) root.compose.leaveEditor() }

  // Re-runs the current step on the next tick until ok holds, at most 10 times, so a
  // loaded machine cannot fail a check on timer timing alone. Use it only as the first
  // statement of a step, before anything with side effects.
  function waitFor(ok) {
    if (ok || root.waitTicks >= 10) { root.waitTicks = 0; return false }
    root.waitTicks++
    root.stage--
    return true
  }

  Timer {
    interval: 450
    running: true
    repeat: true
    onTriggered: {
      try { root.step() } catch (err) { console.warn("threw at stage " + root.stage + ": " + err + "\n" + err.stack); root.firstFail = 250; Qt.exit(250) }
    }
  }

  function step() {
    var s = root.stage++
    if (s === 0) {
      root.check(!!root.compose && !!root.editor && !!root.when && !!root.options && !!root.sendTo, "Q2 components found")
      root.check(!!root.sessionSheet && !!root.settingsSheet && !!root.noticeRow, "sheets and notice row found")
      panel.open()
      root.check(root.calls("viewerOpened").length === 1, "viewerOpened on open")
      return
    }
    if (s === 1) {
      root.unfocus()
      root.check(panel.view === "compose", "default view with no jobs is Compose")
      root.check(panel.contentState === "ready", "content ready")
      root.check(panel.theme.inks.claude !== undefined && panel.theme.textTarget === 4.5, "theme tokens")
      root.ctrl(Qt.Key_2); root.check(panel.view === "queue", "Ctrl+2 queue")
      root.ctrl(Qt.Key_3); root.check(panel.view === "history", "Ctrl+3 history")
      root.ctrl(Qt.Key_PageDown); root.check(panel.view === "compose", "Ctrl+PgDn wraps to compose")
      root.unfocus()
      root.ctrl(Qt.Key_PageUp); root.check(panel.view === "history", "Ctrl+PgUp wraps to history")
      root.ctrl(Qt.Key_1); root.check(panel.view === "compose", "Ctrl+1 compose")
      root.unfocus()
      root.ctrl(Qt.Key_Comma)
      root.check(panel.sheet === "settings" && panel.footerText.indexOf("setting") >= 0, "Ctrl+, opens settings with its hints")
      root.press(Qt.Key_2, Qt.ControlModifier, "")
      root.check(panel.view === "compose" && panel.sheet === "settings", "a sheet is modal: Ctrl+2 swallowed")
      root.press(Qt.Key_Escape)
      root.check(panel.sheet === "" && panel.opened, "Esc closes the sheet, not the panel")

      // Prompt: typing with the section cursor focuses the editor and inserts the key.
      root.unfocus()
      root.press(Qt.Key_H, 0, "H")
      root.check(root.compose.draft.prompt === "H", "first key reaches the prompt")
      root.unfocus()
      root.editor.insertText(new Array(70001).join("x"))
      root.check(root.editor.text === "H", "an edit past 64 KiB is refused whole")
      root.check(root.noticeRow.text === "The prompt is longer than 64 KiB.", "cap notice")
      root.check(root.editor.bytes === 1, "byte counter")

      // Tab walks the sections.
      root.press(Qt.Key_Tab)
      root.check(root.compose.section === 1 && panel.footerText.indexOf("agent") >= 0, "Tab to Send to")
      root.press(Qt.Key_Right)
      root.check(root.compose.harness === "opencode", "Right picks OpenCode")
      root.press(Qt.Key_Right)
      root.check(root.compose.harness === "gemini", "Right skips the disabled Codex")
      root.check(JSON.stringify(root.sendTo.modes) === JSON.stringify(["resume", "new"]), "no fork for Gemini")
      root.check(root.sendTo.agentState("codex").reasonKey === "not_signed_in" && !root.sendTo.agentState("codex").enabled, "Codex state, no words")
      root.check(root.sendTo.agentState("gemini").reasonKey === "sign_in_unknown" && root.sendTo.agentState("gemini").warn
                 && root.sendTo.agentState("gemini").enabled, "Gemini sign-in unknown, still pickable")
      root.check(root.sendTo.agentState("claude").ready && root.sendTo.agentState("claude").reasonKey === "", "Claude ready")

      // The chips carry a mark, a short name and one alert glyph; a chip that is not
      // ready raises the sign-in sheet instead of saying the reason in the row.
      var codexCell = root.find(root.sendTo, function (i) { return i.agentState !== undefined && i.modelData === "codex" })
      var claudeChip = root.find(root.sendTo, function (i) { return i.pill === true && i.harness === "claude" })
      root.check(!!codexCell && !!claudeChip && claudeChip.text === "Claude", "short chip labels keep six agents on one line")
      var codexChip = root.find(codexCell, function (i) { return i.pill === true && i.harness === "codex" })
      root.check(!!codexChip && codexChip.text === "Codex" && codexChip.note === Model.GLYPH.alert, "one glyph, no words: " + codexChip.note)
      root.check(String(codexChip.noteColor) === String(panel.theme.warnInk), "the glyph is in the warning ink")
      var dimmedClick = root.find(codexCell, function (i) { return i.cursorShape === Qt.PointingHandCursor && i.hoverEnabled === false })
      root.check(!!dimmedClick, "a dimmed chip still answers a click")
      dimmedClick.clicked(null)
      root.check(panel.sheet === "signin" && root.signInSheet.harness === "codex", "a chip that is not ready raises the sign-in sheet")
      root.check(root.signInSheet.reasonKey === "not_signed_in" && !root.signInSheet.ready, "the sheet reads the chip's own state")
      root.check(root.signInSheet.command === "codex login", "the exact sign-in command: " + root.signInSheet.command)
      root.check(panel.footerText.indexOf("Enter check again") === 0, "sign-in hints: " + panel.footerText)
      var checked = root.calls("refreshAgents").length
      root.press(Qt.Key_Return)
      var asked = root.calls("refreshAgents")
      root.check(asked.length === checked + 1 && asked[asked.length - 1].args[0] === true, "Enter asks the service to check the sign-in again")
      root.check(panel.sheet === "signin", "and the sheet stays until an answer lands")
      // The helper answers that Codex is signed in now.
      var signedIn = {}
      for (var a in svc.agents) signedIn[a] = svc.agents[a]
      signedIn.codex = { harness: "codex", available: true, enabled: true, loggedIn: true, reason: null }
      svc.agents = signedIn
      root.check(panel.sheet === "" && root.noticeRow.text === "Codex is signed in.", "it closes and says so: " + root.noticeRow.text)
      root.check(root.sendTo.agentState("codex").ready && root.sendTo.agentState("codex").reasonKey === "", "the chip is plain again")
      // Back to a signed-out Codex for the rest of this suite.
      var signedOut = {}
      for (var b in svc.agents) signedOut[b] = svc.agents[b]
      signedOut.codex = { harness: "codex", available: true, enabled: false, loggedIn: false, reason: "not_logged_in" }
      svc.agents = signedOut
      root.press(Qt.Key_Left)
      root.press(Qt.Key_Left)
      root.check(root.compose.harness === "claude", "Left back to Claude")
      root.check(root.compose.argvDisplay.indexOf("Pick a session") === 0, "no preview before a target")
      root.press(Qt.Key_Return)
      root.check(panel.sheet === "session" && root.calls("loadSessions").length === 1, "Enter opens the session sheet and loads sessions")
      return
    }
    if (s === 2) {
      var sh = root.sessionSheet
      root.check(sh.rows.length === 2 && sh.cursor === 1, "filtered to Claude, cursor on the newest session")
      root.check(sh.boundLine === "Sessions older than 90 days are not listed.", "honest bound line: " + sh.boundLine)
      root.press(Qt.Key_Left)
      root.check(sh.filter === "all" && sh.rows.length === 3, "Left to All")
      root.check(sh.errorLine === "Gemini CLI sessions too large to list.", "per-agent error caption: " + sh.errorLine)
      root.check(sh.boundLine.indexOf("Only the newest 50 per agent are listed.") > 0, "truncation said")
      root.typeText("docs")
      root.check(sh.rows.length === 1 && sh.rows[0].title === "docs pass" && sh.cursor === 1, "type to filter")
      root.press(Qt.Key_Escape)
      root.check(sh.query === "" && panel.sheet === "session", "Esc clears the filter first")
      root.typeText("~/proj/new")
      root.check(sh.queryPath === "/home/tester/proj/new" && sh.cursor === 0, "a typed folder path points row 0 at it")
      root.press(Qt.Key_Backspace, 0, "")
      root.check(sh.query === "~/proj/ne", "Backspace edits the filter")
      root.press(Qt.Key_U, Qt.ControlModifier, "")
      root.check(sh.query === "", "Ctrl+U clears")
      root.press(Qt.Key_Down)
      root.check(sh.cursor === 2, "Down moves the cursor")
      root.press(Qt.Key_Up)
      root.check(sh.cursor === 1 && sh.rows[0].id === "ses_abcdefgh1234", "Up moves back to the newest session")
      root.press(Qt.Key_Return)
      root.check(panel.sheet === "" && root.compose.target.sessionId === "ses_abcdefgh1234", "Enter resumes the highlighted session")
      root.check(root.compose.harness === "opencode" && root.compose.target.mode === "resume", "picking sets agent and mode")
      root.check(root.compose.section === 1, "back on Send to")
      return
    }
    if (s === 3) {
      // The preview is debounced; a slow engine may reach this stage first.
      if (!root.compose.previewCurrent && root.waits < 20) { root.waits++; root.stage = 3; return }
      root.unfocus()
      var p = root.last("preview")
      root.check(p !== null && p.args[0].target.sessionId === "ses_abcdefgh1234", "debounced preview for the picked session")
      root.check(p !== null && p.args[0].prompt === undefined, "a preview is never handed the prompt")
      root.check(root.compose.argvDisplay === "opencode --level plan <stdin>", "Will run shows the helper display: " + root.compose.argvDisplay)
      root.press(Qt.Key_F)
      root.check(root.compose.target.mode === "fork", "f forks")
      root.press(Qt.Key_N)
      root.check(root.compose.target.mode === "new" && root.compose.target.cwd === "/home/tester/proj/web", "n: new session in the same folder")
      root.press(Qt.Key_R)
      root.check(root.compose.target.mode === "resume" && root.compose.target.sessionId === "ses_abcdefgh1234", "r: back to the remembered session")

      // When.
      root.press(Qt.Key_Tab)
      root.check(root.compose.section === 2, "Tab to When")
      // OpenCode without a model follows no reset, so the hints do not offer one.
      root.check(panel.footerText === "j/k ±5m  ·  Shift+J/K ±1h  ·  n now  ·  Ctrl+J/K ±1 day  ·  0-9 type a time  ·  Tab next  ·  Ctrl+Enter run now  ·  Esc close",
                 "When hints: " + panel.footerText)
      // The bound comes from the moment before the key, so a minute boundary that passes
      // between the nudge and this check cannot move it.
      var pressedAt = Date.now()
      root.press(Qt.Key_K)
      var t = root.compose.trigger
      root.check(t.kind === "at" && t.fireAt * 1000 >= Math.ceil((pressedAt + 60000) / 60000) * 60000 - 1000, "k from now: a clock time at least a minute away")
      root.check(new Date(t.fireAt * 1000).getMinutes() % 5 === 0 || t.fireAt * 1000 === root.when.earliestMs(),
                 "first nudge snaps to the 5-minute grid, or to the earliest time when that is later")
      var before = root.compose.trigger.fireAt
      root.press(Qt.Key_K, Qt.ShiftModifier, "K")
      root.check(root.compose.trigger.fireAt === before + 3600, "Shift+K +1 h")
      root.press(Qt.Key_J, Qt.ControlModifier, "")
      root.check(root.compose.trigger.fireAt * 1000 === root.when.earliestMs() && root.when.captionText.indexOf("Earliest is ") === 0,
                 "Ctrl+J -1 day into the past clamps to the earliest time and says so")
      // 14:05 less than a minute away is clamped to the earliest time, so near 14:05 type 18:05.
      var clockNow = new Date(), minsNow = clockNow.getHours() * 60 + clockNow.getMinutes()
      var typed = minsNow >= 14 * 60 + 2 && minsNow <= 14 * 60 + 5 ? "1805" : "1405"
      var typedClock = typed.slice(0, 2) + ":" + typed.slice(2)
      root.typeDigits(typed)
      root.check(root.when.typing && root.when.readoutWord === typedClock, "typed digits show in the readout")
      root.press(Qt.Key_Return)
      t = root.compose.trigger
      root.check(t.kind === "at" && Model.formatClock(t.fireAt * 1000) === typedClock, "Enter sets " + typedClock)
      root.press(Qt.Key_N)
      root.check(root.compose.trigger.kind === "now" && root.compose.isRunNow, "n: now")
      root.check(root.when.cursorStop === "now", "n moves the cursor to Now")
      root.press(Qt.Key_Right); root.press(Qt.Key_Right)
      root.check(root.when.cursorStop === "plus60", "cursor on +1h")
      root.press(Qt.Key_Return)
      root.press(Qt.Key_Return)
      t = root.compose.trigger
      root.check(t.kind === "in" && t.delaySec === 7200, "+1h twice is a 2 h delay")
      root.press(Qt.Key_R)
      root.check(root.compose.trigger.kind === "in" && root.when.captionText === "No reset applies to this agent. Pick a time instead.",
                 "r on OpenCode without a model has no reset to follow: " + root.when.captionText)
      // Claude Code follows its own 5-hour window.
      root.compose.setHarness("claude")
      root.compose.setSection(2)
      root.press(Qt.Key_R)
      t = root.compose.trigger
      root.check(t.kind === "claude_5h_reset" && t.marginSec === 120, "r binds to the Claude reset, +2m default")
      root.check(root.when.captionText === "follows Claude reset +2m", "follows line: " + root.when.captionText)
      root.press(Qt.Key_K)
      root.check(root.compose.trigger.marginSec === 180, "k while bound moves the buffer by a minute")
      for (var i = 0; i < 8; i++) root.press(Qt.Key_K)
      root.check(root.compose.trigger.marginSec === 540, "buffer stops at 9 minutes")
      root.check(root.when.captionText === "The next 5-hour window will start later.", "C4 warning: " + root.when.captionText)
      root.press(Qt.Key_K, Qt.ShiftModifier, "K")
      root.check(root.compose.trigger.kind === "at" && root.when.captionText === "Unbound from reset.", "Shift unbinds")
      // Back to the OpenCode session for the Options checks.
      root.compose.applySession({ harness: "opencode", mode: "resume", sessionId: "ses_abcdefgh1234", cwd: "/home/tester/proj/web", title: "client regen" })
      root.check(root.compose.harness === "opencode" && root.compose.trigger.kind === "at", "back on OpenCode, the clock time kept")
      root.compose.setSection(2)

      // Options.
      root.press(Qt.Key_Tab)
      root.check(root.compose.section === 3, "Tab to Options")
      root.check(root.options.caption === "PLAN-OPENCODE", "caption from the level table for this agent")
      root.check(JSON.stringify(root.options.rows) === JSON.stringify(["level", "paid", "runtime", "model"]), "no turns or budget for OpenCode")
      root.press(Qt.Key_Right)
      root.check(root.compose.draft.level === "unattended" && root.options.caption === "UNATTENDED-OPENCODE", "Right: Unattended")
      root.press(Qt.Key_Down)
      root.check(root.options.rowId === "paid" && panel.footerText.indexOf("Space allow paid usage") === 0, "the paid usage row: " + panel.footerText)
      root.press(Qt.Key_Space, 0, " ")
      root.check(root.compose.draft.allowPaid === true, "Space allows paid usage")
      root.press(Qt.Key_Space, 0, " ")
      root.check(root.compose.draft.allowPaid === false, "Space turns paid usage off again")
      root.press(Qt.Key_Down)
      root.press(Qt.Key_Left)
      root.check(root.compose.draft.limits.runtimeSec === 5100, "runtime -5 min")
      root.typeDigits("45")
      root.press(Qt.Key_Return)
      root.check(root.compose.draft.limits.runtimeSec === 2700, "typed runtime minutes")
      root.typeDigits("1")
      root.press(Qt.Key_Return)
      root.check(root.compose.draft.limits.runtimeSec === 2700 && root.options._note.indexOf("run time goes from 5 to 240") >= 0, "out of range refused with the range")
      root.press(Qt.Key_Down)
      root.check(root.options.rowId === "model" && root.options.valueText("model") === "Agent default", "model row, Agent default")
      root.press(Qt.Key_Return)
      var lm = root.last("loadModels")
      root.check(panel.sheet === "model" && lm !== null && lm.args[0] === "opencode", "Enter opens the model sheet and reads OpenCode's models")
      root.check(panel.footerText === "Type to filter  ·  Enter pick  ·  Ctrl+R refresh list  ·  Esc back", "model sheet hints: " + panel.footerText)
      root.typeText("sonnet")
      root.press(Qt.Key_Return)
      root.check(panel.sheet === "" && root.compose.draft.model === "anthropic/claude-sonnet-4", "filter and Enter pick the model: " + root.compose.draft.model)
      root.check(root.options.valueText("model") === "Claude Sonnet 4", "the row shows the model's label")
      root.press(Qt.Key_Tab)
      root.check(root.compose.section === 0, "Tab wraps to Prompt")
      root.unfocus()
      return
    }
    if (s === 4) {
      // Arm a clock-time job: the preview digest rides along, the view moves to the Queue.
      root.unfocus()
      root.check(!root.compose.isRunNow, "trigger is a clock time")
      var armsBefore = root.calls("createAndArm").length
      root.ctrl(Qt.Key_Return)
      var armed = root.calls("createAndArm")
      root.check(armed.length === armsBefore + 1, "Ctrl+Enter arms once")
      var d = armed.length ? armed[armed.length - 1].args[0] : {}
      root.check(d.expectCommandDigest === root.digestA && d.prompt === "H", "arm carries the preview digest and the prompt")
      root.check(d.harness === "opencode" && d.level === "unattended" && d.model === "anthropic/claude-sonnet-4" && d.allowPaid === false,
                 "arm carries the draft")
      root.check(panel.view === "queue" && root.queue && root.queue.focused === root.jobId, "after arm: Queue, cursor on the job")
      root.check(root.noticeRow.text.indexOf("Armed. Fires ") === 0 && root.noticeRow.undoAvailable, "armed notice with undo: " + root.noticeRow.text)
      root.ctrl(Qt.Key_Z)
      root.check(root.calls("disarm").length === 1 && root.noticeRow.text === "Disarmed.", "Ctrl+Z disarms")

      // Run now needs a second press.
      root.ctrl(Qt.Key_1)
      root.unfocus()
      root.check(root.compose.draft.prompt === "" && root.compose.isRunNow, "compose was reset after arm")
      root.editor.insertText("Go")
      root.compose.applySession({ harness: "claude", mode: "new", sessionId: null, cwd: "/home/tester/proj/api", title: "api" })
      root.compose.requestPreview()
      var before = root.calls("createAndArm").length
      root.ctrl(Qt.Key_Return)
      root.check(root.calls("createAndArm").length === before && panel.footerText === "Press Ctrl+Enter again to run now.", "first Ctrl+Enter only lights the guard")
      root.press(Qt.Key_Tab)
      root.check(!root.compose._guardArmed, "another key clears the guard")
      root.ctrl(Qt.Key_Return)
      root.ctrl(Qt.Key_Return)
      root.check(root.calls("createAndArm").length === before + 1 && root.noticeRow.text === "Armed. It runs now.", "second press runs now")
      return
    }
    if (s === 5) {
      // Settings sheet: each change is one settings-set; reduced motion reaches the theme.
      root.ctrl(Qt.Key_1)
      root.unfocus()
      root.ctrl(Qt.Key_Comma)
      var st = root.settingsSheet
      for (var i = 0; i < 7; i++) root.press(Qt.Key_Down)
      root.check(st.rowId === "motion", "cursor on Motion")
      root.press(Qt.Key_Right)
      var ss = root.last("setSettings")
      root.check(ss && ss.args[0].motion === "reduced" && panel.theme.reduceMotion && panel.theme.moveMs(160) === 0, "motion reduced wired")
      root.press(Qt.Key_Down)
      root.press(Qt.Key_Down)
      root.check(st.rowId === "cancelAll", "cursor on Cancel all jobs, after Limits in the header")
      root.press(Qt.Key_Return)
      root.check(root.calls("cancelAll").length === 0 && st.hints === "Press Enter again to cancel every job.", "cancel all: first press guards")
      root.press(Qt.Key_Return)
      root.check(root.calls("cancelAll").length === 1 && root.noticeRow.text === "Cancelled 1 job.", "cancel all: second press")
      root.press(Qt.Key_Escape)
      root.check(panel.sheet === "", "Esc closes settings")
      svc.setSettings({ motion: "full" }, null)

      // Save a draft into a typed folder.
      root.unfocus()
      root.editor.insertText("Draft me")
      root.compose.openSessionSheet()
      root.check(panel.sheet === "session", "session sheet from compose")
      root.typeText("~/proj/typed")
      root.press(Qt.Key_Return)
      root.check(root.compose.target.mode === "new" && root.compose.target.cwd === "/home/tester/proj/typed", "new session in a typed folder")
      root.unfocus()
      root.ctrl(Qt.Key_S)
      root.check(root.calls("saveDraft").length === 1 && root.compose.draft.id === "fedcba9876543210", "Ctrl+S saves and keeps the id")

      // Ctrl+N with a dirty draft offers an undo.
      root.ctrl(Qt.Key_N)
      root.unfocus()
      root.check(root.compose.draft.prompt === "" && root.noticeRow.undoAvailable, "Ctrl+N clears with undo")
      root.ctrl(Qt.Key_Z)
      root.unfocus()
      root.check(root.compose.draft.prompt === "Draft me" && root.compose.draft.id === "fedcba9876543210", "undo restores the draft")

      // Edit a stored job.
      panel.editJob(root.jobId, "edit")
      root.unfocus()
      root.check(panel.view === "compose" && root.compose.draft.prompt === "stored prompt", "edit loads the stored prompt")
      root.check(root.compose.draft.id === root.jobId && root.compose.target.title === "client regen", "edit keeps the id and the title")
      return
    }
    if (s === 6) {
      // Default view: a running job opens the Queue with the cursor on it.
      panel.close()
      root.check(root.calls("viewerClosed").length === 1, "viewerClosed on close")
      svc.jobs = [{ id: "1111222233334444", harness: "claude", queue: true, updatedAt: 1, state: { status: "running", lastEvent: null } }]
      root.compose.newDraft()
      root.compose.reset()
      panel.open()
      return
    }
    if (s === 7) {
      if (root.waitFor(panel.view === "queue" && !!root.queue && root.queue.focused === "1111222233334444")) return
      root.check(panel.view === "queue" && root.queue && root.queue.focused === "1111222233334444", "running job opens the Queue")
      // Esc from a view with nothing to clear closes the panel.
      root.press(Qt.Key_Escape)
      root.check(!panel.opened, "Esc closes")
      // Setup problem: a card, Enter retries.
      svc.setupProblem = "The helper did not answer."
      panel.open()
      root.check(panel.contentState === "setup" && panel.footerText.indexOf("Enter try again") === 0, "setup state")
      var refreshes = root.calls("refresh").length
      root.press(Qt.Key_Return)
      root.check(root.calls("refresh").length === refreshes + 1, "Enter retries")
      svc.setupProblem = ""
      svc.ready = false
      root.check(panel.contentState === "loading" && panel.footerText.indexOf("Esc close") >= 0, "loading state")
      svc.ready = true
      svc.listMeta = { killSwitch: true, enabledInShell: true, linger: null, reconciledAt: null, nowMs: Date.now() }
      root.check(panel.systemLine.indexOf("kill switch") > 0, "kill switch line")
      svc.listMeta = { killSwitch: false, enabledInShell: true, linger: null, reconciledAt: null, nowMs: svc.nowMs - 600000 }
      root.check(panel.systemLine.indexOf("The job list is 10m old.") === 0, "stale list line: " + panel.systemLine)
      Qt.exit(root.firstFail)
    }
  }
}
QML

out="$(cd "$W" && timeout 90 "$QML" -I "$W" Q2Smoke.qml 2>&1)"; rc=$?
if [ "$rc" -eq 0 ]; then ok "Panel, Compose, sheets: every scripted check passed"
else no "Panel, Compose, sheets" "rc=$rc $(printf '%s' "$out" | grep -E 'check|threw|Error' | head -8 | tr '\n' ' ')"; fi

# Any engine warning while the panel was alive is a defect: a binding error, an
# unresolved type, a wrong assignment, a binding loop.
# One known warning belongs to another owner's file (NoticeRow's glyph width), so it
# is reported as a note instead of failing this suite.
q1loop="$(printf '%s\n' "$out" | grep -E 'NoticeRow\.qml:[0-9]+:[0-9]+: QML QQuickText: Binding loop detected for property "width"' || true)"
[ -n "$q1loop" ] && echo "  note components/NoticeRow.qml (owner Q1) reports a width binding loop; not counted here"
warn="$(printf '%s\n' "$out" | grep -E 'Warning|TypeError|ReferenceError|Unable to assign|Cannot read|is not a function|Binding loop|is not a type|preview saw prompt' \
  | grep -v 'check [0-9]* failed' | grep -vE 'NoticeRow\.qml:[0-9]+:[0-9]+: QML QQuickText: Binding loop detected for property "width"' || true)"
if [ -z "$warn" ]; then ok "and the engine reported no warnings"
else no "and the engine reported no warnings" "$(printf '%s' "$warn" | head -8 | tr '\n' ' ')"; fi

printf '\n%d passed' "$pass"
[ "$fail" -gt 0 ] && printf ', %d FAILED' "$fail"
printf '\n'
[ "$fail" -eq 0 ]
