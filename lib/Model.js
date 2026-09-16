.pragma library
.import "Edition.js" as Edition

// Pure helpers for the panel and the bar: time, formatting, status words, grouping
// and drafts. Nothing here touches QML, the clock or the disk; every function takes
// "now" as an argument, so the same inputs always give the same answer and the
// whole file runs in a bare engine under test.
//
// Times: fields of helper JSON are epoch seconds; everything named ...Ms is epoch
// milliseconds. Local time is for display only.

var INT32_MAX = 2147483647
var MINUTE_MS = 60000
var HOUR_MS = 3600000
var DAY_MS = 86400000

var WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

var HARNESS_NAMES = { claude: "Claude Code", opencode: "OpenCode", codex: "Codex", gemini: "Gemini CLI",
                      cursor: "Cursor Agent", pi: "Pi" }
var CLI_NAMES = { claude: "claude", opencode: "opencode", codex: "codex", gemini: "gemini",
                  cursor: "cursor-agent", pi: "pi" }
// The first reset kind each agent offers (consts.RESET_TRIGGER); "" when it has none.
var RESET_TRIGGER = { claude: "claude_5h_reset", opencode: "zen_free_reset", codex: "codex_window_reset",
                      gemini: "gemini_daily_reset", cursor: "", pi: "codex_window_reset" }
// Every reset kind a draft may use per agent (consts.RESET_KINDS_FOR). OpenCode no
// longer follows Claude's window; a stored job that does still loads, but a draft
// made from it starts over at "now".
var RESET_KINDS_FOR = { claude: ["claude_5h_reset"], opencode: ["zen_free_reset", "go_window_reset"],
                        codex: ["codex_window_reset"], gemini: ["gemini_daily_reset"], cursor: [],
                        pi: ["codex_window_reset"] }
var HARNESS_ORDER = ["claude", "opencode", "codex", "gemini", "cursor", "pi"]

// How each agent is signed in: the exact command to type in a terminal, and one
// sentence of what to expect from it. The sign-in sheet shows these; nothing here is
// ever run by the plugin, and no agent is ever asked for a key or a token.
var SIGN_IN_STEPS = {
  claude: { command: "claude auth login",
            note: "Claude Code also signs in the first time you run it." },
  opencode: { command: "opencode auth login",
              note: "Sign in once in a terminal, then check again here." },
  codex: { command: "codex login",
           note: "A free ChatGPT account is enough." },
  gemini: { command: "gemini",
            note: "Run it once and pick a sign-in there." },
  cursor: { command: "cursor-agent login",
            note: "Sign in once in a terminal, then check again here." },
  pi: { command: "pi",
        note: "Type /login in Pi and pick a provider. Pi needs at least one signed-in provider." }
}

// One reading of an `agents` entry, shared by the Send to chips and the sign-in sheet so
// both always say the same thing about an agent. `enabled` is whether it can be chosen at
// all, `warn` whether it needs the user, `ready` whether it can be armed as it stands, and
// `reasonKey` which sentence says why: "" | "not_found" | "shim" | "untrusted" |
// "not_signed_in" | "sign_in_unknown" | "gated" | "unavailable".
//
// Gemini is the one agent whose sign-in cannot be read without starting it, so before its
// first run it is selectable but marked.
function signInState(agent, id, geminiRan) {
  var key = String(id || "")
  var unknown = key === "gemini" && geminiRan !== true
  function out(enabled, warn, reasonKey) {
    return { enabled: enabled, warn: warn, ready: enabled && !warn, reasonKey: reasonKey }
  }
  if (!agent) return out(true, unknown, unknown ? "sign_in_unknown" : "")
  if (agent.available !== true) {
    if (agent.reason === "untrusted") return out(false, true, "untrusted")
    if (agent.reason === "shim") return out(false, true, "shim")
    return out(false, true, "not_found")
  }
  // Selectable (drafts and the preview work), with the reason it cannot be armed yet.
  if (agent.gated === true || agent.reason === "gated") return out(agent.enabled !== false, true, "gated")
  if (key === "codex" && agent.enabled !== true) return out(false, true, "not_signed_in")
  if (agent.enabled === false) return out(false, true, agent.reason === "not_logged_in" ? "not_signed_in" : "unavailable")
  return out(true, unknown, unknown ? "sign_in_unknown" : "")
}

// Bar countdown bands (FEEDBACK-UI 1): under an hour is near (green), under six hours
// soon (amber), anything later is calm.
var NEAR_SEC = 3600
var SOON_SEC = 21600
// States that ask for the user once they happen after the panel was last opened.
var PROBLEM_STATUSES = ["failed", "interrupted", "missed", "gave_up", "needs_confirm", "paused"]
var FAILURE_STATUSES = ["failed", "gave_up"]
var ATTENTION_STATUSES = ["missed", "busy", "interrupted", "paused", "needs_confirm"]
// Finished badly: these need attention only until the panel has been seen after they ended.
// The bar's attention count and the panel's default view both read these two lists.
var ENDED_ATTENTION_STATUSES = ["failed", "gave_up", "limit"]
var EXHAUSTED = 0.99
var LABEL_MAX = 40
var ID_RE = /^[0-9a-f]{16}$/

// Nerd Font Material Design Icons, written as surrogate pairs so a diff shows
// which glyph is meant (a private-use code point is invisible in review).
var GLYPH = {
  pencil: "\uDB80\uDFEB",     // md-pencil        U+F03EB
  clock: "\uDB80\uDD50",      // md-clock_outline U+F0150
  sand: "\uDB81\uDD1F",       // md-timer_sand    U+F051F
  update: "\uDB81\uDEB0",     // md-update        U+F06B0
  play: "\uDB81\uDC0A",       // md-play          U+F040A
  check: "\uDB80\uDD2C",      // md-check         U+F012C
  close: "\uDB80\uDD56",      // md-close         U+F0156
  alert: "\uDB80\uDC28",      // md-alert_circle  U+F0028
  skip: "\uDB81\uDCAD",       // md-skip_next     U+F04AD
  history: "\uDB80\uDEDA",    // md-history       U+F02DA
  pause: "\uDB80\uDFE4",      // md-pause         U+F03E4
  calendar: "\uDB80\uDCF0",   // md-calendar_clock U+F00F0
  left: "\uDB80\uDD41",       // md-chevron_left  U+F0141
  right: "\uDB80\uDD42"       // md-chevron_right U+F0142
}

