pragma ComponentBehavior: Bound
import QtQuick
import qs.Commons
import "../lib/Model.js" as Model
import "../lib/Timeline.js" as Timeline

// History (R6 7.4, 8.3): every job that left the queue, newest first, grouped by the
// day it ended. A filter narrows it to failures, successes or skips; Enter opens a
// card with the fixed reason sentence and the last lines the run printed. From here a
// job can be loaded back into Compose, run again, or its resume command copied.
//
// Above the list, one day at a time (FEATURE-LIMITS): each limit source's resets and
// the runs that drew from it. Ctrl+Left/Right steps a day (two weeks back, a week
// ahead), Ctrl+Home comes back to today.
Item {
  id: root

  required property var theme
  required property var service

  property bool active: false
  property string home: ""

  signal noticeRequested(string text, string kind, var undo)
  signal editRequested(string jobId, string mode)

  // "All" is a control; the others name states, so they are lowercase and carry that
  // state's glyph and ink. "not run" holds skipped, missed and disarmed jobs.
  readonly property var filters: [
    { id: "all", text: "All", status: "" },
    { id: "failed", text: "failed", status: "failed" },
    { id: "limit", text: "limit hit", status: "limit" },
    { id: "done", text: "done", status: "done" },
    { id: "skipped", text: "not run", status: "skipped" }
  ]

  property string _query: ""
  property string _filter: "all"
  property string _cursorId: ""
  property string _expandedId: ""
  property var _detail: null
  property string _detailFor: ""
  property bool _detailLoading: false
  property string _detailError: ""
  // {action, id} of a two-press guard waiting for its second press, or null.
  property var _guard: null

  property double _frozenNow: Date.now()
  readonly property double nowMs: root.active && root.service ? root.service.nowMs : root._frozenNow
  readonly property string dayKey: Model.dayKey(root.nowMs)
  // Day words ("Today") only change at midnight, so the grouping follows this
  // anchor rather than every tick of the clock.
  property double _dayAnchor: Date.now()
  onDayKeyChanged: root._dayAnchor = root.nowMs
  onServiceChanged: if (root.service) root._frozenNow = root.service.nowMs
  readonly property bool ready: !!root.service && root.service.ready === true
  readonly property var models: root.service && root.service.models ? root.service.models : null

  // ---- day timeline

  readonly property int dayOffsetMin: -14
  readonly property int dayOffsetMax: 7
  property int _dayOffset: 0
  readonly property double dayStartMs: Timeline.dayStartFor(root._dayAnchor, root._dayOffset)
  // "Today", "Yesterday", "Tomorrow", else the date itself ("Mon 14 Sep").
  readonly property string dayWord: root._dayOffset === 0 ? "Today" : root._dayOffset === -1 ? "Yesterday"
    : root._dayOffset === 1 ? "Tomorrow" : Model.dateText(root.dayStartMs)
  readonly property string dayDate: root._dayOffset >= -1 && root._dayOffset <= 1 ? Model.dateText(root.dayStartMs) : ""
  readonly property var dayTimeline: {
    var map = root.service && root.service.timeline ? root.service.timeline : null
    var key = Model.dayKey(root.dayStartMs)
    return map && map.hasOwnProperty(key) ? map[key] : null
  }

  function shiftDay(step) {
    var next = Math.max(root.dayOffsetMin, Math.min(root.dayOffsetMax, root._dayOffset + step))
    if (next === root._dayOffset) return false
    root._dayOffset = next
    return true
  }

  function loadDay() {
    if (root.service && typeof root.service.loadTimeline === "function") root.service.loadTimeline(root.dayStartMs)
  }

  onDayStartMsChanged: if (root.active) root.loadDay()
  onActiveChanged: {
    if (root.active) root.loadDay()
    else if (root.service) root._frozenNow = root.service.nowMs
  }

  readonly property string hints: {
    if (root._guard && root._guard.action === "run") return "Press Ctrl+Enter again to run now."
    if (root._expandedId !== "")
      return "Ctrl+E edit and re-arm  ·  Ctrl+Enter run again  ·  Ctrl+C copy resume  ·  Esc close card"
    return "Type to search  ·  Enter open  ·  Ctrl+E edit and re-arm  ·  Ctrl+C copy resume  ·  Ctrl+G filter  ·  Ctrl+←/→ day  ·  Esc close"
  }

  function categoryOf(job) {
    switch (Model.statusOf(job)) {
    case "done": return "done"
    case "limit": return "limit"
    case "skipped": case "missed": case "disarmed": return "skipped"
    }
    return "failed"
  }

  // Typing in the search rebuilds the list at once. The list transitions are for changes
  // the system makes (an arm, a fire, a swap), not for every key.
  property bool _instantRows: false

  function setQuery(q) {
    root._instantRows = true
    root._query = q
    Qt.callLater(function () { root._instantRows = false })
  }

  function setFilter(id) {
    root._instantRows = true
    root._filter = id
    Qt.callLater(function () { root._instantRows = false })
  }

  readonly property var historyJobs: {
    var list = root.service && Array.isArray(root.service.jobs) ? root.service.jobs : []
    return list.filter(function (j) { return !!j && j.queue === false })
  }

  readonly property var counts: {
    var c = { all: 0, failed: 0, limit: 0, done: 0, skipped: 0 }
    for (var i = 0; i < root.historyJobs.length; i++) {
      c.all++
      c[root.categoryOf(root.historyJobs[i])]++
    }
    return c
  }

  readonly property var visibleJobs: {
    var f = root._filter
    var q = root._query
    return root.historyJobs.filter(function (j) {
      return (f === "all" || root.categoryOf(j) === f) && Model.matchesFilter(j, q)
    })
  }

  // Flat rows for the list: a header per group, then its jobs.
  readonly property var rows: {
    var groups = Model.groupByDay(root.visibleJobs, root._dayAnchor, "endedAt")
    var out = []
    for (var g = 0; g < groups.length; g++) {
      var grp = groups[g]
      out.push({ uid: "h:" + grp.key, kind: "header", jobId: "", groupKey: grp.key,
                 word: grp.word, date: grp.date, count: grp.jobs.length })
      for (var i = 0; i < grp.jobs.length; i++)
        out.push({ uid: "j:" + grp.jobs[i].id, kind: "job", jobId: String(grp.jobs[i].id), groupKey: grp.key,
                   word: "", date: "", count: 0 })
    }
    return out
  }

  readonly property var jobOrder: root.rows.filter(function (r) { return r.kind === "job" }).map(function (r) { return r.jobId })

  onRowsChanged: {
    root.syncModel(rowModel, root.rows)
    if (root.jobOrder.indexOf(root._cursorId) < 0) root._cursorId = root.jobOrder.length > 0 ? root.jobOrder[0] : ""
    if (root._expandedId !== "" && root.jobOrder.indexOf(root._expandedId) < 0) root.collapse()
  }

  // The card re-reads when its job changes underneath it.
  readonly property var expandedStamp: {
    var j = root._expandedId !== "" && root.service ? root.service.jobsById[root._expandedId] : null
    return j ? String(j.updatedAt) + ":" + String(j.state ? j.state.status : "") : ""
  }
  onExpandedStampChanged: if (root._expandedId !== "" && root.expandedStamp !== "") root.loadDetail(root._expandedId)

  // Keeps delegates alive across list updates: remove what left, move what moved,
  // insert what arrived, update the rest in place.
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

  function jobFor(id) {
    return id !== "" && root.service && root.service.jobsById ? (root.service.jobsById[id] || null) : null
  }

  function indexOfJob(id) {
    for (var i = 0; i < rowModel.count; i++) if (rowModel.get(i).jobId === id) return i
    return -1
  }

  function setCursor(id) {
    root._cursorId = id
    var idx = root.indexOfJob(id)
    if (idx >= 0) list.positionViewAtIndex(idx, ListView.Contain)
  }

  function moveCursor(step) {
    var order = root.jobOrder
    if (order.length === 0) return
    var at = order.indexOf(root._cursorId)
    var next = at < 0 ? 0 : Math.max(0, Math.min(order.length - 1, at + step))
    root.setCursor(order[next])
  }

  function activate() {
    if (root.jobOrder.indexOf(root._cursorId) < 0 && root.jobOrder.length > 0) root._cursorId = root.jobOrder[0]
  }

  function focusJob(id) {
    var j = root.jobFor(String(id))
    if (!j || j.queue !== false) return
    if (root.visibleJobs.indexOf(j) < 0) { root.setQuery(""); root.setFilter("all") }
    Qt.callLater(function () { root.setCursor(String(id)) })
  }

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
    if (id === "") return
    // Setting the id changes expandedStamp, whose handler reads the job once.
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

  function clearGuard() {
    root._guard = null
    guardTimer.stop()
  }

  function runAgain(id) {
    var j = root.jobFor(id)
    if (!j) return
    if (j.canRunNow !== true) {
      root.clearGuard()
      root.noticeRequested(j.promptAvailable === false
        ? "The prompt was deleted after the run. Edit and re-arm it instead."
        : "This job cannot run again in its current state.", "warn", null)
      return
    }
    if (!root._guard || root._guard.action !== "run" || root._guard.id !== id) {
      root._guard = { action: "run", id: id }
      guardTimer.restart()
      return
    }
    root.clearGuard()
    root.service.runNow(id, j.digest, function (res) {
      if (res.ok === true) root.noticeRequested("Running \"" + String(j.label || "") + "\" now. It is in the Queue.", "ok", null)
      else root.noticeRequested(String(res.message || ""), "error", null)
    })
  }

  function copyResume(id) {
    if (id === "" || !root.service) return
    root.service.copyResume(id, function (res) {
      if (res.ok !== true) root.noticeRequested(String(res.message || ""), "error", null)
    })
  }

  function act(action, id) {
    if (id === "") return
    root._cursorId = id
    if (action === "rearm") { root.clearGuard(); root.editRequested(id, "rearm") }
    else if (action === "run") root.runAgain(id)
    else if (action === "copy") { root.clearGuard(); root.copyResume(id) }
  }

  function cycleFilter() {
    var ids = root.filters.map(function (f) { return f.id })
    root.setFilter(ids[(ids.indexOf(root._filter) + 1) % ids.length])
  }

  function handleKey(event) {
    if (!root.active) return false
    var k = event.key
    var mods = event.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier | Qt.MetaModifier)
    var ctrl = mods === Qt.ControlModifier
    var plain = mods === 0 || mods === Qt.ShiftModifier
    var enter = k === Qt.Key_Return || k === Qt.Key_Enter

    // Any key but the guard's own second press lets the guard lapse.
    if (root._guard && !(ctrl && enter)) root.clearGuard()

    if (k === Qt.Key_Escape && mods === 0) {
      if (root._guard) { root.clearGuard(); return true }
      if (root._expandedId !== "") { root.collapse(); return true }
      if (root._query !== "") { root.setQuery(""); return true }
      return false
    }
    if (k === Qt.Key_Tab || k === Qt.Key_Backtab) return false

    if (ctrl) {
      if (enter) { root.runAgain(root._cursorId); return true }
      if (k === Qt.Key_E) { root.act("rearm", root._cursorId); return true }
      if (k === Qt.Key_C) { root.act("copy", root._cursorId); return true }
      if (k === Qt.Key_G) { root.cycleFilter(); return true }
      if (k === Qt.Key_R) { root.service.refresh(); root.loadDay(); return true }
      if (k === Qt.Key_Left) { root.shiftDay(-1); return true }
      if (k === Qt.Key_Right) { root.shiftDay(1); return true }
      if (k === Qt.Key_Home) { root._dayOffset = 0; return true }
    }

    if (Util.editsFilter(event, root._query)) { root.setQuery(Util.editedFilter(event, root._query)); return true }

    if (mods === 0) {
      if (k === Qt.Key_Up) { root.moveCursor(-1); return true }
      if (k === Qt.Key_Down) { root.moveCursor(1); return true }
      if (k === Qt.Key_PageUp) { root.moveCursor(-8); return true }
      if (k === Qt.Key_PageDown) { root.moveCursor(8); return true }
      if (k === Qt.Key_Home) { root.moveCursor(-100000); return true }
      if (k === Qt.Key_End) { root.moveCursor(100000); return true }
      if (enter) {
        if (root._expandedId === root._cursorId && root._cursorId !== "") root.collapse()
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

  implicitWidth: Style.space(1100)
  implicitHeight: Style.space(640)

  Timer {
    id: guardTimer
    interval: 3000
    repeat: false
    onTriggered: root._guard = null
  }

  // The shown day is read again every minute while History is on screen.
  Timer {
    interval: 60000
    repeat: true
    running: root.active
    onTriggered: root.loadDay()
  }

  ListModel { id: rowModel }

  Item {
    id: bar
    x: Style.space(14)
    y: Style.space(10)
    width: Math.max(0, root.width - Style.space(14) * 2)
    height: Style.spacing.controlHeight

    SearchField {
      id: search
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: Math.max(Style.space(200), parent.width - chips.width - kept.width - Style.space(28))
      theme: root.theme
      text: root._query
      active: root.active
      trailing: root.ready ? (root.visibleJobs.length === 1 ? "1 job" : root.visibleJobs.length + " jobs") : ""
    }

    Row {
      id: chips
      anchors.left: search.right
      anchors.leftMargin: Style.space(14)
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.space(4)

      Repeater {
        model: root.filters

        Chip {
          required property var modelData
          theme: root.theme
          pill: true
          glyph: modelData.status !== "" ? Model.statusSpec(modelData.status, "").glyph : ""
          text: modelData.text
          inkText: modelData.id !== "all"
          note: modelData.id === "all" ? "" : String(root.counts[modelData.id] || 0)
          selected: root._filter === modelData.id
          hasCursor: hovered
          tint: modelData.id === "failed" ? root.theme.badInk
            : modelData.id === "limit" ? root.theme.warnInk
            : modelData.id === "done" ? root.theme.okInk
            : modelData.id === "skipped" ? root.theme.readable : root.theme.accent
          onClicked: root.setFilter(modelData.id)
        }
      }
    }

    Text {
      id: kept
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: "Kept for 30 days"
      color: root.theme.soft
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
    }
  }

  Item {
    id: dayBar
    anchors.top: bar.bottom
    anchors.topMargin: Style.space(10)
    x: Style.space(14)
    width: Math.max(0, root.width - Style.space(14) * 2)
    height: Style.space(22)

    Row {
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.space(8)

      Text {
        id: dayBack
        anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText
        text: Model.GLYPH.left
        color: backMouse.containsMouse ? root.theme.fg : root.theme.readable
        opacity: root._dayOffset > root.dayOffsetMin ? 1 : 0.35
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.glyph

        MouseArea {
          id: backMouse
          anchors.fill: parent
          anchors.margins: -Style.space(4)
          hoverEnabled: true
          cursorShape: Qt.PointingHandCursor
          onClicked: root.shiftDay(-1)
        }
      }

      Text {
        anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText
        text: root.dayWord
        color: root._dayOffset === 0 ? root.theme.accentInk : root.theme.strong
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.label
        font.bold: true
        font.letterSpacing: root.theme.type.tracking
      }

      Text {
        anchors.verticalCenter: parent.verticalCenter
        visible: root.dayDate !== ""
        textFormat: Text.PlainText
        text: root.dayDate
        color: root.theme.soft
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.meta
      }

      Text {
        anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText
        text: Model.GLYPH.right
        color: forwardMouse.containsMouse ? root.theme.fg : root.theme.readable
        opacity: root._dayOffset < root.dayOffsetMax ? 1 : 0.35
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.glyph

        MouseArea {
          id: forwardMouse
          anchors.fill: parent
          anchors.margins: -Style.space(4)
          hoverEnabled: true
          cursorShape: Qt.PointingHandCursor
          onClicked: root.shiftDay(1)
        }
      }
    }

    Text {
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      visible: root.service && root.service.loadingTimeline === true
      textFormat: Text.PlainText
      text: "Reading…"
      color: root.theme.soft
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
    }
  }

  DayTimeline {
    id: dayTimeline
    anchors.top: dayBar.bottom
    anchors.topMargin: Style.space(4)
    x: Style.space(14)
    width: Math.max(0, root.width - Style.space(14) * 2)
    height: implicitHeight
    theme: root.theme
    timeline: root.dayTimeline
    dayStartMs: root.dayStartMs
    nowMs: root.nowMs
    cursorJobId: root._cursorId
    onJobClicked: function (id) { root.focusJob(id) }
  }

  Rectangle {
    id: rule
    anchors.top: dayTimeline.bottom
    anchors.topMargin: Style.space(10)
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
    model: rowModel
    visible: root.ready && rowModel.count > 0
    reuseItems: false

    add: Transition {
      enabled: !root._instantRows
      NumberAnimation { property: "opacity"; from: 0; to: 1; duration: root.theme.fadeMs(180); easing.type: Easing.OutCubic }
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

      readonly property var job: slot.kind === "job" ? root.jobFor(slot.jobId) : null
      readonly property bool expanded: slot.kind === "job" && root._expandedId === slot.jobId

      // The card stays drawn while the slot is still closing, so collapsing fades the card
      // in step with the height instead of blanking it on the first frame.
      readonly property bool showCard: slot.expanded || (slot.kind === "job" && slot.height > row.implicitHeight + 1)
      property var _lastDetail: null
      readonly property var liveDetail: slot.expanded ? root._detail : null
      onLiveDetailChanged: if (slot.liveDetail !== null) slot._lastDetail = slot.liveDetail
      onShowCardChanged: if (!slot.showCard) slot._lastDetail = null

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
      }

      JobRow {
        id: row
        visible: slot.kind === "job" && !!slot.job
        width: slot.width
        theme: root.theme
        job: slot.job
        mode: "history"
        nowMs: root.nowMs
        home: root.home
        modelLabel: slot.job ? Model.modelLabel(slot.job, root.models) : ""
        hasCursor: root._cursorId === slot.jobId
        guard: !!root._guard && root._guard.id === slot.jobId
        onHovered: root._cursorId = slot.jobId
        onActivated: function (modifiers) {
          root._cursorId = slot.jobId
          if (root._expandedId === slot.jobId) root.collapse()
          else root.expand(slot.jobId)
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
        mode: "history"
        nowMs: root.nowMs
        home: root.home
        modelLabel: slot.job ? Model.modelLabel(slot.job, root.models) : ""
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

  // Loading, empty and no-match states. Never "0 jobs" before the first read lands.
  Column {
    visible: !list.visible
    x: Style.space(24)
    anchors.top: rule.bottom
    anchors.topMargin: Style.space(28)
    width: Math.max(0, root.width - Style.space(24) * 2)
    spacing: Style.space(6)

    Text {
      width: parent.width
      visible: !(root.ready && root.historyJobs.length > 0 && root._query !== "")
      textFormat: Text.PlainText
      text: !root.ready ? "Reading jobs…"
        : root.historyJobs.length === 0 ? "No runs yet."
        : root._filter === "failed" ? "No failed runs."
        : root._filter === "limit" ? "No runs hit a limit."
        : root._filter === "done" ? "No completed runs."
        : "Nothing was skipped or missed."
      color: root.ready ? root.theme.strong : root.theme.soft
      elide: Text.ElideRight
      maximumLineCount: 1
      font.family: root.theme.fontFamily
      font.pixelSize: root.ready && root.historyJobs.length === 0 ? root.theme.type.title : root.theme.type.body
      font.bold: root.ready && root.historyJobs.length === 0
    }

    // The fact, then the way back, in two inks.
    Row {
      width: parent.width
      visible: root.ready && root.historyJobs.length > 0 && root._query !== ""
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

    Text {
      visible: root.ready && root.historyJobs.length === 0
      width: parent.width
      textFormat: Text.PlainText
      text: "Finished jobs show up here with their result."
      color: root.theme.readable
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.body
    }
  }
}
