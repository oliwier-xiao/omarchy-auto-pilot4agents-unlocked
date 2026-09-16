pragma ComponentBehavior: Bound

import QtQuick
import qs.Commons
import qs.Ui
import "../lib/Edition.js" as Edition
import "../lib/Model.js" as Model
import "../lib/Compose.js" as Compose

// Which model the job asks for, picked from the agent's own list (`ap4a models`). A
// list that drops out of the Options model row, modal while it is up: type to filter,
// Up and Down choose, Enter picks, Ctrl+R asks the agent again, Esc clears the filter
// and then goes back.
//
// The first row is "Agent default", which passes no --model at all, and it stays
// selectable when the list could not be read. Pi has no such row: without a provider
// and a model Pi would try a paid provider first. OpenCode rows carry their billing
// class (free, Zen, Go); a Zen row is amber while paid usage is off.
Item {
  id: root

  required property var theme
  required property var service
  property bool active: false

  signal picked(var selection)
  signal closed()
  signal noticeRequested(string text, string kind, var undo)

  property string harness: Edition.HARNESS_IDS[0]
  // The draft's current choice, marked in the list.
  property string currentProvider: ""
  property string currentModel: ""
  property bool allowPaid: false
  property string query: ""
  property int cursor: 0

  // The model row this list drops out of, in panel coordinates: { x, y, width, height }.
  // Null when the caller has no row to point at (a test, or a pick asked for from
  // elsewhere), and then the card sits in the middle of the view instead.
  property var anchor: null

  readonly property real cardWidth: Math.min(Style.space(460), Math.max(Style.space(320), root.width - Style.space(48)))
  readonly property real rowHeight: Style.space(34)
  // What the card spends on everything that is not the list: the head and its top margin,
  // the search field and its gap, the status line when there is one, the gap above the
  // list and the foot with its own bottom margin. Measured from the same numbers the
  // items below use, so the card and the list never disagree about the space left.
  readonly property real chromeHeight: Style.space(4) + Style.space(28)
    + Style.space(6) + Style.spacing.controlHeight
    + (statusLine.visible ? Style.space(6) + Style.space(22) : 0)
    + Style.space(6) + Style.space(22) + Style.space(4)
  // Eleven rows at most, and never more than the list actually has; capped at what the
  // view leaves once that chrome is paid for, so a short view still drops a list.
  readonly property real listHeight: {
    var wanted = Math.max(1, Math.min(11, root.rows.length)) * root.rowHeight
    if (root.rows.length === 0) wanted = root.rowHeight * 2
    var room = root.height - Style.space(16) - root.chromeHeight
    if (!(room > 0)) return wanted
    return Math.min(wanted, Math.max(root.rowHeight * 3, room))
  }
  readonly property real cardHeight: root.chromeHeight + root.listHeight

  readonly property real anchorX: root.anchor && isFinite(root.anchor.x) ? Number(root.anchor.x) : -1
  readonly property real anchorY: root.anchor && isFinite(root.anchor.y) ? Number(root.anchor.y) : -1
  readonly property real anchorH: root.anchor && isFinite(root.anchor.height) ? Number(root.anchor.height) : 0
  readonly property bool anchored: root.anchorX >= 0 && root.anchorY >= 0

  // Below the row when it fits, above it when it does not — the list never runs off
  // the card and never covers the row it belongs to.
  readonly property bool dropsUp: root.anchored
    && root.anchorY + root.anchorH + Style.space(6) + root.cardHeight > root.height
    && root.anchorY - Style.space(6) - root.cardHeight >= 0

  readonly property real cardX: root.anchored
    ? Math.max(Style.space(12), Math.min(root.anchorX, root.width - root.cardWidth - Style.space(12)))
    : Math.max(0, (root.width - root.cardWidth) / 2)
  readonly property real cardY: {
    if (!root.anchored) return Math.max(0, (root.height - root.cardHeight) / 2)
    if (root.dropsUp) return Math.max(Style.space(8), root.anchorY - Style.space(6) - root.cardHeight)
    var below = root.anchorY + root.anchorH + Style.space(6)
    return Math.max(Style.space(8), Math.min(below, root.height - root.cardHeight - Style.space(8)))
  }

  readonly property string hints: "Type to filter  ·  Enter pick  ·  Ctrl+R refresh list  ·  Esc back"

  readonly property var result: root.service && root.service.models && typeof root.service.models === "object"
    && root.service.models[root.harness] ? root.service.models[root.harness] : null
  readonly property bool loading: !!root.service && root.service.loadingModels === true
  // No answer for this agent yet: the list shows the reading line.
  readonly property bool waiting: root.result === null && root.loading
  readonly property var rows: Compose.modelRows(root.result, root.harness, root.query, root.allowPaid)
  readonly property int modelCount: root.rows.filter(function (r) { return r.kind === "model" }).length
  readonly property string reasonText: root.result && typeof root.result.reason === "string"
    && Compose.MODEL_REASONS.hasOwnProperty(root.result.reason) ? Compose.MODEL_REASONS[root.result.reason] : ""
  readonly property bool noMatch: root.query !== "" && root.rows.length === 0 && !root.waiting

  readonly property string footLine: {
    var r = root.result
    if (!r || root.waiting) return ""
    var n = root.modelCount
    var parts = []
    parts.push(root.query !== "" ? n + " match" + (n === 1 ? "" : "es") : n + " model" + (n === 1 ? "" : "s"))
    if (typeof r.fetchedAt === "number" && r.fetchedAt > 0 && root.service) {
      var age = Math.max(0, Math.round((Number(root.service.nowMs) || 0) / 1000 - r.fetchedAt))
      parts.push(age < 60 ? "listed just now" : "listed " + Model.formatDuration(age * 1000) + " ago")
    }
    if (r.truncated === true) parts.push("only the first 1000 are listed")
    return parts.join(", ")
  }

  function selectable(i) {
    return i >= 0 && i < root.rows.length && root.rows[i].kind !== "group"
  }

  function firstSelectable() {
    for (var i = 0; i < root.rows.length; i++) if (root.selectable(i)) return i
    return -1
  }

  function isCurrent(row) {
    if (!row) return false
    if (row.kind === "default") return root.currentModel === ""
    if (row.kind !== "model") return false
    if (root.harness === "pi") return row.provider === root.currentProvider && row.modelId === root.currentModel
    return row.id === root.currentModel
  }

  function resetCursor() {
    var start = -1
    if (root.query === "") for (var i = 0; i < root.rows.length; i++) if (root.isCurrent(root.rows[i])) { start = i; break }
    root.cursor = start >= 0 ? start : Math.max(0, root.firstSelectable())
  }

  function moveCursor(delta) {
    if (root.rows.length === 0) return
    var dir = delta < 0 ? -1 : 1
    var steps = Math.abs(delta)
    var i = root.cursor
    var last = i
    while (steps > 0) {
      i += dir
      if (i < 0 || i >= root.rows.length) break
      if (root.selectable(i)) { last = i; steps-- }
    }
    root.cursor = last
  }

  function open(args) {
    var a = args && typeof args === "object" ? args : {}
    root.harness = Edition.HARNESS_IDS.indexOf(a.harness) >= 0 ? a.harness : Edition.HARNESS_IDS[0]
    root.currentProvider = typeof a.provider === "string" ? a.provider : ""
    root.currentModel = typeof a.model === "string" ? a.model : ""
    root.allowPaid = a.allowPaid === true
    root.query = typeof a.query === "string" ? a.query.slice(0, 80) : ""
    root.anchor = a.anchor && typeof a.anchor === "object" ? a.anchor : null
    root.resetCursor()
    // The helper keeps its own cache; a list that failed last time is asked again.
    if (root.service && typeof root.service.loadModels === "function" && (root.result === null || root.result.ok !== true))
      root.service.loadModels(root.harness, false)
    // Opening on the agent it was last opened on leaves the rows untouched, so nothing
    // else would lay the list out: the rows would be a frame late for no visible reason.
    root.relayout()
  }

  function close() {
    root.query = ""
    root.closed()
  }

  function refresh() {
    if (root.service && typeof root.service.loadModels === "function") root.service.loadModels(root.harness, true)
    root.noticeRequested("Asking " + Model.harnessName(root.harness) + " for its models…", "info", null)
  }

  function pickRow(row) {
    if (!row || row.kind === "group") return
    if (row.kind === "default") {
      root.picked({ harness: root.harness, provider: null, model: null, label: "Agent default" })
      return
    }
    if (root.harness === "pi") {
      root.picked({ harness: root.harness, provider: row.provider, model: row.modelId, label: row.label })
      return
    }
    if (!Compose.modelOk(row.id)) {
      root.noticeRequested("The model name is not valid.", "warn", null)
      return
    }
    root.picked({ harness: root.harness, provider: null, model: row.id, label: row.label })
  }

  function pickCursor() {
    if (root.selectable(root.cursor)) {
      root.pickRow(root.rows[root.cursor])
      return
    }
    if (root.waiting) return
    if (root.reasonText !== "") root.noticeRequested(root.reasonText, "warn", null)
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
    if (ctrl && key === Qt.Key_R) { root.refresh(); return true }
    if (key === Qt.Key_Return || key === Qt.Key_Enter) { root.pickCursor(); return true }
    if (key === Qt.Key_Up || (ctrl && key === Qt.Key_P)) { root.moveCursor(-1); return true }
    if (key === Qt.Key_Down || (ctrl && key === Qt.Key_N)) { root.moveCursor(1); return true }
    if (key === Qt.Key_PageUp) { root.moveCursor(-8); return true }
    if (key === Qt.Key_PageDown) { root.moveCursor(8); return true }
    if (key === Qt.Key_Home) { root.cursor = Math.max(0, root.firstSelectable()); return true }
    if (key === Qt.Key_End) { root.moveCursor(root.rows.length); return true }
    if (Util.editsFilter(event, root.query)) {
      root.query = Util.editedFilter(event, root.query)
      root.resetCursor()
      return true
    }
    var text = String(event.text || "")
    if (!ctrl && text.length === 1 && text.charCodeAt(0) >= 32 && text.charCodeAt(0) !== 127) {
      if (root.query.length < 80) root.query = root.query + text
      root.resetCursor()
      return true
    }
    return false
  }

  // The list hangs one level deeper than it used to, inside the card, so its rows are
  // not built by the time the list is asked to show them — an opened picker would spend
  // a frame with the filter's first match not there yet. Laying it out at once keeps the
  // row the cursor sits on real from the moment the list has content.
  function relayout() {
    if (list && typeof list.forceLayout === "function") list.forceLayout()
  }

  onRowsChanged: {
    if (!root.selectable(root.cursor)) root.resetCursor()
    root.relayout()
  }
  onCursorChanged: if (list.count > 0 && root.cursor >= 0) list.positionViewAtIndex(root.cursor, ListView.Contain)

  // A dropdown, not a full-card sheet: the view stays visible behind it, dimmed just
  // enough that the card in front reads as the thing with the keyboard. Clicking off
  // the card is the way out, the way it is for any list you drop open.
  Rectangle {
    anchors.fill: parent
    color: Util.alpha(root.theme.surface, 0.55)

    MouseArea {
      anchors.fill: parent
      acceptedButtons: Qt.AllButtons
      onClicked: root.close()
      onWheel: function (wheel) { wheel.accepted = true }
    }
  }

  // The list itself: a card hanging off the model row, bordered like every other popup
  // this shell draws, with its own surface so the dimmed view never shows through it.
  BorderSurface {
    id: card
    x: root.cardX
    y: root.cardY
    width: root.cardWidth
    // cardHeight already fits what the card has room for; clamping it again here is the
    // second opinion that used to cut rows off the bottom of the list.
    height: root.cardHeight
    radius: Style.cornerRadius
    color: root.theme.surface
    borderSpec: Border.controlSpec("normal", root.theme.fg, root.theme.accent)

    // Clicks belong to the card, not to the scrim that closes on them.
    MouseArea {
      anchors.fill: parent
      acceptedButtons: Qt.AllButtons
      onWheel: function (wheel) { wheel.accepted = true }
    }

  Item {
    id: head
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: parent.top
    anchors.leftMargin: Style.space(12)
    anchors.rightMargin: Style.space(12)
    anchors.topMargin: Style.space(4)
    height: Style.space(28)

    AgentMark {
      id: headMark
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      theme: root.theme
      agent: root.harness
      size: Style.space(16)
    }

    Text {
      anchors.left: headMark.right
      anchors.leftMargin: Style.space(8)
      anchors.right: backButton.left
      anchors.rightMargin: Style.space(12)
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: "Pick a model for " + Model.harnessName(root.harness)
      color: root.theme.strong
      elide: Text.ElideRight
      maximumLineCount: 1
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
      glyph: "󰁍"   // md-arrow_left U+F004D
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
    anchors.leftMargin: Style.space(12)
    anchors.rightMargin: Style.space(12)
    anchors.topMargin: Style.space(6)
    theme: root.theme
    text: root.query
    placeholder: "Type to filter models"
    active: root.active
    trailing: root.loading ? "Reading models…" : (root.result ? root.modelCount + (root.modelCount === 1 ? " model" : " models") : "")
  }

  // Loading and failure say so in words; Agent default stays below either way.
  Text {
    id: statusLine
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: search.bottom
    anchors.leftMargin: Style.space(12)
    anchors.rightMargin: Style.space(12)
    anchors.topMargin: Style.space(6)
    visible: root.waiting || root.reasonText !== ""
    textFormat: Text.PlainText
    text: root.waiting ? "Reading models…"
      : root.reasonText + (root.harness === "pi" ? "" : " Agent default still works.")
    color: root.waiting ? root.theme.soft : root.theme.readable
    elide: Text.ElideRight
    maximumLineCount: 1
    font.family: root.theme.fontFamily
    font.pixelSize: root.theme.type.body
  }

  ListView {
    id: list
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: statusLine.visible ? statusLine.bottom : search.bottom
    anchors.leftMargin: Style.space(4)
    anchors.rightMargin: Style.space(4)
    anchors.topMargin: Style.space(6)
    // Straight from the number the card sized itself with. Deriving it from where the
    // foot landed would settle a frame later, and a list with no height yet builds no
    // rows at all for that frame.
    height: root.listHeight
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    // Eleven rows is the whole list for most agents, so build a few screens' worth at
    // once: scrolling a picked-open list never waits for rows, and the rows a filter
    // lands on exist the moment the filter does.
    cacheBuffer: Math.round(root.rowHeight * 24)
    model: root.rows

    delegate: Item {
      id: row
      required property var modelData
      required property int index
      readonly property bool group: row.modelData.kind === "group"
      readonly property bool cursorHere: root.cursor === row.index && !row.group
      readonly property bool current: root.isCurrent(row.modelData)
      width: list.width
      height: Style.space(34)

      CursorSurface {
        anchors.fill: parent
        visible: !row.group
        hasCursor: row.cursorHere
        foreground: root.theme.fg
        accent: root.theme.accent
      }

      // A provider heading: the label role, in the soft ink, sitting on the rows below.
      Text {
        anchors.left: parent.left
        anchors.leftMargin: Style.space(12)
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Style.space(6)
        visible: row.group
        textFormat: Text.PlainText
        text: row.modelData.label
        color: root.theme.soft
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.label
        font.bold: true
        font.letterSpacing: root.theme.type.tracking
      }

      Text {
        id: checkGlyph
        anchors.left: parent.left
        anchors.leftMargin: Style.space(12)
        anchors.verticalCenter: parent.verticalCenter
        width: Style.space(16)
        visible: !row.group
        textFormat: Text.PlainText
        text: row.current ? "󰄬" : ""   // md-check U+F012C
        color: root.theme.okInk
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.glyph
      }

      Text {
        id: rowLabel
        anchors.left: checkGlyph.right
        anchors.leftMargin: Style.space(8)
        anchors.verticalCenter: parent.verticalCenter
        width: Math.min(implicitWidth, row.width * 0.45)
        visible: !row.group
        textFormat: Text.PlainText
        text: row.modelData.label
        color: row.cursorHere ? root.theme.fg : root.theme.strong
        elide: Text.ElideRight
        maximumLineCount: 1
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.body
      }

      Text {
        anchors.left: rowLabel.right
        anchors.leftMargin: Style.space(10)
        anchors.right: tail.left
        anchors.rightMargin: Style.space(10)
        anchors.verticalCenter: parent.verticalCenter
        visible: !row.group
        textFormat: Text.PlainText
        // What the row resolves to when the helper knows ("newest: Claude Opus 5"), else the
        // exact value passed to --model when the label is not already it.
        text: row.modelData.kind === "default" ? "no --model flag"
          : (row.modelData.note !== "" ? row.modelData.note
            : (row.modelData.label !== row.modelData.id ? row.modelData.id : ""))
        color: root.theme.soft
        elide: Text.ElideMiddle
        maximumLineCount: 1
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.meta
        // Coding fonts join "--" into one long dash; a CLI flag must read as typed.
        font.features: ({ "liga": 0, "calt": 0 })
      }

      Row {
        id: tail
        anchors.right: parent.right
        anchors.rightMargin: Style.space(12)
        anchors.verticalCenter: parent.verticalCenter
        visible: !row.group
        spacing: Style.space(8)

        Text {
          anchors.verticalCenter: parent.verticalCenter
          visible: row.modelData.isDefault === true
          textFormat: Text.PlainText
          text: "default"
          color: root.theme.accentInk
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
        }

        Rectangle {
          id: badgePill
          readonly property color ink: row.modelData.badgeTone === "ok" ? root.theme.okInk
            : (row.modelData.badgeTone === "warn" ? root.theme.warnInk
              : (row.modelData.badgeTone === "accent" ? root.theme.accentInk : root.theme.readable))
          anchors.verticalCenter: parent.verticalCenter
          visible: row.modelData.badge !== ""
          width: badgeText.implicitWidth + Style.space(12)
          height: Style.space(18)
          radius: height / 2
          color: Util.alpha(badgePill.ink, 0.14)
          border.width: 1
          border.color: Util.alpha(badgePill.ink, 0.55)

          Text {
            id: badgeText
            anchors.centerIn: parent
            textFormat: Text.PlainText
            text: row.modelData.badge
            color: badgePill.ink
            font.family: root.theme.fontFamily
            font.pixelSize: root.theme.type.meta
          }
        }
      }

      MouseArea {
        anchors.fill: parent
        enabled: !row.group
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onEntered: root.cursor = row.index
        onClicked: {
          root.cursor = row.index
          root.pickRow(row.modelData)
        }
      }
    }

    // The fact, then the way back, in two inks.
    Row {
      anchors.left: parent.left
      anchors.leftMargin: Style.space(36)
      y: Style.space(12)
      width: parent.width - Style.space(48)
      visible: root.noMatch
      spacing: Style.space(7)

      Text {
        width: Math.max(0, Math.min(implicitWidth, parent.width - recoverText.implicitWidth - parent.spacing))
        textFormat: Text.PlainText
        text: "No models match \"" + root.query + "\"."
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

  // What is not on screen, and the keys that move it, on the card's own bottom edge.
  Item {
    id: foot
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.bottom: parent.bottom
    height: Style.space(22)

    Rectangle {
      anchors.top: parent.top
      anchors.left: parent.left
      anchors.right: parent.right
      height: Math.max(1, Style.space(1))
      color: root.theme.faint
    }

    Text {
      anchors.left: parent.left
      anchors.leftMargin: Style.space(12)
      anchors.verticalCenter: parent.verticalCenter
      anchors.right: footKeys.left
      anchors.rightMargin: Style.space(10)
      textFormat: Text.PlainText
      text: root.footLine
      color: root.theme.soft
      elide: Text.ElideRight
      maximumLineCount: 1
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
      font.features: root.theme.type.digits
    }

    Text {
      id: footKeys
      anchors.right: parent.right
      anchors.rightMargin: Style.space(12)
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: "⏎ pick   ⌃R refresh   Esc back"
      color: root.theme.soft
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
    }
  }
  }
}