// The runner's notification sentences (CONTRACT 3.8.8), so a History caption and
// the toast that announced the same event say the same thing.
var REASON_SENTENCES = {
  auth: "The agent is not signed in.",
  not_found: "The session no longer exists.",
  untrusted: "The agent does not trust this folder yet.",
  boundary_mismatch: "The agent did not start in the requested permission level.",
  max_turns: "It reached the turn limit.",
  budget: "It reached the budget limit.",
  timeout: "It ran past its time limit.",
  failed: "The agent exited with an error.",
  cli_missing: "The agent command was not found.",
  cli_untrusted: "The agent command failed a safety check.",
  cwd_refused: "The working folder is not allowed.",
  weekly_exhausted: "The weekly limit is used up.",
  window_later: "The 5-hour window ends later than expected.",
  window_exhausted: "The new 5-hour window is already used up.",
  horizon: "It waited longer than 8 days.",
  overage: "Usage credits are in use, so it does not retry.",
  stale_auth: "The agent reports a limit while usage is low. Sign in again.",
  kill_switch: "The kill switch file is present.",
  plugin_disabled: Edition.DISPLAY_NAME + " is not enabled in the bar.",
  plugin_identity: "The plugin folder does not match its manifest.",
  digest_mismatch: "The job changed since it was armed.",
  late: "The computer was off or asleep past the grace time.",
  session_busy: "The session stayed in use.",
  session_locked: "Another job was using the session.",
  not_logged_in: "The agent is not signed in.",
  limit_retries: "It gave up after 3 retries.",
  transient_retries: "Temporary errors continued after 3 retries.",
  defers: "It was deferred 4 times.",
  interrupted: "It stopped before it finished.",
  prompt_missing: "The prompt is no longer stored.",
  paid_blocked: "It would have used paid usage, which is off for this job.",
  overage_blocked: "Usage credits would have been used, so it waits for the reset.",
  paid_defer: "Included usage is used up, so it waits for the reset.",
  paid_exhausted: "Included usage is used up until after the 8-day limit, so it was skipped.",
  limit_full: "The usage limit is used up, so it waits for the reset.",
  zen_billing: "The OpenCode Zen balance or spending limit stopped it.",
  limit_suspected: "The agent was waiting on a usage limit.",
  stalled: "The agent produced no output.",
  cursor_autorun_config: "Cursor is set to approve every tool.",
  cursor_network_config: "Cursor's sandbox allows all network access.",
  cursor_project_rules: "This folder has its own Cursor or Claude allow rules.",
  harness_gated: "Cursor support is waiting for a one-time check.",
  monthly_limit: "The monthly limit is used up, so it does not retry.",
  cursor_sandbox: "Cursor's sandbox could not start, so nothing ran. Turn it off in cursor-agent.",
  quota_final: "The provider reports no quota or balance left, so it does not retry."
}

// ---------------------------------------------------------------- numbers

// Every Timer interval goes through here. A QML interval is a signed 32-bit int:
// hours * 3600 * 1000 wraps negative past 24.8 days and the timer then spins.
function clampInterval(ms) {
  var n = Number(ms)
  if (isNaN(n)) return 1000
  if (n === Infinity) return INT32_MAX
  n = Math.round(n)
  if (n < 1) return 1
  if (n > INT32_MAX) return INT32_MAX
  return n
}

function pad2(n) {
  var v = Math.floor(Math.abs(Number(n) || 0))
  return v < 10 ? "0" + v : String(v)
}

function isNum(v) { return typeof v === "number" && isFinite(v) }

function secToMs(v) { return isNum(v) ? v * 1000 : null }

// ---------------------------------------------------------------- durations

// Rounded to the nearest second first. The clock ticks 5 ms after a boundary, so
// flooring would show 2h 47m for a job that is 2h 48m away at the tick.
function roundSec(ms) {
  var s = Math.round(Number(ms) / 1000)
  return isFinite(s) ? s : 0
}

// "2d 3h", "2h 48m", "48m", "1m 09s" (seconds only under two minutes, padded so the
// width holds while it ticks), "0s".
function formatDuration(ms) {
  var s = roundSec(ms)
  if (s <= 0) return "0s"
  if (s < 120) {
    var m0 = Math.floor(s / 60)
    return m0 > 0 ? m0 + "m " + pad2(s % 60) + "s" : s + "s"
  }
  if (s < 3600) return Math.floor(s / 60) + "m"
  if (s < 86400) return Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m"
  return Math.floor(s / 86400) + "d " + Math.floor((s % 86400) / 3600) + "h"
}

// Elapsed time of a run: seconds stay visible for the first hour, because a run is
// watched in seconds and a countdown is not.
function formatElapsed(ms) {
  var s = Math.max(0, roundSec(ms))
  if (s < 60) return s + "s"
  if (s < 3600) return Math.floor(s / 60) + "m " + pad2(s % 60) + "s"
  if (s < 86400) return Math.floor(s / 3600) + "h " + Math.floor((s % 3600) / 60) + "m"
  return Math.floor(s / 86400) + "d " + Math.floor((s % 86400) / 3600) + "h"
}

// How long a finished run took, as a record reads it rather than a ticker: zero parts
// are dropped ("4m", "1h"), seconds stay only under ten minutes ("3m 12s").
function formatRunLength(ms) {
  var s = Math.max(0, roundSec(ms))
  if (s < 60) return s + "s"
  if (s < 600) return Math.floor(s / 60) + "m" + (s % 60 > 0 ? " " + pad2(s % 60) + "s" : "")
  if (s < 3600) return Math.floor(s / 60) + "m"
  if (s < 86400) {
    var m = Math.floor((s % 3600) / 60)
    return Math.floor(s / 3600) + "h" + (m > 0 ? " " + m + "m" : "")
  }
  var h = Math.floor((s % 86400) / 3600)
  return Math.floor(s / 86400) + "d" + (h > 0 ? " " + h + "h" : "")
}

// How old a reading is: "45s", "42m", "2h 5m", "3d 4h". No seconds past a minute,
// because an age is read once, not watched.
function formatAge(sec) {
  var s = Math.max(0, Math.round(Number(sec) || 0))
  if (s < 60) return s + "s"
  if (s < 3600) return Math.floor(s / 60) + "m"
  return formatDuration(s * 1000)
}

function formatCountdown(fireAtMs, nowMs) {
  var d = Number(fireAtMs) - Number(nowMs)
  if (!isFinite(d) || roundSec(d) <= 0) return "firing"
  return "in " + formatDuration(d)
}

function formatRunning(startedAtMs, nowMs) {
  var d = Number(nowMs) - Number(startedAtMs)
  return "running " + formatElapsed(isFinite(d) ? d : 0)
}

// The bar has five characters to spare: "2d3h", "22h", "2h48m", "48m", "<1m". Every
// figure carries its unit, so a countdown never reads as a clock time.
function formatBarCountdown(ms) {
  var s = roundSec(ms)
  if (s < 60) return "<1m"
  if (s < 3600) return Math.floor(s / 60) + "m"
  if (s < 36000) return Math.floor(s / 3600) + "h" + pad2(Math.floor((s % 3600) / 60)) + "m"
  if (s < 86400) return Math.floor(s / 3600) + "h"
  return Math.floor(s / 86400) + "d" + Math.floor((s % 86400) / 3600) + "h"
}

