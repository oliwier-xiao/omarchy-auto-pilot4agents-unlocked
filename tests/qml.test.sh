#!/bin/bash
# tests/qml.test.sh: the QML side of the plugin, in the real Qt 6 engine and under the Qt 6 linter.
#
#   1. lib/Model.js: every function of contract section 6.3, checked against the contract's examples.
#   2. lib/Tint.js: solved inks reach their contrast target on six Omarchy theme backgrounds.
#   3. /usr/lib/qt6/bin/qmllint -I lint on every .qml file (plugin and stand-ins), zero warnings, both
#      with the system Qt modules and with the lint/ stand-ins alone (--bare, as on a bare runner).
#   4. Panel.qml instantiates offscreen with a stub service holding every property and function of
#      contract 5.1 and 5.2, then shows all three views and opens all three sheets, with no QML warning.
#
# Each engine case is its own process and its exit code is the verdict: 0 passed, n = the first
# failing check, 250 = the case threw. Local time is pinned to Europe/Warsaw so every expected clock
# string is a fact rather than a property of the machine running it.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QML=/usr/lib/qt6/bin/qml
QLINT=/usr/lib/qt6/bin/qmllint
PY=/usr/bin/python3

pass=0; fail=0
ok() { printf '  ok   %s\n' "$1"; pass=$((pass + 1)); }
no() { printf '  FAIL %s\n         %s\n' "$1" "$2"; fail=$((fail + 1)); }

