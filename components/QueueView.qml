pragma ComponentBehavior: Bound
import QtQuick
import qs.Commons
import "../lib/Model.js" as Model

// Queue (R6 4, 7.3, 8.2): everything armed, running, drafted or waiting for a
// decision, sorted by when it fires and grouped by day, under a timeline of the next
// 24 hours and, when a limit holds jobs back, a banner that says so.
//
// Rearranging trades fire slots rather than list positions: Ctrl+K/J (or a drag onto
// another row) swaps two jobs' times, Ctrl+Left/Right moves one by five minutes,
// Shift makes it an hour, Ctrl+Shift+J/K a day. Every move is a helper call that
// re-arms real timers, so the view is pessimistic: the row goes where it was asked to
// go wearing a "re-arming" pill, and slides back if the helper refuses. Each move can
// be undone for ten seconds.
Item {
  id: root

  required property var theme
  required property var service

  property bool active: false
  property string home: ""

  signal noticeRequested(string text, string kind, var undo)
  signal editRequested(string jobId, string mode)
  signal sheetRequested(string sheet, var args)
  signal viewRequested(string view, string jobId)
  // Compose, optionally with a time already picked (a Draft trigger, or null).
  signal composeRequested(var trigger)

  property string _query: ""
  property string _cursorId: ""
  property int _cursorIndex: 0
  property string _expandedId: ""
  property var _detail: null
  property string _detailFor: ""
  property bool _detailLoading: false
  property string _detailError: ""
  // id -> true
  property var _marks: ({})
  // {action: "run"|"disarm"|"delete"|"banner", id, ids} waiting for its second press.
  property var _guard: null
  // id -> {fireAt: epoch sec, confirmed: bool, at: ms}: where a job is being moved.
  property var _pending: ({})
  // The time nudge being gathered before it is sent: {id, fromSec, toSec}.
  property var _nudge: null
  property var _lastUndo: null
  property bool _bannerWorking: false

  // Drag lane.
  property string _dragId: ""
  property string _dropKind: ""        // "" | "job" | "day"
  property string _dropId: ""
  property string _dropDay: ""
  property double _dropDaySec: 0
  property real _ghostY: 0
  property int _autoScroll: 0

  readonly property double nowMs: root.service ? root.service.nowMs : Date.now()
  readonly property bool ready: !!root.service && root.service.ready === true
  readonly property string dayKey: Model.dayKey(root.nowMs)
  property double _dayAnchor: Date.now()
  onDayKeyChanged: root._dayAnchor = root.nowMs

  readonly property var jobsList: root.service && Array.isArray(root.service.jobs) ? root.service.jobs : []
  readonly property var providers: root.service && Array.isArray(root.service.providers) ? root.service.providers : []
  readonly property var models: root.service && root.service.models ? root.service.models : null
  onJobsListChanged: root.prune()
  onNowMsChanged: if (Object.keys(root._pending).length > 0) root.prune()

  readonly property var queueJobs: root.jobsList.filter(function (j) { return !!j && j.queue === true })
  readonly property int markCount: Object.keys(root._marks).length

  readonly property string hints: {
    if (root._dragId !== "") return "Drop on a job to swap times  ·  Drop on a day to move it there  ·  Esc cancel"
    if (root._guard) {
      var j = root.jobFor(root._guard.id)
      var label = j ? String(j.label || "") : ""
      if (root._guard.action === "disarm") return "Press Delete again to disarm \"" + label + "\"."
      if (root._guard.action === "delete") return "Press Delete again to delete \"" + label + "\"."
      if (root._guard.action === "run") return "Press Ctrl+Enter again to run now."
      if (root._guard.action === "banner") return "Click again to disarm " + root._guard.ids.length + (root._guard.ids.length === 1 ? " job." : " jobs.")
    }
    if (root.markCount > 0)
      return "Ctrl+Space mark  ·  Ctrl+S shift " + root.markCount + " marked  ·  Esc clear marks"
    if (root._expandedId !== "") {
      var open = root.jobFor(root._expandedId)
      var remove = open && open.canDisarm !== true ? "Delete delete draft" : "Delete disarm"
      return "Ctrl+E edit  ·  Ctrl+D duplicate  ·  Ctrl+Enter run now  ·  " + remove + "  ·  Esc close card"
    }
    return "Type to search  ·  Ctrl+J/K swap  ·  Ctrl+←/→ ±5m  ·  Ctrl+S shift  ·  Delete disarm  ·  Enter open  ·  Ctrl+L limits  ·  Esc close"
  }

  // ---------------------------------------------------------------- display order

  // Typing in the search rebuilds the list at once. The list transitions are for changes
  // the system makes (an arm, a fire, a swap), not for every key.
  property bool _instantRows: false

  function setQuery(q) {
    root._instantRows = true
    root._query = q
    Qt.callLater(function () { root._instantRows = false })
  }

  function jobFor(id) {
    return id !== "" && root.service && root.service.jobsById ? (root.service.jobsById[id] || null) : null
  }

  function isMovable(j) {
    return !!j && j.state && j.state.status === "armed" && typeof j.state.fireAt === "number"
  }

  function baseFire(j) {
    if (!j) return null
    var p = root._pending[j.id]
    if (p) return p.fireAt
    return typeof j.state.fireAt === "number" ? j.state.fireAt : null
  }

  function displayFire(j) {
    if (root._dragId !== "" && root._dropKind === "job") {
      if (j.id === root._dragId) return root.baseFire(root.jobFor(root._dropId))
      if (j.id === root._dropId) return root.baseFire(root.jobFor(root._dragId))
    }
    if (root._dragId !== "" && root._dropKind === "day" && j.id === root._dragId) return root._dropDaySec
    return root.baseFire(j)
  }

  // Jobs as they should be drawn: the times being moved to replace the stored ones
  // (copies, the service's objects are never touched).
  readonly property var displayJobs: {
    var q = root._query
    var out = []
    var marker = root._pending
    for (var i = 0; i < root.queueJobs.length; i++) {
      var j = root.queueJobs[i]
      if (!Model.matchesFilter(j, q)) continue
      var f = root.displayFire(j)
      if (f === j.state.fireAt && marker[j.id] === undefined) { out.push(j); continue }
      var copy = Object.assign({}, j)
      copy.state = Object.assign({}, j.state, { fireAt: f })
      out.push(copy)
    }
    return out
  }

  readonly property var rows: {
    var groups = Model.groupByDay(root.displayJobs, root._dayAnchor, "fireAt")
    var out = []
    for (var g = 0; g < groups.length; g++) {
      var grp = groups[g]
      var first = grp.jobs.length > 0 && typeof grp.jobs[0].state.fireAt === "number" ? grp.jobs[0].state.fireAt * 1000 : 0
      out.push({ uid: "h:" + grp.key, kind: "header", jobId: "", groupKey: grp.key, word: grp.word, date: grp.date,
                 count: grp.jobs.length, dayMs: first })
      for (var i = 0; i < grp.jobs.length; i++)
        out.push({ uid: "j:" + grp.jobs[i].id, kind: "job", jobId: String(grp.jobs[i].id), groupKey: grp.key,
                   word: "", date: "", count: 0, dayMs: 0 })
    }
    return out
  }

  readonly property var jobOrder: root.rows.filter(function (r) { return r.kind === "job" }).map(function (r) { return r.jobId })

  onRowsChanged: {
    root.syncModel(rowModel, root.rows)
    if (root._dragId !== "") return
    if (root.jobOrder.indexOf(root._cursorId) < 0) {
      var n = root.jobOrder.length
      root._cursorId = n > 0 ? root.jobOrder[Math.max(0, Math.min(n - 1, root._cursorIndex))] : ""
    }
    if (root._expandedId !== "" && root.jobOrder.indexOf(root._expandedId) < 0) root.collapse()
  }

  readonly property var ribbonItems: root.displayJobs.filter(function (j) {
    return (j.state.status === "armed" || j.state.status === "running") && typeof j.state.fireAt === "number"
  }).map(function (j) {
    return { id: j.id, harness: j.harness, fireAtMs: j.state.fireAt * 1000,
             running: j.state.status === "running", pending: root._pending[j.id] !== undefined }
  })

  readonly property string countLine: {
    if (!root.ready) return ""
    var armed = 0, running = 0
    for (var i = 0; i < root.queueJobs.length; i++) {
      var s = root.queueJobs[i].state.status
      if (s === "armed") armed++
      else if (s === "running") running++
    }
    return running > 0 ? armed + " armed, " + running + " running" : armed + " armed"
  }

  function syncModel(model, rows) {
    var want = {}
    for (var w = 0; w < rows.length; w++) want[rows[w].uid] = true
    for (var r = model.count - 1; r >= 0; r--) if (want[model.get(r).uid] !== true) model.remove(r, 1)
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i]
      if (i < model.count && model.get(i).uid === row.uid) { model.set(i, row); continue }
      var found = -1
      for (var j = i + 1; j < model.count; j++) if (model.get(j).uid === row.uid) { found = j; break }
      if (found >= 0) { model.move(found, i, 1); model.set(i, row) }
      else model.insert(i, row)
    }
    while (model.count > rows.length) model.remove(model.count - 1, 1)
  }

  // Drops moves the list has caught up with, marks on jobs that can no longer shift.
  function prune() {
    var now = Date.now()
    var keep = {}, changed = false
    for (var id in root._pending) {
      var p = root._pending[id]
      var j = root.jobFor(id)
      var done = p.confirmed && (!root.isMovable(j) || j.state.fireAt === p.fireAt || now - p.at > 20000)
      if (!j || done) changed = true
      else keep[id] = p
    }
    if (changed) root._pending = keep
    var marks = {}, dropped = false
    for (var m in root._marks) {
      if (root.isMovable(root.jobFor(m))) marks[m] = true
      else dropped = true
    }
    if (dropped) root._marks = marks
  }

  function withPending(changes) {
    var next = Object.assign({}, root._pending)
    for (var id in changes) {
      if (changes[id] === null) delete next[id]
      else next[id] = changes[id]
    }
    root._pending = next
  }

  // ---------------------------------------------------------------- cursor and card

  function indexOfJob(id) {
    for (var i = 0; i < rowModel.count; i++) if (rowModel.get(i).jobId === id) return i
    return -1
  }

  function setCursor(id) {
    root._cursorId = id
    root._cursorIndex = Math.max(0, root.jobOrder.indexOf(id))
    var idx = root.indexOfJob(id)
    if (idx >= 0) list.positionViewAtIndex(idx, ListView.Contain)
  }

  function moveCursor(step) {
    var order = root.jobOrder
    if (order.length === 0) return
    var at = order.indexOf(root._cursorId)
    root.setCursor(order[at < 0 ? 0 : Math.max(0, Math.min(order.length - 1, at + step))])
  }

  function activate() {
    if (root.jobOrder.indexOf(root._cursorId) < 0 && root.jobOrder.length > 0) root._cursorId = root.jobOrder[0]
  }

  // The arm moment (R6 6.1): the rail of the job just armed flashes once, when its
  // row appears or right away if it is already there.
  property string _flashId: ""
  function flashJob(id) {
    root._flashId = String(id)
    flashClear.restart()
  }

  function focusJob(id) {
    var j = root.jobFor(String(id))
    if (!j || j.queue !== true) return
    if (!Model.matchesFilter(j, root._query)) root.setQuery("")
    Qt.callLater(function () { root.setCursor(String(id)) })
  }

  readonly property var expandedStamp: {
    var j = root.jobFor(root._expandedId)
    return j ? String(j.updatedAt) + ":" + String(j.state.status) + ":" + String(j.digest) : ""
  }
  onExpandedStampChanged: if (root._expandedId !== "" && root.expandedStamp !== "") root.loadDetail(root._expandedId)

  function loadDetail(id) {
    if (!root.service) return
    root._detailFor = id
    root._detailLoading = true
    root._detailError = ""
    root.service.getJob(id, function (res) {
      if (root._detailFor !== id) return
      root._detailLoading = false
      if (res.ok === true) { root._detail = res; root._detailError = "" }
      else if (res.code !== "superseded") root._detailError = String(res.message || "")
    })
  }

  function expand(id) {
    if (id === "" || root._dragId !== "") return
    root._detail = null
    root._detailLoading = true
    root._expandedId = id
    Qt.callLater(function () { root.setCursor(id) })
  }

  function collapse() {
    root._expandedId = ""
    root._detail = null
    root._detailFor = ""
    root._detailLoading = false
    root._detailError = ""
  }

  // ---------------------------------------------------------------- notices and guards

  function notify(text, kind, undoFn) {
    if (typeof undoFn !== "function") { root.noticeRequested(text, kind, null); return }
    var used = false
    var once = function () {
      if (used) return
      used = true
      if (root._lastUndo && root._lastUndo.fn === once) { root._lastUndo = null; undoTimer.stop() }
      undoFn()
    }
    root._lastUndo = { fn: once }
    undoTimer.restart()
    root.noticeRequested(text, kind, once)
  }

  function undo() {
    if (!root._lastUndo) return false
    root._lastUndo.fn()
    return true
  }

  function fail(res) {
    if (res && res.code === "superseded") return
    root.noticeRequested(String(res && res.message ? res.message : "The helper did not answer. Try again."), "error", null)
  }

  function clearGuard() {
    root._guard = null
    guardTimer.stop()
  }

  // True when this press is the second one of the same guard.
  function pressGuard(action, id, ids) {
    if (root._guard && root._guard.action === action && root._guard.id === id) {
      root.clearGuard()
      return true
    }
    root._guard = { action: action, id: id, ids: ids || [] }
    guardTimer.restart()
    return false
  }

  function fireText(sec) {
    return Model.formatClock(sec * 1000) + " (" + Model.formatCountdown(sec * 1000, root.nowMs) + ")"
  }

  // ---------------------------------------------------------------- actions

  function editJob(id, mode) {
    var j = root.jobFor(id)
    if (!j) return
    if (mode === "edit" && j.canEdit !== true) {
      root.noticeRequested("A running job cannot be edited. Disarm it first.", "warn", null)
      return
    }
    root.editRequested(id, mode)
  }

  function runNow(id) {
    var j = root.jobFor(id)
    if (!j) return
    if (j.canRunNow !== true) {
      root.clearGuard()
      root.noticeRequested(j.state.status === "running" ? "It is already running."
        : j.promptAvailable === false ? "The prompt was deleted after the run. Write it again."
        : "This job cannot run in its current state.", "warn", null)
      return
    }
    if (!root.pressGuard("run", id)) return
    root.service.runNow(id, j.digest, function (res) {
      if (res.ok === true) root.notify("Running \"" + String(j.label || "") + "\" now.", "ok", null)
      else root.fail(res)
    })
  }

  function removeJob(id) {
    var j = root.jobFor(id)
    if (!j) return
    if (j.canDisarm === true) {
      if (!root.pressGuard("disarm", id)) return
      var digest = j.digest
      var label = String(j.label || "")
      root.service.disarm(id, function (res) {
        if (res.ok !== true) { root.fail(res); return }
        root.notify("Disarmed \"" + label + "\".", "ok", j.promptAvailable === true ? function () {
          root.service.arm(id, digest, function (back) {
            if (back.ok === true && typeof back.fireAt === "number") root.notify("Armed again. Fires " + root.fireText(back.fireAt) + ".", "ok", null)
            else if (back.ok === true) root.notify("Armed again.", "ok", null)
            else root.fail(back)
          })
        } : null)
      })
      return
    }
    if (j.canDelete === true) {
      if (!root.pressGuard("delete", id)) return
      var name = String(j.label || "")
      root.service.deleteJob(id, function (res) {
        if (res.ok === true) root.notify("Deleted \"" + name + "\".", "ok", null)
        else root.fail(res)
      })
      return
    }
    root.clearGuard()
    root.noticeRequested("A running job has to stop before it can be removed.", "warn", null)
  }

  function neighbourOf(id, dir) {
    var order = root.jobOrder
    var at = order.indexOf(id)
    for (var i = at + dir; i >= 0 && i < order.length; i += dir) {
      var j = root.jobFor(order[i])
      if (root.isMovable(j) && root._pending[j.id] === undefined) return j
    }
    return null
  }

  function swapJobs(aId, bId) {
    var a = root.jobFor(aId), b = root.jobFor(bId)
    if (!root.isMovable(a) || !root.isMovable(b) || aId === bId) return
    var fa = root.baseFire(a), fb = root.baseFire(b)
    var now = Date.now()
    var change = {}
    change[aId] = { fireAt: fb, confirmed: false, at: now }
    change[bId] = { fireAt: fa, confirmed: false, at: now }
    root.withPending(change)
    root.service.swap(aId, bId, function (res) {
      if (res.ok !== true) {
        var back = {}
        back[aId] = null
        back[bId] = null
        root.withPending(back)
        root.fail(res)
        return
      }
      var done = {}
      var list = Array.isArray(res.jobs) ? res.jobs : []
      var newA = fb, unbound = false
      for (var i = 0; i < list.length; i++) {
        var r = list[i]
        if (!r || (r.id !== aId && r.id !== bId)) continue
        done[r.id] = { fireAt: typeof r.fireAt === "number" ? r.fireAt : (r.id === aId ? fb : fa), confirmed: true, at: Date.now() }
        if (r.id === aId && typeof r.fireAt === "number") newA = r.fireAt
        if (r.unbound === true) unbound = true
      }
      if (!done[aId]) done[aId] = { fireAt: fb, confirmed: true, at: Date.now() }
      if (!done[bId]) done[bId] = { fireAt: fa, confirmed: true, at: Date.now() }
      root.withPending(done)
      var text = "Swapped with \"" + String(b.label || "") + "\". Fires " + Model.formatClock(newA * 1000) + " now."
      if (unbound) text = text + " Unbound from reset."
      root.notify(text, "ok", function () { root.swapJobs(aId, bId) })
    })
  }

  function swapCursor(dir) {
    var a = root.jobFor(root._cursorId)
    if (!root.isMovable(a)) {
      root.noticeRequested("Only armed jobs trade times.", "info", null)
      return
    }
    if (root._pending[a.id] !== undefined) return
    var b = root.neighbourOf(a.id, dir)
    if (!b) {
      root.noticeRequested(dir < 0 ? "No armed job before this one." : "No armed job after this one.", "info", null)
      return
    }
    root.swapJobs(a.id, b.id)
  }

  function rescheduleJob(id, fromSec, toSec) {
    var j = root.jobFor(id)
    if (!j || toSec === fromSec) {
      var clear = {}
      clear[id] = null
      root.withPending(clear)
      return
    }
    var change = {}
    change[id] = { fireAt: toSec, confirmed: false, at: Date.now() }
    root.withPending(change)
    root.service.reschedule(id, toSec, function (res) {
      if (res.ok !== true) {
        var back = {}
        back[id] = null
        root.withPending(back)
        root.fail(res)
        return
      }
      var at = typeof res.fireAt === "number" ? res.fireAt : toSec
      var done = {}
      done[id] = { fireAt: at, confirmed: true, at: Date.now() }
      root.withPending(done)
      var text = res.unbound === true
        ? "Unbound from reset. Fires " + Model.formatClock(at * 1000) + "."
        : "Moved \"" + String(j.label || "") + "\" to " + root.fireText(at) + "."
      root.notify(text, "ok", function () { root.rescheduleJob(id, at, fromSec) })
    })
  }

  // kind "min" (amount in minutes), "day" (amount in days).
  function nudge(kind, amount) {
    var j = root.jobFor(root._cursorId)
    if (!root.isMovable(j)) {
      root.noticeRequested("Only armed jobs can be moved.", "info", null)
      return
    }
    var id = j.id
    if (root._nudge && root._nudge.id !== id) root.flushNudge()
    var p = root._pending[id]
    if (p && !(root._nudge && root._nudge.id === id)) return   // a move for it is still with the helper
    var fromSec = root._nudge ? root._nudge.fromSec : j.state.fireAt
    var baseMs = root.baseFire(j) * 1000
    var nextMs = kind === "day" ? Model.nudgedDays(baseMs, amount, root.nowMs)
      : Model.nudged(baseMs, amount, Math.abs(amount) === 5, root.nowMs)
    var toSec = Math.round(nextMs / 1000)
    root._nudge = { id: id, fromSec: fromSec, toSec: toSec }
    var change = {}
    change[id] = { fireAt: toSec, confirmed: false, at: Date.now() }
    root.withPending(change)
    nudgeTimer.restart()
  }

  function flushNudge() {
    nudgeTimer.stop()
    var n = root._nudge
    root._nudge = null
    if (n) root.rescheduleJob(n.id, n.fromSec, n.toSec)
  }

  function toggleMark() {
    var j = root.jobFor(root._cursorId)
    if (!root.isMovable(j)) {
      root.noticeRequested("Only armed jobs can be marked for a shift.", "info", null)
      return
    }
    var next = Object.assign({}, root._marks)
    if (next[j.id] === true) delete next[j.id]
    else next[j.id] = true
    root._marks = next
  }

  function openShift() {
    var ids = []
    var order = root.jobOrder
    if (root.markCount > 0) {
      for (var i = 0; i < order.length; i++) if (root._marks[order[i]] === true) ids.push(order[i])
    } else {
      // Nothing marked: this job and every armed job after it.
      var at = Math.max(0, order.indexOf(root._cursorId))
      for (var k = at; k < order.length && ids.length < 50; k++) if (root.isMovable(root.jobFor(order[k]))) ids.push(order[k])
    }
    ids = ids.slice(0, 50)
    if (ids.length === 0) {
      root.noticeRequested("Arm a job before shifting it.", "info", null)
      return
    }
    root.flushNudge()
    root.sheetRequested("shift", { ids: ids })
  }

  function bannerDisarm(ids) {
    if (root._bannerWorking || !Array.isArray(ids) || ids.length === 0) return
    if (!root.pressGuard("banner", "banner", ids)) return
    var armed = []
    for (var i = 0; i < ids.length; i++) {
      var j = root.jobFor(ids[i])
      if (j) armed.push({ id: j.id, digest: j.digest, prompt: j.promptAvailable === true })
    }
    root._bannerWorking = true
    var left = armed.length, ok = [], firstError = null
    armed.forEach(function (entry) {
      root.service.disarm(entry.id, function (res) {
        if (res.ok === true) ok.push(entry)
        else if (!firstError) firstError = res
        if (--left > 0) return
        root._bannerWorking = false
        if (firstError) root.fail(firstError)
        if (ok.length === 0) return
        var again = ok.filter(function (e) { return e.prompt })
        root.notify("Disarmed " + ok.length + (ok.length === 1 ? " job." : " jobs."), "ok", again.length === 0 ? null : function () {
          again.forEach(function (e) {
            root.service.arm(e.id, e.digest, function (back) { if (back.ok !== true) root.fail(back) })
          })
        })
      })
    })
  }

  function act(action, id) {
    if (id === "") return
    root._cursorId = id
    if (action !== "run" && action !== "disarm" && action !== "delete") root.clearGuard()
    if (action === "edit") root.editJob(id, "edit")
    else if (action === "duplicate") root.editJob(id, "duplicate")
    else if (action === "run") root.runNow(id)
    else if (action === "disarm" || action === "delete") root.removeJob(id)
  }

  // ---------------------------------------------------------------- drag lane

  function beginDrag(id, area, x, y) {
    var j = root.jobFor(id)
    if (root._dragId !== "" || !root.isMovable(j) || root._pending[id] !== undefined) return
    root.flushNudge()
    root.collapse()
    root.clearGuard()
    root._cursorId = id
    root._dropKind = ""
    root._dropId = ""
    root._dropDay = ""
    root._dragId = id
    root.moveDrag(area, x, y)
  }

  function moveDrag(area, x, y) {
    if (root._dragId === "") return
    var g = area.mapToItem(root, x, y)
    root._ghostY = g.y - Style.space(24)
    var inList = area.mapToItem(list, x, y)
    var edge = Style.space(28)
    root._autoScroll = inList.y < edge ? -1 : (inList.y > list.height - edge ? 1 : 0)
    var p = area.mapToItem(list.contentItem, x, y)
    var idx = list.indexAt(Math.max(0, Math.min(list.width - 1, p.x)), p.y)
    if (idx < 0) return
    var row = rowModel.get(idx)
    if (row.kind === "job") {
      if (row.jobId === root._dragId) return
      var t = root.jobFor(row.jobId)
      if (root.isMovable(t) && root._pending[row.jobId] === undefined) {
        root._dropKind = "job"
        root._dropId = row.jobId
        root._dropDay = ""
      }
      return
    }
    if (row.kind === "header" && /^[0-9]{4}-/.test(row.groupKey) && row.dayMs > 0) {
      var src = root.jobFor(root._dragId)
      var fromMs = root.baseFire(src) * 1000
      var diff = Model.dayDiff(row.dayMs, fromMs)
      if (diff === 0) {
        if (root._dropKind === "day") { root._dropKind = ""; root._dropDay = "" }
        return
      }
      root._dropKind = "day"
      root._dropId = ""
      root._dropDay = row.groupKey
      root._dropDaySec = Math.round(Model.nudgedDays(fromMs, diff, root.nowMs) / 1000)
    }
  }

  function endDrag() {
    if (root._dragId === "") return
    var id = root._dragId, kind = root._dropKind, target = root._dropId, daySec = root._dropDaySec
    var src = root.jobFor(id)
    var fromSec = src ? root.baseFire(src) : null
    root._autoScroll = 0
    if (kind === "job") {
      // Pending first, then the drag state goes: the rows stay where they were dropped.
      root.swapJobs(id, target)
    } else if (kind === "day" && fromSec !== null) {
      root.rescheduleJob(id, fromSec, daySec)
    }
    root._dragId = ""
    root._dropKind = ""
    root._dropId = ""
    root._dropDay = ""
    root.setCursor(id)
  }

  function cancelDrag() {
    root._autoScroll = 0
    root._dragId = ""
    root._dropKind = ""
    root._dropId = ""
    root._dropDay = ""
  }

  // ---------------------------------------------------------------- keys

  function handleKey(event) {
    if (!root.active) return false
    var k = event.key
    var mods = event.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier | Qt.MetaModifier)
    var ctrl = mods === Qt.ControlModifier
    var ctrlShift = mods === (Qt.ControlModifier | Qt.ShiftModifier)
    var alt = mods === Qt.AltModifier
    var plain = mods === 0 || mods === Qt.ShiftModifier
    var enter = k === Qt.Key_Return || k === Qt.Key_Enter

    if (root._guard) {
      var second = (root._guard.action === "run" && ctrl && enter)
        || ((root._guard.action === "disarm" || root._guard.action === "delete") && mods === 0 && k === Qt.Key_Delete)
      if (!second) root.clearGuard()
    }

    if (k === Qt.Key_Escape && mods === 0) {
      if (root._dragId !== "") { root.cancelDrag(); return true }
      if (root._guard) { root.clearGuard(); return true }
      if (root._expandedId !== "") { root.collapse(); return true }
      if (root.markCount > 0) { root._marks = ({}); return true }
      if (root._query !== "") { root.setQuery(""); return true }
      return false
    }
    if (root._dragId !== "") return true
    if (k === Qt.Key_Tab || k === Qt.Key_Backtab) return false

    if (ctrlShift) {
      if (k === Qt.Key_J) { root.nudge("day", -1); return true }
      if (k === Qt.Key_K) { root.nudge("day", 1); return true }
      if (k === Qt.Key_Left) { root.nudge("min", -60); return true }
      if (k === Qt.Key_Right) { root.nudge("min", 60); return true }
    }
    if (ctrl) {
      if (enter) { root.runNow(root._cursorId); return true }
      if (k === Qt.Key_K) { root.swapCursor(-1); return true }
      if (k === Qt.Key_J) { root.swapCursor(1); return true }
      if (k === Qt.Key_Left) { root.nudge("min", -5); return true }
      if (k === Qt.Key_Right) { root.nudge("min", 5); return true }
      if (k === Qt.Key_Space) { root.toggleMark(); return true }
      if (k === Qt.Key_S) { root.openShift(); return true }
      if (k === Qt.Key_E) { root.editJob(root._cursorId, "edit"); return true }
      if (k === Qt.Key_D) { root.editJob(root._cursorId, "duplicate"); return true }
      if (k === Qt.Key_Z) return root.undo()
    }
    if (alt) {
      if (k === Qt.Key_Up) { root.swapCursor(-1); return true }
      if (k === Qt.Key_Down) { root.swapCursor(1); return true }
    }

    if (Util.editsFilter(event, root._query)) { root.setQuery(Util.editedFilter(event, root._query)); return true }

    if (mods === 0) {
      if (k === Qt.Key_Up) { root.moveCursor(-1); return true }
      if (k === Qt.Key_Down) { root.moveCursor(1); return true }
      if (k === Qt.Key_PageUp) { root.moveCursor(-8); return true }
      if (k === Qt.Key_PageDown) { root.moveCursor(8); return true }
      if (k === Qt.Key_Home) { root.moveCursor(-100000); return true }
      if (k === Qt.Key_End) { root.moveCursor(100000); return true }
      if (k === Qt.Key_Delete) { root.removeJob(root._cursorId); return true }
      if (enter) {
        if (root._expandedId !== "" && root._expandedId === root._cursorId) root.collapse()
        else root.expand(root._cursorId)
        return true
      }
      if (k === Qt.Key_Right) { root.expand(root._cursorId); return true }
      if (k === Qt.Key_Left) { if (root._expandedId !== "") { root.collapse(); return true } return false }
    }

    if (plain && event.text && event.text.length > 0 && event.text.charCodeAt(0) >= 32 && event.text.charCodeAt(0) !== 127) {
      if (root._query.length < 120) root.setQuery(root._query + event.text)
      return true
    }
    return false
  }

  // ---------------------------------------------------------------- layout

  implicitWidth: Style.space(1100)
  implicitHeight: Style.space(640)

  Timer {
    id: guardTimer
    interval: 3000
    repeat: false
    onTriggered: root._guard = null
  }

  Timer {
    id: undoTimer
    interval: 10000
    repeat: false
    onTriggered: root._lastUndo = null
  }

  Timer {
    id: nudgeTimer
    interval: 450
    repeat: false
    onTriggered: root.flushNudge()
  }

  Timer {
    interval: 16
    repeat: true
    running: root._dragId !== "" && root._autoScroll !== 0 && root.visible
    onTriggered: {
      var maxY = Math.max(0, list.contentHeight - list.height)
      list.contentY = Math.max(0, Math.min(maxY, list.contentY + root._autoScroll * Style.space(8)))
    }
  }

  onActiveChanged: if (!root.active) { root.cancelDrag(); root.clearGuard(); root.flushNudge() }

  ListModel { id: rowModel }

  Column {
    id: top
    x: Style.space(14)
    y: Style.space(10)
    width: Math.max(0, root.width - Style.space(14) * 2)
    spacing: Style.space(8)

    SearchField {
      width: parent.width
      theme: root.theme
      text: root._query
      active: root.active
      trailing: root.countLine
    }

    TimelineRibbon {
      width: parent.width
      visible: root.ready && root.queueJobs.length > 0
      theme: root.theme
      items: root.ribbonItems
      nowMs: root.nowMs
      providers: root.providers
      cursorId: root._cursorId
      onJobClicked: function (id) { root.focusJob(id) }
    }

    LimitBanner {
      width: parent.width
      theme: root.theme
      jobs: root.queueJobs
      providers: root.providers
      nowMs: root.nowMs
      guard: !!root._guard && root._guard.action === "banner"
      working: root._bannerWorking
      onDisarmRequested: function (ids) { root.bannerDisarm(ids) }
    }
  }

  Rectangle {
    id: rule
    anchors.top: top.bottom
    anchors.topMargin: Style.space(8)
    width: root.width
    height: Math.max(1, Style.space(1))
    color: root.theme.faint
  }

  ListView {
    id: list
    anchors.top: rule.bottom
    anchors.topMargin: Style.space(4)
    anchors.bottom: parent.bottom
    x: Style.space(6)
    width: Math.max(0, root.width - Style.space(6) * 2)
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    interactive: root._dragId === ""
    model: rowModel
    visible: root.ready && rowModel.count > 0

    add: Transition {
      enabled: !root._instantRows
      ParallelAnimation {
        NumberAnimation { property: "opacity"; from: 0; to: 1; duration: root.theme.fadeMs(180); easing.type: Easing.OutCubic }
        // Settles into place from 6 px below (R6 6.1); reduced motion drops the travel.
        // It moves a Translate, never `y`: when one sync inserts a group header and its
        // first row together (the Now group appearing with its running job), a `y`
        // animation and the displaced transition of the second insert fought, and the
        // row was left under the header.
        NumberAnimation {
          property: "enterShift"
          from: Style.space(6)
          to: 0
          duration: root.theme.moveMs(180)
          easing.type: Easing.BezierSpline
          easing.bezierCurve: [0.23, 1, 0.32, 1, 1, 1]
        }
      }
    }
    remove: Transition {
      enabled: !root._instantRows
      NumberAnimation { property: "opacity"; to: 0; duration: root.theme.fadeMs(120); easing.type: Easing.OutCubic }
    }
    move: Transition {
      enabled: !root._instantRows
      NumberAnimation { properties: "y"; duration: root.theme.moveMs(160); easing.type: Easing.OutCubic }
    }
    displaced: Transition {
      enabled: !root._instantRows
      NumberAnimation { properties: "y"; duration: root.theme.moveMs(160); easing.type: Easing.OutCubic }
    }

    delegate: Item {
      id: slot
      required property string uid
      required property string kind
      required property string jobId
      required property string groupKey
      required property string word
      required property string date
      required property int count
      required property double dayMs

      readonly property var job: slot.kind === "job" ? root.jobFor(slot.jobId) : null
      readonly property bool expanded: slot.kind === "job" && root._expandedId === slot.jobId

      // The card stays drawn while the slot is still closing, so collapsing fades the card
      // in step with the height instead of blanking it on the first frame.
      readonly property bool showCard: slot.expanded || (slot.kind === "job" && slot.height > row.implicitHeight + 1)
      property var _lastDetail: null
      readonly property var liveDetail: slot.expanded ? root._detail : null
      onLiveDetailChanged: if (slot.liveDetail !== null) slot._lastDetail = slot.liveDetail
      onShowCardChanged: if (!slot.showCard) slot._lastDetail = null
      readonly property var pending: slot.kind === "job" ? root._pending[slot.jobId] : undefined
      readonly property string modelLabel: slot.job ? Model.modelLabel(slot.job, root.models) : ""

      // The entrance offset the add transition animates (see `add` above).
      property real enterShift: 0
      transform: Translate { y: slot.enterShift }

      width: list.width
      height: slot.kind === "header" ? header.implicitHeight
        : row.implicitHeight + (slot.expanded ? card.implicitHeight + Style.space(6) : 0)
      clip: true

      // Opens a little slower than it closes; exits are quicker than entries.
      Behavior on height {
        NumberAnimation { duration: root.theme.moveMs(slot.expanded ? 160 : 120); easing.type: Easing.OutCubic }
      }

      DayHeader {
        id: header
        visible: slot.kind === "header"
        width: slot.width
        theme: root.theme
        groupKey: slot.groupKey
        word: slot.word
        date: slot.date
        count: slot.count
        dropHere: root._dropKind === "day" && root._dropDay === slot.groupKey
      }

      JobRow {
        id: row
        visible: slot.kind === "job" && !!slot.job
        width: slot.width
        theme: root.theme
        job: slot.job
        mode: "queue"
        nowMs: root.nowMs
        home: root.home
        modelLabel: slot.modelLabel
        hasCursor: root._dragId === "" && root._cursorId === slot.jobId
        marked: root._marks[slot.jobId] === true
        flash: root._flashId !== "" && root._flashId === slot.jobId
        guard: !!root._guard && root._guard.id === slot.jobId
        dropHere: root._dropKind === "job" && root._dropId === slot.jobId
        placeholder: root._dragId === slot.jobId
        pendingFireAt: slot.pending !== undefined ? slot.pending.fireAt : null
        draggable: root.isMovable(slot.job) && slot.pending === undefined
          && (root._dragId === "" || root._dragId === slot.jobId)
        onHovered: if (root._dragId === "") root._cursorId = slot.jobId
        onActivated: function (modifiers) {
          if (modifiers & Qt.ShiftModifier) { root._cursorId = slot.jobId; root.toggleMark(); return }
          root._cursorId = slot.jobId
          if (root._expandedId === slot.jobId) root.collapse()
          else root.expand(slot.jobId)
        }
        onDragBegin: function (area, x, y) { root.beginDrag(slot.jobId, area, x, y) }
        onDragMove: function (area, x, y) { root.moveDrag(area, x, y) }
        onDragEnd: root.endDrag()
        onDragCancel: root.cancelDrag()
        onWheelNudge: function (steps) {
          root._cursorId = slot.jobId
          root.nudge("min", 5 * steps)
        }
      }

      JobCard {
        id: card
        visible: slot.showCard
        opacity: slot.expanded ? 1 : 0
        anchors.top: row.bottom
        anchors.topMargin: Style.space(2)
        width: slot.width
        theme: root.theme
        job: slot.showCard ? slot.job : null
        mode: "queue"
        nowMs: root.nowMs
        home: root.home
        modelLabel: slot.modelLabel
        detail: slot.expanded ? root._detail : (slot.showCard ? slot._lastDetail : null)
        loading: slot.expanded && root._detailLoading
        errorText: slot.expanded ? root._detailError : ""
        guardAction: root._guard && root._guard.id === slot.jobId ? root._guard.action : ""
        onActionRequested: function (action) { root.act(action, slot.jobId) }

        Behavior on opacity {
          NumberAnimation { duration: slot.expanded ? root.theme.fadeMs(160) : root.theme.fadeMs(90); easing.type: Easing.OutCubic }
        }
      }
    }
  }

  // The row that follows the pointer while dragging.
  JobRow {
    visible: root._dragId !== ""
    x: list.x
    y: root._ghostY
    z: 10
    width: list.width
    theme: root.theme
    ghost: true
    job: root.jobFor(root._dragId)
    mode: "queue"
    nowMs: root.nowMs
    home: root.home
    dropPreview: root._dropKind === "job" ? "→ " + Model.formatClock(root.baseFire(root.jobFor(root._dropId)) * 1000)
      : root._dropKind === "day" ? "→ " + Model.clockOrDay(root._dropDaySec * 1000, root.nowMs) : ""
  }

  Text {
    visible: !root.ready
    x: Style.space(24)
    anchors.top: rule.bottom
    anchors.topMargin: Style.space(28)
    textFormat: Text.PlainText
    text: "Reading jobs…"
    color: root.theme.soft
    font.family: root.theme.fontFamily
    font.pixelSize: root.theme.type.body
  }

  // The fact, then the way back, in two inks.
  Row {
    visible: root.ready && root.queueJobs.length > 0 && rowModel.count === 0
    x: Style.space(24)
    width: Math.max(0, root.width - Style.space(48))
    anchors.top: rule.bottom
    anchors.topMargin: Style.space(28)
    spacing: Style.space(7)

    Text {
      width: Math.max(0, Math.min(implicitWidth, parent.width - recoverText.implicitWidth - parent.spacing))
      textFormat: Text.PlainText
      text: "No jobs match \"" + root._query + "\"."
      color: root.theme.strong
      elide: Text.ElideRight
      maximumLineCount: 1
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.body
    }

    Text {
      id: recoverText
      textFormat: Text.PlainText
      text: "Esc clears the search."
      color: root.theme.soft
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.body
    }
  }

  Timer {
    id: flashClear
    interval: 1500
    repeat: false
    onTriggered: root._flashId = ""
  }

  QueueEmpty {
    visible: root.ready && root.queueJobs.length === 0
    x: Style.space(24)
    width: Math.max(0, root.width - Style.space(48))
    anchors.top: rule.bottom
    anchors.topMargin: Style.space(28)
    theme: root.theme
    service: root.service
    onComposeRequested: function (trigger) { root.composeRequested(trigger) }
  }
}