// ---------------------------------------------------------------- calendar

function validDate(ms) {
  var d = new Date(Number(ms))
  return isNaN(d.getTime()) ? null : d
}

function formatClock(ms) {
  var d = validDate(ms)
  if (!d) return "--:--"
  return pad2(d.getHours()) + ":" + pad2(d.getMinutes())
}

function dayKey(ms) {
  var d = validDate(ms)
  if (!d) return ""
  return d.getFullYear() + "-" + pad2(d.getMonth() + 1) + "-" + pad2(d.getDate())
}

function localMidnight(ms) {
  var d = validDate(ms)
  if (!d) return NaN
  return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime()
}

// Whole calendar days between the two local dates. Rounded, because a day that
// crosses a DST change is 23 or 25 hours long.
function dayDiff(ms, nowMs) {
  return Math.round((localMidnight(ms) - localMidnight(nowMs)) / DAY_MS)
}

// "Mon 14 Sep"
function dateText(ms) {
  var d = validDate(ms)
  if (!d) return ""
  return WEEKDAYS[d.getDay()] + " " + d.getDate() + " " + MONTHS[d.getMonth()]
}

function dayLabel(ms, nowMs) {
  var d = validDate(ms)
  if (!d) return { word: "", date: "" }
  var diff = dayDiff(ms, nowMs)
  var word = diff === 0 ? "Today" : diff === 1 ? "Tomorrow" : diff === -1 ? "Yesterday" : WEEKDAYS[d.getDay()]
  return { word: word, date: dateText(ms) }
}

// "10:10" today, "Sun 13 Sep 09:00" on any other day.
function clockOrDay(ms, nowMs) {
  if (!validDate(ms)) return "--:--"
  return dayDiff(ms, nowMs) === 0 ? formatClock(ms) : dateText(ms) + " " + formatClock(ms)
}

// "today, in 2h 48m", "tomorrow, in 16h 50m", "Wed 16 Sep, in 2d 3h".
function resolvedLine(ms, nowMs) {
  if (!validDate(ms)) return ""
  var diff = dayDiff(ms, nowMs)
  var day = diff === 0 ? "today" : diff === 1 ? "tomorrow" : diff === -1 ? "yesterday" : dateText(ms)
  return day + ", " + formatCountdown(ms, nowMs)
}

// The earliest time the UI offers: one minute from now, on a whole minute, so the
// helper's own one-minute lead still holds when the request lands a moment later.
function earliestMs(nowMs) {
  return Math.ceil((Number(nowMs) + MINUTE_MS) / MINUTE_MS) * MINUTE_MS
}

// Local-calendar nudge (R6 3.3). setHours normalises minute overflow across
// midnight and DST. The first 5-minute step snaps to the grid: 10:13 +5 is 10:15.
function nudged(ms, stepMin, snap, nowMs) {
  var base = Number(ms)
  if (!isFinite(base)) base = Number(nowMs)
  var d = new Date(base)
  d.setSeconds(0, 0)
  var m = d.getHours() * 60 + d.getMinutes()
  var step = Math.round(Number(stepMin)) || 0
  var next = m + step
  if (snap && Math.abs(step) === 5 && m % 5 !== 0)
    next = step > 0 ? Math.ceil(m / 5) * 5 : Math.floor(m / 5) * 5
  d.setHours(0, next, 0, 0)
  return Math.max(d.getTime(), earliestMs(nowMs))
}

// Same clock time, another day.
function nudgedDays(ms, days, nowMs) {
  var base = Number(ms)
  if (!isFinite(base)) base = Number(nowMs)
  var d = new Date(base)
  d.setSeconds(0, 0)
  d.setDate(d.getDate() + (Math.round(Number(days)) || 0))
  return Math.max(d.getTime(), earliestMs(nowMs))
}

function atClock(h, mi, nowMs) {
  var d = new Date(Number(nowMs))
  d.setHours(h, mi, 0, 0)
  var rolled = false
  if (d.getTime() <= Number(nowMs)) {
    d = new Date(Number(nowMs))
    d.setDate(d.getDate() + 1)
    d.setHours(h, mi, 0, 0)
    rolled = true
  }
  return { ms: d.getTime(), rolled: rolled }
}

// Typed times: "1405", "905", "14:05". A time already passed today means tomorrow.
function parseTyped(text, nowMs) {
  var t = String(text === undefined || text === null ? "" : text).trim()
  var m = /^([0-9]{1,2}):([0-9]{2})$/.exec(t) || /^([0-9]{1,2})([0-9]{2})$/.exec(t)
  if (!m || !isFinite(Number(nowMs))) return null
  var h = parseInt(m[1], 10), mi = parseInt(m[2], 10)
  if (h > 23 || mi > 59) return null
  return atClock(h, mi, nowMs)
}

// The evening and morning chips ("23:00", "07:00" from settings).
// The morning preset is always tomorrow, even at 05:00 with a 07:00 morning, so
// Compose's chip and the empty Queue's chip name the same time.
function morningPreset(hhmm, nowMs) {
  var p = presetTime(hhmm, nowMs)
  if (!p) return null
  return p.tomorrow ? p : { ms: nudgedDays(p.ms, 1, 0), tomorrow: true }
}

function presetTime(hhmm, nowMs) {
  var m = /^([01][0-9]|2[0-3]):([0-5][0-9])$/.exec(String(hhmm || ""))
  if (!m || !isFinite(Number(nowMs))) return null
  var r = atClock(parseInt(m[1], 10), parseInt(m[2], 10), nowMs)
  return { ms: r.ms, tomorrow: r.rolled }
}

// ---------------------------------------------------------------- resets

function sessionWindow(usage) {
  var c = usage && usage.claude
  if (!c || !Array.isArray(c.windows)) return null
  for (var i = 0; i < c.windows.length; i++) {
    var w = c.windows[i]
    if (w && w.kind === "session") return w
  }
  return null
}

function nthSundayUtc(year, month, n) {
  var first = new Date(Date.UTC(year, month, 1)).getUTCDay()
  return 1 + ((7 - first) % 7) + (n - 1) * 7
}

// US Pacific rule since 2007: daylight time from the second Sunday of March at
// 02:00 PST (10:00 UTC) to the first Sunday of November at 02:00 PDT (09:00 UTC).
function laOffsetHours(utcMs) {
  var y = new Date(utcMs).getUTCFullYear()
  var start = Date.UTC(y, 2, nthSundayUtc(y, 2, 2), 10)
  var end = Date.UTC(y, 10, nthSundayUtc(y, 10, 1), 9)
  return utcMs >= start && utcMs < end ? -7 : -8
}

