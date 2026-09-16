.pragma library
.import "Model.js" as Model

// Pure layout for the two timelines: the Queue's ribbon of the next day and History's
// day timeline. Nothing here touches QML, the clock or the disk. Every function takes
// the times it needs, so the same inputs always lay out the same way and the file runs
// in a bare engine under test.
//
// Times: fields of helper JSON are epoch seconds; everything named ...Ms is epoch
// milliseconds.

var HOUR_MS = 3600000

// Label priorities (FEEDBACK-UI 2): now > reset > later > hour ticks.
var PRIORITY = { tick: 0, later: 1, reset: 2, now: 3 }

// Window sources the helper names (CONTRACT-V2 3.3 limitSource), for a lane whose
// answer carries no name or agent of its own.
var SOURCE_NAMES = {
  claude: "Claude", codex: "Codex", cursor: "Cursor", "opencode-go": "OpenCode Go",
  "zen-free": "OpenCode Zen free", gemini: "Gemini", "gemini-daily": "Gemini CLI daily"
}
var SOURCE_HARNESS = {
  claude: "claude", codex: "codex", cursor: "cursor", "opencode-go": "opencode",
  "zen-free": "opencode", gemini: "gemini", "gemini-daily": "gemini"
}
var NO_SOURCE_NAME = "No limit data"
// limits_history.RESET_SAME_S: two resets of one source this close are the same reset.
var SAME_RESET_MS = 60000

function isNum(v) { return typeof v === "number" && isFinite(v) }

function harnessRank(id) {
  var order = Model.harnessOrder()
  var i = order.indexOf(String(id || ""))
  return i < 0 ? order.length : i
}

// ---------------------------------------------------------------- ticks

// Local hours divisible by `stepHours` from `startMs` up to, not including, `endMs`.
// Hours are stepped on the local calendar, so a DST day still reads 00, 03, 06.
function ticks(startMs, endMs, stepHours) {
  var out = []
  var start = Number(startMs), end = Number(endMs)
  var step = Math.max(1, Math.round(Number(stepHours) || 3))
  if (!isFinite(start) || !isFinite(end) || end <= start) return out
  var d = new Date(start)
  if (d.getMinutes() !== 0 || d.getSeconds() !== 0 || d.getMilliseconds() !== 0) {
    d.setMinutes(0, 0, 0)
    d.setHours(d.getHours() + 1)
  }
  var guard = 0
  while (d.getHours() % step !== 0 && guard++ < 24) d.setHours(d.getHours() + 1)
  for (var i = 0; i < 64; i++) {
    var t = d.getTime()
    if (t >= end) break
    out.push({ ms: t, label: Model.pad2(d.getHours()) })
    d.setHours(d.getHours() + step)
  }
  return out
}

// ---------------------------------------------------------------- labels

// labels: [{id, x, width, priority, row, altX?, anchorX?}], x the preferred left edge, altX a
// second place to try (a reset label flips to the left of its marker), anchorX the marker.
// -> the same labels in the same order as [{id, x, row, visible, collapsed: 0}], plus
//    {id: "count", x, row, visible, collapsed: n, anchorX} when n reset labels found no room.
//
// Placement order: "later" first (it owns the right edge), then now, then resets by
// position, then the count of the resets that did not fit, then hour ticks. A label
// is drawn only where no label already placed on its row is closer than `gap`, so no
// two visible labels on one row ever touch.
function layoutLabels(labels, width, countWidth, gap) {
  var W = Math.max(0, Number(width) || 0)
  var g = isNum(gap) ? Math.max(0, gap) : 4
  var cw = isNum(countWidth) ? Math.max(0, countWidth) : 0
  var list = Array.isArray(labels) ? labels : []
  var placed = []
  var out = []

  function widthOf(l) { return Math.max(0, Number(l && l.width) || 0) }
  function clampX(x, w) {
    var n = Number(x)
    if (!isFinite(n)) n = 0
    return Math.max(0, Math.min(Math.max(0, W - w), n))
  }
  function fits(x, w, row) {
    if (w > W + 0.5) return false
    for (var i = 0; i < placed.length; i++) {
      var p = placed[i]
      if (p.row !== row) continue
      if (x < p.x + p.w + g && p.x < x + w + g) return false
    }
    return true
  }
  function phase(priority) {
    var p = Number(priority)
    if (p === PRIORITY.later) return 0
    if (p === PRIORITY.now) return 1
    if (p === PRIORITY.reset) return 2
    return 4
  }

  for (var i = 0; i < list.length; i++) {
    var l = list[i] || {}
    out.push({ id: String(l.id === undefined || l.id === null ? i : l.id), x: clampX(l.x, widthOf(l)),
               row: l.row === 1 ? 1 : 0, visible: false, collapsed: 0 })
  }

  var order = []
  for (var k = 0; k < list.length; k++) order.push(k)
  order.sort(function (a, b) {
    var pa = phase(list[a] && list[a].priority), pb = phase(list[b] && list[b].priority)
    if (pa !== pb) return pa - pb
    return ((Number(list[a] && list[a].x) || 0) - (Number(list[b] && list[b].x) || 0)) || a - b
  })

  var crowd = []
  var counted = false

  function placeCount() {
    counted = true
    if (crowd.length === 0) return
    var row = out[crowd[0]].row
    var anchor = Number(list[crowd[0]].x) || 0
    // anchorX: where the first collapsed reset's marker is, so a chip that had to move away
    // from it can draw a leader back (the caller gives it; the label's own x otherwise).
    var anchorX = isNum(list[crowd[0]].anchorX) ? Number(list[crowd[0]].anchorX) : anchor
    var entry = { id: "count", x: clampX(anchor, cw), row: row, visible: false, collapsed: crowd.length, anchorX: anchorX }
    var step = Math.max(4, cw / 2)
    for (var s = 0; s <= 48 && !entry.visible; s++) {
      var offsets = s === 0 ? [0] : [s * step, -s * step]
      for (var q = 0; q < offsets.length; q++) {
        var x = clampX(anchor + offsets[q], cw)
        if (fits(x, cw, row)) {
          entry.x = x
          entry.visible = true
          placed.push({ x: x, w: cw, row: row })
          break
        }
      }
    }
    out.push(entry)
  }

  for (var o = 0; o < order.length; o++) {
    var idx = order[o]
    var lab = list[idx] || {}
    if (phase(lab.priority) === 4 && !counted) placeCount()
    var w = widthOf(lab)
    var row = out[idx].row
    var candidates = [clampX(lab.x, w)]
    if (isNum(lab.altX)) candidates.push(clampX(lab.altX, w))
    var ok = false
    for (var c = 0; c < candidates.length && !ok; c++) {
      if (fits(candidates[c], w, row)) {
        out[idx].x = candidates[c]
        out[idx].visible = true
        placed.push({ x: candidates[c], w: w, row: row })
        ok = true
      }
    }
    if (!ok && Number(lab.priority) === PRIORITY.reset) crowd.push(idx)
  }
  if (!counted) placeCount()
  return out
}

