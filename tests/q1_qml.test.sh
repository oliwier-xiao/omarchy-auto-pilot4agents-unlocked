#!/bin/bash
# tests/q1_qml.test.sh: lib/Model.js, lib/Tint.js and the shared components, run in
# a real Qt 6 QML engine (offscreen), not under node: the same engine the shell uses,
# with its own Date, String and color behaviour.
#
# Each case is its own process and the exit code carries the verdict: 0 passed,
# n = the first failing check in that case, 250 = the case threw.
#
# Local time is pinned to Europe/Warsaw so every expected clock string below is a
# fact rather than a property of the machine running it. The Los Angeles midnights
# were computed with Python's zoneinfo, across both 2026 DST changes.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QML=/usr/lib/qt6/bin/qml

pass=0; fail=0
ok() { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
no() { printf '  FAIL %s\n         %s\n' "$1" "$2"; fail=$((fail+1)); }

if [ ! -x "$QML" ]; then
  echo "  skip the QML suite (no Qt 6 qml runtime at $QML)"
  exit 0
fi

T="$(mktemp -d)" || exit 2
trap 'rm -rf "$T"' EXIT INT TERM
mkdir -p "$T/lib" "$T/components"
cp "$REPO"/lib/*.js "$T/lib/"
cp "$REPO"/components/*.qml "$T/components/"

export TZ=Europe/Warsaw
export QT_QPA_PLATFORM=offscreen
export QT_FORCE_STDERR_LOGGING=1
# No compiled-QML cache under the real ~/.cache.
export QML_DISABLE_DISK_CACHE=1

# ---------------------------------------------------------------- harness

lib_case() { # <name> <js body>
  cat > "$T/Lib_$1.qml" <<QML
import QtQuick
import "lib/Model.js" as Model
import "lib/Tint.js" as Tint
import "lib/Edition.js" as Edition

Item {
  id: root
  property int n: 0
  property int firstFail: 0
  function check(c) { root.n++; if (!c && root.firstFail === 0) root.firstFail = root.n }
  function eq(a, b) {
    var same = JSON.stringify(a) === JSON.stringify(b)
    if (!same) console.warn("check " + (root.n + 1) + ": " + JSON.stringify(a) + " !== " + JSON.stringify(b))
    root.check(same)
  }
  function at(y, mo, d, h, mi, s) { return new Date(y, mo - 1, d, h, mi, s || 0).getTime() }

  Component.onCompleted: {
    try {
$2
    } catch (err) { console.warn("threw: " + err); root.firstFail = 250 }
    Qt.exit(root.firstFail > 249 ? 250 : root.firstFail)
  }
}
QML
}

run_case() { # <file> <description>
  local out rc
  out="$(timeout 60 "$QML" "$T/$1" 2>&1)"; rc=$?
  if [ "$rc" -eq 0 ]; then ok "$2"
  else no "$2" "rc=$rc $(printf '%s' "$out" | grep -E 'check [0-9]+|threw|Error|Warning' | head -4 | tr '\n' ' ')"
  fi
}

# ---------------------------------------------------------------- Model: durations

echo "=== lib/Model.js ==="

lib_case durations '
      var now = at(2026, 9, 14, 7, 22)
      check(now === 1789363320000)
      eq(Model.clampInterval(NaN), 1000)
      eq(Model.clampInterval(-5), 1)
      eq(Model.clampInterval(0), 1)
      eq(Model.clampInterval(1500.4), 1500)
      eq(Model.clampInterval(720 * 3600 * 1000), 2147483647)
      eq(Model.clampInterval(Infinity), 2147483647)
      eq(Model.formatDuration(0), "0s")
      eq(Model.formatDuration(-4000), "0s")
      eq(Model.formatDuration(41000), "41s")
      eq(Model.formatDuration(92000), "1m 32s")
      eq(Model.formatDuration(69000), "1m 09s")
      eq(Model.formatDuration(120000), "2m")
      eq(Model.formatDuration(48 * 60000), "48m")
      eq(Model.formatDuration(168 * 60000), "2h 48m")
      eq(Model.formatDuration(167 * 60000 + 59995), "2h 48m")
      eq(Model.formatDuration(51 * 3600000), "2d 3h")
      eq(Model.formatCountdown(now + 168 * 60000, now), "in 2h 48m")
      eq(Model.formatCountdown(now + 92000, now), "in 1m 32s")
      eq(Model.formatCountdown(now, now), "firing")
      eq(Model.formatCountdown(now - 5000, now), "firing")
      eq(Model.formatRunning(now - 192000, now), "running 3m 12s")
      eq(Model.formatRunning(now - 12000, now), "running 12s")
      eq(Model.formatRunning(now - 187000, now), "running 3m 07s")
      eq(Model.formatRunLength(41000), "41s")
      eq(Model.formatRunLength(240000), "4m")
      eq(Model.formatRunLength(192000), "3m 12s")
      eq(Model.formatRunLength(840000), "14m")
      eq(Model.formatRunLength(3600000), "1h")
      eq(Model.formatRunLength(3900000), "1h 5m")
      eq(Model.formatRunLength(51 * 3600000), "2d 3h")
      eq(Model.formatBarCountdown(51 * 3600000), "2d3h")
      eq(Model.formatBarCountdown(168 * 60000), "2h48m")
      eq(Model.formatBarCountdown(125 * 60000), "2h05m")
      eq(Model.formatBarCountdown(22 * 3600000 + 42 * 60000), "22h")
      eq(Model.formatBarCountdown(48 * 60000), "48m")
      eq(Model.formatBarCountdown(30000), "<1m")
      eq(Model.formatBarCountdown(-5000), "<1m")'
run_case Lib_durations.qml "durations, countdowns and interval clamping"

lib_case calendar '
      var now = at(2026, 9, 14, 7, 22)
      eq(Model.formatClock(at(2026, 9, 14, 10, 10)), "10:10")
      eq(Model.formatClock(at(2026, 9, 14, 0, 5)), "00:05")
      eq(Model.formatClock(NaN), "--:--")
      eq(Model.dayKey(now), "2026-09-14")
      eq(Model.dayLabel(now, now), { word: "Today", date: "Mon 14 Sep" })
      eq(Model.dayLabel(at(2026, 9, 15, 0, 1), now), { word: "Tomorrow", date: "Tue 15 Sep" })
      eq(Model.dayLabel(at(2026, 9, 13, 23, 59), now), { word: "Yesterday", date: "Sun 13 Sep" })
      eq(Model.dayLabel(at(2026, 9, 16, 12, 0), now), { word: "Wed", date: "Wed 16 Sep" })
      eq(Model.resolvedLine(at(2026, 9, 14, 10, 10), now), "today, in 2h 48m")
      eq(Model.resolvedLine(at(2026, 9, 15, 0, 12), now), "tomorrow, in 16h 50m")
      eq(Model.resolvedLine(at(2026, 9, 16, 10, 22), now), "Wed 16 Sep, in 2d 3h")
      eq(Model.dayLabel(at(2026, 10, 26, 0, 30), at(2026, 10, 25, 23, 0)).word, "Tomorrow")
      eq(Model.dayLabel(at(2026, 10, 25, 23, 30), at(2026, 10, 25, 0, 30)).word, "Today")'
run_case Lib_calendar.qml "clock, day keys, day labels and resolved lines (DST day included)"

lib_case nudge '
      var now = at(2026, 9, 14, 7, 22)
      var t1013 = at(2026, 9, 14, 10, 13)
      eq(Model.nudged(t1013, 5, true, now), at(2026, 9, 14, 10, 15))
      eq(Model.nudged(t1013, -5, true, now), at(2026, 9, 14, 10, 10))
      eq(Model.nudged(t1013, 5, false, now), at(2026, 9, 14, 10, 18))
      eq(Model.nudged(at(2026, 9, 14, 10, 15), 5, true, now), at(2026, 9, 14, 10, 20))
      eq(Model.nudged(t1013, 60, true, now), at(2026, 9, 14, 11, 13))
      eq(Model.nudged(at(2026, 9, 14, 23, 58), 5, true, now), at(2026, 9, 15, 0, 0))
      eq(Model.nudged(at(2026, 9, 14, 7, 25), -5, true, now), at(2026, 9, 14, 7, 23))
      eq(Model.nudged(at(2026, 9, 14, 7, 25), -5, true, at(2026, 9, 14, 7, 22, 30)), at(2026, 9, 14, 7, 24))
      eq(Model.nudgedDays(at(2026, 9, 14, 10, 10), 1, now), at(2026, 9, 15, 10, 10))
      eq(Model.nudgedDays(at(2026, 9, 14, 10, 10), -1, now), at(2026, 9, 14, 7, 23))
      eq(Model.nudgedDays(at(2026, 10, 24, 10, 10), 1, now), at(2026, 10, 25, 10, 10))
      eq(Model.parseTyped("1405", now), { ms: at(2026, 9, 14, 14, 5), rolled: false })
      eq(Model.parseTyped("905", now), { ms: at(2026, 9, 14, 9, 5), rolled: false })
      eq(Model.parseTyped("14:05", now), { ms: at(2026, 9, 14, 14, 5), rolled: false })
      eq(Model.parseTyped("0700", now), { ms: at(2026, 9, 15, 7, 0), rolled: true })
      eq(Model.parseTyped("7:22", now), { ms: at(2026, 9, 15, 7, 22), rolled: true })
      eq(Model.parseTyped("2460", now), null)
      eq(Model.parseTyped("2400", now), null)
      eq(Model.parseTyped("14", now), null)
      eq(Model.parseTyped("abc", now), null)
      eq(Model.parseTyped("", now), null)
      eq(Model.presetTime("23:00", now), { ms: at(2026, 9, 14, 23, 0), tomorrow: false })
      eq(Model.presetTime("07:00", now), { ms: at(2026, 9, 15, 7, 0), tomorrow: true })
      eq(Model.presetTime("7:00", now), null)'
run_case Lib_nudge.qml "nudges snap to 5 minutes, clamp to a minute ahead; typed and preset times"

lib_case resets '
      var now = 1789363320000
      var resets = now / 1000 + 168 * 60
      var usage = { ok: true, nowMs: now,
        claude: { available: true, fetchedAtMs: now - 120000, ageSec: 120, stale: false, status: "", tierLabel: "Max 5x",
          windows: [ { label: "Session (5-hour)", kind: "session", percent: 0.22, resetsAt: resets, title: null },
                     { label: "Weekly (7-day)", kind: "weekly", percent: 0.03, resetsAt: resets + 259200, title: null } ] },
        codex: { available: true, updatedAtMs: now, stale: false, status: "",
          windows: [ { label: "5h window", kind: "other", percent: 0.4, resetsAt: resets + 3600, title: null },
                     { label: "Weekly (7-day)", kind: "weekly", percent: 1.0, resetsAt: resets + 7200, title: null } ] } }
      eq(Model.sessionWindow(usage).percent, 0.22)
      eq(Model.sessionWindow(null), null)
      eq(Model.resetChip(usage, "claude", now, 120), { kind: "claude_5h_reset", label: "At reset 10:10", fireAtMs: resets * 1000 + 120000, percent: 0.22, stale: false, available: true, reason: null })
      eq(Model.resetChip(usage, "opencode", now, 60).kind, "zen_free_reset")
      eq(Model.resetChip(usage, "pi", now, 60).kind, "codex_window_reset")
      eq(Model.resetChip(usage, "cursor", now, 60).kind, "")
      var stale = JSON.parse(JSON.stringify(usage)); stale.claude.stale = true
      var c2 = Model.resetChip(stale, "claude", now, 120)
      check(c2.available === true && c2.reason === "stale" && c2.stale === true)
      var none = Model.resetChip(null, "claude", now, 120)
      check(none.available === false && none.reason === "no_data" && none.fireAtMs === null)
      check(Model.resetChip({ claude: { available: false, windows: [] } }, "claude", now, 120).reason === "no_data")
      var past = JSON.parse(JSON.stringify(usage)); past.claude.windows[0].resetsAt = now / 1000 - 10
      var c3 = Model.resetChip(past, "claude", now, 120)
      check(c3.reason === "not_open" && c3.available === false)
      var nosession = JSON.parse(JSON.stringify(usage)); nosession.claude.windows.splice(0, 1)
      check(Model.resetChip(nosession, "claude", now, 120).reason === "not_open")
      var cx = Model.resetChip(usage, "codex", now, 120)
      check(cx.kind === "codex_window_reset" && cx.fireAtMs === (resets + 7200) * 1000 + 120000 && cx.percent === 1.0 && cx.label === "At reset 12:10")
      var cxNone = Model.resetChip({ codex: { available: true, windows: [] } }, "codex", now, 120)
      check(cxNone.available === false && cxNone.reason === "no_data")
      var gm = Model.resetChip(null, "gemini", now, 120)
      check(gm.kind === "gemini_daily_reset" && gm.fireAtMs === 1789369200000 + 120000 && gm.available === true && gm.reason === null)
      eq(gm.label, "At reset " + Model.formatClock(1789369200000))
      check(Model.resetChip(usage, "nope", now, 120).kind === "")
      eq(Model.nextLaMidnightMs(1789363320000), 1789369200000)
      eq(Model.nextLaMidnightMs(1768046400000), 1768118400000)
      eq(Model.nextLaMidnightMs(1793448000000), 1793516400000)
      eq(Model.nextLaMidnightMs(1793527200000), 1793606400000)
      eq(Model.nextLaMidnightMs(1772884800000), 1772956800000)
      eq(Model.nextLaMidnightMs(1772967600000), 1773039600000)
      eq(Model.nextLaMidnightMs(1789369200000), 1789455600000)
      eq(Model.nudgedMargin(120, 1), 180)
      eq(Model.nudgedMargin(120, -5), 60)
      eq(Model.nudgedMargin(500, 5), 540)
      eq(Model.nudgedMargin(NaN, 0), 120)
      eq(Model.marginLabel(120), "+2m")
      eq(Model.marginLabel(10000), "+9m")
      eq(Model.followsLine("opencode", 60), "follows Zen free reset +1m")
      eq(Model.followsLine("claude_5h_reset", 60), "follows Claude reset +1m")
      eq(Model.followsLine("go_window_reset", 60), "follows Go reset +1m")
      eq(Model.followsLine("codex", 300), "follows Codex reset +5m")
      eq(Model.followsLine("gemini", 120), "follows Gemini reset +2m")
      eq(Model.followsLine("pi", 120), "follows Codex reset +2m")
      eq(Model.followsLine("cursor", 120), "")
      eq(Model.followsLine("nope", 120), "")'
run_case Lib_resets.qml "reset chips (OpenCode on the Zen free reset, Pi on Codex, Cursor none); Los Angeles midnight across DST; reset buffer nudges"

lib_case status '
      var rows = [
        ["draft", "", 0xF03EB, "draft", "soft"],
        ["armed", "", 0xF0150, "armed", "readable"],
        ["armed", "reset", 0xF051F, "waits for reset", "harness"],
        ["armed", "limit", 0xF051F, "waits for reset", "harness"],
        ["armed", "transient", 0xF06B0, "retrying", "readable"],
        ["armed", "busy", 0xF051F, "deferred", "readable"],
        ["armed", "deferred", 0xF051F, "deferred", "readable"],
        ["running", "", 0xF040A, "running", "accent"],
        ["done", "", 0xF012C, "done", "ok"],
        ["failed", "", 0xF0156, "failed", "bad"],
        ["limit", "", 0xF0028, "limit hit", "warn"],
        ["busy", "", 0xF0028, "session busy", "warn"],
        ["skipped", "", 0xF04AD, "skipped", "readable"],
        ["missed", "", 0xF02DA, "missed", "warn"],
        ["interrupted", "", 0xF0156, "interrupted", "warn"],
        ["paused", "", 0xF03E4, "paused", "warn"],
        ["needs_confirm", "", 0xF0028, "check job", "warn"],
        ["disarmed", "", 0xF0156, "disarmed", "soft"],
        ["gave_up", "", 0xF0156, "gave up", "bad"]
      ]
      for (var i = 0; i < rows.length; i++)
        eq(Model.statusSpec(rows[i][0], rows[i][1]), { glyph: String.fromCodePoint(rows[i][2]), word: rows[i][3], tone: rows[i][4] })
      eq(Model.reasonSentence("late"), "The computer was off or asleep past the grace time.")
      eq(Model.reasonSentence("nope"), "")
      eq(Model.harnessName("gemini"), "Gemini CLI")
      eq(Model.cliName("opencode"), "opencode")
      var now = at(2026, 9, 14, 7, 22)
      var s0 = now / 1000
      function job(status, extra) {
        var j = { id: "0123456789abcdef", harness: "claude", label: "x", createdAt: 1, updatedAt: 2,
                  state: { status: status, wait: null, reason: null, fireAt: null, lastRun: null } }
        for (var k in extra) j.state[k] = extra[k]
        return j
      }
      eq(Model.outcomeSentence(job("failed", { lastRun: { startedAt: s0 - 192, endedAt: s0, exit: 1, signal: null } }), now), "Failed. Exited with code 1 after 3m 12s.")
      eq(Model.outcomeSentence(job("armed", { wait: "limit", fireAt: at(2026, 9, 14, 20, 10) / 1000 }), now), "Limit hit. Re-armed for 20:10.")
      eq(Model.outcomeSentence(job("missed", { fireAt: at(2026, 9, 14, 7, 0) / 1000 }), now), "Missed. It was due at 07:00.")
      eq(Model.outcomeSentence(job("missed", { fireAt: at(2026, 9, 13, 9, 0) / 1000 }), now), "Missed. It was due at Sun 13 Sep 09:00.")
      eq(Model.outcomeSentence(job("failed", { reason: "auth" }), now), "Failed. The agent is not signed in.")
      eq(Model.outcomeSentence(job("done", { lastRun: { startedAt: s0 - 840, endedAt: s0, exit: 0 } }), now), "Done in 14m.")
      eq(Model.outcomeSentence(job("skipped", { reason: "weekly_exhausted" }), now), "Skipped. The weekly limit is used up.")
      eq(Model.outcomeSentence(job("paused", { reason: "kill_switch" }), now), "Paused. The kill switch file is present.")'
run_case Lib_status.qml "status vocabulary table 6.4 and outcome sentences"

lib_case lists '
      var now = at(2026, 9, 14, 7, 22)
      function s(y, mo, d, h, mi) { return at(y, mo, d, h, mi) / 1000 }
      function mk(id, status, fireAt, extra) {
        var j = { id: id, harness: "claude", label: "Job " + id, createdAt: 100, updatedAt: 100,
                  target: { title: "api-refactor", cwd: "/home/u/proj/api" }, queue: true,
                  state: { status: status, wait: null, fireAt: fireAt, lastRun: null } }
        if (extra) for (var k in extra) j[k] = extra[k]
        return j
      }
      function tails(list) { return list.map(function (j) { return j.id.substr(15) }) }
      var a = mk("000000000000000a", "armed", s(2026, 9, 14, 13, 30))
      var b = mk("000000000000000b", "armed", s(2026, 9, 14, 10, 10))
      var c = mk("000000000000000c", "running", null); c.state.lastRun = { startedAt: s(2026, 9, 14, 7, 18) }
      var d = mk("000000000000000d", "draft", null, { updatedAt: 300 })
      var e = mk("000000000000000e", "draft", null, { updatedAt: 500 })
      var f = mk("000000000000000f", "armed", s(2026, 9, 15, 7, 0))
      var m = mk("0000000000000010", "missed", s(2026, 9, 14, 7, 0))
      var input = [a, d, f, c, e, b, m]
      eq(tails([a, d, f, c, e, b].slice().sort(Model.jobSort)), ["c", "b", "a", "f", "e", "d"])
      var groups = Model.groupByDay(input, now, "fireAt")
      eq(groups.map(function (g) { return g.key }), ["attention", "now", "2026-09-14", "2026-09-15", "drafts"])
      eq([groups[0].word, groups[1].word, groups[2].word, groups[2].date, groups[3].word, groups[4].word],
         ["Needs attention", "Now", "Today", "Mon 14 Sep", "Tomorrow", "Drafts"])
      eq(tails(groups[2].jobs), ["b", "a"])
      eq(tails(groups[4].jobs), ["e", "d"])
      eq(tails(input), ["a", "d", "f", "c", "e", "b", "0"])
      var h1 = mk("0000000000000001", "done", null, { queue: false }); h1.state.lastRun = { endedAt: s(2026, 9, 14, 3, 14) }
      var h2 = mk("0000000000000002", "failed", null, { queue: false }); h2.state.lastRun = { endedAt: s(2026, 9, 14, 5, 31) }
      var h3 = mk("0000000000000003", "limit", null, { queue: false, updatedAt: s(2026, 9, 13, 15, 10) })
      var hg = Model.groupByDay([h3, h1, h2], now, "endedAt")
      eq(hg.map(function (g) { return g.key }), ["2026-09-14", "2026-09-13"])
      eq(tails(hg[0].jobs), ["2", "1"])
      eq(hg[1].word, "Yesterday")
      check(Model.isQueue(a) && !Model.isQueue(h1) && !Model.isQueue(null))
      check(Model.matchesFilter(a, "") && Model.matchesFilter(a, "api") && Model.matchesFilter(a, "CLAUDE code"))
      check(Model.matchesFilter(a, "armed") && !Model.matchesFilter(a, "gemini") && Model.matchesFilter(a, "job refactor"))
      eq(Model.shortPath("/home/u/proj/api", "/home/u"), "~/proj/api")
      eq(Model.shortPath("/home/u", "/home/u/"), "~")
      eq(Model.shortPath("/home/user2/x", "/home/u"), "/home/user2/x")
      eq(Model.elideMiddle("3f2a0c19-1111-2222-3333-444455556c19", 8), "3f2a…c19")
      eq(Model.elideMiddle("short", 8), "short")
      eq(Model.utf8Bytes("zażółć"), 10)
      eq(Model.utf8Bytes("€"), 3)
      eq(Model.utf8Bytes("😀"), 4)
      eq(Model.defaultLabel("\n  \n  Run the api test suite. Fix only failing tests in tests/api\nsecond"), "Run the api test suite. Fix only failing")
      eq(Model.defaultLabel("bell\ttab"), "belltab")
      eq(Model.defaultLabel(""), "")'
run_case Lib_lists.qml "sorting, day grouping for Queue and History, filters and text helpers"

lib_case drafts '
      var d1 = Model.draftFromSettings({ defaultHarness: "codex", defaultLevel: "unattended" },
                                       { codex: { enabled: false }, claude: { enabled: true }, opencode: { enabled: true } })
      eq(d1, { harness: "claude", target: { mode: "new", sessionId: null, cwd: null, allowNonGit: false, sessionPath: null }, level: "unattended",
               limits: {}, model: null, allowPaid: false, provider: null, trigger: { kind: "now" }, prompt: "" })
      eq(Model.draftFromSettings({ defaultHarness: "gemini", defaultLevel: "turbo" }, {}).harness, "gemini")
      eq(Model.draftFromSettings({ defaultHarness: "gemini", defaultLevel: "turbo" }, {}).level, "plan")
      eq(Model.draftFromSettings({ defaultHarness: "gemini", defaultLevel: "full" }, {}).level, "full")
      eq(Model.draftFromSettings(null, null).harness, "claude")
      eq(Model.draftFromSettings({ defaultHarness: "pi" }, {}).harness, "pi")
      eq(Model.draftFromSettings({ defaultHarness: "cursor" }, {}).harness, "cursor")
      eq(Model.draftFromSettings({ defaultAllowPaid: true }, {}).allowPaid, true)
      eq(Model.draftFromSettings({ defaultAllowPaid: "yes" }, {}).allowPaid, false)
      var job = { id: "0123456789abcdef", label: "Nightly", harness: "codex",
        target: { mode: "resume", sessionId: "3f2a0c19-1111-2222-3333-444455556c19", newSessionId: null, cwd: "/home/u/p", title: "p", allowNonGit: true },
        level: "unattended", limits: { maxTurns: 30, budgetUsd: 5, runtimeSec: 5400 }, model: null, allowPaid: true, provider: null,
        trigger: { kind: "at", fireAt: 1789400000, delaySec: null, marginSec: 120, weeklyPolicy: "defer", graceSec: 900 }, state: {} }
      var e1 = Model.draftFromJob(job, "hello", "edit")
      eq(e1.id, "0123456789abcdef")
      eq(e1.prompt, "hello")
      eq(e1.target, { mode: "resume", sessionId: "3f2a0c19-1111-2222-3333-444455556c19", cwd: "/home/u/p", allowNonGit: true, sessionPath: null })
      eq(e1.trigger, { kind: "at", fireAt: 1789400000, delaySec: null, marginSec: 120, weeklyPolicy: "defer" })
      eq(e1.limits, { maxTurns: 30, budgetUsd: 5, runtimeSec: 5400 })
      eq([e1.allowPaid, e1.provider], [true, null])
      var e2 = Model.draftFromJob(job, null, "duplicate")
      check(e2.id === undefined && e2.prompt === "" && e2.label === "Nightly")
      check(Model.draftFromJob(job, null, "rearm").id === undefined)
      var piPath = "/home/u/.pi/agent/sessions/--home-u-p--/2026-09-15T08-00-00-000Z_9b1d7e42-aaaa-4bbb-8ccc-ddddeeeeffff.jsonl"
      var pi = { id: "fedcba9876543210", label: "Pi in p", harness: "pi", provider: "openai-codex", model: "gpt-5.5", allowPaid: false,
        target: { mode: "fork", sessionId: "9b1d7e42-aaaa-4bbb-8ccc-ddddeeeeffff", newSessionId: null, cwd: "/home/u/p", title: "p", allowNonGit: false, sessionPath: piPath },
        level: "plan", limits: { runtimeSec: 5400 },
        trigger: { kind: "codex_window_reset", fireAt: null, delaySec: null, marginSec: 120, weeklyPolicy: "defer", graceSec: 900 }, state: {} }
      var p1 = Model.draftFromJob(pi, "x", "duplicate")
      eq([p1.harness, p1.provider, p1.model, p1.allowPaid, p1.target.mode, p1.target.sessionPath, p1.trigger.kind],
         ["pi", "openai-codex", "gpt-5.5", false, "fork", piPath, "codex_window_reset"])
      var legacy = { id: "0123456789abcde0", label: "Old", harness: "opencode",
        target: { mode: "new", sessionId: null, newSessionId: null, cwd: "/home/u/p", title: "p", allowNonGit: false },
        level: "plan", limits: {}, model: null, trigger: { kind: "claude_5h_reset", fireAt: 1789400000, delaySec: null, marginSec: 120 }, state: {} }
      var l1 = Model.draftFromJob(legacy, "", "rearm")
      eq([l1.trigger, l1.allowPaid, l1.provider, l1.target.sessionPath], [{ kind: "now" }, false, null, null])
      eq(Edition.LEVEL_IDS, ["plan", "unattended", "auto", "full"])
      eq(Object.keys(Edition.LEVEL_LABELS), ["plan", "unattended", "auto", "full"])
      eq(Edition.LEVEL_TONES, { plan: "accent", unattended: "warn", auto: "warn", full: "bad" })
      eq(Edition.HARNESS_IDS, ["claude", "opencode", "codex", "gemini", "cursor", "pi"])'
run_case Lib_drafts.qml "drafts from settings and stored jobs (allowPaid, provider, sessionPath; a legacy OpenCode Claude reset starts over at now); six agents; level enum"

# ---------------------------------------------------------------- Tint

echo "=== lib/Tint.js ==="

lib_case tint '
      // Popup backgrounds of installed themes (colors.toml background), dark and light.
      var themes = ["#1a1b26", "#eff1f5", "#2e3440", "#2d353b", "#282828", "#FFFCF0", "#faf4ed"]
      var ids = ["claude", "opencode", "codex", "gemini", "cursor", "pi"]
      var hexRe = /^#[0-9a-f]{6}$/
      check(Math.abs(Tint.contrast("#000000", "#ffffff") - 21) < 0.01)
      check(Math.abs(Tint.contrast("#ff000000", "#ffffff") - 21) < 0.01)
      for (var t = 0; t < themes.length; t++) {
        var bg = themes[t]
        for (var i = 0; i < ids.length; i++) {
          var text = Tint.harnessInk(ids[i], bg, 4.5)
          check(hexRe.test(text) && Tint.contrast(text, bg) >= 4.5)
          var mark = Tint.harnessInk(ids[i], bg, 3.0)
          check(hexRe.test(mark) && Tint.contrast(mark, bg) >= 3.0)
          var monoMark = Tint.harnessMark(ids[i], bg)
          check(hexRe.test(monoMark) && Tint.contrast(monoMark, bg) >= 3.0)
          var high = Tint.harnessInk(ids[i], bg, 7.0)
          check(Tint.contrast(high, bg) >= 7.0)
          var want = Tint.toHsl(Tint.BRAND[ids[i]]).h
          var got = Tint.toHsl(text).h
          var dh = Math.abs(want - got); dh = Math.min(dh, 360 - dh)
          check(dh <= 8)
        }
        var stops = Tint.geminiStops(bg, 3.0)
        check(stops.length === 3 && Tint.contrast(stops[0], bg) >= 3 && Tint.contrast(stops[1], bg) >= 3 && Tint.contrast(stops[2], bg) >= 3)
        check(Tint.contrast(Tint.solved(150, 60, bg, 4.5), bg) >= 4.5)
        check(Tint.contrast(Tint.solved(38, 85, bg, 4.5), bg) >= 4.5)
        check(Tint.contrast(Tint.ink("#f7768e", bg, 4.5), bg) >= 4.5)
      }
      eq(Tint.harnessInk("claude", "#1a1b26", 4.5), "#d97757")
      check(Tint.harnessInk("claude", "#eff1f5", 4.5) !== "#d97757")
      check(hexRe.test(Tint.harnessInk("nope", "#1a1b26", 4.5)))
      check(hexRe.test(Tint.ink("not a colour", "#1a1b26", 4.5)))
      eq(Tint.BRAND, { claude: "#D97757", opencode: "#5C9CF5", codex: "#10A37F", gemini: "#847ACE", cursor: "#BB64D8", pi: "#97C639" })
      eq(Tint.MARK_BRAND, { cursor: "#8A8A8A", pi: "#C9C9C9" })
      eq(Tint.harnessMark("claude", "#1a1b26"), Tint.harnessInk("claude", "#1a1b26", 3.0))
      eq(Tint.GEMINI_STOPS, ["#4796E4", "#847ACE", "#C3677F"])'
run_case Lib_tint.qml "six inks reach 4.5:1 (7:1 in high contrast), marks 3:1 (Cursor and Pi mono), hue kept, on 7 themes"

# ---------------------------------------------------------------- components

echo "=== shared components (Q1) ==="

# Minimal stand-ins for the shell singletons: the real qs.Commons needs the
# Quickshell runtime, which a bare engine does not have. Only what these
# components call exists here, so a component reaching for anything else fails.
mkdir -p "$T/qs/Commons" "$T/qs/Ui"
cat > "$T/qs/Commons/qmldir" <<'EOF'
module qs.Commons
singleton Style 1.0 Style.qml
singleton Util 1.0 Util.qml
singleton Border 1.0 Border.qml
EOF
cat > "$T/qs/Commons/Style.qml" <<'EOF'
pragma Singleton
import QtQuick
QtObject {
  property int cornerRadius: 0
  function space(px) { return Math.round(px) }
  function spaceReal(px) { return px }
  readonly property QtObject spacing: QtObject {
    readonly property int sm: 4
    readonly property int lg: 8
    readonly property int controlHeight: 28
    readonly property int controlPaddingX: 10
  }
  readonly property QtObject font: QtObject {
    readonly property string family: "monospace"
    readonly property int caption: 10
    readonly property int bodySmall: 11
    readonly property int body: 12
    readonly property int title: 14
    readonly property int heading: 16
    readonly property int display: 24
    readonly property int iconSmall: 11
    readonly property int icon: 14
  }
}
EOF
cat > "$T/qs/Commons/Util.qml" <<'EOF'
pragma Singleton
import QtQuick
QtObject {
  function alpha(c, opacity) {
    if (!c) return Qt.rgba(0, 0, 0, opacity)
    if (typeof c === "string") c = Qt.color(c)
    return Qt.rgba(c.r, c.g, c.b, Math.max(0, Math.min(1, opacity)))
  }
}
EOF
cat > "$T/qs/Commons/Border.qml" <<'EOF'
pragma Singleton
import QtQuick
QtObject {
  function none() { return { color: "transparent", widths: { top: 0, right: 0, bottom: 0, left: 0 } } }
  function flat(color, width) { return { color: color, widths: { top: width, right: width, bottom: width, left: width } } }
  function controlSpec(state, foreground, accent) { return flat(state === "hover-cursor" ? accent : foreground, 1) }
}
EOF
cat > "$T/qs/Ui/qmldir" <<'EOF'
module qs.Ui
BorderSurface 1.0 BorderSurface.qml
EOF
cat > "$T/qs/Ui/BorderSurface.qml" <<'EOF'
import QtQuick
Rectangle {
  property var borderSpec: null
  border.width: borderSpec && borderSpec.widths ? borderSpec.widths.top : 0
  border.color: borderSpec ? borderSpec.color : "transparent"
}
EOF

cat > "$T/Smoke.qml" <<'QML'
import QtQuick
import "components"
import "lib/Tint.js" as Tint
import "lib/Model.js" as Model

Item {
  id: root
  width: 1100
  height: 800
  property int n: 0
  property int firstFail: 0
  property bool undone: false
  function check(c) { root.n++; if (!c && root.firstFail === 0) root.firstFail = root.n }

  readonly property double nowMs: 1789363320000
  // Usage v2 providers (CONTRACT-V2-DELTA 3.7): a stale Claude record, a fresh Codex record and the
  // computed Gemini daily reset.
  function win(key, shortLabel, kind, percent, inSec, computed) {
    return { key: key, label: computed ? "" : shortLabel, title: null, shortLabel: shortLabel, kind: kind, percent: percent,
             over: percent !== null && percent > 1, resetsAt: root.nowMs / 1000 + inSec, sliding: false,
             source: computed ? "computed" : "record", bindable: true }
  }
  function source(id, name, harness, stale, windows, headlineKey, computed) {
    return { id: id, name: name, tier: "", statusText: "", scope: null, source: computed ? "computed" : "record", readable: true,
             unreadableReason: null, updatedAtMs: computed ? null : root.nowMs - (stale ? 3000000 : 60000),
             ageSec: computed ? null : (stale ? 3000 : 60), stale: stale, cadenceSec: computed ? null : 900,
             keptFromLastPoll: false, harnesses: [harness], relevant: true, headlineKey: headlineKey, windows: windows }
  }
  readonly property var providers: [
    root.source("claude", "Claude", "claude", true, [root.win("claude:5-hour", "5-hour", "session", 0.93, 600, false),
                                                     root.win("claude:weekly", "Weekly", "weekly", 0.41, 86400, false)], "claude:5-hour", false),
    root.source("codex", "Codex", "codex", false, [root.win("codex:5-hour", "5-hour", "session", 0.2, 900, false)], "codex:5-hour", false),
    root.source("gemini-daily", "Gemini CLI daily", "gemini", false, [root.win("gemini-daily:daily", "daily", "daily", null, 20000, true)],
                "gemini-daily:daily", true)
  ]
  readonly property var shown: ["claude", "codex", "gemini-daily"]

  readonly property QtObject theme: QtObject {
    id: theme
    readonly property color surface: "#eff1f5"
    readonly property color fg: "#4c4f69"
    readonly property color accent: "#1e66f5"
    readonly property color accentInk: Tint.ink("#1e66f5", "#eff1f5", 4.5)
    readonly property color strong: Qt.rgba(theme.fg.r, theme.fg.g, theme.fg.b, 0.88)
    readonly property color readable: Qt.rgba(theme.fg.r, theme.fg.g, theme.fg.b, 0.76)
    readonly property color soft: Qt.rgba(theme.fg.r, theme.fg.g, theme.fg.b, 0.60)
    readonly property color faint: Qt.rgba(theme.fg.r, theme.fg.g, theme.fg.b, 0.14)
    readonly property real textTarget: 4.5
    readonly property var inks: ({ claude: Tint.harnessInk("claude", "#eff1f5", 4.5), opencode: Tint.harnessInk("opencode", "#eff1f5", 4.5),
                                   codex: Tint.harnessInk("codex", "#eff1f5", 4.5), gemini: Tint.harnessInk("gemini", "#eff1f5", 4.5),
                                   cursor: Tint.harnessInk("cursor", "#eff1f5", 4.5), pi: Tint.harnessInk("pi", "#eff1f5", 4.5) })
    readonly property var marks: ({ claude: Tint.harnessMark("claude", "#eff1f5", 3), opencode: Tint.harnessMark("opencode", "#eff1f5", 3),
                                    codex: Tint.harnessMark("codex", "#eff1f5", 3), gemini: Tint.harnessMark("gemini", "#eff1f5", 3),
                                    cursor: Tint.harnessMark("cursor", "#eff1f5", 3), pi: Tint.harnessMark("pi", "#eff1f5", 3) })
    readonly property var geminiStops: Tint.geminiStops("#eff1f5", 3.0)
    readonly property color okInk: Tint.solved(150, 60, "#eff1f5", 4.5)
    readonly property color warnInk: Tint.solved(38, 85, "#eff1f5", 4.5)
    readonly property color badInk: Tint.ink("#d20f39", "#eff1f5", 4.5)
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
    function harnessFill(id) { return Tint.harnessInk(id, "#eff1f5", 3.0) }
    function moveMs(ms) { return theme.reduceMotion ? 0 : ms }
    function fadeMs(ms) { return theme.reduceMotion ? Math.min(ms, 120) : ms }
  }

  readonly property var statuses: ["draft", "armed", "running", "done", "failed", "limit", "busy", "skipped", "missed",
                                   "interrupted", "paused", "needs_confirm", "disarmed", "gave_up"]

  Column {
    width: parent.width
    spacing: 4

    Row {
      spacing: 4
      AgentMark { id: markClaude; theme: root.theme; agent: "claude" }
      AgentMark { theme: root.theme; agent: "opencode"; dimmed: true }
      AgentMark { theme: root.theme; agent: "codex"; size: 20 }
      AgentMark { id: markGemini; theme: root.theme; agent: "gemini" }
      AgentMark { id: markCursor; theme: root.theme; agent: "cursor" }
      AgentMark { id: markPi; theme: root.theme; agent: "pi" }
      AgentMark { id: markNone; theme: root.theme; agent: "nope" }
      HarnessRail { id: railGemini; theme: root.theme; harness: "gemini"; pulse: true; height: 38 }
      HarnessRail { theme: root.theme; harness: "claude"; strength: 0.35; height: 38 }
    }

    Flow {
      width: parent.width
      spacing: 4
      Repeater {
        id: pills
        model: root.statuses
        StatusPill { required property string modelData; theme: root.theme; status: modelData; harness: "claude" }
      }
      StatusPill { id: waitPill; theme: root.theme; status: "armed"; wait: "reset"; harness: "codex" }
      StatusPill { id: runPill; theme: root.theme; status: "running"; text: "running 3m 12s" }
    }

    Meter { theme: root.theme; width: 120; value: 0.5 }
    Meter { theme: root.theme; width: 120; value: -1 }
    LimitStrip { id: strip; theme: root.theme; width: 900; providers: root.providers; shownIds: root.shown; nowMs: root.nowMs }
    LimitStrip { id: stripNarrow; theme: root.theme; width: 260; providers: root.providers; shownIds: root.shown; nowMs: root.nowMs; compact: true }
    LimitStrip { id: stripHidden; theme: root.theme; width: 900; providers: root.providers; shownIds: []; nowMs: root.nowMs }
    LimitStrip { id: stripNone; theme: root.theme; width: 900; providers: []; shownIds: []; nowMs: root.nowMs }
    NoticeRow { id: notice; theme: root.theme; width: 900 }

    Row {
      spacing: 4
      Chip { id: harnessChip; theme: root.theme; pill: true; harness: "gemini"; text: "Gemini CLI"; note: "2"; selected: true }
      Chip { id: timeChip; theme: root.theme; text: "At reset 10:10"; meterValue: 0.22; selected: true; hasCursor: true }
      Chip { theme: root.theme; text: "+30m"; enabled: false }
      Chip { theme: root.theme; text: "+ New session"; italic: true; glyph: "+" }
      ActionButton { id: armButton; theme: root.theme; text: "Arm"; shortcut: "Ctrl+Enter"; primary: true }
      ActionButton { theme: root.theme; text: "Disarm"; danger: true; armed: true; hasCursor: true }
    }

    SearchField { id: search; theme: root.theme; width: 600; active: true; trailing: "4 armed" }
    SearchField { theme: root.theme; width: 600; text: "api" }
    ArgvLine {
      id: argv
      theme: root.theme
      width: 900
      display: "claude -p --output-format stream-json --verbose --permission-mode plan --permission-prompts none --max-turns 15 --max-budget-usd 5 --resume 3f2a0c19-1111-2222-3333-444455556c19 <stdin>"
      cwd: "~/proj/api"
      binary: "/home/u/.local/share/mise/installs/claude/2.1.270/claude"
      caption: "Plan mode. Claude reads and proposes a plan. It does not edit files or run commands."
    }
    ArgvLine { theme: root.theme; width: 900; loading: true }
    ArgvLine { theme: root.theme; width: 900; errorText: "That session could not be found." }
    FooterHints { theme: root.theme; width: 1100; hints: "Tab next  ·  Ctrl+Enter arm  ·  Esc close" }
    ProblemCard { id: problem; theme: root.theme; width: 560; message: "The helper did not answer."; detail: "Nothing was armed." }
  }

  Timer {
    interval: 400
    running: true
    onTriggered: {
      try {
        root.check(markClaude.width === 14 && markClaude.height === 14)
        root.check(markGemini.gradientFill === true && markClaude.gradientFill === false)
        root.check(markNone.pathData === "" && markCursor.pathData !== "" && markPi.pathData !== "")
        root.check(railGemini.gradientRail === true && railGemini.pulsing === true)
        root.check(pills.count === root.statuses.length)
        root.check(waitPill.spec.word === "waits for reset" && Qt.colorEqual(waitPill.ink, root.theme.inks.codex))
        root.check(runPill.width > 0 && Qt.colorEqual(runPill.ink, root.theme.accentInk))
        root.check(strip.chips.length === 3 && strip.hiddenCount === 0 && strip.chips[0].stale === true && strip.chips[0].percent === 0.93)
        root.check(strip.chips[2].computed === true && String(strip.chips[2].text).indexOf("resets ") === 0)
        root.check(stripNarrow.hiddenCount > 0 && stripNarrow.moreText === "+" + stripNarrow.hiddenCount)
        root.check(stripHidden.chips.length === 0 && stripHidden.moreText === "Limits")
        root.check(stripNone.chips.length === 0 && stripNone.moreText === "")
        root.check(JSON.stringify(Model.shownSources(root.providers, "auto")) === JSON.stringify(["codex", "gemini-daily"]))
        root.check(JSON.stringify(Model.shownSources(root.providers, ["gemini-daily", "nope", "claude"])) === JSON.stringify(["gemini-daily", "claude"]))

        notice.show("Armed. Fires 10:10 (in 2h 48m).", "ok", function () { root.undone = true }, 0)
        root.check(notice.undoAvailable === true && notice.text === "Armed. Fires 10:10 (in 2h 48m).")
        root.check(notice.undo() === true && root.undone === true)
        root.check(notice.undoAvailable === false && notice.text === "" && notice.undo() === false)
        notice.show("That job no longer exists.", "error", null, 0)
        root.check(notice.undoAvailable === false && notice.text !== "")
        notice.clear()
        root.check(notice.text === "")

        root.check(harnessChip.implicitHeight === 26 && timeChip.implicitHeight === 28)
        root.check(Qt.colorEqual(harnessChip.textColor, root.theme.inks.gemini))
        root.check(armButton.implicitHeight === 28 && armButton.implicitWidth > 40)
        root.check(search.blinking === true)
        root.check(argv.implicitHeight > 0)
        root.check(problem.implicitHeight > 0)

        root.theme.reduceMotion = true
        root.check(railGemini.pulsing === false && root.theme.moveMs(160) === 0 && root.theme.fadeMs(400) === 120)
      } catch (err) { console.warn("threw: " + err); root.firstFail = 250 }
      Qt.exit(root.firstFail)
    }
  }
}
QML

out="$(cd "$T" && timeout 60 "$QML" -I "$T" Smoke.qml 2>&1)"; rc=$?
if [ "$rc" -eq 0 ]; then ok "every Q1 component instantiates and behaves (notice undo, gradient mark, pulse gating)"
else no "every Q1 component instantiates and behaves" "rc=$rc $(printf '%s' "$out" | head -6 | tr '\n' ' ')"; fi

# Any engine warning while those components were alive is a defect: a binding
# error, an unresolved type, an assignment of the wrong type.
warn="$(printf '%s\n' "$out" | grep -E 'Warning|Error|TypeError|ReferenceError|Unable to assign|Cannot read|is not a function|Binding loop' | grep -v 'Did not load any objects' || true)"
if [ -z "$warn" ]; then ok "and the engine reported no warnings while they ran"
else no "and the engine reported no warnings while they ran" "$(printf '%s' "$warn" | head -6 | tr '\n' ' ')"; fi

# ---------------------------------------------------------------- Service.qml helper table

echo "=== Service.qml verb table against CONTRACT 2.4 and CONTRACT-V2-DELTA 4 ==="

# QML deadline = helper deadline + 5 s, answer cap = the helper's output cap,
# stdin only for the verbs that read a request, reads concurrent, changes in order.
if out="$(/usr/bin/python3 -I -S -B - "$REPO/Service.qml" 2>&1 <<'PY'
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
)"; then ok "every verb's deadline, cap, stdin use and lane match the contract"
else no "every verb's deadline, cap, stdin use and lane match the contract" "$out"; fi

# ---------------------------------------------------------------- live processes (Quickshell)

# BoundedProcess and Service.qml need Quickshell.Io, which loads only inside
# Quickshell. So they run in a throwaway `qs` instance started with an empty
# environment: its own temp HOME and runtime folder, the offscreen platform, no
# Wayland display, so it never touches the running bar. The helper that
# Service.qml calls is a stub that records what it was given.
QS=/usr/bin/qs

qs_run() { # <config file> <log file>
  env -i HOME="$T/home" XDG_RUNTIME_DIR="$T/run" PATH=/usr/bin LANG=C.UTF-8 \
    QT_QPA_PLATFORM=offscreen QML_DISABLE_DISK_CACHE=1 Q1_CANARY=leaked-if-seen \
    timeout 150 "$QS" -p "$1" >"$2" 2>&1
}

report_marked() { # <marker> <log> <expected case count>
  local marker="$1" log="$2" want="$3" line body n=0
  while IFS= read -r line; do
    body="${line#"$marker "}"
    case "$body" in
      "ok "*) ok "${body#ok }"; n=$((n+1)) ;;
      "FAIL "*) body="${body#FAIL }"; no "${body%% :: *}" "${body#* :: }"; n=$((n+1)) ;;
    esac
  done < <(grep -o "$marker \(ok\|FAIL\) .*" "$log")
  grep -q "$marker done" "$log" \
    || no "$marker ran to the end" "$(grep -E 'WARN|ERROR' "$log" | grep -v 'quickshell.ipc' | head -4 | tr '\n' ' ')"
  [ "$n" -eq "$want" ] || no "$marker reported every case" "$n of $want"
  local warn
  # Two widgets share one IPC target on purpose (as on two monitors); the shell's
  # notice that only the first handler answers is the documented behaviour.
  warn="$(grep -E 'WARN|ERROR' "$log" | grep -E '\.qml|TypeError|ReferenceError' \
    | grep -v 'Handler was registered but will not be used' | head -4 || true)"
  if [ -z "$warn" ]; then ok "$marker: no QML warnings while it ran"
  else no "$marker: no QML warnings while it ran" "$(printf '%s' "$warn" | tr '\n' ' ')"; fi
}

if [ ! -x "$QS" ]; then
  echo "  skip the live suites (no Quickshell at $QS)"
else
  mkdir -p "$T/home" "$T/run" "$T/bp/lib" "$T/svc/lib" "$T/svc/bin"
  chmod 700 "$T/run"

  echo "=== BoundedProcess.qml (live) ==="
  cp "$REPO/BoundedProcess.qml" "$T/bp/"
  cp "$REPO/lib/Model.js" "$REPO/lib/Edition.js" "$T/bp/lib/"
  cat > "$T/bp/shell.qml" <<'QML'
import QtQuick
import Quickshell

ShellRoot {
  id: root

  readonly property string py: "/usr/bin/python3"
  readonly property string pidFile: "@PIDFILE@"
  property int failures: 0

  Component { id: procC; BoundedProcess {} }
  // An Item parent, as Service.qml gives them; ShellRoot is not a scene item.
  Item { id: holder }
  Component { id: delayC; Timer { repeat: false } }

  function report(name, ok, detail) {
    if (!ok) root.failures++
    console.log("Q1BP " + (ok ? "ok " : "FAIL ") + name + (ok ? "" : " :: " + detail))
  }

  function after(ms, fn) {
    var t = delayC.createObject(root, { interval: ms })
    t.triggered.connect(function () { t.destroy(); fn() })
    t.start()
  }

  function runCase(c, done) {
    var p = procC.createObject(holder, c.props)
    var r = { calls: 0, code: null, over: null, timed: null, out: null, err: null, ms: -1 }
    var t0 = Date.now()
    p.finished.connect(function (code, over, timed, out, err) {
      r.calls++
      if (r.calls > 1) return
      r.code = code; r.over = over; r.timed = timed; r.out = out; r.err = err; r.ms = Date.now() - t0
      // Linger, so a second emission is counted before the verdict.
      root.after(400, function () {
        var ok = false
        try { ok = c.check(r) === true } catch (e) { r.threw = String(e) }
        root.report(c.name, ok, JSON.stringify(r).substr(0, 400))
        p.destroy()
        done()
      })
    })
    if (!p.start()) {
      root.report(c.name, false, "start() returned false")
      p.destroy()
      done()
      return
    }
    if (typeof c.during === "function") c.during(p)
  }

  function runAll(list, i) {
    if (i >= list.length) {
      console.log("Q1BP done failures=" + root.failures)
      root.after(200, function () { Qt.quit() })
      return
    }
    root.runCase(list[i], function () { root.runAll(list, i + 1) })
  }

  Component.onCompleted: {
    var py = root.py
    var rel = procC.createObject(holder, { command: ["sh", "-c", "true"] })
    root.report("a relative argv[0] is refused", rel.start() === false && rel.running === false, "started")
    rel.destroy()
    var empty = procC.createObject(holder, { command: [] })
    root.report("an empty command is refused", empty.start() === false, "started")
    empty.destroy()
    var twice = procC.createObject(holder, { command: ["/usr/bin/sleep", "5"], deadlineMs: 20000 })
    var first = twice.start()
    root.report("start() while running is refused", first === true && twice.start() === false && twice.running === true, "first=" + first)
    twice.destroy()

    var doomed = procC.createObject(holder, {
      command: [py, "-I", "-S", "-c", "import os, time\nf = open('" + root.pidFile + "', 'w')\nf.write(str(os.getpid()))\nf.close()\ntime.sleep(30)"],
      deadlineMs: 60000
    })
    doomed.start()
    root.after(800, function () { doomed.destroy() })

    root.runAll([
      { name: "stdout arrives and exit code 0 is reported, once",
        props: { command: ["/usr/bin/printf", "%s", "hello"] },
        check: function (r) { return r.calls === 1 && r.code === 0 && r.out === "hello" && r.err === "" && !r.over && !r.timed } },
      { name: "a non-zero exit code is reported as it is",
        props: { command: ["/usr/bin/sh", "-c", "exit 3"] },
        check: function (r) { return r.code === 3 && !r.timed } },
      { name: "stdinText is written once, then stdin is closed (200 KB)",
        props: { command: [py, "-I", "-S", "-c", "import sys; d = sys.stdin.buffer.read(); sys.stdout.write('%d %d %s' % (len(d), d.count(b'x'), d.endswith(b'\\n')))"],
                 stdinText: "x".repeat(200000) + "\n", deadlineMs: 8000 },
        check: function (r) { return r.out === "200001 200000 True" && r.code === 0 && !r.timed } },
      { name: "without stdinText the child reads end of file at once",
        props: { command: [py, "-I", "-S", "-c", "import sys; sys.stdout.write(str(len(sys.stdin.buffer.read())))"], deadlineMs: 4000 },
        check: function (r) { return r.out === "0" && !r.timed } },
      { name: "the environment is exactly the one given",
        props: { command: ["/usr/bin/env"], environment: { Q1_ONE: "1", PATH: "/usr/bin" } },
        check: function (r) { return r.out.split("\n").filter(function (l) { return l !== "" }).sort().join(",") === "PATH=/usr/bin,Q1_ONE=1" } },
      { name: "stdout past its cap kills the child at once",
        props: { command: [py, "-I", "-S", "-c", "import sys\nwhile True:\n    sys.stdout.write('y' * 65536)\n    sys.stdout.flush()"], maxStdoutBytes: 100000, deadlineMs: 20000 },
        check: function (r) { return r.over === true && r.code === -1 && r.out === "" && !r.timed && r.ms < 3000 } },
      { name: "stderr past its cap kills the child at once",
        props: { command: [py, "-I", "-S", "-c", "import sys\nwhile True:\n    sys.stderr.write('z' * 4096)\n    sys.stderr.flush()"], maxStderrBytes: 10000, deadlineMs: 20000 },
        check: function (r) { return r.over === true && r.code === -1 && r.err === "" && !r.timed && r.ms < 3000 } },
      { name: "the deadline ends a child that runs too long",
        props: { command: ["/usr/bin/sleep", "30"], deadlineMs: 400 },
        check: function (r) { return r.timed === true && r.code === -1 && r.ms < 2500 } },
      { name: "a child that ignores TERM gets KILL 2 s later",
        props: { command: [py, "-I", "-S", "-c", "import signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\ntime.sleep(30)"], deadlineMs: 400 },
        check: function (r) { return r.timed === true && r.code === -1 && r.ms >= 2350 && r.ms < 5500 } },
      { name: "kill() ends the child and the call still finishes once",
        props: { command: ["/usr/bin/sleep", "30"], deadlineMs: 20000 },
        during: function (p) { root.after(300, function () { p.kill() }) },
        check: function (r) { return r.calls === 1 && r.code === -1 && !r.timed && r.ms < 2000 } },
      { name: "a binary that cannot start finishes once, without waiting for the deadline",
        props: { command: ["/nonexistent/q1-binary"], deadlineMs: 20000 },
        check: function (r) { return r.calls === 1 && r.code === -1 && !r.timed && r.ms < 2000 } },
      { name: "destroying a running BoundedProcess kills its child",
        props: { command: ["/usr/bin/sh", "-c", "test -s '" + root.pidFile + "' && ! kill -0 \"$(cat '" + root.pidFile + "')\" 2>/dev/null"] },
        check: function (r) { return r.code === 0 } }
    ], 0)
  }
}
QML
  sed -i "s|@PIDFILE@|$T/bp/doomed.pid|" "$T/bp/shell.qml"
  qs_run "$T/bp/shell.qml" "$T/bp.log"
  report_marked Q1BP "$T/bp.log" 15

  echo "=== Service.qml (live, stub helper) ==="
  cp "$REPO/Service.qml" "$REPO/BoundedProcess.qml" "$T/svc/"
  cp "$REPO"/lib/*.js "$T/svc/lib/"
  cat > "$T/svc/bin/ap4a" <<'PY'
#!/usr/bin/python3 -I -S
"""Stand-in for bin/ap4a: records every call, then answers from a fixed table."""
import json
import os
import select
import sys
import time

START = time.time()
VERB = sys.argv[1] if len(sys.argv) > 1 else ""
ARGS = sys.argv[2:]
NOW = int(START)
D64 = "d" * 64
C64 = "c" * 64
SETTINGS = {"schemaVersion": 1, "defaultHarness": "codex", "defaultLevel": "plan", "resetMarginSec": 120,
            "eveningTime": "23:00", "morningTime": "07:00", "notify": "all", "motion": "full"}

chunks, eof = [], False
try:
    while True:
        wait = START + 3.0 - time.time()
        if wait <= 0 or not select.select([0], [], [], wait)[0]:
            break
        block = os.read(0, 65536)
        if not block:
            eof = True
            break
        chunks.append(block)
except OSError:
    eof = True
STDIN = b"".join(chunks).decode("utf-8", "replace")


def record():
    entry = {"verb": VERB, "args": ARGS, "start": START, "end": time.time(),
             "script": os.path.realpath(sys.argv[0]), "executable": sys.executable,
             "flags": [sys.flags.isolated, sys.flags.no_site, sys.flags.dont_write_bytecode],
             "env": dict(os.environ), "stdin": STDIN, "stdinEof": eof}
    fd = os.open(os.path.join(os.environ["HOME"], "calls.jsonl"), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    os.write(fd, (json.dumps(entry) + "\n").encode("utf-8"))
    os.close(fd)


def reply(obj, code=0):
    record()
    sys.stdout.write(json.dumps(obj, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    raise SystemExit(code)


def job(jid, status, fire_at=None, last_run=None):
    return {"id": jid, "label": "Job " + jid[:4], "createdAt": NOW - 500, "updatedAt": NOW - 400, "harness": "claude",
            "target": {"mode": "new", "sessionId": None, "cwd": "/home/q1/proj", "title": "proj", "allowNonGit": False},
            "level": "plan", "fireAtMs": fire_at * 1000 if fire_at else None,
            "queue": status in ("armed", "running", "missed"),
            "state": {"status": status, "wait": None, "reason": None, "gen": 1, "fireAt": fire_at,
                      "lastRun": last_run, "lastEvent": None}}


if VERB == "edition":
    reply({"ok": True,
           "edition": {"pluginId": "stub", "displayName": "Stub", "unitPrefix": "stub", "schemaVersion": 1,
                       "pluginDir": "/stub", "version": "0.0.0"},
           "levels": [{"id": "plan", "label": "Plan"}, {"id": "unattended", "label": "Unattended"},
                      {"id": "other", "label": "Other"}],
           "harnesses": [{"id": "claude", "name": "Claude Code", "canFork": True, "resetTrigger": "claude_5h_reset"}],
           "caps": {"promptBytes": 65536}})
if VERB == "settings-get":
    reply({"ok": True, "settings": SETTINGS})
if VERB == "settings-set":
    merged = dict(SETTINGS)
    merged.update(json.loads(STDIN))
    reply({"ok": True, "settings": merged})
if VERB == "list":
    reply({"ok": True, "nowMs": int(time.time() * 1000), "killSwitch": False, "enabledInShell": True,
           "linger": None, "reconciledAt": NOW,
           "jobs": [job("1111111111111111", "armed", NOW + 3600),
                    job("2222222222222222", "running", last_run={"startedAt": NOW - 60}),
                    job("3333333333333333", "failed", last_run={"startedAt": NOW - 300, "endedAt": NOW - 100, "exit": 1}),
                    job("4444444444444444", "missed", NOW - 4000),
                    job("5555555555555555", "armed", NOW + 7200),
                    {"id": "not-an-id", "state": {"status": "armed", "fireAt": NOW + 10}}]})
if VERB == "reconcile":
    reply({"ok": True, "rearmed": [], "fired": [], "missed": [], "interrupted": [], "resumed": [], "stopped": [],
           "pruned": 0, "linger": None})
if VERB == "usage":
    reply({"ok": True, "nowMs": int(time.time() * 1000),
           "claude": {"available": True, "fetchedAtMs": int(time.time() * 1000), "ageSec": 5, "stale": False,
                      "status": "", "tierLabel": "",
                      "windows": [{"label": "Session (5-hour)", "kind": "session", "percent": 0.4,
                                   "resetsAt": NOW + 3000, "title": None}]},
           "codex": {"available": False, "updatedAtMs": None, "stale": True, "status": "", "windows": []}})
if VERB == "agents":
    agents = [{"harness": h, "name": h, "available": True, "enabled": h != "codex", "loggedIn": None,
               "reason": None, "canFork": h != "gemini", "triggers": []}
              for h in ("claude", "opencode", "codex", "gemini")]
    agents.append({"harness": "other", "enabled": True})
    reply({"ok": True, "checkedLogin": "--login" in ARGS, "agents": agents})
if VERB == "sessions":
    reply({"ok": True, "harness": ARGS[1] if len(ARGS) == 2 else None, "nowMs": 0, "sessions": [], "counts": {},
           "truncated": {}, "errors": {}, "limitDays": 90, "perHarnessCap": 50})
if VERB == "preview":
    time.sleep(0.6)
    reply({"ok": True, "preview": {"display": "claude -p <stdin>", "commandDigest": C64}})
if VERB in ("job-create", "job-update"):
    time.sleep(0.2)
    reply({"ok": True, "id": ARGS[0] if ARGS else "aaaaaaaaaaaaaaaa", "job": {}, "digest": D64,
           "commandDigest": C64, "wasArmed": False})
if VERB == "arm":
    time.sleep(0.2)
    if ARGS[0] == "eeeeeeeeeeeeeeee":
        reply({"ok": False, "code": "digest_mismatch",
               "message": "The job changed since you reviewed it. Check it again."}, 1)
    reply({"ok": True, "id": ARGS[0], "status": "armed", "fireAt": NOW + 60, "unit": "stub", "immediate": False,
           "hint": None, "job": {}})
if VERB in ("run-now", "disarm", "reschedule", "swap", "shift", "cancel-all"):
    time.sleep(0.2)
    reply({"ok": True})
if VERB == "job-delete":
    time.sleep(0.1)
    reply({"ok": False, "code": "bad_status", "message": "That job cannot do this in its current state."}, 1)
if VERB == "job-get":
    if ARGS[0] == "bbbbbbbbbbbbbbbb":
        record()
        sys.stdout.write("Traceback: this is not the protocol\n")
        raise SystemExit(70)
    if ARGS[0] == "cccccccccccccccc":
        record()
        try:
            for _ in range(64):
                sys.stdout.write("x" * 65536)
                sys.stdout.flush()
        except OSError:
            pass
        raise SystemExit(0)
    reply({"ok": True, "job": {}, "prompt": "stub prompt", "promptAvailable": True, "preview": None, "runs": [],
           "lastLines": []})
if VERB == "copy-resume":
    reply({"ok": True, "command": "cd '/home/q1/proj' && claude --resume 3f2a0c19-1111-2222-3333-444455556c19"})
reply({"ok": False, "code": "bad_args", "message": "The request was not understood."}, 2)
PY
  cat > "$T/svc/shell.qml" <<'QML'
import QtQuick
import Quickshell
import "lib/Edition.js" as Edition

ShellRoot {
  id: root

  property int failures: 0
  property var notices: []
  property int updates: 0
  readonly property string idArmed: "1111111111111111"
  readonly property string idLater: "5555555555555555"
  readonly property string digest: "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"
  readonly property string cdigest: "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"

  Service { id: svc }

  Connections {
    target: svc
    function onNotice(text, kind) { root.notices = root.notices.concat([kind + ":" + text]) }
    function onJobsUpdated() { root.updates++ }
  }

  Component { id: delayC; Timer { repeat: false } }

  function report(name, ok, detail) {
    if (!ok) root.failures++
    console.log("Q1SVC " + (ok ? "ok " : "FAIL ") + name + (ok ? "" : " :: " + String(detail).substr(0, 500)))
  }

  function after(ms, fn) {
    var t = delayC.createObject(root, { interval: ms })
    t.triggered.connect(function () { t.destroy(); fn() })
    t.start()
  }

  function waitFor(cond, ms, fn) {
    var t0 = Date.now()
    function poll() {
      var v = false
      try { v = cond() === true } catch (e) { v = false }
      if (v || Date.now() - t0 > ms) fn(v)
      else root.after(50, poll)
    }
    poll()
  }

  function steps(list) {
    var i = 0
    function next() { if (i < list.length) list[i++](next) }
    next()
  }

  function draft(extra) {
    var d = { harness: "claude", target: { mode: "new", sessionId: null, cwd: "/tmp", allowNonGit: false },
              level: "plan", limits: { maxTurns: 15 }, model: null, trigger: { kind: "now" } }
    for (var k in extra) d[k] = extra[k]
    return d
  }

  Component.onCompleted: root.steps([
    function (next) {
      root.report("not ready before the helper answers", svc.ready === false && svc.levels.length === 0, svc.ready)
      root.report("the edition falls back to Edition.js until it loads",
        svc.edition.pluginId === Edition.PLUGIN_ID && svc.edition.serviceIpcTarget === Edition.SERVICE_IPC_TARGET,
        JSON.stringify(svc.edition))
      var sync = true, got = null
      svc.createAndArm(root.draft({ prompt: "x" }), function (res) { got = { sync: sync, res: res } })
      sync = false
      root.waitFor(function () { return got !== null }, 3000, function (ok) {
        root.report("a change before ready answers not_ready, on a later turn",
          ok && got.sync === false && got.res.code === "not_ready" && got.res.message === "Auto Pilot Unlocked is still starting.",
          JSON.stringify(got))
        next()
      })
    },
    function (next) {
      root.waitFor(function () { return svc.ready && svc.usage !== null && !svc.busy }, 12000, function (ok) {
        root.report("ready once the edition and the list answered", ok, "ready=" + svc.ready)
        var ids = svc.levels.map(function (l) { return l.id }).join(",")
        root.report("only the closed level enum is offered", ids === "plan,unattended", ids)
        root.report("settings come from settings-get", svc.settings.defaultHarness === "codex", JSON.stringify(svc.settings))
        root.report("jobs without a valid id are dropped", svc.jobs.length === 5 && !!svc.jobsById[root.idLater], svc.jobs.length)
        root.report("counts by status", svc.armedCount === 2 && svc.runningCount === 1 && svc.anyRunning === true,
          svc.armedCount + "/" + svc.runningCount)
        root.report("attention counts missed jobs and recent failures", svc.attentionCount === 2, svc.attentionCount)
        root.report("nextJob is the soonest armed job", !!svc.nextJob && svc.nextJob.id === root.idArmed, JSON.stringify(svc.nextJob))
        root.report("no setup problem", svc.setupProblem === "", svc.setupProblem)
        next()
      })
    },
    function (next) {
      var a = null, b = null, c = null
      svc.preview(root.draft({ prompt: "a preview never carries this" }), function (res) { a = res })
      svc.preview(root.draft({}), function (res) { b = res })
      svc.preview(root.draft({}), function (res) { c = res })
      var bSync = b !== null
      root.waitFor(function () { return a !== null && b !== null && c !== null }, 10000, function (ok) {
        root.report("previews: the newest wins and earlier callbacks hear superseded, later",
          ok && !bSync && a.code === "superseded" && b.code === "superseded" && c.ok === true && c.preview.commandDigest === root.cdigest,
          JSON.stringify([a, b, c]))
        root.report("superseded is never a notice or lastError",
          root.notices.length === 0 && (svc.lastError === null || svc.lastError.code !== "superseded"), JSON.stringify(root.notices))
        next()
      })
    },
    function (next) {
      var got = null, before = root.updates
      svc.createAndArm(root.draft({ prompt: "Q1 canary prompt 7f3e", expectCommandDigest: root.cdigest, unknownKey: true }),
        function (res) { got = res })
      root.waitFor(function () { return got !== null && !svc.busy && root.updates > before }, 10000, function (ok) {
        root.report("createAndArm saves, arms with the returned digest, then re-reads the list",
          ok && got.ok === true && got.id === "aaaaaaaaaaaaaaaa" && got.status === "armed", JSON.stringify(got))
        next()
      })
    },
    function (next) {
      var n = root.notices.length
      svc.createAndArm(root.draft({ id: "eeeeeeeeeeeeeeee", prompt: "x" }), null)
      root.waitFor(function () { return root.notices.length > n && !svc.busy }, 10000, function (ok) {
        root.report("an arm that fails after a save, with no callback, still says why",
          ok && root.notices[n] === "error:The job changed since you reviewed it. Check it again.", JSON.stringify(root.notices))
        next()
      })
    },
    function (next) {
      var n = root.notices.length
      svc.deleteJob(root.idArmed, null)
      root.waitFor(function () { return root.notices.length > n }, 5000, function (ok) {
        root.report("a failure without a callback is an error notice with the helper's fixed sentence",
          ok && root.notices[n] === "error:That job cannot do this in its current state." && svc.lastError.code === "bad_status",
          JSON.stringify(root.notices))
        var got = null, m = root.notices.length
        svc.deleteJob(root.idArmed, function (res) { got = res })
        root.waitFor(function () { return got !== null }, 5000, function () {
          root.after(300, function () {
            root.report("a failure with a callback goes to the callback only",
              got !== null && got.code === "bad_status" && got.message === "That job cannot do this in its current state."
                && root.notices.length === m, JSON.stringify(got))
            next()
          })
        })
      })
    },
    function (next) {
      var r1 = null, r2 = null, r3 = null
      svc.getJob("bbbbbbbbbbbbbbbb", function (res) { r1 = res })
      svc.getJob("cccccccccccccccc", function (res) { r2 = res })
      svc.getJob(root.idArmed, function (res) { r3 = res })
      root.waitFor(function () { return r1 !== null && r2 !== null && r3 !== null }, 12000, function (ok) {
        root.report("an answer that is not the protocol is helper_failed",
          ok && r1.code === "helper_failed" && r1.message === "The helper did not answer.", JSON.stringify(r1))
        root.report("an answer past the cap is helper_output_too_large", ok && r2.code === "helper_output_too_large", JSON.stringify(r2))
        root.report("a good answer is passed through", ok && r3.ok === true && r3.prompt === "stub prompt", JSON.stringify(r3))
        next()
      })
    },
    function (next) {
      var answers = [], sync = true
      function keep(res) { answers.push({ sync: sync, code: res.code }) }
      svc.arm("nope", root.digest, keep)
      svc.arm(root.idLater, "short", keep)
      svc.swap(root.idLater, root.idLater, keep)
      svc.shift([], 60, keep)
      svc.shift([root.idLater], 0, keep)
      svc.reschedule(root.idLater, 12, keep)
      svc.getJob("../x", keep)
      sync = false
      root.waitFor(function () { return answers.length === 7 }, 3000, function (ok) {
        root.report("bad ids, digests and numbers never reach the helper",
          ok && answers.every(function (a) { return a.sync === false && a.code === "bad_args" }), JSON.stringify(answers))
        next()
      })
    },
    function (next) {
      root.waitFor(function () { return !svc.busy }, 10000, function () {
        var answers = [], busy = 0, busySync = false, sync = true
        var epoch = Math.floor(Date.now() / 1000) + 7200
        svc.disarm(root.idArmed, function (res) { answers.push("disarm:" + res.ok) })
        svc.reschedule(root.idLater, epoch, function (res) { answers.push("reschedule:" + res.ok) })
        svc.runNow(root.idLater, root.digest, function (res) { answers.push("run-now:" + res.ok) })
        for (var i = 0; i < 16; i++) {
          svc.reschedule(root.idLater, epoch + i + 1, function (res) {
            if (res.code === "busy_queue") { busy++; if (sync) busySync = true }
            else answers.push("queued:" + res.ok)
          })
        }
        sync = false
        root.waitFor(function () { return answers.length + busy === 19 && !svc.busy }, 25000, function (ok) {
          root.report("changes run one at a time, in order, with at most 16 waiting",
            ok && answers.slice(0, 3).join(",") === "disarm:true,reschedule:true,run-now:true" && busy === 2 && !busySync,
            JSON.stringify({ answers: answers, busy: busy, busySync: busySync }))
          next()
        })
      })
    },
    function (next) {
      var got = null
      svc.setSettings({ motion: "reduced" }, function (res) { got = res })
      root.waitFor(function () { return got !== null }, 5000, function (ok) {
        root.report("setSettings sends the partial object and keeps the merged answer",
          ok && got.ok === true && svc.settings.motion === "reduced" && svc.settings.defaultHarness === "codex", JSON.stringify(svc.settings))
        next()
      })
    },
    function (next) {
      svc.loadSessions("bogus")
      svc.loadSessions("claude")
      svc.loadSessions("")
      root.waitFor(function () { return !!svc.sessions.claude && !!svc.sessions.all && !svc.loadingSessions }, 8000, function (ok) {
        root.report("sessions are kept per agent and for all agents; an unknown agent is not asked for",
          ok && svc.sessions.claude.harness === "claude" && svc.sessions.all.harness === null && !svc.sessions.bogus,
          JSON.stringify(Object.keys(svc.sessions)))
        next()
      })
    },
    function (next) {
      svc.viewerOpened()
      root.report("viewerOpened counts a viewer", svc.viewers === 1, svc.viewers)
      root.waitFor(function () { return !!svc.agentFor("claude") && !svc.busy && !svc.loadingAgents }, 10000, function (ok) {
        root.report("opening a panel loads the agents; unknown agents are dropped",
          ok && svc.agentFor("other") === null && svc.agentFor("codex").enabled === false, JSON.stringify(Object.keys(svc.agents)))
        root.report("levelFor finds a level by id", !!svc.levelFor("plan") && svc.levelFor("other") === null, "")
        svc.viewerClosed()
        root.report("viewerClosed counts it back", svc.viewers === 0, svc.viewers)
        next()
      })
    },
    function (next) {
      var n = root.notices.length
      svc.copyResume(root.idArmed, null)
      root.waitFor(function () { return root.notices.length > n }, 5000, function (ok) {
        root.report("copyResume puts the command on the clipboard and says so",
          ok && root.notices[n] === "ok:Resume command copied."
            && Quickshell.clipboardText === "cd '/home/q1/proj' && claude --resume 3f2a0c19-1111-2222-3333-444455556c19",
          JSON.stringify({ notices: root.notices, clipboard: Quickshell.clipboardText }))
        next()
      })
    },
    function (next) {
      var got = null
      svc.reconcileNow(function (res) { got = res })
      root.waitFor(function () { return got !== null && !svc.busy }, 8000, function (ok) {
        root.report("reconcileNow answers through its callback", ok && got.ok === true, JSON.stringify(got))
        next()
      })
    },
    function (next) {
      console.log("Q1SVC done failures=" + root.failures)
      root.after(300, function () { Qt.quit() })
    }
  ])
}
QML
  rm -f "$T/home/calls.jsonl"
  qs_run "$T/svc/shell.qml" "$T/svc.log"
  report_marked Q1SVC "$T/svc.log" 30

  # What the helper actually received, seen from its side of the pipe.
  if out="$(/usr/bin/python3 -I -S -B - "$T/home" "$T/svc" 2>&1 <<'PY'
import json
import os
import sys

home, svcdir = sys.argv[1], sys.argv[2]
path = os.path.join(home, "calls.jsonl")
calls = [json.loads(line) for line in open(path, encoding="utf-8")] if os.path.exists(path) else []
calls.sort(key=lambda c: c["start"])
problems = []


def need(cond, what):
    if not cond:
        problems.append(what)


VERBS = {"edition", "list", "job-get", "job-create", "job-update", "job-delete", "preview", "arm", "run-now", "disarm",
         "cancel-all", "reschedule", "swap", "shift", "reconcile", "settings-get", "settings-set", "copy-resume",
         "sessions", "usage", "agents"}
WRITE = {"job-create", "job-update", "job-delete", "arm", "run-now", "disarm", "cancel-all", "reschedule", "swap",
         "shift", "reconcile", "settings-set", "copy-resume"}
STDIN = {"job-create", "job-update", "preview", "settings-set"}
ALLOWED_ENV = {"HOME", "USER", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "LANG", "PATH", "PYTHONDONTWRITEBYTECODE"}
DRAFT_KEYS = {"label", "harness", "target", "level", "limits", "model", "trigger", "prompt", "expectCommandDigest"}
CANARY = "Q1 canary prompt 7f3e"
script = os.path.realpath(os.path.join(svcdir, "bin", "ap4a"))
verbs = [c["verb"] for c in calls]

need(len(calls) >= 30, "only %d helper calls were recorded" % len(calls))
need(verbs.count("edition") == 1, "edition was asked for %d times" % verbs.count("edition"))
need(sorted(verbs[:5]) == sorted(["edition", "settings-get", "reconcile", "list", "usage"]), "start-up calls: %s" % verbs[:5])
for c in calls:
    tag = " ".join([c["verb"]] + c["args"])
    env = c["env"]
    need(c["verb"] in VERBS, "unknown verb: " + tag)
    need(c["executable"] == "/usr/bin/python3" and c["flags"] == [1, 1, 1], "interpreter or -I -S -B flags: " + tag)
    need(c["script"] == script, "helper path: " + tag)
    need(set(env) <= ALLOWED_ENV, "environment beyond the allowlist for %s: %s" % (tag, sorted(set(env) - ALLOWED_ENV)))
    need(env.get("LANG") == "C.UTF-8" and env.get("PATH") == "/usr/bin" and env.get("PYTHONDONTWRITEBYTECODE") == "1",
         "fixed environment values: " + tag)
    need(env.get("HOME") == home and "XDG_RUNTIME_DIR" in env, "HOME and XDG_RUNTIME_DIR passed on: " + tag)
    need(c["stdinEof"] is True, "stdin left open: " + tag)
    need(CANARY not in json.dumps(c["args"]) and CANARY not in json.dumps(env), "the prompt reached argv or env: " + tag)
    if c["verb"] in STDIN:
        text = c["stdin"]
        need(text.endswith("\n") and text.count("\n") == 1, "stdin is not one line: " + tag)
        try:
            payload = json.loads(text)
        except ValueError:
            payload = None
        need(isinstance(payload, dict), "stdin is not a JSON object: " + tag)
        if isinstance(payload, dict) and c["verb"] == "preview":
            need("prompt" not in payload and "expectCommandDigest" not in payload, "a preview carried the prompt")
        if isinstance(payload, dict) and c["verb"] in ("job-create", "job-update"):
            need(set(payload) <= DRAFT_KEYS, "draft keys outside the schema: %s" % sorted(set(payload) - DRAFT_KEYS))
    else:
        need(c["stdin"] == "", "stdin text sent to " + tag)

creates = [c for c in calls if c["verb"] == "job-create"]
need(len(creates) == 1, "job-create ran %d times" % len(creates))
if creates:
    payload = json.loads(creates[0]["stdin"])
    need(payload.get("prompt") == CANARY and payload.get("expectCommandDigest") == "c" * 64, "job-create payload")
writes = [c for c in calls if c["verb"] in WRITE]
for prev, cur in zip(writes, writes[1:]):
    need(cur["start"] >= prev["end"], "%s started before %s ended" % (cur["verb"], prev["verb"]))
for i, c in enumerate(writes):
    if c["verb"] in ("job-create", "job-update"):
        jid = c["args"][0] if c["args"] else "a" * 16
        nxt = writes[i + 1] if i + 1 < len(writes) else None
        need(nxt is not None and nxt["verb"] == "arm" and nxt["args"] == [jid, "--digest", "d" * 64],
             "the save of %s was not followed straight away by its arm" % jid)
need(sorted(tuple(c["args"]) for c in calls if c["verb"] == "sessions") == [(), ("--harness", "claude")],
     "sessions calls: %s" % [c["args"] for c in calls if c["verb"] == "sessions"])
need([c["args"] for c in calls if c["verb"] == "agents"] == [[]], "agents calls")
need(verbs.count("reschedule") == 15 and verbs.count("run-now") == 1 and verbs.count("disarm") == 1,
     "queued changes: reschedule %d, run-now %d, disarm %d" % (verbs.count("reschedule"), verbs.count("run-now"), verbs.count("disarm")))
if problems:
    print("; ".join(problems[:6]))
    raise SystemExit(1)
PY
)"; then ok "the helper saw absolute argv, -I -S -B, the env allowlist, one-line stdin only for request verbs, and no prompt outside stdin"
  else no "the helper saw absolute argv, -I -S -B, the env allowlist, one-line stdin only for request verbs, and no prompt outside stdin" "$out"; fi

  echo "=== BarWidget.qml (live, installed qs.Commons and qs.Ui) ==="
  if [ ! -d /usr/share/omarchy/shell/Ui ]; then
    echo "  skip (no omarchy shell at /usr/share/omarchy/shell)"
  else
    mkdir -p "$T/bw/lib"
    cp "$REPO/BarWidget.qml" "$T/bw/"
    cp "$REPO"/lib/*.js "$T/bw/lib/"
    # Inside Quickshell, qs.Commons and qs.Ui resolve to <config dir>/Commons and /Ui.
    ln -s /usr/share/omarchy/shell/Commons "$T/bw/Commons"
    ln -s /usr/share/omarchy/shell/Ui "$T/bw/Ui"
    # Panel.qml belongs to another part of the build; this stand-in has only the
    # members BarWidget touches.
    cat > "$T/bw/Panel.qml" <<'QML'
import QtQuick
Item {
  property var bar: null
  property var settings: null
  property var anchorItem: null
  property var hostWidget: null
  property var service: null
  property bool opened: false
  property bool popoutSwitchClosing: false
  property string view: ""
  function open() { opened = true }
  function close() { opened = false }
  function toggle() { opened = !opened }
  function closeForPopoutSwitch() { opened = false }
  function showView(v) { view = v }
}
QML
    cat > "$T/bw/shell.qml" <<'QML'
import QtQuick
import Quickshell
import "lib/Edition.js" as Edition

ShellRoot {
  id: root

  property int failures: 0

  function report(name, ok, detail) {
    if (!ok) root.failures++
    console.log("Q1BW " + (ok ? "ok " : "FAIL ") + name + (ok ? "" : " :: " + detail))
  }

  QtObject {
    id: svc
    property bool ready: true
    property bool anyRunning: false
    property int armedCount: 2
    property int runningCount: 0
    property int attentionCount: 1
    property double nowMs: 1789363320000
    property var nextJob: ({ fireAtMs: 1789363320000 + 168 * 60000 })
    property string setupProblem: ""
    property string barLabel: ""
  }

  QtObject { id: ownShell; function serviceFor(id) { return id === Edition.PLUGIN_ID ? svc : null } }
  QtObject { id: emptyShell; function serviceFor(id) { return null } }

  QtObject {
    id: fakeBar
    property var shell: ownShell
    property bool vertical: false
    property int barSize: 30
    property string fontFamily: "monospace"
    property color barForeground: "white"
    property color urgent: "red"
    property bool foregroundAnimationEnabled: false
    function showTooltip(target, text) {}
    function hideTooltip(target) {}
    function registerClickTarget(target) {}
    function unregisterClickTarget(target) {}
  }

  QtObject {
    id: bareBar
    property var shell: emptyShell
    property bool vertical: false
    property int barSize: 30
    property string fontFamily: "monospace"
    property color barForeground: "white"
    property color urgent: "red"
    property bool foregroundAnimationEnabled: false
    function showTooltip(target, text) {}
    function hideTooltip(target) {}
    function registerClickTarget(target) {}
    function unregisterClickTarget(target) {}
  }

  Item {
    BarWidget { id: w; bar: fakeBar; settings: ({ barLabel: "Next run" }) }
    BarWidget { id: lonely; bar: bareBar; settings: ({}) }
  }

  Timer {
    interval: 800
    running: true
    onTriggered: {
      try {
        // CONTRACT-V2-DELTA 9.7: the service has no barState here, so the widget derives it from the
        // v1 counts; attention comes before running, which comes before the countdown.
        root.report("finds its service through bar.shell.serviceFor(PLUGIN_ID)", w.service === svc, "")
        root.report("pushes the bar label setting into the service", svc.barLabel === "Next run", svc.barLabel)
        root.report("attention comes first: the label counts the jobs that need you",
          w.labelText === "1 needs you" && w.barState.state === "attention" && w.glyph === String.fromCodePoint(0xF0028) && w.tone === "bad",
          JSON.stringify([w.labelText, w.barState.state, w.tone]))
        root.report("the tooltip opens with the state sentence, then the counts",
          w.tooltipText === "1 job needs you since the panel was last opened.\n" + Edition.DISPLAY_NAME + " · 2 armed", JSON.stringify(w.tooltipText))
        root.report("mounts no Panel.qml before the panel is first used", w.panel === null && w.opened === false, "")
        root.report("the open-panel indicator follows the label", w.openPanelIndicatorWidth > 0, w.openPanelIndicatorWidth)
        w.openView("queue")
        root.report("loads Panel.qml on first use and injects bar, settings, anchor, host and service",
          w.panel !== null && w.panel.service === svc && w.panel.bar === fakeBar && w.panel.hostWidget === w
            && w.panel.anchorItem !== null && w.panel.settings.barLabel === "Next run", "")
        root.report("openView shows the view, then opens the panel", w.panel.view === "queue" && w.opened === true, w.panel.view)
        w.close()
        root.report("close closes the panel", w.opened === false, "")
        w.togglePanel()
        root.report("togglePanel opens it again", w.opened === true, "")
        svc.attentionCount = 0
        root.report("Next run shows the bar countdown once nothing needs you (soon: under 6 hours)",
          w.labelText === "2h48" && w.barState.state === "soon" && w.glyph === String.fromCodePoint(0xF0150) && w.tone === "warn",
          JSON.stringify([w.labelText, w.barState.state, w.tone]))
        w.settings = { barLabel: "Queued count" }
        root.report("Queued count shows the armed count and is pushed", w.labelText === "2" && svc.barLabel === "Queued count", w.labelText)
        w.settings = { barLabel: "Nothing" }
        root.report("Nothing leaves the icon alone", w.labelText === "", w.labelText)
        svc.anyRunning = true
        svc.runningCount = 1
        root.report("a running job turns the glyph into play and pulses",
          w.glyph === w.glyphRunning && w.glyphRunning === "󰐊" && w.barState.state === "running" && w.pulsing === true, w.glyph)
        svc.ready = false
        root.report("no label and a starting tooltip until the service is ready",
          w.labelText === "" && w.tooltipText === Edition.DISPLAY_NAME + " · starting", JSON.stringify(w.tooltipText))
        root.report("without its service the widget stays inert",
          lonely.service === null && lonely.labelText === "" && lonely.tooltipText === Edition.DISPLAY_NAME + " · starting", "")
      } catch (e) {
        root.report("the checks ran", false, String(e))
      }
      console.log("Q1BW done failures=" + root.failures)
      Qt.quit()
    }
  }
}
QML
    qs_run "$T/bw/shell.qml" "$T/bw.log"
    report_marked Q1BW "$T/bw.log" 16
  fi
fi

# ---------------------------------------------------------------- qmllint

echo "=== qmllint (Qt 6) ==="
QLINT=/usr/lib/qt6/bin/qmllint
LROOT=""
if [ -d /usr/share/omarchy/shell/Commons ]; then
  # qs.Commons and qs.Ui resolve to <root>/Commons at run time; qmllint needs a
  # qs/ directory, so point one at the installed shell.
  mkdir -p "$T/lintroot"
  ln -s /usr/share/omarchy/shell "$T/lintroot/qs"
  LROOT="$T/lintroot"
elif [ -d "$REPO/lint/qs" ]; then
  LROOT="$REPO/lint"
fi
if [ ! -x "$QLINT" ] || [ -z "$LROOT" ]; then
  echo "  skip qmllint (no Qt 6 qmllint or no shell to lint against)"
else
  # The shell types Style.font, Style.bar, Style.spacing and the bar facade as
  # plain QtObject, so their members are invisible to the linter although they
  # resolve at run time. That one category is off; everything else counts.
  for f in Service.qml BarWidget.qml BoundedProcess.qml components/AgentMark.qml components/HarnessRail.qml \
           components/StatusPill.qml components/Meter.qml components/LimitStrip.qml components/NoticeRow.qml \
           components/Chip.qml components/ActionButton.qml components/SearchField.qml components/ArgvLine.qml \
           components/FooterHints.qml components/ProblemCard.qml; do
    lout="$(cd "$REPO" && timeout 120 "$QLINT" --ignore-settings --missing-property disable -I "$LROOT" "$f" 2>&1)"; lrc=$?
    if [ "$lrc" -eq 0 ] && ! printf '%s' "$lout" | grep -qE '^(Warning|Error):'; then ok "qmllint $f"
    else no "qmllint $f" "$(printf '%s' "$lout" | grep -m2 -E '^(Warning|Error):' | tr '\n' ' ')"; fi
  done
fi

printf '\n%d passed' "$pass"
[ "$fail" -gt 0 ] && printf ', %d FAILED' "$fail"
printf '\n'
[ "$fail" -eq 0 ]