// Next 00:00 in America/Los_Angeles (Gemini's daily quota day), strictly after now.
// Pacific offsets are whole hours, so midnight always falls on a UTC hour: walk the
// UTC hours and ask each one what the wall clock in Los Angeles reads.
function nextLaMidnightMs(nowMs) {
  var now = Number(nowMs)
  if (!isFinite(now)) return null
  var h = Math.floor(now / HOUR_MS) * HOUR_MS
  for (var i = 0; i <= 50; i++) {
    var t = h + i * HOUR_MS
    if (t <= now) continue
    var local = t + laOffsetHours(t) * HOUR_MS
    if (((local % DAY_MS) + DAY_MS) % DAY_MS === 0) return t
  }
  return null
}

// What the "At reset" chip can offer for this agent right now. The helper decides
// the real fire time at arm; this mirrors its rules so the chip never promises a
// time the helper would refuse.
function resetChip(usage, harness, nowMs, marginSec) {
  var kind = RESET_TRIGGER.hasOwnProperty(String(harness)) ? RESET_TRIGGER[harness] : ""
  var margin = Number(marginSec)
  if (!isFinite(margin)) margin = 120
  var now = Number(nowMs)
  var out = { kind: kind, label: "", fireAtMs: null, percent: null, stale: false, available: false, reason: "no_data" }

  if (kind === "gemini_daily_reset") {
    var mid = nextLaMidnightMs(now)
    if (mid === null) return out
    out.label = "At reset " + formatClock(mid)
    out.fireAtMs = mid + margin * 1000
    out.available = true
    out.reason = null
    return out
  }

  if (kind === "claude_5h_reset") {
    out.label = "At reset"
    var c = usage && usage.claude
    if (!c || c.available !== true) return out
    out.stale = c.stale === true
    var w = sessionWindow(usage)
    if (w && isNum(w.percent)) out.percent = w.percent
    if (!w || !isNum(w.resetsAt) || w.resetsAt * 1000 <= now) {
      out.reason = "not_open"
      return out
    }
    out.label = "At reset " + formatClock(w.resetsAt * 1000)
    out.fireAtMs = w.resetsAt * 1000 + margin * 1000
    out.available = true
    out.reason = out.stale ? "stale" : null
    return out
  }

  if (kind === "codex_window_reset") {
    out.label = "At reset"
    var x = usage && usage.codex
    if (!x || x.available !== true || !Array.isArray(x.windows)) return out
    out.stale = x.stale === true
    var pick = null
    for (var i = 0; i < x.windows.length; i++) {
      var cw = x.windows[i]
      if (!cw || !isNum(cw.resetsAt) || cw.resetsAt * 1000 <= now) continue
      var full = isNum(cw.percent) && cw.percent >= EXHAUSTED
      var pickFull = pick !== null && isNum(pick.percent) && pick.percent >= EXHAUSTED
      if (pick === null || (full && !pickFull) || (full === pickFull && cw.resetsAt < pick.resetsAt)) pick = cw
    }
    if (pick === null) return out
    out.percent = isNum(pick.percent) ? pick.percent : null
    out.label = "At reset " + formatClock(pick.resetsAt * 1000)
    out.fireAtMs = pick.resetsAt * 1000 + margin * 1000
    out.available = true
    out.reason = out.stale ? "stale" : null
    return out
  }

  return out
}

// ---------------------------------------------------------------- reset binding

// While the When control follows a reset, a nudge moves the buffer after that
// reset, not a clock time: whole minutes, clamped to the helper's marginSec range
// (60..540 s, so 1 to 9 minutes).
var MARGIN_MIN_SEC = 60
var MARGIN_MAX_SEC = 540

var RESET_NAMES = { claude_5h_reset: "Claude reset", codex_window_reset: "Codex reset",
                    gemini_daily_reset: "Gemini reset", zen_free_reset: "Zen free reset", go_window_reset: "Go reset" }

function clampMargin(sec) {
  var n = Number(sec)
  if (!isFinite(n)) n = 120
  return Math.max(MARGIN_MIN_SEC, Math.min(MARGIN_MAX_SEC, Math.round(n / 60) * 60))
}

function nudgedMargin(marginSec, stepMin) {
  var minutes = Math.round(clampMargin(marginSec) / 60) + (Math.round(Number(stepMin)) || 0)
  return clampMargin(minutes * 60)
}

// "+2m"
function marginLabel(marginSec) {
  return "+" + Math.round(clampMargin(marginSec) / 60) + "m"
}

// "follows Zen free reset +2m". Takes the job's reset kind; a bare agent id (the v1
// call) still works and names that agent's first reset kind.
function followsLine(harnessOrKind, marginSec) {
  var key = String(harnessOrKind || "")
  var kind = RESET_NAMES.hasOwnProperty(key) ? key : (RESET_TRIGGER.hasOwnProperty(key) ? RESET_TRIGGER[key] : "")
  if (kind === "" || !RESET_NAMES.hasOwnProperty(kind)) return ""
  return "follows " + RESET_NAMES[kind] + " " + marginLabel(marginSec)
}

// ---------------------------------------------------------------- status

// CONTRACT 6.4. tone: soft | readable | accent | harness | ok | warn | bad.
function statusSpec(status, wait) {
  switch (String(status || "")) {
  case "draft": return { glyph: GLYPH.pencil, word: "draft", tone: "soft" }
  case "armed":
    if (wait === "reset" || wait === "limit") return { glyph: GLYPH.sand, word: "waits for reset", tone: "harness" }
    if (wait === "transient") return { glyph: GLYPH.update, word: "retrying", tone: "readable" }
    if (wait === "busy" || wait === "deferred") return { glyph: GLYPH.sand, word: "deferred", tone: "readable" }
    return { glyph: GLYPH.clock, word: "armed", tone: "readable" }
  case "running": return { glyph: GLYPH.play, word: "running", tone: "accent" }
  case "done": return { glyph: GLYPH.check, word: "done", tone: "ok" }
  case "failed": return { glyph: GLYPH.close, word: "failed", tone: "bad" }
  case "limit": return { glyph: GLYPH.alert, word: "limit hit", tone: "warn" }
  case "busy": return { glyph: GLYPH.alert, word: "session busy", tone: "warn" }
  case "skipped": return { glyph: GLYPH.skip, word: "skipped", tone: "readable" }
  case "missed": return { glyph: GLYPH.history, word: "missed", tone: "warn" }
  case "interrupted": return { glyph: GLYPH.close, word: "interrupted", tone: "warn" }
  case "paused": return { glyph: GLYPH.pause, word: "paused", tone: "warn" }
  case "needs_confirm": return { glyph: GLYPH.alert, word: "check job", tone: "warn" }
  case "disarmed": return { glyph: GLYPH.close, word: "disarmed", tone: "soft" }
  case "gave_up": return { glyph: GLYPH.close, word: "gave up", tone: "bad" }
  }
  return { glyph: "", word: String(status || ""), tone: "soft" }
}

