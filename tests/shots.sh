#!/bin/bash
# tests/shots.sh: render Compose, Queue and History from the real Panel.qml into
# preview.png (1600-wide panel crop for plugins.omarchy.org) and docs/{compose,queue,history}.png.
#
# Offscreen Qt 6, the repository's lint stand-ins, a populated stub service. Nothing
# here talks to the live shell or to the job store on this machine.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QML=/usr/lib/qt6/bin/qml
PY=/usr/bin/python3
MAGICK=/usr/bin/magick
OUT="${1:-$REPO}"

[ -x "$QML" ] || { echo "shots: no Qt 6 qml at $QML" >&2; exit 2; }
[ -x "$MAGICK" ] || { echo "shots: magick is required" >&2; exit 2; }

T="$(mktemp -d "${TMPDIR:-/tmp}/ap4a-shots.XXXXXX")"
trap 'rm -rf "$T"' EXIT INT TERM
W="$T/w"
mkdir -p "$W/imports" "$T/out" "$T/home" "$T/run"
cp "$REPO"/*.qml "$W/"
cp -r "$REPO/lib" "$REPO/components" "$W/"
cp -r "$REPO/lint/qs" "$REPO/lint/Quickshell" "$W/imports/"

# Tokyo Night, the palette Auto Pilot is developed against, so the listing matches the live card.
cat > "$W/imports/qs/Commons/Color.qml" <<'QML'
pragma Singleton
import QtQuick
QtObject {
  id: root
  component BarColors: QtObject {
    property color background: "#16161e"
    property color text: "#c0caf5"
    property color active: "#f7768e"
  }
  component SurfaceColors: QtObject {
    property color background: "#1a1b26"
    property color text: "#c0caf5"
    property color border: "#3b4261"
  }
  readonly property string home: ""
  property color foreground: "#c0caf5"
  property color background: "#1a1b26"
  property color accent: "#7aa2f7"
  property color urgent: "#f7768e"
  property color muted: "#565f89"
  readonly property BarColors bar: BarColors {}
  readonly property SurfaceColors popups: SurfaceColors {}
  readonly property SurfaceColors tooltip: SurfaceColors {}
  readonly property SurfaceColors notifications: SurfaceColors {}
  readonly property SurfaceColors menu: SurfaceColors {}
}
QML

EDITION="$(env -i HOME="$T/home" USER=shots LANG=C.UTF-8 PATH=/usr/bin XDG_RUNTIME_DIR="$T/run" \
  "$PY" -I -S -B "$REPO/bin/ap4a" edition 2>/dev/null)" || EDITION=""
case "$EDITION" in '{"ok":true'*) ;; *)
  echo "shots: ap4a edition did not answer: ${EDITION:-empty}" >&2
  exit 2
esac

export TZ=Europe/Warsaw
export QT_QPA_PLATFORM=offscreen
export QT_FORCE_STDERR_LOGGING=1
export QML_DISABLE_DISK_CACHE=1
export PYTHONDONTWRITEBYTECODE=1

cat > "$W/Shots.qml" <<QML
import QtQuick
import QtQuick.Window

Window {
  id: host
  width: 1920
  height: 1080
  visible: true
  color: "#1a1b26"
  title: "ap4a-shots"

  property var editionRes: $EDITION
  property int step: 0
  property int fails: 0
  property string lastGrab: ""
  readonly property double nowStart: host.at(2026, 9, 17, 14, 5)
  readonly property string fontName: "JetBrainsMono Nerd Font"

  function at(y, mo, d, h, mi) { return new Date(y, mo - 1, d, h, mi, 0).getTime() }
  function fail(msg) { console.warn("SHOT-FAIL " + msg); host.fails++ }
  function find(item, pred, depth) {
    var d = depth || 0
    if (!item || d > 50) return null
    if (pred(item)) return item
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) {
      var hit = host.find(kids[i], pred, d + 1)
      if (hit) return hit
    }
    return null
  }
  function card() {
    return host.find(loader.item, function (it) { return it && typeof it.contentWidth === "number" && it.contentWidth > 800 && it.open === true })
  }
  function compose() {
    return host.find(loader.item, function (it) { return it && typeof it.loadDraft === "function" && typeof it.newDraft === "function" })
  }
  function history() {
    return host.find(loader.item, function (it) { return it && typeof it.expand === "function" && typeof it.loadDetail === "function" })
  }
  function grab(item, path, w, h, done) {
    if (!item) { host.fail("no item for " + path); done(); return }
    item.grabToImage(function (res) {
      host.lastGrab = res && res.saveToFile(path) ? "saved" : "not saved"
      if (host.lastGrab !== "saved") host.fail("save failed: " + path)
      done()
    }, Qt.size(w, h))
  }

  function sampleJob(id, status, fireAtSec, queue, extra) {
    var ended = Math.floor(host.nowStart / 1000) - (queue ? 0 : 2400)
    var j = {
      id: id, label: extra && extra.label ? extra.label : "Sample " + id.slice(0, 4),
      harness: extra && extra.harness ? extra.harness : "claude", createdAt: 1789400000, updatedAt: ended,
      cli: { link: "/home/u/.local/share/mise/installs/claude/latest/claude", version: "2.1.270" },
      target: { mode: "resume", sessionId: "3f2a0c19-1111-4222-8333-444455556666", newSessionId: null,
                cwd: "/home/u/proj", title: extra && extra.title ? extra.title : "proj", allowNonGit: false, sessionPath: null },
      level: extra && extra.level ? extra.level : "plan",
      limits: { maxTurns: 15, budgetUsd: 5, runtimeSec: 5400 }, model: extra && extra.model ? extra.model : null,
      trigger: { kind: extra && extra.kind ? extra.kind : "at", fireAt: fireAtSec, delaySec: extra && extra.delaySec ? extra.delaySec : null,
                 marginSec: 120, weeklyPolicy: "defer", graceSec: 900 },
      promptSha256: "", promptBytes: 48, promptAvailable: queue === true, commandDigest: "", digest: "",
      allowPaid: extra && extra.allowPaid === true, provider: extra && extra.provider ? extra.provider : null,
      state: { status: status, wait: extra && extra.wait ? extra.wait : null, reason: extra && extra.reason ? extra.reason : null,
               gen: 1, fireAt: fireAtSec, unit: queue && status === "armed" ? "ap4a-" + id + "-g1" : null,
               armedAt: fireAtSec ? fireAtSec - 60 : null, pluginDir: null, basis: null, runSessionId: null, runSessionPath: null,
               defers: 0, limitRetries: 0, transientRetries: 0, busyDefers: 0, unknownRetries: 0,
               statusAt: ended,
               lastRun: queue ? null : { runId: id + "-g1", startedAt: ended - 1800, endedAt: ended,
                                         outcome: status, exit: status === "done" ? 0 : 1, signal: null, bytesDropped: 0 },
               lastEvent: { event: status, at: ended, detail: extra && extra.reason ? extra.reason : null } },
      fireAtMs: fireAtSec ? fireAtSec * 1000 : null,
      gated: false, limitSource: extra && extra.limitSource ? extra.limitSource : "claude",
      canRunNow: true, canDisarm: status === "armed" || status === "running", canEdit: status !== "running",
      canDelete: status !== "armed" && status !== "running", queue: queue
    }
    if (extra && extra.cli) j.cli = extra.cli
    if (extra && extra.target) j.target = extra.target
    return j
  }

  function provider(id, name, harness, stale, windows, headlineKey, computed) {
    return { id: id, name: name, tier: computed ? "" : "Pro", statusText: "", scope: null, source: computed ? "computed" : "record",
             readable: true, unreadableReason: null, updatedAtMs: computed ? null : host.nowStart - 120000, ageSec: computed ? null : 120,
             stale: stale, cadenceSec: computed ? null : 900, keptFromLastPoll: false, harnesses: [harness], relevant: true,
             headlineKey: headlineKey, windows: windows }
  }
  function win(key, label, shortLabel, kind, percent, resetsInSec, computed) {
    return { key: key, label: label, title: null, shortLabel: shortLabel, kind: kind, percent: percent,
             over: percent !== null && percent > 1, resetsAt: Math.floor(host.nowStart / 1000) + resetsInSec, sliding: false,
             source: computed ? "computed" : "record", bindable: true }
  }

  Rectangle {
    anchors.fill: parent
    gradient: Gradient {
      GradientStop { position: 0.0; color: "#1a1b26" }
      GradientStop { position: 1.0; color: "#16161e" }
    }
  }
  Repeater {
    model: 7
    delegate: Rectangle {
      required property int index
      x: 80 + index * 270
      y: 90
      width: 220
      height: 140
      color: "#11131a"
      opacity: 0.55
      border.color: "#24283b"
      border.width: 1
    }
  }

  Rectangle {
    id: bar
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: parent.top
    height: 26
    color: "#16161e"
    Text {
      anchors.left: parent.left
      anchors.leftMargin: 12
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: "\uF17A   1  2  3"
      color: "#a9b1d6"
      font.family: host.fontName
      font.pixelSize: 12
    }
    Text {
      anchors.horizontalCenter: parent.horizontalCenter
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: "Thursday 14:05"
      color: "#c0caf5"
      font.family: host.fontName
      font.pixelSize: 12
    }
    Row {
      anchors.right: parent.right
      anchors.rightMargin: 14
      anchors.verticalCenter: parent.verticalCenter
      spacing: 14
      Text {
        textFormat: Text.PlainText
        text: "\uDB80\uDCF0  2h00"
        color: "#e0af68"
        font.family: host.fontName
        font.pixelSize: 12
      }
      Text {
        textFormat: Text.PlainText
        text: "\uF1EB  \uF026  14"
        color: "#a9b1d6"
        font.family: host.fontName
        font.pixelSize: 12
      }
    }
  }

  QtObject {
    id: stubBar
    property string fontFamily: host.fontName
    property color barForeground: "#c0caf5"
    property color urgent: "#f7768e"
    property bool vertical: false
    property int barSize: 26
    property string position: "top"
    property bool foregroundAnimationEnabled: false
    property var shell: null
    function switchPanelFrom(owner, direction) { return false }
    function showTooltip(item, text) {}
    function hideTooltip(item) {}
    function requestPopout(key) {}
    function releasePopout(key) {}
    function registerClickTarget(item) {}
    function unregisterClickTarget(item) {}
    function moduleWidgets(name) { return [] }
  }
  Item { id: anchor; width: 24; height: 24; x: 1680; y: 0 }

  QtObject {
    id: stubService
    signal notice(string text, string kind)
    signal jobsUpdated()
    readonly property double nowStart: host.nowStart
    property bool ready: true
    property string setupProblem: ""
    property var edition: host.editionRes.edition
    property var levels: host.editionRes.levels
    property var harnesses: host.editionRes.harnesses
    property var caps: host.editionRes.caps
    property var jobs: []
    property var jobsById: ({})
    property var listMeta: ({ killSwitch: false, enabledInShell: true, linger: false, reconciledAt: null, nowMs: nowStart })
    property var providers: [
      host.provider("claude", "Claude", "claude", false,
        [host.win("claude:5-hour", "Session (5-hour)", "5-hour", "session", 0.62, 7200, false),
         host.win("claude:weekly", "Weekly (7-day)", "Weekly", "weekly", 0.41, 86400, false)], "claude:5-hour", false),
      host.provider("codex", "Codex", "codex", false,
        [host.win("codex:weekly", "Weekly (7-day)", "Weekly", "weekly", 0.28, 14400, false)], "codex:weekly", false),
      host.provider("gemini-daily", "Gemini CLI daily", "gemini", false,
        [host.win("gemini-daily:daily", "", "daily", "daily", 0.11, 36000, true)], "gemini-daily:daily", true)
    ]
    property var usage: ({ ok: true, nowMs: stubService.nowStart, providers: stubService.providers })
    property string usageError: ""
    property var agents: ({
      claude: { harness: "claude", name: "Claude Code", cliName: "claude", available: true, link: "/usr/bin/claude", real: "/usr/bin/claude", version: "2.1.270", loggedIn: null, reason: null, canFork: true, triggers: ["now", "in", "at", "claude_5h_reset"], enabled: true, armable: true, gated: false },
      opencode: { harness: "opencode", name: "OpenCode", cliName: "opencode", available: true, link: "/usr/bin/opencode", real: "/usr/bin/opencode", version: "1.18.30", loggedIn: null, reason: null, canFork: true, triggers: ["now", "in", "at", "zen_free_reset", "go_window_reset"], enabled: true, armable: true, gated: false },
      codex: { harness: "codex", name: "Codex", cliName: "codex", available: true, link: "/usr/bin/codex", real: "/usr/bin/codex", version: "0.50.0", loggedIn: true, reason: null, canFork: true, triggers: ["now", "in", "at", "codex_window_reset"], enabled: true, armable: true, gated: false },
      gemini: { harness: "gemini", name: "Gemini CLI", cliName: "gemini", available: true, link: "/usr/lib/node_modules/@google/gemini-cli/bundle/gemini.js", real: "/usr/lib/node_modules/@google/gemini-cli/bundle/gemini.js", version: "0.50.0", loggedIn: null, reason: null, canFork: false, triggers: ["now", "in", "at", "gemini_daily_reset"], enabled: true, armable: true, gated: false },
      cursor: { harness: "cursor", name: "Cursor Agent", cliName: "cursor-agent", available: true, link: "/usr/bin/cursor-agent", real: "/usr/bin/cursor-agent", version: "2026.09.10-fd3934a", loggedIn: true, reason: null, canFork: false, triggers: ["now", "in", "at"], enabled: true, armable: true, gated: false },
      pi: { harness: "pi", name: "Pi", cliName: "pi", available: true, link: "/home/u/.local/share/mise/installs/pi/latest/pi/pi", real: "/home/u/.local/share/mise/installs/pi/0.74.0/pi/pi", version: "0.74.0", loggedIn: true, reason: null, canFork: true, triggers: ["now", "in", "at", "codex_window_reset"], enabled: true, armable: true, gated: false }
    })
    property var sessions: ({})
    property var models: ({})
    property var timeline: ({})
    property var settings: ({ schemaVersion: 1, defaultHarness: "claude", defaultLevel: "plan", resetMarginSec: 120, eveningTime: "23:00", morningTime: "07:00", notify: "all", motion: "reduced", defaultAllowPaid: false, limitsShown: "auto", lastSeenAt: null })
    property double nowMs: nowStart
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
    property int armedCount: 2
    property int runningCount: 1
    property int attentionCount: 0
    property bool anyRunning: true
    property int viewers: 0
    property double lastSeenAt: Math.floor(nowStart / 1000) - 3600
    property double attentionSeenAt: lastSeenAt
    property int draftCount: 1
    property int doneSinceSeen: 1
    property int problemCount: 0
    property int authProblemCount: 0
    property var barState: ({ state: "running", glyph: "", text: "run", tone: "accent", sentence: "A job is running.", pulse: false })
    property string barLabel: "Next run"
    function later(cb, res) { Qt.callLater(function () { if (cb) cb(res) }) }
    function refresh() {}
    function refreshUsage() {}
    function refreshAgents(checkLogin) {}
    function loadSessions(harness, cwd) {}
    function loadModels(harness, refresh) {}
    function loadTimeline(dayStartMs) {
      var d = new Date(dayStartMs)
      var key = d.getFullYear() + "-" + ("0" + (d.getMonth() + 1)).slice(-2) + "-" + ("0" + d.getDate()).slice(-2)
      var start = Math.floor(dayStartMs / 1000)
      var map = {}
      map[key] = {
        ok: true, dayStartMs: dayStartMs,
        resets: [{ source: "claude", name: "Claude", kind: "session", shortLabel: "5-hour",
                   at: start + 16 * 3600 + 5 * 60, origin: "record", harnesses: ["claude"] }],
        runs: [
          { runId: "c3c3c3c3c3c3c3c3-g1", jobId: "c3c3c3c3c3c3c3c3", harness: "claude", label: "Summarise the diff",
            startedAt: start + 12 * 3600 + 55 * 60, endedAt: start + 13 * 3600 + 25 * 60, outcome: "done", status: null, limitSource: "claude" },
          { runId: "f5f5f5f5f5f5f5f5-g1", jobId: "f5f5f5f5f5f5f5f5", harness: "opencode", label: "OpenCode models list",
            startedAt: start + 12 * 3600 + 55 * 60, endedAt: start + 13 * 3600 + 25 * 60, outcome: "failed", status: null, limitSource: null }
        ],
        armed: []
      }
      stubService.timeline = map
    }
    function markSeen() {}
    function setLimitsShown(value, cb) { stubService.later(cb, { ok: true }) }
    function getJob(id, cb) {
      var j = stubService.jobsById[id]
      stubService.later(cb, { ok: !!j, job: j || null, prompt: j && j.queue ? "Review the last CI run and summarise failures." : null,
        promptAvailable: !!(j && j.queue), lastLines: j && !j.queue ? [
          "Reading tests/test_core.py",
          "3 failed, 1 skipped",
          j.state.status === "failed" ? "error: helper timed out on preview" : "Done. Wrote the summary in the session."
        ] : [], runs: [] })
    }
    function preview(draft, cb) {
      stubService.later(cb, { ok: true, preview: {
        display: "claude -p --permission-mode auto --permission-prompts none --max-turns 40 --output-format stream-json --verbose --resume 3f2a0c19-…",
        cwd: "/home/u/proj", binary: "/home/u/.local/share/mise/installs/claude/latest/claude",
        levelCaption: "Auto mode. Claude's classifier approves actions it judges safe and blocks risky ones. Nothing asks you.",
        commandDigest: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        argv: ["claude", "-p"], warnings: [],
        gate: { ok: true, code: null, detail: null, notes: [], billing: null, provider: null, resetAtMs: null, pending: false },
        fireAt: Math.floor(host.nowStart / 1000) + 7200, immediate: false
      } })
    }
    function createAndArm(draft, cb) { stubService.later(cb, { ok: false, code: "not_ready" }) }
    function saveDraft(draft, cb) { stubService.later(cb, { ok: false, code: "not_ready" }) }
    function arm(id, digest, cb) { stubService.later(cb, { ok: false, code: "not_ready" }) }
    function runNow(id, digest, cb) { stubService.later(cb, { ok: false, code: "not_ready" }) }
    function disarm(id, cb) { stubService.later(cb, { ok: false, code: "not_ready" }) }
    function deleteJob(id, cb) { stubService.later(cb, { ok: false, code: "not_ready" }) }
    function cancelAll(cb) { stubService.later(cb, { ok: false, code: "not_ready" }) }
    function reschedule(id, epochSec, cb) { stubService.later(cb, { ok: false, code: "not_ready" }) }
    function swap(a, b, cb) { stubService.later(cb, { ok: false, code: "not_ready" }) }
    function shift(ids, deltaSec, cb) { stubService.later(cb, { ok: false, code: "not_ready" }) }
    function setSettings(obj, cb) { stubService.later(cb, { ok: true }) }
    function copyResume(id, cb) { stubService.later(cb, { ok: true }) }
    function reconcileNow(cb) { stubService.later(cb, { ok: true }) }
    function viewerOpened() { viewers = viewers + 1 }
    function viewerClosed() { viewers = Math.max(0, viewers - 1) }
    function levelFor(id) {
      for (var i = 0; i < levels.length; i++) if (levels[i].id === id) return levels[i]
      return null
    }
    function agentFor(harness) { return agents[harness] || null }
  }

  Loader {
    id: loader
    x: 400
    y: 42
    active: true
    source: "Panel.qml"
    onLoaded: {
      item.bar = stubBar
      item.anchorItem = anchor
      item.service = stubService
    }
  }

  Component.onCompleted: {
    var nowSec = Math.floor(host.nowStart / 1000)
    var run = host.sampleJob("a1a1a1a1a1a1a1a1", "running", nowSec - 90, true, { label: "Review the CI log", title: "api", level: "unattended" })
    run.state.lastRun = { runId: "a1a1a1a1a1a1a1a1-g1", startedAt: nowSec - 90, endedAt: null, outcome: null, exit: null, signal: null, bytesDropped: 0 }
    var armed = host.sampleJob("b2b2b2b2b2b2b2b2", "armed", nowSec + 7200, true, { label: "Nightly plan", kind: "at", title: "proj", level: "auto" })
    var pi = host.sampleJob("e4e4e4e4e4e4e4e4", "armed", nowSec + 10800, true, {
      label: "Pi in proj", harness: "pi", level: "full", kind: "codex_window_reset", provider: "openai-codex",
      model: "gpt-5.5", allowPaid: true, limitSource: "codex", title: "proj"
    })
    pi.cli = { link: "/home/u/.local/share/mise/installs/pi/latest/pi/pi", version: "0.74.0" }
    pi.target = { mode: "fork", sessionId: "9b1d7e42-aaaa-4bbb-8ccc-ddddeeeeffff", newSessionId: null, cwd: "/home/u/proj",
                  title: "proj", allowNonGit: false, sessionPath: "/home/u/.pi/agent/sessions/--home-u-proj--/s.jsonl" }
    var draft = host.sampleJob("d2d2d2d2d2d2d2d2", "draft", null, true, { label: "Draft: changelog", title: "website", level: "auto" })
    var done = host.sampleJob("c3c3c3c3c3c3c3c3", "done", null, false, { label: "Summarise the diff", title: "api", level: "auto" })
    var failed = host.sampleJob("f5f5f5f5f5f5f5f5", "failed", null, false, { label: "OpenCode models list", harness: "opencode", title: "omarchy", reason: "timeout", level: "full" })
    failed.cli = { link: "/usr/bin/opencode", version: "1.18.30" }
    stubService.jobs = [run, armed, pi, draft, done, failed]
    var by = {}
    for (var i = 0; i < stubService.jobs.length; i++) by[stubService.jobs[i].id] = stubService.jobs[i]
    stubService.jobsById = by
    stubService.nextJob = armed
  }

  property bool grabbing: false
  property int wait: 0

  function pair(cardPath, then) {
    var card = host.card()
    if (!card) { host.fail("no card for " + cardPath); then(); return }
    host.grabbing = true
    host.grab(card, cardPath, Math.round(card.width * 2), Math.round(card.height * 2), function () {
      host.grabbing = false
      then()
    })
  }
  function go(n) { host.step = n; host.wait = 0 }

  Timer {
    interval: 80
    repeat: true
    running: true
    onTriggered: {
      if (host.grabbing) return
      host.wait++
      if (host.wait > 80) { host.fail("timed out at step " + host.step); Qt.exit(5); return }
      var p = loader.item
      if (!p) return
      var s = host.step
      if (s === 0) {
        p.open()
        p.showView("compose")
        host.go(1)
      } else if (s === 1 && host.wait >= 4) {
        var c = host.compose()
        if (!c) { host.fail("ComposeView not found"); Qt.exit(3); return }
        c.loadDraft({
          harness: "claude",
          target: { mode: "resume", sessionId: "3f2a0c19-1111-4222-8333-444455556666", cwd: "/home/u/proj",
                    title: "proj", allowNonGit: false, sessionPath: null },
          level: "auto", limits: { maxTurns: 40 }, model: null, allowPaid: false, provider: null,
          trigger: { kind: "at", fireAt: Math.floor(host.nowStart / 1000) + 7200 }
        }, "Review the last CI run and summarise failures. Name the flaky tests.", "")
        host.go(2)
      } else if (s === 2 && host.wait >= 8) {
        host.pair("$T/out/compose.png", function () { host.go(3) })
      } else if (s === 3) {
        p.showView("queue")
        host.go(4)
      } else if (s === 4 && host.wait >= 6) {
        host.pair("$T/out/queue.png", function () { host.go(5) })
      } else if (s === 5) {
        p.showView("history")
        host.go(6)
      } else if (s === 6 && host.wait >= 3) {
        var hv = host.history()
        if (hv) hv.expand("f5f5f5f5f5f5f5f5")
        host.go(7)
      } else if (s === 7 && host.wait >= 6) {
        host.pair("$T/out/history.png", function () {
          Qt.exit(host.fails === 0 ? 0 : 4)
        })
      }
    }
  }
}
QML

out="$("$QML" -I "$W/imports" "$W/Shots.qml" 2>&1)" || {
  printf '%s\n' "$out" | tail -40 >&2
  echo "shots: qml exited $?" >&2
  exit 1
}
printf '%s\n' "$out" | grep -E 'SHOT-FAIL|TypeError|ReferenceError' && exit 1 || true

need() {
  local f="$1"
  [ -s "$T/out/$f" ] || { echo "shots: missing $f" >&2; exit 1; }
}
need compose.png
need queue.png
need history.png

# plugins.omarchy.org detail is fit-inside 1600 with withoutEnlargement. A 1920x1080
# desktop mock becomes 1600x900 of wallpaper; a 1600-wide panel crop (same as ASM)
# lands 1:1 in the listing. 2x grab then downscale keeps type sharp.
mkdir -p "$OUT/docs"
fit1600() { "$MAGICK" "$1" -resize '1600x1600>' -strip -define png:compression-level=9 "$2"; }
fit1600 "$T/out/compose.png" "$OUT/preview.png"
fit1600 "$T/out/compose.png" "$OUT/docs/compose.png"
fit1600 "$T/out/queue.png" "$OUT/docs/queue.png"
fit1600 "$T/out/history.png" "$OUT/docs/history.png"

"$PY" -I -S -B - "$OUT/preview.png" <<'PY'
import struct, sys
p = sys.argv[1]
d = open(p, "rb").read(32)
assert d[:8] == b"\x89PNG\r\n\x1a\n" and d[12:16] == b"IHDR", p
w, h = struct.unpack(">II", d[16:24])
if w != 1600:
    raise SystemExit("preview width %d, want 1600 (marketplace detail limit)" % w)
print("preview.png %dx%d %dB" % (w, h, __import__("os").path.getsize(p)))
PY
echo "shots: wrote $OUT/preview.png and $OUT/docs/{compose,queue,history}.png"
