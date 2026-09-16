pragma ComponentBehavior: Bound

import QtQuick
import qs.Commons
import qs.Ui
import "../lib/Edition.js" as Edition
import "../lib/Model.js" as Model

// Which session the prompt goes to. An in-card overlay over the view area, modal
// while it is up (R6 8.4): type to filter, Left and Right pick the agent, Up and
// Down pick the row, Enter resumes it, Ctrl+N starts a new session in that row's
// folder. Row 0 is always the new-session action; typing a folder path (/... or
// ~/...) points it at that folder.
//
// Everything listed comes from `ap4a sessions`, a bounded read; the sheet says what
// the bound left out instead of pretending the list is complete. Pi keeps its sessions
// per folder, so they are listed for one folder at a time: the draft's folder (or the
// home folder), or the folder typed into the filter. Cursor lists only the chats Auto
// Pilot itself started.
Item {
  id: root

  required property var theme
  required property var service
  property bool active: false
  property string home: ""
  property string initialHarness: ""
  property string initialCwd: ""

  signal picked(var selection)
  signal closed()
  signal noticeRequested(string text, string kind, var undo)

  property string query: ""
  property string filter: "all"
  property int cursor: 0
  // The folder the last sessions read was scoped to (Pi lists only that folder).
  property string _listedCwd: ""

  readonly property string hints: "Type to filter  ·  ←/→ agent  ·  ↑/↓ choose  ·  Enter pick  ·  Ctrl+N new session  ·  Esc back"

  readonly property var filters: ["all"].concat(Edition.HARNESS_IDS)
  readonly property var result: root.service && root.service.sessions && root.service.sessions["all"]
    ? root.service.sessions["all"] : null
  readonly property bool loading: root.result === null && !!root.service && root.service.loadingSessions === true
  readonly property var allRows: root.result && Array.isArray(root.result.sessions) ? root.result.sessions : []
  readonly property string queryPath: root.expandPath(root.query)

  readonly property var rows: {
    var out = []
    var path = root.queryPath
    var q = root.query.trim().toLowerCase()
    var words = path !== "" || q === "" ? [] : q.split(/\s+/)
    for (var i = 0; i < root.allRows.length; i++) {
      var r = root.allRows[i]
      if (!r || typeof r.id !== "string" || Edition.HARNESS_IDS.indexOf(r.harness) < 0) continue
      if (root.filter !== "all" && r.harness !== root.filter) continue
      if (path !== "") {
        var cwd = String(r.cwd || "")
        if (cwd !== path && cwd.indexOf(path + "/") !== 0) continue
      } else if (words.length > 0) {
        var hay = [r.title, r.cwd, Model.shortPath(r.cwd, root.home), Model.harnessName(r.harness)]
          .map(function (v) { return v === undefined || v === null ? "" : String(v) }).join("\n").toLowerCase()
        var hit = true
        for (var w = 0; w < words.length; w++) if (words[w] !== "" && hay.indexOf(words[w]) < 0) hit = false
        if (!hit) continue
      }
      out.push(r)
    }
    out.sort(function (a, b) { return (Number(b.updatedAtMs) || 0) - (Number(a.updatedAtMs) || 0) })
    return out
  }

  readonly property int rowCount: root.rows.length + 1
  readonly property string newHarness: root.filter !== "all" ? root.filter
    : (Edition.HARNESS_IDS.indexOf(root.initialHarness) >= 0 ? root.initialHarness : Edition.HARNESS_IDS[0])
  readonly property string newCwd: root.queryPath !== "" ? root.queryPath : (root.initialCwd !== "" ? root.initialCwd : root.home)

  readonly property string boundLine: {
    if (root.result === null) return ""
    var days = typeof root.result.limitDays === "number" ? root.result.limitDays : 90
    var line = "Sessions older than " + days + " days are not listed."
    var cap = typeof root.result.perHarnessCap === "number" ? root.result.perHarnessCap : 50
    var truncated = root.result.truncated || {}
    for (var i = 0; i < Edition.HARNESS_IDS.length; i++) {
      var h = Edition.HARNESS_IDS[i]
      if ((root.filter === "all" || root.filter === h) && truncated[h] === true)
        return line + " Only the newest " + cap + " per agent are listed."
    }
    return line
  }

  readonly property string errorLine: {
    var errors = root.result && root.result.errors ? root.result.errors : {}
    var out = []
    for (var i = 0; i < Edition.HARNESS_IDS.length; i++) {
      var h = Edition.HARNESS_IDS[i]
      if (root.filter !== "all" && root.filter !== h) continue
      var e = errors[h]
      if (e === "too_large") out.push(Model.harnessName(h) + " sessions too large to list.")
      else if (e === "timeout") out.push(Model.harnessName(h) + " sessions took too long to list.")
      else if (typeof e === "string" && e !== "") out.push(Model.harnessName(h) + " sessions could not be read.")
    }
    return out.join(" ")
  }

  // Which folder Pi's sessions are listed for, and how to list another.
  readonly property string piLine: {
    if (root.result === null || (root.filter !== "all" && root.filter !== "pi")) return ""
    if (Array.isArray(root.result.needsCwd) && root.result.needsCwd.indexOf("pi") >= 0) return "Type a folder path to list Pi sessions."
    if (typeof root.result.cwd === "string" && root.result.cwd !== "")
      return "Pi sessions are listed for " + Model.shortPath(root.result.cwd, root.home) + " only. Type a folder path to list another."
    return ""
  }

  function expandPath(text) {
    var t = String(text || "").trim()
    if (t === "~" || t.indexOf("~/") === 0) {
      if (root.home === "") return ""
      t = root.home + t.slice(1)
    }
    if (t.charAt(0) !== "/") return ""
    t = t.replace(/\/+$/, "")
    return t === "" ? "/" : t
  }

  function countFor(id) {
    var c = root.result && root.result.counts ? root.result.counts : {}
    if (id === "all") {
      var n = 0
      for (var k in c) if (typeof c[k] === "number") n += c[k]
      return n
    }
    return typeof c[id] === "number" ? c[id] : 0
  }

  function basename(path) {
    var p = String(path || "").replace(/\/+$/, "")
    var i = p.lastIndexOf("/")
    return i >= 0 ? p.slice(i + 1) : p
  }

  function ago(ms) {
    var now = root.service ? Number(root.service.nowMs) || 0 : 0
    if (typeof ms !== "number" || !isFinite(ms) || now <= 0) return ""
    var s = Math.max(0, Math.round((now - ms) / 1000))
    if (s < 60) return "just now"
    if (s < 3600) return Math.floor(s / 60) + "m ago"
    if (s < 86400) return Math.floor(s / 3600) + "h ago"
    var d = Math.floor(s / 86400)
    return d === 1 ? "yesterday" : d + "d ago"
  }

  function resetCursor() {
    root.cursor = root.queryPath === "" && root.rows.length > 0 ? 1 : 0
  }

  function open(args) {
    var a = args && typeof args === "object" ? args : {}
    root.initialHarness = Edition.HARNESS_IDS.indexOf(a.harness) >= 0 ? a.harness : ""
    root.initialCwd = typeof a.cwd === "string" ? a.cwd : ""
    root.query = ""
    root.filter = root.initialHarness !== "" ? root.initialHarness : "all"
    root.resetCursor()
    root._listedCwd = root.initialCwd !== "" ? root.initialCwd : root.home
    if (root.service && typeof root.service.loadSessions === "function") root.service.loadSessions("", root._listedCwd)
  }

  // A typed folder lists Pi's sessions there too (every other agent's rows are already
  // filtered by it).
  function reloadForPath() {
    var p = root.queryPath
    if (!root.active || p === "" || p === "/" || p === root._listedCwd) return
    root._listedCwd = p
    if (root.service && typeof root.service.loadSessions === "function") root.service.loadSessions("", p)
  }

  function close() {
    root.query = ""
    root.closed()
  }

  // A fact and the way out of it.
  function blockedSentence(harness) {
    return harness === "codex"
      ? "Codex is not signed in. Run codex login in a terminal, then open " + Edition.DISPLAY_NAME + " again."
      : Model.harnessName(harness) + " cannot run right now."
  }

  readonly property bool noMatch: !root.loading && root.query !== "" && root.queryPath === ""

  function agentBlocked(harness) {
    var a = root.service && typeof root.service.agentFor === "function" ? root.service.agentFor(harness) : null
    return !!a && a.enabled === false
  }

  function pickSession(r) {
    if (!r) return
    if (root.agentBlocked(r.harness)) {
      root.noticeRequested(root.blockedSentence(r.harness), "warn", null)
      return
    }
    // Pi resumes a session by its file, so a row without one cannot be resumed.
    var path = r.harness === "pi" && typeof r.path === "string" && r.path.charAt(0) === "/" ? r.path : null
    if (r.harness === "pi" && path === null) {
      root.noticeRequested("That Pi session has no file to resume. Pick another, or start a new session.", "warn", null)
      return
    }
    root.picked({
      harness: r.harness, mode: "resume", sessionId: r.id, cwd: r.cwd,
      title: typeof r.title === "string" ? r.title : "",
      updatedAtMs: r.updatedAtMs, messages: r.messages, sessionPath: path
    })
  }

  // fromRow: Ctrl+N, a new session in the highlighted row's folder.
  function pickNew(fromRow) {
    var cwd = root.newCwd
    var harness = root.newHarness
    if (fromRow && root.cursor > 0) {
      var r = root.rows[root.cursor - 1]
      if (r) {
        cwd = String(r.cwd || "")
        if (root.filter === "all") harness = r.harness
      }
    }
    if (cwd === "") {
      root.noticeRequested("Type a folder such as ~/proj/api, or press Ctrl+N on a session.", "warn", null)
      return
    }
    // The helper never starts a session in the home folder itself; say so before asking it.
    if (cwd === root.home) {
      root.noticeRequested("Type a folder such as ~/proj/api. Your home folder itself is not allowed.", "warn", null)
      return
    }
    if (root.agentBlocked(harness)) {
      root.noticeRequested(root.blockedSentence(harness), "warn", null)
      return
    }
    root.picked({ harness: harness, mode: "new", sessionId: null, cwd: cwd, title: root.basename(cwd), sessionPath: null })
  }

  function pickCursor() {
    if (root.cursor <= 0) root.pickNew(false)
    else root.pickSession(root.rows[root.cursor - 1])
  }

  function stepFilter(dir) {
    var i = root.filters.indexOf(root.filter)
    root.filter = root.filters[(i + dir + root.filters.length) % root.filters.length]
    root.resetCursor()
  }

  function moveCursor(delta) {
    root.cursor = Math.max(0, Math.min(root.rowCount - 1, root.cursor + delta))
  }

  function handleKey(event) {
    var key = event.key
    var mods = event.modifiers
    var ctrl = (mods & Qt.ControlModifier) !== 0
    if ((mods & (Qt.AltModifier | Qt.MetaModifier)) !== 0) return true

    if (key === Qt.Key_Escape) {
      if (root.query === "") return false
      root.query = ""
      root.resetCursor()
      return true
    }
    if (ctrl && key === Qt.Key_N) { root.pickNew(true); return true }
    if (key === Qt.Key_Return || key === Qt.Key_Enter) { root.pickCursor(); return true }
    if (key === Qt.Key_Left) { root.stepFilter(-1); return true }
    if (key === Qt.Key_Right) { root.stepFilter(1); return true }
    if (key === Qt.Key_Up || (ctrl && key === Qt.Key_P)) { root.moveCursor(-1); return true }
    if (key === Qt.Key_Down) { root.moveCursor(1); return true }
    if (key === Qt.Key_PageUp) { root.moveCursor(-8); return true }
    if (key === Qt.Key_PageDown) { root.moveCursor(8); return true }
    if (key === Qt.Key_Home) { root.cursor = 0; return true }
    if (key === Qt.Key_End) { root.cursor = root.rowCount - 1; return true }
    if (Util.editsFilter(event, root.query)) {
      root.query = Util.editedFilter(event, root.query)
      root.resetCursor()
      return true
    }
    var text = String(event.text || "")
    if (!ctrl && text.length === 1 && text.charCodeAt(0) >= 32 && text.charCodeAt(0) !== 127) {
      if (root.query.length < 200) root.query = root.query + text
      root.resetCursor()
      return true
    }
    return false
  }

  onCursorChanged: if (list.count > 0) list.positionViewAtIndex(root.cursor, ListView.Contain)
  onQueryPathChanged: if (root.active && root.queryPath !== "") cwdReload.restart()

  Timer {
    id: cwdReload
    interval: 500
    repeat: false
    onTriggered: root.reloadForPath()
  }
  onRowsChanged: {
    if (root.cursor > root.rowCount - 1) root.cursor = root.rowCount - 1
    // The first answer lands after the sheet opened: start on the newest session.
    if (root.active && root.cursor === 0 && root.query === "" && root.rows.length > 0) root.cursor = 1
  }

  // Opaque, so the view underneath never shows through; no entrance animation
  // (a keyboard-opened overlay appears at once, R6 6.1).
  Rectangle {
    anchors.fill: parent
    color: root.theme.surface

    MouseArea {
      anchors.fill: parent
      acceptedButtons: Qt.AllButtons
      onWheel: function (wheel) { wheel.accepted = true }
    }
  }

  Item {
    id: head
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: parent.top
    height: Style.space(28)

    Text {
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: "Pick a session"
      color: root.theme.strong
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.title
      font.bold: true
    }

    ActionButton {
      id: backButton
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      theme: root.theme
      hasCursor: backButton.hovered
      glyph: "\uDB80\uDC4D"   // md-arrow_left U+F004D
      text: "Back"
      shortcut: "Esc"
      onClicked: root.close()
    }
  }

  SearchField {
    id: search
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: head.bottom
    anchors.topMargin: Style.spacing.lg
    theme: root.theme
    text: root.query
    placeholder: "Type to filter, or type a folder path for a new session"
    active: root.active
    trailing: root.loading ? "Reading sessions…"
      : (root.query !== "" || root.filter !== "all"
        ? root.rows.length + " of " + root.countFor(root.filter)
        : root.countFor("all") + " sessions")
  }

  Flow {
    id: filterRow
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: search.bottom
    anchors.topMargin: Style.spacing.lg
    spacing: Style.space(6)

    Repeater {
      model: root.filters

      delegate: Chip {
        id: filterChip
        required property string modelData
        theme: root.theme
        pill: true
        harness: filterChip.modelData === "all" ? "" : filterChip.modelData
        text: filterChip.modelData === "all" ? "All" : Model.harnessName(filterChip.modelData)
        note: root.result === null ? "" : String(root.countFor(filterChip.modelData))
        selected: root.filter === filterChip.modelData
        hasCursor: root.filter === filterChip.modelData
        onClicked: {
          root.filter = filterChip.modelData
          root.resetCursor()
        }
      }
    }
  }

  ListView {
    id: list
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: filterRow.bottom
    anchors.topMargin: Style.spacing.lg
    // Whole rows only, so the last one is never cut through.
    height: Math.floor(Math.max(0, footerColumn.y - Style.spacing.lg - list.y) / Style.space(34)) * Style.space(34)
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    model: root.rowCount

    delegate: Item {
      id: row
      required property int index
      readonly property var session: row.index > 0 ? root.rows[row.index - 1] : null
      readonly property bool cursorHere: root.cursor === row.index
      width: list.width
      height: Style.space(34)

      CursorSurface {
        anchors.fill: parent
        hasCursor: row.cursorHere
        foreground: root.theme.fg
        accent: root.theme.accent
      }

      HarnessRail {
        anchors.left: parent.left
        anchors.leftMargin: Style.space(2)
        anchors.top: parent.top
        anchors.topMargin: Style.space(5)
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Style.space(5)
        visible: row.session !== null
        theme: root.theme
        harness: row.session ? row.session.harness : ""
      }

      AgentMark {
        id: rowMark
        anchors.left: parent.left
        anchors.leftMargin: Style.space(14)
        anchors.verticalCenter: parent.verticalCenter
        visible: row.session !== null
        theme: root.theme
        agent: row.session ? row.session.harness : ""
        size: Style.space(14)
      }

      Text {
        id: rowTitle
        anchors.left: parent.left
        anchors.leftMargin: Style.space(38)
        anchors.verticalCenter: parent.verticalCenter
        width: row.session ? parent.width * 0.40 : parent.width - Style.space(160)
        textFormat: Text.PlainText
        text: row.session
          ? (row.session.title ? String(row.session.title) : Model.elideMiddle(row.session.id, 13))
          : (root.queryPath === "" && (root.newCwd === "" || root.newCwd === root.home)
            ? "+ New session: type a folder path"
            : "+ New session in " + Model.shortPath(root.newCwd, root.home)
              + (root.filter === "all" ? " (" + Model.harnessName(root.newHarness) + ")" : ""))
        color: row.cursorHere ? root.theme.fg : (row.session ? root.theme.strong : root.theme.readable)
        elide: Text.ElideRight
        maximumLineCount: 1
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.body
      }

      Text {
        anchors.left: rowTitle.right
        anchors.leftMargin: Style.space(12)
        anchors.verticalCenter: parent.verticalCenter
        width: parent.width * 0.26
        visible: row.session !== null
        textFormat: Text.PlainText
        text: row.session ? Model.shortPath(row.session.cwd, root.home) : ""
        color: root.theme.readable
        elide: Text.ElideMiddle
        maximumLineCount: 1
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.data
      }

      Row {
        anchors.right: parent.right
        anchors.rightMargin: Style.space(12)
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(14)

        Text {
          width: Style.space(70)
          horizontalAlignment: Text.AlignRight
          visible: row.session !== null
          textFormat: Text.PlainText
          text: row.session ? root.ago(row.session.updatedAtMs) : ""
          color: root.theme.soft
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
          font.features: root.theme.type.digits
        }

        Text {
          width: Style.space(64)
          horizontalAlignment: Text.AlignRight
          visible: row.session !== null
          textFormat: Text.PlainText
          text: row.session && typeof row.session.messages === "number" ? row.session.messages + " msgs" : ""
          color: root.theme.soft
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
          font.features: root.theme.type.digits
        }

        Text {
          width: Style.space(52)
          horizontalAlignment: Text.AlignRight
          textFormat: Text.PlainText
          text: row.session ? "resume" : "new"
          color: row.cursorHere ? root.theme.accentInk : root.theme.soft
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
        }
      }

      MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onEntered: root.cursor = row.index
        onClicked: {
          root.cursor = row.index
          root.pickCursor()
        }
      }
    }

    Text {
      anchors.left: parent.left
      anchors.leftMargin: Style.space(38)
      y: Style.space(34) + Style.space(12)
      width: parent.width - Style.space(50)
      visible: root.rows.length === 0 && !root.noMatch
      textFormat: Text.PlainText
      text: root.loading ? "Reading sessions…"
        : (root.result === null ? "Sessions could not be read." : "No sessions to resume here. Start a new one above.")
      color: root.loading ? root.theme.soft : root.theme.readable
      elide: Text.ElideRight
      maximumLineCount: 1
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.body
    }

    // The fact, then the way back, in two inks.
    Row {
      anchors.left: parent.left
      anchors.leftMargin: Style.space(38)
      y: Style.space(34) + Style.space(12)
      width: parent.width - Style.space(50)
      visible: root.rows.length === 0 && root.noMatch
      spacing: Style.space(7)

      Text {
        width: Math.max(0, Math.min(implicitWidth, parent.width - recoverText.implicitWidth - parent.spacing))
        textFormat: Text.PlainText
        text: "No sessions match \"" + root.query + "\"."
        color: root.theme.strong
        elide: Text.ElideRight
        maximumLineCount: 1
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.body
      }

      Text {
        id: recoverText
        textFormat: Text.PlainText
        text: "Esc clears the filter."
        color: root.theme.soft
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.body
      }
    }
  }

  Column {
    id: footerColumn
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.bottom: parent.bottom
    spacing: Style.space(2)

    Text {
      width: parent.width
      visible: root.boundLine !== ""
      textFormat: Text.PlainText
      text: root.boundLine
      color: root.theme.soft
      elide: Text.ElideRight
      maximumLineCount: 1
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
    }

    Text {
      width: parent.width
      visible: root.piLine !== ""
      textFormat: Text.PlainText
      text: root.piLine
      color: root.theme.soft
      elide: Text.ElideRight
      maximumLineCount: 1
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
    }

    Text {
      width: parent.width
      visible: root.errorLine !== ""
      textFormat: Text.PlainText
      text: root.errorLine
      color: root.theme.warnInk
      elide: Text.ElideRight
      maximumLineCount: 1
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
    }
  }
}