function reasonSentence(reason) {
  var k = String(reason || "")
  return REASON_SENTENCES.hasOwnProperty(k) ? REASON_SENTENCES[k] : ""
}

function harnessName(id) {
  var k = String(id || "")
  return HARNESS_NAMES.hasOwnProperty(k) ? HARNESS_NAMES[k] : k
}

function cliName(id) {
  var k = String(id || "")
  return CLI_NAMES.hasOwnProperty(k) ? CLI_NAMES[k] : k
}

function withReason(prefix, reason) {
  var s = reasonSentence(reason)
  return s ? prefix + " " + s : prefix
}

// The one-line caption History shows under a job. Only fixed sentences and numbers,
// never output from the agent. The state word leads and the fact follows at once, so
// the part that matters survives when the line is cut short. The agent is not named:
// the row's mark and rail already say which one it was.
function outcomeSentence(job, nowMs) {
  var st = job && job.state ? job.state : {}
  var status = String(st.status || "")
  var reason = st.reason || null
  var run = st.lastRun || null
  var fireMs = secToMs(st.fireAt)
  var runMs = run && isNum(run.startedAt) && isNum(run.endedAt) ? (run.endedAt - run.startedAt) * 1000 : null

  switch (status) {
  case "done":
    return runMs !== null ? "Done in " + formatRunLength(runMs) + "." : "Done."
  case "failed":
    if (reason && reason !== "failed") return withReason("Failed.", reason)
    if (run && isNum(run.signal) && run.signal > 0)
      return "Failed. Stopped by signal " + run.signal + (runMs !== null ? " after " + formatRunLength(runMs) : "") + "."
    if (run && isNum(run.exit))
      return "Failed. Exited with code " + run.exit + (runMs !== null ? " after " + formatRunLength(runMs) : "") + "."
    return withReason("Failed.", reason)
  case "limit":
    return withReason("Limit hit.", reason)
  case "armed":
    if (st.wait === "limit") return fireMs !== null ? "Limit hit. Re-armed for " + clockOrDay(fireMs, nowMs) + "." : "Limit hit. Re-armed for the next reset."
    if (st.wait === "reset") return fireMs !== null ? "Waits for the reset, " + clockOrDay(fireMs, nowMs) + "." : "Waits for the reset."
    if (st.wait === "transient") return fireMs !== null ? "Temporary error. Retries at " + clockOrDay(fireMs, nowMs) + "." : "Temporary error. It retries."
    if (st.wait === "busy") return fireMs !== null ? "Session in use. Next try at " + clockOrDay(fireMs, nowMs) + "." : "Session in use."
    if (st.wait === "deferred") return withReason(fireMs !== null ? "Deferred to " + clockOrDay(fireMs, nowMs) + "." : "Deferred.", reason)
    return fireMs !== null ? "Armed. Fires " + clockOrDay(fireMs, nowMs) + " (" + formatCountdown(fireMs, nowMs) + ")." : "Armed."
  case "running":
    return run && isNum(run.startedAt) ? "Running for " + formatElapsed(Number(nowMs) - run.startedAt * 1000) + "." : "Running."
  case "missed":
    return fireMs !== null ? "Missed. It was due at " + clockOrDay(fireMs, nowMs) + "." : "Missed."
  case "skipped": return withReason("Skipped.", reason)
  case "gave_up": return withReason("Gave up.", reason)
  case "interrupted": return "Interrupted. It stopped before it finished."
  case "paused": return withReason("Paused.", reason)
  case "busy": return withReason("Session busy.", reason || "session_busy")
  case "needs_confirm": return "Check the job. It changed since it was armed."
  case "disarmed": return "Disarmed."
  case "draft": return "Draft."
  }
  return ""
}

// ---------------------------------------------------------------- lists

function isQueue(job) { return !!job && job.queue === true }

function statusOf(job) { return job && job.state ? String(job.state.status || "") : "" }

function fireAtOf(job) {
  var v = job && job.state ? job.state.fireAt : null
  return isNum(v) ? v : null
}

function numOr(v, fallback) { return isNum(v) ? v : fallback }

function idCompare(a, b) {
  var x = a && a.id ? String(a.id) : "", y = b && b.id ? String(b.id) : ""
  return x < y ? -1 : x > y ? 1 : 0
}

function sortRank(job) {
  var st = statusOf(job)
  if (st === "running") return 0
  if (st === "draft") return 3
  return fireAtOf(job) !== null ? 1 : 2
}

// Running first, then by fire time, then undated jobs, then drafts newest first.
function jobSort(a, b) {
  var ra = sortRank(a), rb = sortRank(b)
  if (ra !== rb) return ra - rb
  if (ra === 0) {
    var sa = a.state.lastRun ? numOr(a.state.lastRun.startedAt, 0) : 0
    var sb = b.state.lastRun ? numOr(b.state.lastRun.startedAt, 0) : 0
    return (sa - sb) || idCompare(a, b)
  }
  if (ra === 1) {
    return (fireAtOf(a) - fireAtOf(b)) || (numOr(a.createdAt, 0) - numOr(b.createdAt, 0)) || idCompare(a, b)
  }
  return (numOr(b.updatedAt, 0) - numOr(a.updatedAt, 0)) || idCompare(a, b)
}

// History time of a job: when its last run ended, else when it last changed.
function endedAtMs(job) {
  var run = job && job.state ? job.state.lastRun : null
  if (run && isNum(run.endedAt)) return run.endedAt * 1000
  return isNum(job && job.updatedAt) ? job.updatedAt * 1000 : 0
}

function queueTimeMs(job) {
  var f = fireAtOf(job)
  if (f !== null) return f * 1000
  return isNum(job && job.updatedAt) ? job.updatedAt * 1000 : 0
}

// [{key, word, date, jobs}]: "Needs attention" first, then "Now" (running), then one
// group per local day (ascending by fire time for the Queue, newest first for
// History), then "Drafts". Input is never modified.
function groupByDay(jobs, nowMs, field) {
  var list = Array.isArray(jobs) ? jobs : []
  var history = field === "endedAt"
  var attention = [], running = [], drafts = [], days = {}, keys = []
  for (var i = 0; i < list.length; i++) {
    var job = list[i]
    if (!job) continue
    var st = statusOf(job)
    if (ATTENTION_STATUSES.indexOf(st) >= 0) { attention.push(job); continue }
    if (st === "running") { running.push(job); continue }
    if (st === "draft") { drafts.push(job); continue }
    var ms = history ? endedAtMs(job) : queueTimeMs(job)
    var key = dayKey(ms)
    if (!days.hasOwnProperty(key)) {
      var label = dayLabel(ms, nowMs)
      days[key] = { key: key, word: label.word, date: label.date, jobs: [], midnight: localMidnight(ms) }
      keys.push(key)
    }
    days[key].jobs.push(job)
  }

  function byHistory(a, b) { return (endedAtMs(b) - endedAtMs(a)) || idCompare(a, b) }
  var order = history ? byHistory : jobSort

  var out = []
  if (attention.length) out.push({ key: "attention", word: "Needs attention", date: "", jobs: attention.sort(order) })
  if (running.length) out.push({ key: "now", word: "Now", date: "", jobs: running.sort(order) })
  keys.sort(function (a, b) { return history ? days[b].midnight - days[a].midnight : days[a].midnight - days[b].midnight })
  for (var k = 0; k < keys.length; k++) {
    var g = days[keys[k]]
    out.push({ key: g.key, word: g.word, date: g.date, jobs: g.jobs.sort(order) })
  }
  if (drafts.length) out.push({ key: "drafts", word: "Drafts", date: "", jobs: drafts.sort(order) })
  return out
}

