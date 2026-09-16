pragma ComponentBehavior: Bound

import QtQuick
import qs.Commons
import qs.Ui
import "../lib/Edition.js" as Edition
import "../lib/Model.js" as Model

// Defaults for new jobs and how the panel behaves (R6 8.7 with C11). Each change is
// one `settings-set` of that key; the service's settings are the only truth, and a
// choice waiting on the helper shows as chosen until it answers.
//
// Paid usage for new jobs starts Off: a new draft then runs on the subscription only.
// Limits in the header are Auto (fresh sources for the agents in use) or Custom: every
// source with a reading gets a row to show or hide, in the order the header uses, and
// Alt+Up/Down moves a shown one.
//
// The last row is the way out: Cancel all jobs, which disarms every job and stops
// every unit of this edition. Two presses, like every other expensive key.
Item {
  id: root

  required property var theme
  required property var service
  property bool active: false

  signal closed()
  signal noticeRequested(string text, string kind, var undo)

  readonly property var settings: root.service && root.service.settings ? root.service.settings : ({})
  readonly property var providers: root.service && Array.isArray(root.service.providers) ? root.service.providers : []
  readonly property var baseRows: ["defaultHarness", "defaultLevel", "defaultAllowPaid", "resetMarginSec", "eveningTime",
                                   "morningTime", "notify", "motion", "limitsShown"]

  // "auto", or the id array in header order (a write waiting on the helper included).
  readonly property var shownValue: {
    var v = root.current("limitsShown")
    return Array.isArray(v) ? v : "auto"
  }
  readonly property bool limitsCustom: Array.isArray(root.shownValue)

  // Custom only: the shown sources in header order, then every other source with a reading.
  readonly property var limitRows: {
    if (!root.limitsCustom) return []
    var choices = typeof Model.limitChoices === "function" ? Model.limitChoices(root.providers) : []
    var out = []
    var seen = {}
    for (var i = 0; i < root.shownValue.length; i++) {
      var id = root.shownValue[i]
      if (typeof id !== "string" || seen[id] === true) continue
      seen[id] = true
      var c = root.choiceById(choices, id)
      out.push({ id: id, name: c ? c.name : id, harness: c ? c.harness : "", shown: true, present: c !== null, position: out.length + 1 })
    }
    for (var j = 0; j < choices.length; j++) {
      if (seen[choices[j].id] === true) continue
      out.push({ id: choices[j].id, name: choices[j].name, harness: choices[j].harness, shown: false, present: true, position: 0 })
    }
    return out
  }

  readonly property var rowIds: root.baseRows.concat(root.limitRows.map(function (r) { return "limit:" + r.id })).concat(["cancelAll"])
  readonly property string rowId: root.rowIds[Math.max(0, Math.min(root.rowIds.length - 1, root.cursor))]
  readonly property string limitId: root.rowId.indexOf("limit:") === 0 ? root.rowId.slice(6) : ""

  property int cursor: 0
  property bool _cancelArmed: false
  property bool _cancelling: false
  property var _pending: ({})
  property var _pendingSeq: ({})
  property int _seq: 0

  readonly property string hints: {
    if (root._cancelArmed) return "Press Enter again to cancel every job."
    if (root.rowId === "cancelAll") return "Enter cancel all jobs  ·  ↑/↓ setting  ·  Esc back"
    if (root.limitId !== "") return "Space show or hide  ·  Alt+↑/↓ move  ·  ↑/↓ setting  ·  Esc back"
    return "↑/↓ setting  ·  ←/→ change  ·  Esc back"
  }

  readonly property var levelOptions: {
    var src = root.service && Array.isArray(root.service.levels) ? root.service.levels : []
    var out = []
    for (var i = 0; i < src.length; i++) {
      if (src[i] && Edition.LEVEL_IDS.indexOf(src[i].id) >= 0) out.push({ value: src[i].id, label: String(src[i].label || src[i].id) })
    }
    if (out.length > 0) return out
    return Edition.LEVEL_IDS.map(function (id) { return { value: id, label: Edition.LEVEL_LABELS[id] || id } })
  }

  function choiceById(choices, id) {
    for (var i = 0; i < choices.length; i++) if (choices[i].id === id) return choices[i]
    return null
  }

  function limitRow(id) {
    for (var i = 0; i < root.limitRows.length; i++) if (root.limitRows[i].id === id) return root.limitRows[i]
    return null
  }

  function timeChoices(base, current) {
    var list = base.slice()
    if (typeof current === "string" && /^([01][0-9]|2[0-3]):[0-5][0-9]$/.test(current) && list.indexOf(current) < 0) {
      list.push(current)
      list.sort()
    }
    return list.map(function (t) { return { value: t, label: t } })
  }

  function options(id) {
    if (id === "defaultHarness")
      return Edition.HARNESS_IDS.map(function (h) { return { value: h, label: Model.harnessName(h), harness: h } })
    if (id === "defaultLevel") return root.levelOptions
    if (id === "defaultAllowPaid") return [{ value: false, label: "Off" }, { value: true, label: "On" }]
    if (id === "resetMarginSec")
      return [60, 120, 300, 540].map(function (s) { return { value: s, label: "+" + Math.round(s / 60) + "m" } })
    if (id === "eveningTime") return root.timeChoices(["20:00", "21:00", "22:00", "23:00"], root.current("eveningTime"))
    if (id === "morningTime") return root.timeChoices(["06:00", "07:00", "08:00", "09:00"], root.current("morningTime"))
    if (id === "notify")
      return [{ value: "all", label: "Every run" }, { value: "failures", label: "Failures only" }, { value: "never", label: "Never" }]
    if (id === "motion") return [{ value: "full", label: "Full" }, { value: "reduced", label: "Reduced" }]
    if (id === "limitsShown") return [{ value: "auto", label: "Auto" }, { value: "custom", label: "Custom" }]
    return []
  }

  function labelFor(id) {
    switch (id) {
    case "defaultHarness": return "Default agent"
    case "defaultLevel": return "Default level"
    case "defaultAllowPaid": return "Paid usage for new jobs"
    case "resetMarginSec": return "Reset buffer"
    case "eveningTime": return "Tonight at"
    case "morningTime": return "Tomorrow at"
    case "notify": return "Notifications"
    case "motion": return "Motion"
    case "limitsShown": return "Limits in the header"
    }
    return "All jobs"
  }

  function noteFor(id) {
    switch (id) {
    case "defaultAllowPaid": return root.current(id) === true ? "new jobs may spend credits or API dollars" : "new jobs use the subscription only"
    case "resetMarginSec": return "wait after a reset before firing"
    case "motion": return "reduced keeps fades, drops movement"
    case "limitsShown": return root.limitsCustom ? "Space shows a source, Alt+↑/↓ moves it" : "fresh sources for the agents in use"
    case "cancelAll": return "disarms every job and stops its timer"
    }
    return ""
  }

  function current(id) {
    var v = root._pending.hasOwnProperty(id) ? root._pending[id] : root.settings[id]
    if (id === "defaultAllowPaid") return v === true
    if (id === "limitsShown") return Array.isArray(v) ? v : "auto"
    return v
  }

  // The value a choice chip compares with.
  function choiceValue(id) {
    if (id === "limitsShown") return root.limitsCustom ? "custom" : "auto"
    return root.current(id)
  }

  function withKey(obj, key, value) {
    var next = {}
    for (var k in obj) next[k] = obj[k]
    next[key] = value
    return next
  }

  function withoutKey(obj, key) {
    var next = {}
    for (var k in obj) if (k !== key) next[k] = obj[k]
    return next
  }

  // Shows `value` at once and forgets it when this write (not an older one) answers.
  function markPending(id, value) {
    root._seq = root._seq + 1
    var seq = root._seq
    root._pending = root.withKey(root._pending, id, value)
    root._pendingSeq = root.withKey(root._pendingSeq, id, seq)
    return function (res) {
      if (root._pendingSeq[id] === seq) {
        root._pending = root.withoutKey(root._pending, id)
        root._pendingSeq = root.withoutKey(root._pendingSeq, id)
      }
      if (!res || res.ok !== true) root.noticeRequested(res && res.message ? res.message : "The helper did not answer. Try again.", "error", null)
    }
  }

  function choose(id, value) {
    if (id === "limitsShown") {
      root.chooseLimits(value)
      return
    }
    if (!root.service || typeof root.service.setSettings !== "function" || root.current(id) === value) return
    var patch = {}
    patch[id] = value
    root.service.setSettings(patch, root.markPending(id, value))
  }

  // Custom starts from what Auto shows right now, so switching changes nothing on screen.
  function chooseLimits(value) {
    if (value === "custom" && !root.limitsCustom) {
      var auto = typeof Model.shownSources === "function" ? Model.shownSources(root.providers, "auto") : []
      root.writeLimits(Array.isArray(auto) ? auto.slice(0, 32) : [])
    } else if (value === "auto" && root.limitsCustom) {
      root.writeLimits("auto")
    }
  }

  function writeLimits(value) {
    if (!root.service) return
    var done = root.markPending("limitsShown", value)
    if (typeof root.service.setLimitsShown === "function") root.service.setLimitsShown(value, done)
    else if (typeof root.service.setSettings === "function") root.service.setSettings({ limitsShown: value }, done)
  }

  function toggleLimit(id) {
    if (!root.limitsCustom) return
    var list = root.shownValue.slice()
    var i = list.indexOf(id)
    if (i >= 0) list.splice(i, 1)
    else if (list.length < 32) list.push(id)
    else {
      root.noticeRequested("The header shows at most 32 sources.", "warn", null)
      return
    }
    root.writeLimits(list)
    root.cursor = Math.max(0, root.rowIds.indexOf("limit:" + id))
  }

  function moveLimit(id, dir) {
    if (!root.limitsCustom) return
    var list = root.shownValue.slice()
    var i = list.indexOf(id)
    var j = i + dir
    if (i < 0 || j < 0 || j >= list.length) return
    list[i] = list[j]
    list[j] = id
    root.writeLimits(list)
    root.cursor = Math.max(0, root.rowIds.indexOf("limit:" + id))
  }

  function stepChoice(dir) {
    var opts = root.options(root.rowId)
    if (opts.length === 0) return
    var i = -1
    for (var n = 0; n < opts.length; n++) if (opts[n].value === root.choiceValue(root.rowId)) i = n
    var j = i < 0 ? (dir > 0 ? 0 : opts.length - 1) : Math.max(0, Math.min(opts.length - 1, i + dir))
    root.choose(root.rowId, opts[j].value)
  }

  function clearGuard() {
    root._cancelArmed = false
    guard.stop()
  }

  function cancelPress() {
    if (root._cancelling || !root.service) return
    if (!root._cancelArmed) {
      root._cancelArmed = true
      guard.restart()
      return
    }
    root.clearGuard()
    root._cancelling = true
    root.service.cancelAll(function (res) {
      root._cancelling = false
      if (!res || res.ok !== true) {
        root.noticeRequested(res && res.message ? res.message : "The helper did not answer. Try again.", "error", null)
        return
      }
      var n = Array.isArray(res.disarmed) ? res.disarmed.length : 0
      var text = n === 0 ? "Nothing was armed. No job timer is left." : (n === 1 ? "Cancelled 1 job." : "Cancelled " + n + " jobs.")
      if (res.verified === false) root.noticeRequested(text + " Some timers could not be confirmed as stopped. Run it again.", "warn", null)
      else root.noticeRequested(text, "ok", null)
    })
  }

  function open() {
    root.cursor = 0
    root.clearGuard()
    flick.contentY = 0
  }

  function close() {
    root.clearGuard()
    root.closed()
  }

  function rowHeight(id) {
    return id.indexOf("limit:") === 0 ? Style.space(32) : Style.space(40)
  }

  function ensureVisible() {
    var y = 0
    for (var i = 0; i < root.cursor && i < root.rowIds.length; i++) y += root.rowHeight(root.rowIds[i]) + rowsColumn.spacing
    var h = root.rowHeight(root.rowId)
    if (y < flick.contentY) flick.contentY = y
    else if (y + h > flick.contentY + flick.height) flick.contentY = Math.max(0, y + h - flick.height)
  }

  function handleKey(event) {
    var key = event.key
    var mods = event.modifiers
    var up = key === Qt.Key_Up || key === Qt.Key_K
    var down = key === Qt.Key_Down || key === Qt.Key_J
    if ((mods & Qt.AltModifier) !== 0 && (mods & (Qt.ControlModifier | Qt.MetaModifier)) === 0) {
      if (root.limitId !== "" && (key === Qt.Key_Up || key === Qt.Key_Down)) {
        root.clearGuard()
        root.moveLimit(root.limitId, key === Qt.Key_Up ? -1 : 1)
        return true
      }
      return false
    }
    if ((mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)) !== 0) return false
    var enter = key === Qt.Key_Return || key === Qt.Key_Enter || key === Qt.Key_Space
    var guardWas = root._cancelArmed
    if (guardWas && !(enter && root.rowId === "cancelAll")) root.clearGuard()
    if (key === Qt.Key_Escape) return guardWas
    if (up) { root.cursor = Math.max(0, root.cursor - 1); return true }
    if (down) { root.cursor = Math.min(root.rowIds.length - 1, root.cursor + 1); return true }
    if (key === Qt.Key_Home) { root.cursor = 0; return true }
    if (key === Qt.Key_End) { root.cursor = root.rowIds.length - 1; return true }
    if (root.limitId !== "") {
      if (enter) root.toggleLimit(root.limitId)
      return enter || key === Qt.Key_Left || key === Qt.Key_Right || key === Qt.Key_H || key === Qt.Key_L
    }
    if (key === Qt.Key_Left || key === Qt.Key_H) { root.stepChoice(-1); return true }
    if (key === Qt.Key_Right || key === Qt.Key_L) { root.stepChoice(1); return true }
    if (enter) {
      if (root.rowId === "cancelAll") root.cancelPress()
      else root.stepChoice(1)
      return true
    }
    return false
  }

  onActiveChanged: if (!root.active) root.clearGuard()
  onCursorChanged: root.ensureVisible()
  onRowIdsChanged: if (root.cursor > root.rowIds.length - 1) root.cursor = root.rowIds.length - 1

  Timer {
    id: guard
    interval: 3000
    repeat: false
    onTriggered: root._cancelArmed = false
  }

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
      text: "Settings"
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
      glyph: "󰁍"   // md-arrow_left U+F004D
      text: "Back"
      shortcut: "Esc"
      onClicked: root.close()
    }
  }

  Flickable {
    id: flick
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: head.bottom
    anchors.topMargin: Style.spacing.lg
    anchors.bottom: parent.bottom
    contentWidth: width
    contentHeight: sheetColumn.implicitHeight
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    interactive: contentHeight > height

    Column {
      id: sheetColumn
      width: flick.width
      spacing: Style.space(16)

      Column {
        id: rowsColumn
        width: parent.width
        spacing: Style.space(2)

        Repeater {
          model: root.rowIds

          delegate: Item {
            id: settingRow
            required property string modelData
            required property int index
            readonly property bool cursorHere: root.cursor === settingRow.index
            readonly property bool limitRowHere: settingRow.modelData.indexOf("limit:") === 0
            readonly property var limit: settingRow.limitRowHere ? root.limitRow(settingRow.modelData.slice(6)) : null
            width: rowsColumn.width
            height: root.rowHeight(settingRow.modelData)

            CursorSurface {
              anchors.fill: parent
              hasCursor: settingRow.cursorHere
              foreground: root.theme.fg
              accent: root.theme.accent
            }

            MouseArea {
              anchors.fill: parent
              hoverEnabled: true
              onEntered: root.cursor = settingRow.index
            }

            Text {
              id: rowLabel
              anchors.left: parent.left
              anchors.leftMargin: Style.spacing.rowPaddingX
              anchors.verticalCenter: parent.verticalCenter
              width: Style.space(200)
              visible: !settingRow.limitRowHere
              textFormat: Text.PlainText
              text: root.labelFor(settingRow.modelData)
              // Red lives on the button only; the row label reads like every other row.
              color: settingRow.cursorHere ? root.theme.fg : root.theme.strong
              elide: Text.ElideRight
              maximumLineCount: 1
              font.family: root.theme.fontFamily
              font.pixelSize: root.theme.type.body
            }

            Row {
              id: choices
              anchors.left: rowLabel.right
              anchors.verticalCenter: parent.verticalCenter
              visible: !settingRow.limitRowHere
              spacing: Style.spacing.sm

              Repeater {
                model: settingRow.limitRowHere ? [] : root.options(settingRow.modelData)

                delegate: Chip {
                  id: choiceChip
                  required property var modelData
                  theme: root.theme
                  pill: settingRow.modelData === "defaultHarness"
                  harness: typeof choiceChip.modelData.harness === "string" ? choiceChip.modelData.harness : ""
                  text: choiceChip.modelData.label
                  tint: settingRow.modelData === "defaultLevel" && Edition.LEVEL_TONES[choiceChip.modelData.value] === "bad"
                    ? root.theme.badInk
                    : (settingRow.modelData === "defaultLevel" && Edition.LEVEL_TONES[choiceChip.modelData.value] === "warn")
                    || (settingRow.modelData === "defaultAllowPaid" && choiceChip.modelData.value === true)
                    ? root.theme.warnInk : (choiceChip.harness !== "" ? root.theme.harnessInk(choiceChip.harness) : root.theme.accent)
                  selected: root.choiceValue(settingRow.modelData) === choiceChip.modelData.value
                  hasCursor: settingRow.cursorHere && choiceChip.selected
                  onClicked: {
                    root.cursor = settingRow.index
                    root.clearGuard()
                    root.choose(settingRow.modelData, choiceChip.modelData.value)
                  }
                }
              }

              ActionButton {
                id: cancelButton
                widthTemplate: "Press again to cancel all"
                visible: settingRow.modelData === "cancelAll"
                theme: root.theme
                danger: true
                armed: root._cancelArmed
                hasCursor: settingRow.cursorHere || cancelButton.hovered
                enabled: !root._cancelling
                text: root._cancelling ? "Cancelling…" : (root._cancelArmed ? "Press again to cancel all" : "Cancel all jobs")
                shortcut: "Enter"
                onClicked: {
                  root.cursor = settingRow.index
                  root.cancelPress()
                }
              }
            }

            Text {
              anchors.left: choices.right
              anchors.leftMargin: Style.space(14)
              anchors.right: parent.right
              anchors.rightMargin: Style.spacing.rowPaddingX
              anchors.verticalCenter: parent.verticalCenter
              visible: !settingRow.limitRowHere
              textFormat: Text.PlainText
              text: root.noteFor(settingRow.modelData)
              color: settingRow.modelData === "defaultAllowPaid" && root.current("defaultAllowPaid") === true
                ? root.theme.warnInk : root.theme.soft
              elide: Text.ElideRight
              maximumLineCount: 1
              font.family: root.theme.fontFamily
              font.pixelSize: root.theme.type.meta
            }

            // A source row of the custom header: its mark, a box to show it, its place.
            AgentMark {
              id: limitMark
              anchors.left: parent.left
              anchors.leftMargin: Style.spacing.rowPaddingX + Style.space(24)
              anchors.verticalCenter: parent.verticalCenter
              visible: settingRow.limitRowHere
              theme: root.theme
              agent: settingRow.limit ? settingRow.limit.harness : ""
              size: Style.space(14)
            }

            CheckRow {
              id: limitCheck
              anchors.left: limitMark.right
              anchors.leftMargin: Style.space(10)
              anchors.verticalCenter: parent.verticalCenter
              width: Style.space(300)
              visible: settingRow.limitRowHere
              theme: root.theme
              text: settingRow.limit ? settingRow.limit.name : ""
              checked: !!settingRow.limit && settingRow.limit.shown
              hasCursor: settingRow.cursorHere
              tint: settingRow.limit && settingRow.limit.harness !== "" ? root.theme.harnessInk(settingRow.limit.harness) : root.theme.accent
              onToggled: {
                root.cursor = settingRow.index
                if (settingRow.limit) root.toggleLimit(settingRow.limit.id)
              }
            }

            Text {
              anchors.left: limitCheck.right
              anchors.leftMargin: Style.space(14)
              anchors.right: parent.right
              anchors.rightMargin: Style.spacing.rowPaddingX
              anchors.verticalCenter: parent.verticalCenter
              visible: settingRow.limitRowHere
              textFormat: Text.PlainText
              text: !settingRow.limit ? ""
                : (!settingRow.limit.present ? "no reading now"
                  : (settingRow.limit.shown ? "chip " + settingRow.limit.position : "hidden"))
              color: settingRow.limit && settingRow.limit.shown ? root.theme.readable : root.theme.soft
              elide: Text.ElideRight
              maximumLineCount: 1
              font.family: root.theme.fontFamily
              font.pixelSize: root.theme.type.meta
              font.features: root.theme.type.digits
            }
          }
        }
      }

      Column {
        x: Style.spacing.rowPaddingX
        width: parent.width - Style.spacing.rowPaddingX * 2
        spacing: Style.space(4)

        Text {
          width: parent.width
          textFormat: Text.PlainText
          text: "Change the bar label in Omarchy's settings for this widget."
          color: root.theme.soft
          wrapMode: Text.WordWrap
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
        }

        Text {
          width: parent.width
          visible: !(root.service && root.service.listMeta && root.service.listMeta.linger === true)
          textFormat: Text.PlainText
          text: "Jobs run only while you are logged in. Missed jobs are offered when you log back in."
          color: root.theme.soft
          wrapMode: Text.WordWrap
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
        }
      }
    }
  }
}