T="$(mktemp -d "${TMPDIR:-/tmp}/ap4a-qml.XXXXXX")" || exit 2
trap 'rm -rf "$T"' EXIT INT TERM
mkdir -p "$T/lib" "$T/home"
cp "$REPO"/lib/*.js "$T/lib/"

export TZ=Europe/Warsaw
export QT_QPA_PLATFORM=offscreen
export QT_FORCE_STDERR_LOGGING=1
export QML_DISABLE_DISK_CACHE=1

# Python's reason table and consts, so the QML copy is checked against the helper's own sentences.
REASONS="$(PYTHONDONTWRITEBYTECODE=1 "$PY" -I -S -B -c '
import json, sys
sys.path.insert(0, sys.argv[1] + "/bin")
from autopilot import consts, edition, notify
print(json.dumps({"reasons": list(consts.REASONS), "sentences": notify.REASON_SENTENCES,
                  "resetTrigger": consts.RESET_TRIGGER,
                  "resetKindsFor": {h: list(v) for h, v in consts.RESET_KINDS_FOR.items()},
                  "harnessIds": list(edition.HARNESS_IDS), "harnessNames": consts.HARNESS_NAMES,
                  "cliNames": consts.CLI_NAMES}))
' "$REPO" 2>/dev/null)" || REASONS=""
[ -n "$REASONS" ] || REASONS='null'

# ---------------------------------------------------------------- engine harness

lib_case() { # <name> <js body>
  cat >"$T/Case_$1.qml" <<QML
import QtQuick
import "lib/Model.js" as Model
import "lib/Tint.js" as Tint

Item {
  id: root
  property int n: 0
  property int firstFail: 0
  property var py: $REASONS
  function check(c) { root.n++; if (!c && root.firstFail === 0) root.firstFail = root.n }
  function eq(a, b) {
    var same = JSON.stringify(a) === JSON.stringify(b)
    if (!same) console.warn("check " + (root.n + 1) + ": got " + JSON.stringify(a) + " want " + JSON.stringify(b))
    root.check(same)
  }
  function at(y, mo, d, h, mi, s) { return new Date(y, mo - 1, d, h, mi, s || 0).getTime() }
  function cp(code) { return String.fromCodePoint(code) }
  function ids(list) { return list.map(function (x) { return x.id }) }
  function job(id, status, wait, fireAtSec) {
    var queueStates = ["draft", "armed", "running", "paused", "needs_confirm", "missed", "busy", "interrupted"]
    return {
      id: id, label: "Job " + id, harness: "claude", createdAt: 1789400000, updatedAt: 1789400000,
      cli: { link: "/usr/bin/claude", version: null },
      target: { mode: "resume", sessionId: "3f2a0c19-1111-4222-8333-444455556666", newSessionId: null,
                cwd: "/home/u/proj/api", title: "api", allowNonGit: false },
      level: "plan", limits: { maxTurns: 15, budgetUsd: 5, runtimeSec: 5400 }, model: null,
      trigger: { kind: "at", fireAt: fireAtSec, delaySec: null, marginSec: 120, weeklyPolicy: "defer", graceSec: 900 },
      promptSha256: "", promptBytes: 10, promptAvailable: true, commandDigest: "", digest: "",
      state: { status: status, wait: wait, reason: null, gen: 1, fireAt: fireAtSec, unit: null, armedAt: null,
               pluginDir: null, basis: null, runSessionId: null, defers: 0, limitRetries: 0, transientRetries: 0,
               busyDefers: 0, unknownRetries: 0, lastRun: null, lastEvent: null },
      fireAtMs: fireAtSec ? fireAtSec * 1000 : null,
      canRunNow: true, canDisarm: status === "armed", canEdit: status !== "running",
      canDelete: status !== "armed" && status !== "running",
      queue: queueStates.indexOf(status) >= 0
    }
  }

  Component.onCompleted: {
    try {
$2
    } catch (e) {
      console.warn("threw: " + e)
      Qt.exit(250)
      return
    }
    Qt.exit(root.firstFail)
  }
}
QML
}

run_case() { # <name> <description>
  local out rc
  out="$(cd "$T" && timeout 60 "$QML" "Case_$1.qml" 2>&1)"; rc=$?
  if [ "$rc" -eq 0 ]; then
    ok "$2"
  else
    no "$2" "exit $rc: $(printf '%s\n' "$out" | grep -m3 -E 'check [0-9]+|threw|rror' | tr '\n' ' ')"
  fi
}

echo "=== lib/Model.js and lib/Tint.js in the Qt 6 engine ==="
if [ ! -x "$QML" ]; then
  no "Qt 6 qml runtime" "no $QML on this machine; install qt6-declarative to run the engine cases"
else

lib_case clamp '
      eq(Model.clampInterval(-5), 1)
      eq(Model.clampInterval(0), 1)
      eq(Model.clampInterval(1500), 1500)
      eq(Model.clampInterval(2147483647), 2147483647)
      eq(Model.clampInterval(2147483648), 2147483647)
      eq(Model.clampInterval(1e15), 2147483647)
      var odd = Model.clampInterval(NaN)
      check(Math.floor(odd) === odd && odd >= 1 && odd <= 2147483647)
      var frac = Model.clampInterval(1500.7)
      check(Math.floor(frac) === frac)'
run_case clamp "clampInterval keeps every timer inside [1, int32]"

lib_case durations '
      var M = 60000, H = 3600000, D = 86400000
      var now = root.at(2026, 9, 14, 7, 22)
      eq(Model.formatDuration(2 * D + 3 * H), "2d 3h")
      eq(Model.formatDuration(2 * H + 48 * M), "2h 48m")
      eq(Model.formatDuration(48 * M), "48m")
      eq(Model.formatDuration(92000), "1m 32s")
      eq(Model.formatDuration(0), "0s")
      eq(Model.formatCountdown(now + 2 * H + 48 * M, now), "in 2h 48m")
      eq(Model.formatCountdown(now - 1000, now), "firing")
      eq(Model.formatCountdown(now, now), "firing")
      eq(Model.formatRunning(now - 192000, now), "running 3m 12s")
      eq(Model.formatBarCountdown(2 * D + 3 * H), "2d3h")
      eq(Model.formatBarCountdown(2 * H + 48 * M), "2h48m")
      eq(Model.formatBarCountdown(22 * H + 42 * M), "22h")
      eq(Model.formatBarCountdown(48 * M), "48m")
      eq(Model.formatBarCountdown(30000), "<1m")'
run_case durations "formatDuration, formatCountdown, formatRunning and formatBarCountdown match the contract"

lib_case days '
      var M = 60000, H = 3600000, D = 86400000
      var now = root.at(2026, 9, 14, 7, 22)
      eq(Model.formatClock(root.at(2026, 9, 14, 10, 10)), "10:10")
      eq(Model.formatClock(root.at(2026, 9, 14, 23, 0)), "23:00")
      eq(Model.dayKey(now), "2026-09-14")
      var today = Model.dayLabel(now, now)
      eq(today.word, "Today")
      eq(today.date, "Mon 14 Sep")
      eq(Model.dayLabel(now + D, now).word, "Tomorrow")
      eq(Model.dayLabel(now - D, now).word, "Yesterday")
      var wed = Model.dayLabel(root.at(2026, 9, 16, 12, 0), now)
      eq(wed.word, "Wed")
      eq(wed.date, "Wed 16 Sep")
      eq(Model.resolvedLine(now + 2 * H + 48 * M, now), "today, in 2h 48m")
      eq(Model.resolvedLine(now + 16 * H + 50 * M, now), "tomorrow, in 16h 50m")
      eq(Model.resolvedLine(now + 2 * D + 3 * H, now), "Wed 16 Sep, in 2d 3h")'
run_case days "formatClock, dayKey, dayLabel and resolvedLine read local days"

lib_case nudging '
      var n0 = root.at(2026, 9, 14, 7, 0)
      eq(Model.nudged(root.at(2026, 9, 14, 10, 13, 40), 5, true, n0), root.at(2026, 9, 14, 10, 15))
      eq(Model.nudged(root.at(2026, 9, 14, 10, 13), -5, true, n0), root.at(2026, 9, 14, 10, 10))
      eq(Model.nudged(root.at(2026, 9, 14, 10, 15), 5, true, n0), root.at(2026, 9, 14, 10, 20))
      eq(Model.nudged(root.at(2026, 9, 14, 10, 13), 60, true, n0), root.at(2026, 9, 14, 11, 13))
      eq(Model.nudged(root.at(2026, 9, 14, 23, 58), 5, false, n0), root.at(2026, 9, 15, 0, 3))
      eq(Model.nudged(n0 + 30000, -5, true, n0), n0 + 60000)
      eq(Model.nudgedDays(root.at(2026, 9, 14, 10, 10), 1, n0), root.at(2026, 9, 15, 10, 10))
      eq(Model.nudgedDays(root.at(2026, 9, 14, 10, 10), -1, n0), n0 + 60000)'
run_case nudging "nudged snaps the first 5-minute step, crosses midnight and never goes below now + 1 min"

lib_case typing '
      var now = root.at(2026, 9, 14, 7, 22)
      var p = Model.parseTyped("1405", now)
      eq(p.ms, root.at(2026, 9, 14, 14, 5))
      eq(p.rolled, false)
      p = Model.parseTyped("905", now)
      eq(p.ms, root.at(2026, 9, 14, 9, 5))
      eq(p.rolled, false)
      eq(Model.parseTyped("14:05", now).ms, root.at(2026, 9, 14, 14, 5))
      p = Model.parseTyped("0700", now)
      eq(p.ms, root.at(2026, 9, 15, 7, 0))
      eq(p.rolled, true)
      eq(Model.parseTyped("2460", now), null)
      eq(Model.parseTyped("abc", now), null)
      var preset = Model.presetTime("23:00", now)
      eq(preset.ms, root.at(2026, 9, 14, 23, 0))
      eq(preset.tomorrow, false)
      preset = Model.presetTime("07:00", now)
      eq(preset.ms, root.at(2026, 9, 15, 7, 0))
      eq(preset.tomorrow, true)'
run_case typing "parseTyped and presetTime roll a passed time over to tomorrow"

lib_case resets '
      var now = root.at(2026, 9, 14, 7, 22)
      var resetSec = Math.floor(root.at(2026, 9, 14, 10, 8) / 1000)
      var usage = { ok: true, nowMs: now,
        claude: { available: true, fetchedAtMs: now - 60000, ageSec: 60, stale: false, status: "", tierLabel: "Max 5x",
          windows: [ { label: "Weekly (7-day)", kind: "weekly", percent: 0.2, resetsAt: resetSec + 86400, title: null },
                     { label: "Session (5-hour)", kind: "session", percent: 0.35, resetsAt: resetSec, title: null } ] },
        codex: { available: false, updatedAtMs: null, stale: true, status: "", windows: [] } }
      eq(Model.sessionWindow(usage).kind, "session")
      eq(Model.sessionWindow(usage).resetsAt, resetSec)
      eq(Model.sessionWindow(null), null)
      eq(Model.sessionWindow({ claude: { available: true, windows: [] } }), null)
      var chip = Model.resetChip(usage, "claude", now, 120)
      eq(chip.label, "At reset 10:08")
      eq(chip.fireAtMs, (resetSec + 120) * 1000)
      eq(chip.percent, 0.35)
      eq(chip.available, true)
      eq(chip.stale, false)
      eq(chip.reason, null)
      eq(Model.resetChip(null, "claude", now, 120).reason, "no_data")
      var closed = JSON.parse(JSON.stringify(usage))
      closed.claude.windows = [closed.claude.windows[0]]
      eq(Model.resetChip(closed, "claude", now, 120).reason, "not_open")
      var stale = JSON.parse(JSON.stringify(usage))
      stale.claude.stale = true
      eq(Model.resetChip(stale, "claude", now, 120).stale, true)
      var gemini = Model.resetChip(usage, "gemini", now, 120)
      check(String(gemini.label).indexOf("At reset ") === 0)
      eq(gemini.fireAtMs, Model.nextLaMidnightMs(now) + 120000)
      check(root.py !== null)
      var map = root.py ? root.py.resetTrigger : {}
      var kindsFor = root.py ? root.py.resetKindsFor : {}
      var hs = ["claude", "opencode", "codex", "gemini", "cursor", "pi"]
      eq(Model.RESET_TRIGGER, { claude: "claude_5h_reset", opencode: "zen_free_reset", codex: "codex_window_reset",
                                gemini: "gemini_daily_reset", cursor: "", pi: "codex_window_reset" })
      for (var i = 0; i < hs.length; i++) {
        eq([hs[i], Model.RESET_TRIGGER[hs[i]]], [hs[i], map[hs[i]] || ""])
        eq([hs[i], Model.resetChip(usage, hs[i], now, 120).kind], [hs[i], map[hs[i]] || ""])
        eq([hs[i], Model.RESET_KINDS_FOR[hs[i]]], [hs[i], kindsFor[hs[i]]])
      }
      eq([Model.RESET_NAMES.zen_free_reset, Model.RESET_NAMES.go_window_reset], ["Zen free reset", "Go reset"])
      eq(Model.nextLaMidnightMs(Date.UTC(2026, 8, 14, 5, 22)), Date.UTC(2026, 8, 14, 7, 0))
      eq(Model.nextLaMidnightMs(Date.UTC(2026, 11, 1, 12, 0)), Date.UTC(2026, 11, 2, 8, 0))
      eq(Model.nextLaMidnightMs(Date.UTC(2026, 10, 1, 6, 30)), Date.UTC(2026, 10, 1, 7, 0))
      eq(Model.nextLaMidnightMs(Date.UTC(2026, 2, 8, 9, 30)), Date.UTC(2026, 2, 9, 7, 0))'
run_case resets "sessionWindow, resetChip (reset clock label, fire time with buffer), RESET_TRIGGER and RESET_KINDS_FOR for six agents as consts, nextLaMidnightMs across both DST changes"

lib_case status '
      var rows = [
        ["draft", null, 0xF03EB, "draft", "soft"],
        ["armed", null, 0xF0150, "armed", "readable"],
        ["armed", "reset", 0xF051F, "waits for reset", "harness"],
        ["armed", "limit", 0xF051F, "waits for reset", "harness"],
        ["armed", "transient", 0xF06B0, "retrying", "readable"],
        ["armed", "busy", 0xF051F, "deferred", "readable"],
        ["armed", "deferred", 0xF051F, "deferred", "readable"],
        ["running", null, 0xF040A, "running", "accent"],
        ["done", null, 0xF012C, "done", "ok"],
        ["failed", null, 0xF0156, "failed", "bad"],
        ["limit", null, 0xF0028, "limit hit", "warn"],
        ["busy", null, 0xF0028, "session busy", "warn"],
        ["skipped", null, 0xF04AD, "skipped", "readable"],
        ["missed", null, 0xF02DA, "missed", "warn"],
        ["interrupted", null, 0xF0156, "interrupted", "warn"],
        ["paused", null, 0xF03E4, "paused", "warn"],
        ["needs_confirm", null, 0xF0028, "check job", "warn"],
        ["disarmed", null, 0xF0156, "disarmed", "soft"],
        ["gave_up", null, 0xF0156, "gave up", "bad"]
      ]
      for (var i = 0; i < rows.length; i++) {
        var s = Model.statusSpec(rows[i][0], rows[i][1])
        eq([rows[i][0], rows[i][1], s.glyph, s.word, s.tone], [rows[i][0], rows[i][1], root.cp(rows[i][2]), rows[i][3], rows[i][4]])
      }'
run_case status "statusSpec covers every row of the status vocabulary (glyph, word, tone)"

lib_case words '
      check(root.py !== null)
      var reasons = root.py ? root.py.reasons : []
      for (var i = 0; i < reasons.length; i++) eq([reasons[i], Model.reasonSentence(reasons[i])], [reasons[i], root.py.sentences[reasons[i]]])
      eq(Model.reasonSentence("late"), "The computer was off or asleep past the grace time.")
      eq(Model.reasonSentence("overage"), "Usage credits are in use, so it does not retry.")
      var six = ["claude", "opencode", "codex", "gemini", "cursor", "pi"]
      eq(six.map(function (h) { return Model.harnessName(h) }), ["Claude Code", "OpenCode", "Codex", "Gemini CLI", "Cursor Agent", "Pi"])
      eq(six.map(function (h) { return Model.cliName(h) }), ["claude", "opencode", "codex", "gemini", "cursor-agent", "pi"])
      if (root.py) {
        eq(root.py.harnessIds, six)
        eq(six.map(function (h) { return Model.harnessName(h) }), six.map(function (h) { return root.py.harnessNames[h] }))
        eq(six.map(function (h) { return Model.cliName(h) }), six.map(function (h) { return root.py.cliNames[h] }))
      }
      var noon = root.at(2026, 9, 14, 12, 0)
      var failed = root.job("o1", "failed", null, null)
      failed.state.reason = "failed"
      failed.state.lastRun = { runId: "o1-g1", startedAt: Math.floor(noon / 1000) - 252, endedAt: Math.floor(noon / 1000) - 60, outcome: "failed", exit: 1, signal: null, bytesDropped: 0 }
      var fs = String(Model.outcomeSentence(failed, noon))
      check(fs.indexOf("Failed.") === 0)
      var missed = root.job("o2", "missed", null, Math.floor(root.at(2026, 9, 14, 9, 0) / 1000))
      missed.state.reason = "late"
      var ms = String(Model.outcomeSentence(missed, noon))
      check(ms.indexOf("Missed.") === 0)
      check(ms.indexOf("09:00") > 0)
      var limited = root.job("o3", "armed", "limit", Math.floor(root.at(2026, 9, 14, 20, 10) / 1000))
      check(String(Model.outcomeSentence(limited, noon)).indexOf("Limit hit.") === 0)
      check((fs + ms).indexOf(root.cp(0x2014)) < 0)'
run_case words "reasonSentence matches notify.REASON_SENTENCES for every consts.REASONS; six agent and CLI names as consts; outcome sentences"

lib_case lists '
      eq(Model.isQueue({ queue: true }), true)
      eq(Model.isQueue({ queue: false }), false)
      eq(Model.isQueue({}), false)
      var a = root.job("a", "draft", null, null); a.updatedAt = 10
      var b = root.job("b", "draft", null, null); b.updatedAt = 20
      var c = root.job("c", "armed", null, 1789500200)
      var d = root.job("d", "armed", null, 1789500100)
      var e = root.job("e", "running", null, 1789500300)
      eq(root.ids([a, b, c, d, e].sort(Model.jobSort)), ["e", "d", "c", "b", "a"])
      var now = root.at(2026, 9, 14, 7, 22)
      var nowSec = Math.floor(now / 1000)
      var t1 = root.job("t1", "armed", null, Math.floor(root.at(2026, 9, 14, 10, 10) / 1000))
      var t2 = root.job("t2", "armed", null, Math.floor(root.at(2026, 9, 15, 9, 0) / 1000))
      var r1 = root.job("r1", "running", null, nowSec - 60)
      var d1 = root.job("d1", "draft", null, null)
      var m1 = root.job("m1", "missed", null, nowSec - 3600)
      var groups = Model.groupByDay([t1, t2, r1, d1, m1], now, "fireAt")
      var byKey = {}
      for (var i = 0; i < groups.length; i++) byKey[groups[i].key] = groups[i]
      eq(groups[0].key, "attention")
      eq(byKey["attention"].word, "Needs attention")
      eq(byKey["now"].word, "Now")
      eq(byKey["drafts"].word, "Drafts")
      eq(byKey["2026-09-14"].word, "Today")
      eq(byKey["2026-09-15"].word, "Tomorrow")
      eq(root.ids(byKey["2026-09-14"].jobs), ["t1"])
      eq(root.ids(byKey["now"].jobs), ["r1"])
      var done = root.job("h1", "done", null, null)
      done.state.lastRun = { runId: "h1-g1", startedAt: nowSec - 87000, endedAt: nowSec - 86400, outcome: "done", exit: 0, signal: null, bytesDropped: 0 }
      var history = Model.groupByDay([done], now, "endedAt")
      eq(history.length, 1)
      eq(history[0].word, "Yesterday")
      var f = root.job("f1", "armed", null, 1789500000)
      f.label = "Nightly triage"
      check(Model.matchesFilter(f, "TRIAGE"))
      check(Model.matchesFilter(f, "proj/api"))
      check(Model.matchesFilter(f, "claude code"))
      check(Model.matchesFilter(f, "armed"))
      check(!Model.matchesFilter(f, "zzzz"))'
run_case lists "isQueue, jobSort, groupByDay (attention first, Now, Drafts, days) and matchesFilter"

lib_case text '
      eq(Model.shortPath("/home/u/proj/api", "/home/u"), "~/proj/api")
      eq(Model.shortPath("/etc/xdg", "/home/u"), "/etc/xdg")
      var el = Model.elideMiddle("3f2a0c19-1111-4222-8333-444455556c19", 8)
      check(el.length <= 8)
      check(el.indexOf(root.cp(0x2026)) > 0)
      check(el.charAt(0) === "3" && el.charAt(el.length - 1) === "9")
      eq(Model.elideMiddle("short", 8), "short")
      eq(Model.utf8Bytes("abc"), 3)
      eq(Model.utf8Bytes(root.cp(0x17C)), 2)
      eq(Model.utf8Bytes(root.cp(0x20AC)), 3)
      eq(Model.utf8Bytes(root.cp(0x1F600)), 4)
      eq(Model.defaultLabel("\n\nFix the flaky test\nsecond line"), "Fix the flaky test")
      check(Model.defaultLabel("x".repeat(100)).length <= 40)'
run_case text "shortPath, elideMiddle, utf8Bytes and defaultLabel"

lib_case drafts '
      var settings = { schemaVersion: 1, defaultHarness: "codex", defaultLevel: "unattended", resetMarginSec: 120,
                       eveningTime: "23:00", morningTime: "07:00", notify: "all", motion: "full",
                       defaultAllowPaid: true, limitsShown: "auto", lastSeenAt: null }
      var agents = { claude: { harness: "claude", enabled: true }, opencode: { harness: "opencode", enabled: true },
                     codex: { harness: "codex", enabled: false }, gemini: { harness: "gemini", enabled: true },
                     cursor: { harness: "cursor", enabled: true }, pi: { harness: "pi", enabled: true } }
      var draft = Model.draftFromSettings(settings, agents)
      eq(draft.harness, "claude")
      eq(draft.level, "unattended")
      eq([draft.target.mode, draft.target.sessionId, draft.target.cwd, draft.target.allowNonGit, draft.target.sessionPath], ["new", null, null, false, null])
      eq([draft.allowPaid, draft.provider], [true, null])
      settings.defaultAllowPaid = false
      eq(Model.draftFromSettings(settings, agents).allowPaid, false)
      var pij = root.job("fedcba9876543210", "done", null, null)
      pij.harness = "pi"; pij.provider = "openai-codex"; pij.model = "gpt-5.5"; pij.allowPaid = true
      pij.target.mode = "fork"; pij.target.sessionPath = "/home/u/.pi/agent/sessions/--home-u-proj-api--/s.jsonl"
      pij.trigger.kind = "codex_window_reset"
      var pid = Model.draftFromJob(pij, "x", "duplicate")
      eq([pid.harness, pid.provider, pid.model, pid.allowPaid, pid.target.sessionPath, pid.trigger.kind],
         ["pi", "openai-codex", "gpt-5.5", true, pij.target.sessionPath, "codex_window_reset"])
      var legacy = root.job("0123456789abcde0", "armed", null, 1789500000)
      legacy.harness = "opencode"; legacy.trigger.kind = "claude_5h_reset"
      eq(Model.draftFromJob(legacy, "", "rearm").trigger.kind, "now")
      eq(draft.trigger.kind, "now")
      eq(draft.limits, {})
      eq(draft.model, null)
      eq(draft.prompt, "")
      settings.defaultHarness = "gemini"
      eq(Model.draftFromSettings(settings, agents).harness, "gemini")
      var src = root.job("0123456789abcdef", "failed", null, null)
      src.harness = "opencode"
      src.level = "unattended"
      src.model = "anthropic/claude-sonnet-5"
      var edit = Model.draftFromJob(src, "hello", "edit")
      eq([edit.id, edit.harness, edit.level, edit.model, edit.prompt, edit.target.mode],
         ["0123456789abcdef", "opencode", "unattended", "anthropic/claude-sonnet-5", "hello", "resume"])
      var dup = Model.draftFromJob(src, "hello", "duplicate")
      check(dup.id === undefined || dup.id === null)
      var rearm = Model.draftFromJob(src, null, "rearm")
      check(rearm.id === undefined || rearm.id === null)
      eq(rearm.harness, "opencode")'
run_case drafts "draftFromSettings skips a disabled default agent and takes defaultAllowPaid; draftFromJob keeps the id only for edit, carries provider and sessionPath, drops a legacy reset"

lib_case tint '
      var themes = [["catppuccin-latte", "#eff1f5"], ["tokyo-night", "#1a1b26"], ["gruvbox", "#282828"],
                    ["nord", "#2e3440"], ["rose-pine", "#faf4ed"], ["matte-black", "#121212"]]
      var brand = { claude: "#d97757", opencode: "#5c9cf5", codex: "#10a37f", gemini: "#847ace", cursor: "#bb64d8", pi: "#97c639" }
      for (var id in brand) eq([id, String(Tint.BRAND[id]).toLowerCase()], [id, brand[id]])
      eq(Object.keys(Tint.BRAND).sort(), Object.keys(brand).sort())
      eq([String(Tint.MARK_BRAND.cursor).toLowerCase(), String(Tint.MARK_BRAND.pi).toLowerCase()], ["#8a8a8a", "#c9c9c9"])
      eq(Object.keys(Tint.MARK_BRAND).sort(), ["cursor", "pi"])
      eq(Tint.GEMINI_STOPS.map(function (x) { return String(x).toLowerCase() }), ["#4796e4", "#847ace", "#c3677f"])
      check(Math.abs(Tint.contrast("#000000", "#ffffff") - 21) < 0.01)
      var over = function (fg, bg, al) {
        var f = Tint.rgbOf(fg), b = Tint.rgbOf(bg)
        return { r: f.r * al + b.r * (1 - al), g: f.g * al + b.g * (1 - al), b: f.b * al + b.b * (1 - al) }
      }
      var tokyo = Tint.alphaFor("#a9b1d6", "#1a1b26", 4.5, 0.60)
      check(tokyo >= 0.68 && tokyo <= 0.70)
      check(Tint.contrast(over("#a9b1d6", "#1a1b26", tokyo), "#1a1b26") >= 4.5)
      eq(Tint.alphaFor("#cdd6f4", "#1e1e2e", 4.5, 0.60), 0.6)
      var latte = Tint.alphaFor("#4c4f69", "#eff1f5", 4.5, 0.60)
      check(latte <= 1 && (latte === 1 || Tint.contrast(over("#4c4f69", "#eff1f5", latte), "#eff1f5") >= 4.5))
      eq(Tint.alphaFor("#4c4f69", "#eff1f5", 21, 0.60), 1)
      var hs = ["claude", "opencode", "codex", "gemini", "cursor", "pi"]
      for (var t = 0; t < themes.length; t++) {
        var bg = themes[t][1]
        for (var k = 0; k < hs.length; k++) {
          eq([themes[t][0], hs[k], 3.0, Tint.contrast(Tint.harnessInk(hs[k], bg, 3.0), bg) >= 3.0], [themes[t][0], hs[k], 3.0, true])
          eq([themes[t][0], hs[k], 4.5, Tint.contrast(Tint.harnessInk(hs[k], bg, 4.5), bg) >= 4.5], [themes[t][0], hs[k], 4.5, true])
          eq([themes[t][0], hs[k], 7.0, Tint.contrast(Tint.harnessInk(hs[k], bg, 7.0), bg) >= 7.0], [themes[t][0], hs[k], 7.0, true])
          eq([themes[t][0], hs[k], "mark", Tint.contrast(Tint.harnessMark(hs[k], bg), bg) >= 3.0], [themes[t][0], hs[k], "mark", true])
        }
        eq([themes[t][0], "ok", Tint.contrast(Tint.solved(150, 60, bg, 4.5), bg) >= 4.5], [themes[t][0], "ok", true])
        eq([themes[t][0], "warn", Tint.contrast(Tint.solved(38, 85, bg, 4.5), bg) >= 4.5], [themes[t][0], "warn", true])
        eq([themes[t][0], "bad", Tint.contrast(Tint.ink("#a55555", bg, 4.5), bg) >= 4.5], [themes[t][0], "bad", true])
        var stops = Tint.geminiStops(bg, 3.0)
        eq(stops.length, 3)
        for (var s = 0; s < stops.length; s++) eq([themes[t][0], "stop", s, Tint.contrast(stops[s], bg) >= 3.0], [themes[t][0], "stop", s, true])
      }'
run_case tint "Tint: six brand inks reach 3.0, 4.5 and 7.0 and six marks (Cursor and Pi mono) reach 3.0 on catppuccin-latte, tokyo-night, gruvbox, nord, rose-pine and matte-black"

fi

# ---------------------------------------------------------------- qmllint

echo
echo "=== qmllint (Qt 6, -I lint, .qmllint.ini) ==="
mapfile -t QFILES < <(cd "$REPO" && find . -name '*.qml' -not -path './tests/*' -not -path './.git/*' | sed 's|^\./||' | LC_ALL=C sort)
if [ ! -x "$QLINT" ]; then
  no "Qt 6 qmllint" "no $QLINT on this machine"
else
  # A bare runner has Qt but no Quickshell: an import folder holding every Qt module except Quickshell,
  # used with --bare, proves the stand-ins in lint/ resolve every import on their own.
  BARE="$T/bare-imports"
  mkdir -p "$BARE"
  for entry in /usr/lib/qt6/qml/*; do
    case "$(basename "$entry")" in Quickshell*) ;; *) ln -s "$entry" "$BARE/$(basename "$entry")" ;; esac
  done
  lint_once() { # <label> <file> <qmllint args...>
    local label="$1" f="$2" out rc n
    shift 2
    out="$(cd "$REPO" && timeout 180 "$QLINT" "$@" "$f" 2>&1)"; rc=$?
    n="$(printf '%s\n' "$out" | grep -cE '^(Warning|Error):' || true)"
    if [ "$rc" -eq 0 ] && [ "${n:-0}" -eq 0 ]; then
      return 0
    fi
    LINT_DETAIL="$label: exit $rc, ${n:-0} warning(s): $(printf '%s\n' "$out" | grep -m2 -E '^(Warning|Error):' | sed "s|$REPO/||g" | tr '\n' ' ')"
    return 1
  }
  for f in "${QFILES[@]}"; do
    LINT_DETAIL=""
    if lint_once "system modules" "$f" -I lint && lint_once "stand-ins only" "$f" --bare -I "$BARE" -I lint; then
      ok "qmllint $f"
    else
      no "qmllint $f" "$LINT_DETAIL"
    fi
  done
fi

# ---------------------------------------------------------------- Panel smoke

echo
echo "=== Panel.qml offscreen with a stub service ==="
if [ ! -x "$QML" ]; then
  no "Panel.qml instantiates" "no Qt 6 qml runtime"
elif [ ! -f "$REPO/Panel.qml" ]; then
  no "Panel.qml instantiates" "Panel.qml is missing"
else
  P="$T/panel"
  mkdir -p "$P/imports"
  cp "$REPO"/*.qml "$P/"
  cp -r "$REPO/lib" "$REPO/components" "$P/"
  cp -r "$REPO/lint/qs" "$REPO/lint/Quickshell" "$P/imports/"
  EDITION="$(env -i HOME="$T/home" LANG=C.UTF-8 PATH=/usr/bin "$PY" -I -S -B "$REPO/bin/ap4a" edition 2>/dev/null)" || EDITION=""
  case "$EDITION" in '{"ok":true'*) ;; *) EDITION='{"ok":false,"edition":{},"levels":[],"harnesses":[],"caps":{}}' ;; esac

  cat >"$P/Smoke.qml" <<QML
import QtQuick

Item {
  id: host
  width: 1400
  height: 1000
  property int failures: 0
  property var editionRes: $EDITION
  property var foundSheets: ({})
  property int step: 0

  function fail(msg) { console.log("SMOKE-FAIL " + msg); host.failures++ }

  function sampleJob(id, status, fireAtSec, queue) {
    return {
      id: id, label: "Sample " + id, harness: "claude", createdAt: 1789400000, updatedAt: 1789400000,
      cli: { link: "/usr/bin/claude", version: null },
      target: { mode: "resume", sessionId: "3f2a0c19-1111-4222-8333-444455556666", newSessionId: null,
                cwd: "/home/u/proj", title: "proj", allowNonGit: false },
      level: "plan", limits: { maxTurns: 15, budgetUsd: 5, runtimeSec: 5400 }, model: null,
      trigger: { kind: "at", fireAt: fireAtSec, delaySec: null, marginSec: 120, weeklyPolicy: "defer", graceSec: 900 },
      promptSha256: "", promptBytes: 12, promptAvailable: queue, commandDigest: "", digest: "",
      allowPaid: false, provider: null,
      state: { status: status, wait: null, reason: null, gen: 1, fireAt: fireAtSec, unit: null, armedAt: null,
               pluginDir: null, basis: null, runSessionId: null, runSessionPath: null, defers: 0, limitRetries: 0, transientRetries: 0,
               busyDefers: 0, unknownRetries: 0,
               lastRun: queue ? null : { runId: id + "-g1", startedAt: 1789400100, endedAt: 1789400700, outcome: "done", exit: 0, signal: null, bytesDropped: 0 },
               lastEvent: null },
      fireAtMs: fireAtSec ? fireAtSec * 1000 : null,
      gated: false, limitSource: "claude",
      canRunNow: true, canDisarm: status === "armed", canEdit: status !== "running",
      canDelete: status !== "armed" && status !== "running", queue: queue
    }
  }

  // A v2 Pi job: provider, model, paid usage on, a fork of a session file.
  function piJob(id, fireAtSec) {
    var j = host.sampleJob(id, "armed", fireAtSec, true)
    j.harness = "pi"; j.label = "Pi in proj"; j.provider = "openai-codex"; j.model = "gpt-5.5"; j.allowPaid = true
    j.cli = { link: "/home/u/.local/share/mise/installs/pi/latest/pi/pi", version: null }
    j.target = { mode: "fork", sessionId: "9b1d7e42-aaaa-4bbb-8ccc-ddddeeeeffff", newSessionId: null, cwd: "/home/u/proj",
                 title: "proj", allowNonGit: false,
                 sessionPath: "/home/u/.pi/agent/sessions/--home-u-proj--/2026-09-15T08-00-00-000Z_9b1d7e42-aaaa-4bbb-8ccc-ddddeeeeffff.jsonl" }
    j.limits = { maxTurns: null, budgetUsd: null, runtimeSec: 5400 }
    j.trigger.kind = "codex_window_reset"
    j.limitSource = "codex"
    return j
  }

  function provider(id, name, harness, stale, windows, headlineKey, computed) {
    return { id: id, name: name, tier: computed ? "" : "Plan", statusText: "", scope: null, source: computed ? "computed" : "record",
             readable: true, unreadableReason: null, updatedAtMs: computed ? null : nowStartMs - 120000, ageSec: computed ? null : 120,
             stale: stale, cadenceSec: computed ? null : 900, keptFromLastPoll: false, harnesses: [harness], relevant: true,
             headlineKey: headlineKey, windows: windows }
  }
  readonly property double nowStartMs: Date.now()
  function win(key, label, shortLabel, kind, percent, resetsInSec, computed) {
    return { key: key, label: label, title: null, shortLabel: shortLabel, kind: kind, percent: percent,
             over: percent !== null && percent > 1, resetsAt: Math.floor(host.nowStartMs / 1000) + resetsInSec, sliding: false,
             source: computed ? "computed" : "record", bindable: true }
  }

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
    id: stubService
    signal notice(string text, string kind)
    signal jobsUpdated()

    readonly property double nowStart: Date.now()
    property bool ready: true
    property string setupProblem: ""
    property var edition: host.editionRes.edition
    property var levels: host.editionRes.levels
    property var harnesses: host.editionRes.harnesses
    property var caps: host.editionRes.caps
    property var jobs: [host.sampleJob("a1a1a1a1a1a1a1a1", "armed", Math.floor(nowStart / 1000) + 7200, true),
                        host.piJob("e4e4e4e4e4e4e4e4", Math.floor(nowStart / 1000) + 10800),
                        host.sampleJob("d2d2d2d2d2d2d2d2", "draft", null, true),
                        host.sampleJob("c3c3c3c3c3c3c3c3", "done", null, false)]
    property var jobsById: ({})
    property var listMeta: ({ killSwitch: false, enabledInShell: true, linger: false, reconciledAt: null, nowMs: nowStart })
    // Usage v2 (CONTRACT-V2-DELTA 3.7): a stale record with two windows, a fresh one, and a computed reset.
    property var providers: [
      host.provider("claude", "Claude", "claude", true,
        [host.win("claude:5-hour", "Session (5-hour)", "5-hour", "session", 0.93, 600, false),
         host.win("claude:weekly", "Weekly (7-day)", "Weekly", "weekly", 0.41, 86400, false)], "claude:5-hour", false),
      host.provider("codex", "Codex", "codex", false,
        [host.win("codex:weekly", "Weekly (7-day)", "Weekly", "weekly", 1.02, 7200, false)], "codex:weekly", false),
      host.provider("gemini-daily", "Gemini CLI daily", "gemini", false,
        [host.win("gemini-daily:daily", "", "daily", "daily", null, 20000, true)], "gemini-daily:daily", true)
    ]
    property var usage: ({ ok: true, nowMs: stubService.nowStart, providers: stubService.providers })
    property string usageError: ""
    property var agents: ({
      claude: { harness: "claude", name: "Claude Code", cliName: "claude", available: true, link: "/usr/bin/claude", real: "/usr/bin/claude", version: "2.1.270", loggedIn: null, reason: null, canFork: true, triggers: ["now", "in", "at", "claude_5h_reset"], enabled: true, armable: true, gated: false },
      opencode: { harness: "opencode", name: "OpenCode", cliName: "opencode", available: true, link: "/usr/bin/opencode", real: "/usr/bin/opencode", version: "1.18.30", loggedIn: null, reason: null, canFork: true, triggers: ["now", "in", "at", "zen_free_reset", "go_window_reset"], enabled: true, armable: true, gated: false },
      codex: { harness: "codex", name: "Codex", cliName: "codex", available: false, link: null, real: null, version: null, loggedIn: false, reason: "not_found", canFork: true, triggers: ["now", "in", "at", "codex_window_reset"], enabled: false, armable: false, gated: false },
      gemini: { harness: "gemini", name: "Gemini CLI", cliName: "gemini", available: true, link: "/usr/lib/node_modules/@google/gemini-cli/bundle/gemini.js", real: "/usr/lib/node_modules/@google/gemini-cli/bundle/gemini.js", version: "0.50.0", loggedIn: null, reason: null, canFork: false, triggers: ["now", "in", "at", "gemini_daily_reset"], enabled: true, armable: true, gated: false },
      cursor: { harness: "cursor", name: "Cursor Agent", cliName: "cursor-agent", available: true, link: "/usr/bin/cursor-agent", real: "/usr/bin/cursor-agent", version: "2026.09.10-fd3934a", loggedIn: null, reason: "gated", canFork: false, triggers: ["now", "in", "at"], enabled: true, armable: false, gated: true },
      pi: { harness: "pi", name: "Pi", cliName: "pi", available: true, link: "/home/u/.local/share/mise/installs/pi/latest/pi/pi", real: "/home/u/.local/share/mise/installs/pi/0.74.0/pi/pi", version: "0.74.0", loggedIn: null, reason: null, canFork: true, triggers: ["now", "in", "at", "codex_window_reset"], enabled: true, armable: true, gated: false }
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
    property var nextJob: jobs[0]
    property int armedCount: 2
    property int runningCount: 0
    property int attentionCount: 0
    property bool anyRunning: false
    property int viewers: 0
    property double lastSeenAt: Math.floor(nowStart / 1000) - 3600
    property double attentionSeenAt: lastSeenAt
    property int draftCount: 1
    property int doneSinceSeen: 1
    property int problemCount: 0
    property int authProblemCount: 0
    property var barState: ({ state: "soon", glyph: "", text: "2h00", tone: "warn", sentence: "The next job fires in 2h.", pulse: false })
    property string barLabel: "Next run"
    property var calls: []

    function answer(name, cb) {
      calls = calls.concat([name])
      if (cb) cb({ ok: false, code: "not_ready", message: "Still starting." })
    }
    function refresh() { answer("refresh", null) }
    function refreshUsage() { answer("refreshUsage", null) }
    function refreshAgents(checkLogin) { answer("refreshAgents", null) }
    function loadSessions(harness, cwd) { answer("loadSessions", null) }
    function loadModels(harness, refresh) { answer("loadModels", null) }
    function loadTimeline(dayStartMs) { answer("loadTimeline", null) }
    function markSeen() { answer("markSeen", null) }
    function setLimitsShown(value, cb) { answer("setLimitsShown", cb) }
    function getJob(id, cb) { answer("getJob", cb) }
    function preview(draft, cb) { answer("preview", cb) }
    function createAndArm(draft, cb) { answer("createAndArm", cb) }
    function saveDraft(draft, cb) { answer("saveDraft", cb) }
    function arm(id, digest, cb) { answer("arm", cb) }
    function runNow(id, digest, cb) { answer("runNow", cb) }
    function disarm(id, cb) { answer("disarm", cb) }
    function deleteJob(id, cb) { answer("deleteJob", cb) }
    function cancelAll(cb) { answer("cancelAll", cb) }
    function reschedule(id, epochSec, cb) { answer("reschedule", cb) }
    function swap(a, b, cb) { answer("swap", cb) }
    function shift(ids, deltaSec, cb) { answer("shift", cb) }
    function setSettings(obj, cb) { answer("setSettings", cb) }
    function copyResume(id, cb) { answer("copyResume", cb) }
    function reconcileNow(cb) { answer("reconcileNow", cb) }
    function viewerOpened() { viewers = viewers + 1 }
    function viewerClosed() { viewers = Math.max(0, viewers - 1) }
    function levelFor(id) {
      for (var i = 0; i < levels.length; i++) if (levels[i].id === id) return levels[i]
      return null
    }
    function agentFor(harness) { return agents[harness] || null }
  }

  function collectSheets(item, depth, seen) {
    if (!item || depth > 40 || seen.indexOf(item) >= 0) return
    seen.push(item)
    if (typeof item.open === "function" && typeof item.close === "function" && typeof item.closed === "function"
        && typeof item.handleKey === "function") {
      // Six sheets: Limits runs a job at a reset, Shift shifts, Model and Session both pick (only the
      // model sheet picks a row), Sign in re-checks an agent, Settings is the rest.
      var name = typeof item.runAtRequested === "function" ? "limits"
        : typeof item.shifted === "function" ? "shift"
        : typeof item.picked === "function" ? (typeof item.pickRow === "function" ? "model" : "session")
        : typeof item.recheck === "function" ? "signin"
        : "settings"
      var copy = host.foundSheets
      copy[name] = item
      host.foundSheets = copy
    }
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) host.collectSheets(kids[i], depth + 1, seen)
  }

  Loader {
    id: loader
    active: false
    source: "Panel.qml"
    onLoaded: {
      item.bar = stubBar
      item.anchorItem = anchor
      item.service = stubService
    }
  }

  Timer {
    id: ticker
    interval: 60
    repeat: true
    onTriggered: host.next()
  }

  function next() {
    var p = loader.item
    var s = host.step++
    if (!p) { host.fail("Panel.qml did not load (status " + loader.status + ")"); return host.finish() }
    if (s === 0) { p.open() }
    else if (s === 1) {
      if (!p.opened) host.fail("the panel did not open")
      if (stubService.viewers !== 1) host.fail("opening did not call service.viewerOpened()")
      p.showView("queue")
    }
    else if (s === 2) { if (p.view !== "queue") host.fail("showView(queue) left view " + p.view); p.showView("history") }
    else if (s === 3) { if (p.view !== "history") host.fail("showView(history) left view " + p.view); p.showView("compose") }
    else if (s === 4) {
      if (p.view !== "compose") host.fail("showView(compose) left view " + p.view)
      host.collectSheets(p, 0, [])
      var names = ["session", "settings", "shift", "model", "limits", "signin"]
      for (var i = 0; i < names.length; i++) if (!host.foundSheets[names[i]]) host.fail("no " + names[i] + " sheet was instantiated")
    }
    else if (s === 5 && host.foundSheets.session) { p.sheet = "session"; host.foundSheets.session.open({ harness: "claude", cwd: "/home/u/proj" }) }
    else if (s === 6 && host.foundSheets.session) { host.foundSheets.session.close(); p.sheet = "" }
    else if (s === 7 && host.foundSheets.settings) { p.sheet = "settings"; host.foundSheets.settings.open() }
    else if (s === 8 && host.foundSheets.settings) { host.foundSheets.settings.close(); p.sheet = "" }
    else if (s === 9 && host.foundSheets.shift) { p.sheet = "shift"; host.foundSheets.shift.open({ ids: ["a1a1a1a1a1a1a1a1"] }) }
    else if (s === 10 && host.foundSheets.shift) { host.foundSheets.shift.close(); p.sheet = "" }
    else if (s === 11 && host.foundSheets.model) { p.sheet = "model"; host.foundSheets.model.open({ harness: "pi", provider: "openai-codex", model: "gpt-5.5", allowPaid: false }) }
    else if (s === 12 && host.foundSheets.model) { host.foundSheets.model.close(); p.sheet = "" }
    else if (s === 13 && host.foundSheets.limits) { p.sheet = "limits"; host.foundSheets.limits.open({}) }
    else if (s === 14 && host.foundSheets.limits) { host.foundSheets.limits.close(); p.sheet = "" }
    else if (s === 15 && host.foundSheets.signin) { p.sheet = "signin"; host.foundSheets.signin.open({ harness: "codex" }) }
    else if (s === 16 && host.foundSheets.signin) { host.foundSheets.signin.close(); p.sheet = "" }
    else if (s === 17) { p.close() }
    else if (s >= 18) {
      if (p.opened) host.fail("the panel did not close")
      if (stubService.viewers !== 0) host.fail("closing did not call service.viewerClosed()")
      host.finish()
    }
  }

  function finish() {
    ticker.stop()
    Qt.exit(host.failures)
  }

  Component.onCompleted: {
    if (!host.editionRes.ok) host.fail("ap4a edition did not answer ok")
    loader.active = true
    ticker.start()
  }
}
QML

  out="$(cd "$P" && timeout 120 "$QML" -I "$P/imports" Smoke.qml 2>&1)"; rc=$?
  smoke_fail="$(printf '%s\n' "$out" | grep 'SMOKE-FAIL' | sed 's/.*SMOKE-FAIL //' || true)"
  warnings="$(printf '%s\n' "$out" | grep -v 'SMOKE-FAIL' \
    | grep -E '\.qml:[0-9]+|\.js:[0-9]+|qml: |QQmlComponent|ReferenceError|TypeError|Cannot assign|Unable to assign|is not a type|Binding loop|failed to load|not installed' \
    | sed "s|file://$P/||g" || true)"
  if [ "$rc" -eq 0 ] && [ -z "$smoke_fail" ]; then
    ok "Panel.qml loads, opens, shows Compose, Queue and History, opens all six sheets (v2 stub service) and closes"
  else
    no "Panel.qml loads, opens, shows Compose, Queue and History, opens all six sheets (v2 stub service) and closes" \
       "exit $rc: $(printf '%s' "${smoke_fail:-$(printf '%s\n' "$out" | tail -3)}" | tr '\n' ' ' | cut -c1-600)"
  fi
  if [ -z "$warnings" ]; then
    ok "no QML warning while the panel ran"
  else
    no "no QML warning while the panel ran" "$(printf '%s\n' "$warnings" | head -5 | tr '\n' ' ' | cut -c1-900)"
  fi
fi

echo
printf '%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