// Every word of the query has to appear somewhere in the job's visible text.
function matchesFilter(job, query) {
  var q = String(query || "").toLowerCase().trim()
  if (q === "") return true
  if (!job) return false
  var t = job.target || {}
  var spec = statusSpec(statusOf(job), job.state ? job.state.wait : "")
  var hay = [job.label, t.title, t.cwd, harnessName(job.harness), spec.word]
    .map(function (v) { return v === undefined || v === null ? "" : String(v) })
    .join("\n").toLowerCase()
  var words = q.split(/\s+/)
  for (var i = 0; i < words.length; i++) if (words[i] && hay.indexOf(words[i]) < 0) return false
  return true
}

// ---------------------------------------------------------------- text

function shortPath(path, home) {
  var p = String(path || "")
  var h = String(home || "").replace(/\/+$/, "")
  if (h === "" || p === "") return p
  if (p === h) return "~"
  if (p.indexOf(h + "/") === 0) return "~" + p.substr(h.length)
  return p
}

// "3f2a…c19": the start and the end of an id are the parts a reader compares.
function elideMiddle(text, max) {
  var s = String(text === undefined || text === null ? "" : text)
  var n = Math.floor(Number(max))
  if (!isFinite(n) || n < 1 || s.length <= n) return s
  if (n < 3) return s.substr(0, n)
  var keep = n - 1
  var head = Math.ceil(keep / 2), tail = Math.floor(keep / 2)
  return s.substr(0, head) + "…" + s.substr(s.length - tail)
}

// UTF-8 bytes of a JS (UTF-16) string; the helper's prompt cap is in bytes.
function utf8Bytes(text) {
  var s = String(text === undefined || text === null ? "" : text)
  var n = 0
  for (var i = 0; i < s.length; i++) {
    var c = s.charCodeAt(i)
    if (c < 0x80) n += 1
    else if (c < 0x800) n += 2
    else if (c >= 0xD800 && c <= 0xDBFF && i + 1 < s.length) {
      var d = s.charCodeAt(i + 1)
      if (d >= 0xDC00 && d <= 0xDFFF) { n += 4; i++ }
      else n += 3
    } else n += 3
  }
  return n
}

// First non-empty line, control characters stripped, at most 40 characters.
function defaultLabel(prompt) {
  var lines = String(prompt === undefined || prompt === null ? "" : prompt).split(/\r\n|\r|\n/)
  for (var i = 0; i < lines.length; i++) {
    var line = lines[i].replace(/[\u0000-\u001F\u007F-\u009F]/g, "").trim()
    if (line !== "") return Array.from(line).slice(0, LABEL_MAX).join("").trim()
  }
  return ""
}

// ---------------------------------------------------------------- drafts

// A fresh Compose draft. An agent the helper reports as unusable is not offered
// as the default; before the agents answer lands every agent counts as usable.
function draftFromSettings(settings, agents) {
  var s = settings || {}
  var ids = Edition.HARNESS_IDS
  var a = agents || {}
  var harness = ids.indexOf(s.defaultHarness) >= 0 ? s.defaultHarness : ids[0]
  if (a[harness] && a[harness].enabled === false) {
    for (var i = 0; i < ids.length; i++) {
      if (a[ids[i]] && a[ids[i]].enabled === true) { harness = ids[i]; break }
    }
  }
  var level = Edition.LEVEL_IDS.indexOf(s.defaultLevel) >= 0 ? s.defaultLevel : Edition.DEFAULT_LEVEL
  return {
    harness: harness,
    target: { mode: "new", sessionId: null, cwd: null, allowNonGit: false, sessionPath: null },
    level: level,
    limits: {},
    model: null,
    allowPaid: s.defaultAllowPaid === true,
    provider: null,
    trigger: { kind: "now" },
    prompt: ""
  }
}

// A draft from a stored job. "edit" keeps the id (the save becomes job-update);
// "duplicate" and "rearm" drop it (the save becomes job-create).
function draftFromJob(job, prompt, mode) {
  var j = job || {}
  var t = j.target || {}
  var tr = j.trigger || {}
  var lim = j.limits || {}
  var limits = {}
  if (isNum(lim.maxTurns)) limits.maxTurns = lim.maxTurns
  if (isNum(lim.budgetUsd)) limits.budgetUsd = lim.budgetUsd
  if (isNum(lim.runtimeSec)) limits.runtimeSec = lim.runtimeSec
  var trigger = {
    kind: typeof tr.kind === "string" && tr.kind !== "" ? tr.kind : "now",
    fireAt: isNum(tr.fireAt) ? tr.fireAt : null,
    delaySec: isNum(tr.delaySec) ? tr.delaySec : null
  }
  if (isNum(tr.marginSec)) trigger.marginSec = tr.marginSec
  if (tr.weeklyPolicy === "defer" || tr.weeklyPolicy === "skip") trigger.weeklyPolicy = tr.weeklyPolicy
  var harness = typeof j.harness === "string" ? j.harness : Edition.HARNESS_IDS[0]
  // A reset this agent no longer offers (OpenCode on Claude's window) would be refused.
  if (RESET_NAMES.hasOwnProperty(trigger.kind)) {
    var kinds = RESET_KINDS_FOR.hasOwnProperty(harness) ? RESET_KINDS_FOR[harness] : []
    if (kinds.indexOf(trigger.kind) < 0) trigger = { kind: "now" }
  }
  var draft = {
    harness: harness,
    target: {
      mode: t.mode === "resume" || t.mode === "fork" || t.mode === "new" ? t.mode : "new",
      sessionId: typeof t.sessionId === "string" ? t.sessionId : null,
      cwd: typeof t.cwd === "string" ? t.cwd : null,
      allowNonGit: t.allowNonGit === true,
      sessionPath: typeof t.sessionPath === "string" && t.sessionPath !== "" ? t.sessionPath : null
    },
    level: Edition.LEVEL_IDS.indexOf(j.level) >= 0 ? j.level : Edition.DEFAULT_LEVEL,
    limits: limits,
    model: typeof j.model === "string" && j.model !== "" ? j.model : null,
    allowPaid: j.allowPaid === true,
    provider: typeof j.provider === "string" && j.provider !== "" ? j.provider : null,
    trigger: trigger,
    prompt: prompt === null || prompt === undefined ? "" : String(prompt)
  }
  if (typeof j.label === "string" && j.label !== "") draft.label = j.label
  if (mode === "edit" && typeof j.id === "string" && ID_RE.test(j.id)) draft.id = j.id
  return draft
}