// True when two {x, y, w, h} boxes overlap by more than a hair.
function intersects(a, b) {
  if (!a || !b) return false
  return a.x < b.x + b.w - 0.5 && b.x < a.x + a.w - 0.5 && a.y < b.y + b.h - 0.5 && b.y < a.y + a.h - 0.5
}

// ---------------------------------------------------------------- ribbon

// Reset markers for the Queue ribbon: every fixed (not sliding) window reset from a
// readable source that can bind to an agent, inside [startMs, endMs]. Cursor draws
// only its billing cycle, not one marker per pool.
// -> [{source, harness, kind, shortLabel, atMs, stale, key}] by time.
function ribbonMarkers(providers, startMs, endMs) {
  var out = []
  var start = Number(startMs), end = Number(endMs)
  var list = Array.isArray(providers) ? providers : []
  var seen = {}
  for (var i = 0; i < list.length; i++) {
    var p = list[i]
    if (!p || typeof p.id !== "string" || p.readable !== true || p.relevant === false) continue
    var windows = Array.isArray(p.windows) ? p.windows : []
    var harness = Model.providerHarness(p)
    for (var j = 0; j < windows.length; j++) {
      var w = windows[j]
      if (!w || w.sliding === true || w.bindable !== true || !isNum(w.resetsAt)) continue
      if (p.id === "cursor" && w.kind !== "billing_total") continue
      var at = w.resetsAt * 1000
      if (!(at >= start && at <= end)) continue
      var label = typeof w.shortLabel === "string" ? w.shortLabel : ""
      var key = p.id + "|" + at + "|" + label
      if (seen[key] === true) continue
      seen[key] = true
      out.push({ source: p.id, harness: harness, kind: String(w.kind || ""), shortLabel: label, atMs: at,
                 stale: p.stale === true, key: typeof w.key === "string" && w.key !== "" ? w.key : key })
    }
  }
  out.sort(function (a, b) { return (a.atMs - b.atMs) || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0) })
  return out
}

// ---------------------------------------------------------------- day lanes

