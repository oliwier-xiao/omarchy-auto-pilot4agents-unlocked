pragma ComponentBehavior: Bound
import QtQuick
import qs.Commons
import "../lib/Model.js" as Model

// Every window source the helper read, in full (FEATURE-LIMITS R7): each window's
// meter, percent, reset time and where the figure came from; how old each reading
// is; sources that report no window; records that could not be read, with the reason.
// These are records other tools keep. This plugin reads them and never collects, and
// no token count is ever shown.
//
// A window that resets within 8 days offers "Run at reset": Compose opens with that
// time and that agent picked (the panel does the switch).
Item {
  id: root

  required property var theme
  required property var service

  property bool active: false

  readonly property string hints: root.actions.length > 0
    ? "↑/↓ move  ·  Enter run at reset  ·  Esc back"
    : "↑/↓ scroll  ·  Esc back"

  signal closed()
  signal noticeRequested(string text, string kind, var undo)
  signal runAtRequested(double fireAtSec, string harness)

  property int _cursor: 0

  readonly property double nowMs: root.service ? Number(root.service.nowMs) || Date.now() : Date.now()
  readonly property var providers: root.service && Array.isArray(root.service.providers) ? root.service.providers : []
  readonly property int marginSec: root.service && root.service.settings && typeof root.service.settings.resetMarginSec === "number"
    ? root.service.settings.resetMarginSec : 120

  readonly property var reasonWords: ({
    too_large: "the record is larger than 64 KiB.",
    invalid: "the record is not valid.",
    id_mismatch: "its id does not match its file name.",
    schema: "its schema version is not supported.",
    refused: "the file failed a safety check."
  })

  function resetLine(w) {
    if (w.sliding === true) return "starts when first used"
    if (typeof w.resetsAt !== "number") return "no reset time"
    var ms = w.resetsAt * 1000
    return "resets " + Model.clockOrDay(ms, root.nowMs) + (ms > root.nowMs ? ", in " + Model.formatDuration(ms - root.nowMs) : "")
  }

  // [{id, name, tier, harness, status, statusTone, note, windows: [{key, shortLabel, percent, over, tone,
  //   percentText, resetLine, sourceWord, actionIndex, fireAtSec}]}]
  readonly property var sections: {
    var out = []
    var action = 0
    var nowSec = root.nowMs / 1000
    var list = root.providers
    for (var i = 0; i < list.length; i++) {
      var p = list[i]
      if (!p || typeof p.id !== "string") continue
      var harness = Model.providerHarness(p)
      var status = "", tone = "soft", note = ""
      if (p.readable !== true) {
        var why = typeof p.unreadableReason === "string" && root.reasonWords.hasOwnProperty(p.unreadableReason)
          ? root.reasonWords[p.unreadableReason] : "the record could not be read."
        status = "Could not be read: " + why
        tone = "warn"
      } else if (p.source === "computed") {
        status = "computed from the clock"
      } else if (p.stale === true) {
        status = typeof p.ageSec === "number" ? "usage " + Model.formatAge(p.ageSec) + " old" : "usage age unknown"
        tone = "warn"
      } else if (typeof p.ageSec === "number") {
        status = p.ageSec < 60 ? "updated just now" : "updated " + Model.formatAge(p.ageSec) + " ago"
      }
      if (p.keptFromLastPoll === true) status += status === "" ? "kept from the last reading" : ", kept from the last reading"
      var windows = []
      var raw = p.readable === true && Array.isArray(p.windows) ? p.windows : []
      for (var j = 0; j < raw.length; j++) {
        var w = raw[j]
        if (!w) continue
        var percent = typeof w.percent === "number" && isFinite(w.percent) ? w.percent : null
        var over = w.over === true || (percent !== null && percent > 1)
        var runnable = harness !== "" && w.bindable === true && w.sliding !== true && typeof w.resetsAt === "number"
          && w.resetsAt > nowSec && w.resetsAt - nowSec <= 8 * 86400
        windows.push({
          key: String(w.key || (p.id + ":" + j)),
          shortLabel: typeof w.shortLabel === "string" && w.shortLabel !== "" ? w.shortLabel : String(p.name || p.id),
          percent: percent, over: over, tone: Model.meterTone(percent, over),
          percentText: percent === null ? "" : Model.percentText(percent, over),
          resetLine: root.resetLine(w),
          sourceWord: w.source === "computed" ? "computed" : "record",
          actionIndex: runnable ? action : -1,
          fireAtSec: runnable ? Math.round(w.resetsAt) + root.marginSec : 0
        })
        if (runnable) action++
      }
      if (p.readable === true && windows.length === 0) note = "No limits reported."
      var statusText = typeof p.statusText === "string" ? p.statusText : ""
      out.push({
        id: p.id, name: typeof p.name === "string" && p.name !== "" ? p.name : p.id,
        tier: p.readable === true && typeof p.tier === "string" ? p.tier : "",
        harness: harness, status: status, statusTone: tone,
        note: note !== "" && statusText !== "" ? note + " " + statusText : (note !== "" ? note : statusText),
        windows: windows
      })
    }
    return out
  }

  readonly property var actions: {
    var out = []
    for (var i = 0; i < root.sections.length; i++) {
      var ws = root.sections[i].windows
      for (var j = 0; j < ws.length; j++) {
        if (ws[j].actionIndex >= 0) out.push({ fireAtSec: ws[j].fireAtSec, harness: root.sections[i].harness, key: ws[j].key })
      }
    }
    return out
  }

  function open(args) {
    root._cursor = 0
    flick.contentY = 0
    if (root.service && typeof root.service.refreshUsage === "function") root.service.refreshUsage()
  }

  // `active` and `visible` belong to the panel (CONTRACT 6.6): the sheet only asks to be closed.
  function close() {
    root.closed()
  }

  function runAt(index) {
    if (index < 0 || index >= root.actions.length) return
    var a = root.actions[index]
    root.runAtRequested(a.fireAtSec, a.harness)
  }

  property var _rowItems: ({})

  function ensureVisible(index) {
    var item = root._rowItems[index]
    if (!item) return
    var p = item.mapToItem(flick.contentItem, 0, 0)
    if (p.y < flick.contentY) flick.contentY = Math.max(0, p.y - Style.space(8))
    else if (p.y + item.height > flick.contentY + flick.height)
      flick.contentY = Math.min(Math.max(0, flick.contentHeight - flick.height), p.y + item.height - flick.height + Style.space(8))
  }

  function scrollBy(dy) {
    flick.contentY = Math.max(0, Math.min(Math.max(0, flick.contentHeight - flick.height), flick.contentY + dy))
  }

  function handleKey(event) {
    if (!root.active) return false
    var k = event.key
    var mods = event.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier | Qt.MetaModifier)
    if (k === Qt.Key_Escape) { root.close(); return true }
    if (mods !== 0) return false
    var n = root.actions.length
    if (k === Qt.Key_Up || k === Qt.Key_K) {
      if (n > 0) { root._cursor = Math.max(0, root._cursor - 1); root.ensureVisible(root._cursor) }
      else root.scrollBy(-Style.space(28))
      return true
    }
    if (k === Qt.Key_Down || k === Qt.Key_J) {
      if (n > 0) { root._cursor = Math.min(n - 1, root._cursor + 1); root.ensureVisible(root._cursor) }
      else root.scrollBy(Style.space(28))
      return true
    }
    if (k === Qt.Key_PageUp) { root.scrollBy(-flick.height * 0.8); return true }
    if (k === Qt.Key_PageDown) { root.scrollBy(flick.height * 0.8); return true }
    if (k === Qt.Key_Return || k === Qt.Key_Enter) { root.runAt(root._cursor); return true }
    return false
  }

  implicitWidth: Style.space(1100)
  implicitHeight: Style.space(600)

  // The sheet covers the view it opened over.
  Rectangle {
    anchors.fill: parent
    color: root.theme.surface
  }

  MouseArea {
    anchors.fill: parent
    acceptedButtons: Qt.AllButtons
    onWheel: function (wheel) { wheel.accepted = false }
  }

  Column {
    id: head
    x: Style.space(24)
    y: Style.space(14)
    width: Math.max(0, root.width - Style.space(48))
    spacing: Style.space(4)

    // The same title row as Settings and the pickers: the name, and a way back for the mouse.
    Item {
      width: parent.width
      height: Style.space(28)

      Text {
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText
        text: "Limits"
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

    Text {
      width: Math.min(parent.width, root.theme.type.measure)
      textFormat: Text.PlainText
      text: "Usage records other tools keep, read as they are. Nothing here collects them."
      color: root.theme.readable
      wrapMode: Text.WordWrap
      maximumLineCount: 2
      elide: Text.ElideRight
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.body
    }
  }

  Text {
    x: Style.space(24)
    anchors.top: head.bottom
    anchors.topMargin: Style.space(18)
    visible: root.sections.length === 0
    textFormat: Text.PlainText
    text: root.service && root.service.loadingUsage === true ? "Reading limits…" : "No usage records were found."
    color: root.theme.soft
    font.family: root.theme.fontFamily
    font.pixelSize: root.theme.type.body
  }

  Flickable {
    id: flick
    anchors.top: head.bottom
    anchors.topMargin: Style.space(14)
    anchors.bottom: parent.bottom
    anchors.bottomMargin: Style.space(8)
    x: Style.space(16)
    width: Math.max(0, root.width - Style.space(32))
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    contentWidth: width
    contentHeight: sectionsColumn.implicitHeight

    Column {
      id: sectionsColumn
      width: flick.width
      spacing: Style.space(12)

      Repeater {
        model: root.sections

        Column {
          id: section
          required property var modelData
          readonly property color ink: section.modelData.harness !== "" ? root.theme.harnessInk(section.modelData.harness) : root.theme.readable
          width: sectionsColumn.width
          spacing: Style.space(3)

          Item {
            width: parent.width
            height: Style.space(24)

            AgentMark {
              id: sectionMark
              x: Style.space(8)
              anchors.verticalCenter: parent.verticalCenter
              visible: section.modelData.harness !== ""
              theme: root.theme
              agent: section.modelData.harness
              size: Style.space(14)
            }

            Text {
              id: sectionName
              x: Style.space(30)
              anchors.verticalCenter: parent.verticalCenter
              width: Math.min(implicitWidth, Style.space(260))
              textFormat: Text.PlainText
              text: section.modelData.name
              color: section.ink
              elide: Text.ElideRight
              maximumLineCount: 1
              font.family: root.theme.fontFamily
              font.pixelSize: root.theme.type.body
              font.bold: true
            }

            Text {
              anchors.left: sectionName.right
              anchors.leftMargin: Style.space(8)
              anchors.verticalCenter: parent.verticalCenter
              width: Math.max(0, Math.min(implicitWidth, statusText.x - x - Style.space(12)))
              visible: section.modelData.tier !== ""
              textFormat: Text.PlainText
              text: section.modelData.tier
              color: root.theme.soft
              elide: Text.ElideRight
              maximumLineCount: 1
              font.family: root.theme.fontFamily
              font.pixelSize: root.theme.type.meta
            }

            Text {
              id: statusText
              anchors.right: parent.right
              anchors.rightMargin: Style.space(8)
              anchors.verticalCenter: parent.verticalCenter
              width: Math.min(implicitWidth, parent.width * 0.55)
              textFormat: Text.PlainText
              text: section.modelData.status
              color: section.modelData.statusTone === "warn" ? root.theme.warnInk : root.theme.soft
              horizontalAlignment: Text.AlignRight
              elide: Text.ElideRight
              maximumLineCount: 1
              font.family: root.theme.fontFamily
              font.pixelSize: root.theme.type.meta
            }
          }

          Text {
            x: Style.space(30)
            width: Math.max(0, parent.width - Style.space(38))
            visible: section.modelData.note !== ""
            textFormat: Text.PlainText
            text: section.modelData.note
            color: root.theme.readable
            elide: Text.ElideRight
            maximumLineCount: 1
            font.family: root.theme.fontFamily
            font.pixelSize: root.theme.type.meta
          }

          Repeater {
            model: section.modelData.windows

            Item {
              id: windowRow
              required property var modelData
              readonly property bool hasCursor: windowRow.modelData.actionIndex >= 0 && windowRow.modelData.actionIndex === root._cursor
              width: section.width
              height: Style.space(30)

              Component.onCompleted: {
                if (windowRow.modelData.actionIndex >= 0) {
                  var map = root._rowItems
                  map[windowRow.modelData.actionIndex] = windowRow
                  root._rowItems = map
                }
              }

              Rectangle {
                anchors.fill: parent
                radius: Style.cornerRadius
                color: windowRow.hasCursor ? Util.alpha(root.theme.fg, 0.08) : "transparent"
                border.width: windowRow.hasCursor ? 1 : 0
                border.color: root.theme.accent
              }

              Text {
                id: windowLabel
                x: Style.space(30)
                anchors.verticalCenter: parent.verticalCenter
                width: Style.space(130)
                textFormat: Text.PlainText
                text: windowRow.modelData.shortLabel
                color: root.theme.readable
                elide: Text.ElideRight
                maximumLineCount: 1
                font.family: root.theme.fontFamily
                font.pixelSize: root.theme.type.data
              }

              Meter {
                id: windowMeter
                anchors.left: windowLabel.right
                anchors.leftMargin: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                width: Style.space(160)
                theme: root.theme
                visible: windowRow.modelData.percent !== null
                value: windowRow.modelData.percent !== null ? windowRow.modelData.percent : -1
                warning: windowRow.modelData.tone === "warn"
                alarming: windowRow.modelData.tone === "bad"
                fill: root.theme.harnessFill(section.modelData.harness)
              }

              Text {
                id: windowPercent
                anchors.left: windowMeter.right
                anchors.leftMargin: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                width: Style.space(44)
                textFormat: Text.PlainText
                text: windowRow.modelData.percentText
                color: windowRow.modelData.tone === "bad" ? root.theme.badInk
                  : windowRow.modelData.tone === "warn" ? root.theme.warnInk : root.theme.strong
                horizontalAlignment: Text.AlignRight
                font.family: root.theme.fontFamily
                font.pixelSize: root.theme.type.data
                font.features: root.theme.type.digits
              }

              Text {
                anchors.left: windowPercent.right
                anchors.leftMargin: Style.space(14)
                anchors.verticalCenter: parent.verticalCenter
                width: Math.max(0, (runButton.visible ? runButton.x : sourceText.x) - x - Style.space(12))
                textFormat: Text.PlainText
                text: windowRow.modelData.resetLine
                color: root.theme.readable
                elide: Text.ElideRight
                maximumLineCount: 1
                font.family: root.theme.fontFamily
                font.pixelSize: root.theme.type.meta
                font.features: root.theme.type.digits
              }

              Text {
                id: sourceText
                anchors.right: parent.right
                anchors.rightMargin: Style.space(8)
                anchors.verticalCenter: parent.verticalCenter
                visible: !runButton.visible
                textFormat: Text.PlainText
                text: windowRow.modelData.sourceWord
                color: root.theme.soft
                font.family: root.theme.fontFamily
                font.pixelSize: root.theme.type.meta
              }

              ActionButton {
                id: runButton
                anchors.right: parent.right
                anchors.rightMargin: Style.space(4)
                anchors.verticalCenter: parent.verticalCenter
                visible: windowRow.modelData.actionIndex >= 0
                theme: root.theme
                text: "Run at reset"
                hasCursor: windowRow.hasCursor || runButton.hovered
                onClicked: {
                  root._cursor = windowRow.modelData.actionIndex
                  root.runAt(windowRow.modelData.actionIndex)
                }
              }
            }
          }
        }
      }
    }
  }
}