// ---------------------------------------------------------------- agents

// The edition's agent order, with every agent this file names appended when the
// edition list does not carry it yet, so each lookup has a place for all of them.
function harnessOrder() {
  var out = Array.isArray(Edition.HARNESS_IDS) ? Edition.HARNESS_IDS.slice() : []
  for (var i = 0; i < HARNESS_ORDER.length; i++) if (out.indexOf(HARNESS_ORDER[i]) < 0) out.push(HARNESS_ORDER[i])
  return out
}

// ---------------------------------------------------------------- bar state

function plural(n, one, many) { return n === 1 ? one : many }

function countOf(v) {
  var n = Math.floor(Number(v))
  return isFinite(n) && n > 0 ? n : 0
}

// What the bar has room for: "45s", "1m 32s", "42m", "3h10", "22h42", "2d03h".
function barCountdown(sec) {
  var s = Math.max(0, Math.round(Number(sec) || 0))
  if (s < 60) return s + "s"
  if (s < 120) return "1m " + pad2(s - 60) + "s"
  if (s < 3600) return Math.floor(s / 60) + "m"
  if (s < 86400) return Math.floor(s / 3600) + "h" + pad2(Math.floor((s % 3600) / 60))
  return Math.floor(s / 86400) + "d" + pad2(Math.floor((s % 86400) / 3600)) + "h"
}

// "12s", "3m", "1h05"
function barElapsed(ms) {
  var s = Math.max(0, roundSec(ms))
  if (s < 60) return s + "s"
  if (s < 3600) return Math.floor(s / 60) + "m"
  return Math.floor(s / 3600) + "h" + pad2(Math.floor((s % 3600) / 60))
}

function barResult(state, glyph, text, tone, sentence) {
  return { state: state, glyph: glyph, text: text, tone: tone, sentence: sentence, pulse: state === "running" }
}

// The bar's one state (FEEDBACK-UI 1), first match wins: attention, running, near,
// soon, later, success, drafts, idle. Every state has a glyph and, except idle, a
// word, so the colour is never the only signal. Red (tone "bad") means something
// needs the user; green means about to fire or finished well.
//
// i = {ready, setupProblem, problems, failures, authProblems, running, runningSinceMs,
//      nextFireAtMs, nowMs, doneSinceSeen, drafts}; `failures` (how many of the
//      problems are failed or gave-up runs) picks "1 failed" over "1 needs you".
function barState(i) {
  var x = i && typeof i === "object" ? i : {}
  var now = Number(x.nowMs)
  var setup = typeof x.setupProblem === "string" ? x.setupProblem : ""
  if (setup !== "") return barResult("attention", GLYPH.alert, "error", "bad", setup)
  if (x.ready !== true) return barResult("idle", GLYPH.calendar, "", "soft", "Starting.")

  var problems = countOf(x.problems)
  var failures = Math.min(problems, countOf(x.failures))
  var auth = countOf(x.authProblems)
  var authLine = auth === 1 ? "An armed job's agent is not signed in." : auth + " armed jobs' agents are not signed in."
  if (problems > 0) {
    var allFailed = failures === problems
    var text = allFailed ? problems + " failed" : problems + plural(problems, " needs you", " need you")
    var sentence = allFailed
      ? plural(problems, "1 job failed", problems + " jobs failed") + " since the panel was last opened."
      : plural(problems, "1 job needs you", problems + " jobs need you") + " since the panel was last opened."
    return barResult("attention", GLYPH.alert, text, "bad", auth > 0 ? sentence + " " + authLine : sentence)
  }
  if (auth > 0) return barResult("attention", GLYPH.alert, "auth", "bad", authLine)

  var running = countOf(x.running)
  if (running > 0) {
    var since = Number(x.runningSinceMs)
    var known = isFinite(since) && since > 0 && isFinite(now)
    var elapsed = known ? Math.max(0, now - since) : 0
    return barResult("running", GLYPH.play, "running " + barElapsed(elapsed), "accent",
      plural(running, "1 job is running", running + " jobs are running")
        + (known ? (running === 1 ? ", for " : ", the longest for ") + formatElapsed(elapsed) : "") + ".")
  }

  var next = x.nextFireAtMs === null || x.nextFireAtMs === undefined ? NaN : Number(x.nextFireAtMs)
  if (isFinite(next) && isFinite(now)) {
    var left = roundSec(next - now)
    var line = left <= 0 ? "The next job is due now."
      : "The next job fires " + (dayDiff(next, now) === 0 ? "at " : "") + clockOrDay(next, now)
        + ", in " + formatDuration(next - now) + "."
    var figure = left <= 0 ? "due" : barCountdown(left)
    if (left < NEAR_SEC) return barResult("near", GLYPH.clock, figure, "ok", line)
    if (left < SOON_SEC) return barResult("soon", GLYPH.clock, figure, "warn", line)
    return barResult("later", GLYPH.calendar, figure, "readable", line)
  }

  var done = countOf(x.doneSinceSeen)
  if (done > 0)
    return barResult("success", GLYPH.check, done + " done", "ok",
      plural(done, "1 job finished", done + " jobs finished") + " since the panel was last opened. Nothing is armed.")
  var drafts = countOf(x.drafts)
  if (drafts > 0)
    return barResult("drafts", GLYPH.pencil, drafts + " drafted", "soft", plural(drafts, "1 draft", drafts + " drafts") + ", nothing armed.")
  return barResult("idle", GLYPH.calendar, "", "soft", "Nothing armed.")
}

// ---------------------------------------------------------------- limits

var LIMIT_KINDS = ["session", "weekly", "monthly", "daily", "billing_total", "billing_pool",
                   "model_session", "model_weekly", "model_monthly", "other"]
var HEADLINE_KINDS = ["session", "weekly", "monthly", "daily", "model_session", "model_weekly", "model_monthly"]
var METER_WARN = 0.80
var METER_BAD = 0.95

// v1 run records name limits five_hour | seven_day | usage | unknown.
function legacyLimitKind(kind) {
  var k = String(kind || "")
  if (k === "five_hour") return "session"
  if (k === "seven_day") return "weekly"
  return LIMIT_KINDS.indexOf(k) >= 0 ? k : "other"
}