// One lane per window source that has a reset marker or a run on the day, in the
// edition's agent order, then a final "No limit data" lane for runs that drew from
// no known source (R7-SYNTHESIS 5.6). Armed jobs due that day are capsules with the
// outcome "armed"; a run still going ends at now.
// -> [{source, name, harness, markers: [{at, atMs, kind, shortLabel, origin, hollow}],
//      capsules: [{jobId, runId, harness, label, startMs, endMs, outcome}]}]
function dayLanes(timeline, dayStartMs, dayEndMs, nowMs) {
  var t = timeline && typeof timeline === "object" ? timeline : {}
  var start = Number(dayStartMs), end = Number(dayEndMs), now = Number(nowMs)
  if (!isFinite(start) || !isFinite(end) || end <= start) return []
  var lanes = {}
  var order = []
  var none = null

  function laneFor(source, name, harness) {
    if (source === null) {
      if (none === null) none = { source: null, name: NO_SOURCE_NAME, harness: "", markers: [], capsules: [], first: 1e9 }
      return none
    }
    if (!lanes.hasOwnProperty(source)) {
      lanes[source] = { source: source, name: "", harness: "", markers: [], capsules: [], first: order.length }
      order.push(source)
    }
    var lane = lanes[source]
    if (lane.name === "" && typeof name === "string" && name !== "") lane.name = name
    if (lane.harness === "" && typeof harness === "string" && harness !== "") lane.harness = harness
    return lane
  }

  function sourceOf(v) {
    return typeof v === "string" && v !== "" ? v : null
  }

  var resets = Array.isArray(t.resets) ? t.resets : []
  for (var i = 0; i < resets.length; i++) {
    var r = resets[i]
    if (!r || !isNum(r.at)) continue
    var atMs = r.at * 1000
    if (atMs < start || atMs >= end) continue
    var src = sourceOf(r.source)
    if (src === null) continue
    var hs = Array.isArray(r.harnesses) && r.harnesses.length > 0 ? String(r.harnesses[0]) : ""
    var lane = laneFor(src, typeof r.name === "string" ? r.name : "", hs)
    var shortLabel = typeof r.shortLabel === "string" ? r.shortLabel : ""
    // One reset written two ways (with and without fractional seconds, or a limit hit
    // rounding its own way) lands within a minute of itself: one marker, never "+1 reset".
    var dup = false
    for (var m = 0; m < lane.markers.length; m++) {
      if (Math.abs(lane.markers[m].atMs - atMs) <= SAME_RESET_MS && lane.markers[m].shortLabel === shortLabel) { dup = true; break }
    }
    if (dup) continue
    lane.markers.push({ at: r.at, atMs: atMs, kind: String(r.kind || ""), shortLabel: shortLabel,
                        origin: String(r.origin || ""), hollow: r.origin === "observed" })
  }

  var runs = Array.isArray(t.runs) ? t.runs : []
  for (var j = 0; j < runs.length; j++) {
    var run = runs[j]
    if (!run || !isNum(run.startedAt)) continue
    var s = run.startedAt * 1000
    var running = run.status === "running" || !isNum(run.endedAt)
    var e = isNum(run.endedAt) ? run.endedAt * 1000 : (isFinite(now) ? Math.max(s, now) : s)
    if (e < start || s >= end) continue
    var rl = laneFor(sourceOf(run.limitSource), "", "")
    rl.capsules.push({ jobId: String(run.jobId || ""), runId: String(run.runId || ""), harness: String(run.harness || ""),
                       label: String(run.label || ""), startMs: Math.max(start, s), endMs: Math.min(end, Math.max(s, e)),
                       outcome: typeof run.outcome === "string" && run.outcome !== "" ? run.outcome : (running ? "running" : "") })
  }

  var armed = Array.isArray(t.armed) ? t.armed : []
  for (var k = 0; k < armed.length; k++) {
    var a = armed[k]
    if (!a || !isNum(a.fireAt)) continue
    var f = a.fireAt * 1000
    if (f < start || f >= end) continue
    var al = laneFor(sourceOf(a.limitSource), "", "")
    al.capsules.push({ jobId: String(a.jobId || ""), runId: "", harness: String(a.harness || ""), label: "",
                       startMs: f, endMs: f, outcome: "armed" })
  }

  var out = []
  for (var n = 0; n < order.length; n++) {
    var ln = lanes[order[n]]
    if (ln.name === "") ln.name = SOURCE_NAMES.hasOwnProperty(ln.source) ? SOURCE_NAMES[ln.source] : ln.source
    if (ln.harness === "") ln.harness = SOURCE_HARNESS.hasOwnProperty(ln.source) ? SOURCE_HARNESS[ln.source] : ""
    if (ln.harness === "" && ln.capsules.length > 0) ln.harness = ln.capsules[0].harness
    out.push(ln)
  }
  out.sort(function (x, y) { return (harnessRank(x.harness) - harnessRank(y.harness)) || (x.first - y.first) })
  if (none !== null && none.capsules.length > 0) out.push(none)
  for (var z = 0; z < out.length; z++) {
    var lz = out[z]
    lz.markers.sort(function (p, q) { return p.atMs - q.atMs })
    lz.capsules.sort(function (p, q) { return (p.startMs - q.startMs) || (p.jobId < q.jobId ? -1 : p.jobId > q.jobId ? 1 : 0) })
    delete lz.first
  }
  return out
}

// The local midnight after the one `dayStartMs` falls in (a DST day is 23 or 25 hours).
function nextDayMs(dayStartMs) {
  var d = new Date(Model.localMidnight(dayStartMs))
  if (isNaN(d.getTime())) return NaN
  d.setDate(d.getDate() + 1)
  return d.getTime()
}

// The local midnight `offset` days from the day `anchorMs` falls in.
function dayStartFor(anchorMs, offset) {
  var d = new Date(Model.localMidnight(anchorMs))
  if (isNaN(d.getTime())) return NaN
  d.setDate(d.getDate() + (Math.round(Number(offset)) || 0))
  return d.getTime()
}