function providerById(providers, id) {
  var list = Array.isArray(providers) ? providers : []
  for (var i = 0; i < list.length; i++) if (list[i] && list[i].id === id) return list[i]
  return null
}

// The agent whose mark a source wears: the first agent it binds to, else the agent
// of the same name (a collector record such as "opencode"), else none.
function providerHarness(provider) {
  var p = provider || {}
  if (Array.isArray(p.harnesses) && p.harnesses.length > 0 && typeof p.harnesses[0] === "string") return p.harnesses[0]
  var id = String(p.id || "")
  return HARNESS_NAMES.hasOwnProperty(id) ? id : ""
}

function windowByKey(provider, key) {
  var ws = provider && Array.isArray(provider.windows) ? provider.windows : []
  if (typeof key !== "string" || key === "") return null
  for (var i = 0; i < ws.length; i++) if (ws[i] && ws[i].key === key) return ws[i]
  return null
}

// "42%", "100%+" once a window is over its allowance, "-" with no figure.
function percentText(percent, over) {
  if (over === true || (isNum(percent) && percent > 1)) return "100%+"
  if (!isNum(percent)) return "-"
  return Math.round(Math.max(0, Math.min(1, percent)) * 100) + "%"
}

// "bad" at 95 % or over, "warn" at 80 %, else the source's own ink.
function meterTone(percent, over) {
  if (over === true || (isNum(percent) && percent >= METER_BAD)) return "bad"
  if (isNum(percent) && percent >= METER_WARN) return "warn"
  return "harness"
}

// One header chip from a Usage v2 provider: its headline window, or a computed reset.
// -> {id, name, harness, shortLabel, percent, over, resetsAtMs, computed, stale, ageSec,
//     text, resetText, tone}
function chipFor(provider, nowMs) {
  var p = provider || {}
  var now = Number(nowMs)
  var name = typeof p.name === "string" && p.name !== "" ? p.name : String(p.id || "")
  var w = windowByKey(p, p.headlineKey)
  var computed = p.source === "computed" || (!!w && w.source === "computed")
  var percent = w && isNum(w.percent) ? w.percent : null
  var over = !!w && (w.over === true || (percent !== null && percent > 1))
  var resetsAtMs = w && isNum(w.resetsAt) ? w.resetsAt * 1000 : null
  var shortLabel = w && typeof w.shortLabel === "string" && w.shortLabel !== "" ? w.shortLabel : name
  var text = !w ? "No limits reported"
    : (computed || percent === null) ? (resetsAtMs !== null ? "resets " + formatClock(resetsAtMs) : "no reset time")
    : percentText(percent, over)
  var resetText = resetsAtMs !== null && isFinite(now) && resetsAtMs > now ? "in " + formatDuration(resetsAtMs - now) : ""
  return {
    id: String(p.id || ""), name: name, harness: providerHarness(p), shortLabel: shortLabel,
    percent: percent, over: over, resetsAtMs: resetsAtMs, computed: computed, stale: p.stale === true,
    ageSec: isNum(p.ageSec) ? p.ageSec : null, text: text, resetText: resetText,
    tone: computed || percent === null ? "harness" : meterTone(percent, over)
  }
}

// Sources in the edition's agent order (by the agent each one binds to), then sources
// bound to no agent, each group in the order the helper listed them.
function editionSorted(providers) {
  var order = harnessOrder()
  var list = []
  for (var i = 0; i < providers.length; i++) {
    var h = providerHarness(providers[i])
    var rank = order.indexOf(h)
    list.push({ p: providers[i], rank: rank < 0 ? order.length : rank, at: i })
  }
  list.sort(function (a, b) { return (a.rank - b.rank) || (a.at - b.at) })
  return list.map(function (e) { return e.p })
}

// Which chips the header shows. Auto: readable, relevant, fresh sources with a headline,
// closest to blocking first, then edition order. Custom (an id array): the listed ids that are present, in that order.
function shownSources(providers, limitsShown) {
  var list = Array.isArray(providers) ? providers.filter(function (p) { return !!p && typeof p.id === "string" && p.id !== "" }) : []
  if (Array.isArray(limitsShown)) {
    var out = [], seen = {}
    for (var i = 0; i < limitsShown.length; i++) {
      var id = limitsShown[i]
      if (typeof id !== "string" || seen[id] === true || providerById(list, id) === null) continue
      seen[id] = true
      out.push(id)
    }
    return out
  }
  var auto = list.filter(function (p) {
    return p.readable === true && p.relevant === true && p.stale !== true
      && typeof p.headlineKey === "string" && p.headlineKey !== ""
  })
  // Closest to blocking first: the urgent tone, then amber, then the fuller meter; ties and
  // sources without a meter keep edition order. Only two chips may fit, and a quiet source
  // must never push one that is running out behind "+N".
  var ranked = editionSorted(auto).map(function (p, i) {
    var w = windowByKey(p, p.headlineKey)
    var computed = p.source === "computed" || (!!w && w.source === "computed")
    var percent = w && !computed && isNum(w.percent) ? w.percent : null
    var tone = percent === null ? "harness" : meterTone(percent, !!w && (w.over === true || percent > 1))
    return { id: p.id, rank: tone === "bad" ? 0 : (tone === "warn" ? 1 : 2), percent: percent === null ? -1 : percent, at: i }
  })
  ranked.sort(function (a, b) { return (a.rank - b.rank) || (b.percent - a.percent) || (a.at - b.at) })
  return ranked.map(function (e) { return e.id })
}

// The sources Settings offers for a custom header: every readable one with a headline.
function limitChoices(providers) {
  var list = Array.isArray(providers) ? providers.filter(function (p) {
    return !!p && typeof p.id === "string" && p.id !== "" && p.readable === true
      && typeof p.headlineKey === "string" && p.headlineKey !== ""
  }) : []
  return editionSorted(list).map(function (p) {
    return { id: p.id, name: typeof p.name === "string" && p.name !== "" ? p.name : p.id, harness: providerHarness(p) }
  })
}

// ---------------------------------------------------------------- models

// A job's model as the models verb names it ("Big Pickle"), else the stored id, else "".
// Pi rows carry provider and modelId; the job stores them apart.
function modelLabel(job, models) {
  var j = job || {}
  var m = typeof j.model === "string" && j.model !== "" ? j.model : null
  if (m === null) return ""
  var res = models && typeof models === "object" ? models[String(j.harness || "")] : null
  var rows = res && Array.isArray(res.models) ? res.models : []
  var provider = typeof j.provider === "string" ? j.provider : ""
  for (var i = 0; i < rows.length; i++) {
    var r = rows[i]
    if (!r) continue
    var hit = provider !== ""
      ? (r.provider === provider && r.modelId === m) || r.id === provider + "/" + m
      : r.id === m
    if (hit && typeof r.label === "string" && r.label !== "") return r.label
  }
  return m
}
