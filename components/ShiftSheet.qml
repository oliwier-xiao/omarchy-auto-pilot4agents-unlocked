pragma ComponentBehavior: Bound
import QtQuick
import qs.Commons
import "../lib/Model.js" as Model

// "Shift by..." (R6 4.1): moves a set of armed jobs by the same amount in one helper
// call, which either moves all of them or none. The sheet shows every job's time
// before and after, and says which new times the helper would refuse before you ask.
// Enter applies; the notice row offers Undo, which shifts back by the same amount.
Item {
  id: root

  required property var theme
  required property var service

  property bool active: false

  readonly property string hints: root._working
    ? "Shifting…  ·  Esc back"
    : "←/→ amount  ·  ↑/↓ scroll  ·  Enter shift  ·  Esc back"

  signal noticeRequested(string text, string kind, var undo)
  signal closed()
  signal shifted(var ids, int deltaSec)

  readonly property var amounts: [300, 900, 1800, 3600, 18000, -300, -900]

  property var _ids: []
  property int _chip: 1
  property bool _working: false
  property string _error: ""

  readonly property int deltaSec: root.amounts[Math.max(0, Math.min(root.amounts.length - 1, root._chip))]
  readonly property double nowMs: root.service ? root.service.nowMs : Date.now()

  function amountLabel(sec) {
    var a = Math.abs(sec)
    var body = a % 3600 === 0 ? (a / 3600) + "h" : Math.round(a / 60) + "m"
    return (sec < 0 ? "-" : "+") + body
  }

  // [{id, label, harness, before, after, problem}]; problem "" | "not armed" |
  // "too early" | "too far".
  readonly property var rows: {
    var out = []
    var byId = root.service && root.service.jobsById ? root.service.jobsById : ({})
    var nowSec = Math.floor(root.nowMs / 1000)
    for (var i = 0; i < root._ids.length; i++) {
      var id = root._ids[i]
      var j = byId[id]
      var armed = !!j && j.state && j.state.status === "armed" && typeof j.state.fireAt === "number"
      var before = armed ? j.state.fireAt : null
      var after = armed ? before + root.deltaSec : null
      var problem = ""
      if (!armed) problem = "not armed"
      else if (after < nowSec + 60) problem = "too early"
      else if (after > nowSec + 691200) problem = "too far"
      out.push({ id: id, label: j ? String(j.label || "") : id, harness: j ? String(j.harness || "") : "",
                 before: before, after: after, problem: problem })
    }
    return out
  }

  readonly property var validIds: root.rows.filter(function (r) { return r.problem !== "not armed" }).map(function (r) { return r.id })
  readonly property bool blocked: root.rows.some(function (r) { return r.problem === "too early" || r.problem === "too far" })
  readonly property bool canApply: !root._working && root.validIds.length > 0 && !root.blocked

  function open(args) {
    var ids = args && Array.isArray(args.ids) ? args.ids : []
    var seen = {}
    var clean = []
    for (var i = 0; i < ids.length && clean.length < 50; i++) {
      var id = String(ids[i])
      if (/^[0-9a-f]{16}$/.test(id) && seen[id] !== true) { seen[id] = true; clean.push(id) }
    }
    root._ids = clean
    root._chip = 1
    root._error = ""
    root._working = false
    list.contentY = 0
  }

  // `active` and `visible` belong to the panel (CONTRACT 6.6): the sheet only asks
  // to be closed, as the session and settings sheets do.
  function close() {
    root.closed()
  }

  function apply() {
    if (!root.canApply || !root.service) return
    var ids = root.validIds.slice()
    var delta = root.deltaSec
    var single = ids.length === 1 ? root.service.jobsById[ids[0]] : null
    root._working = true
    root._error = ""
    root.service.shift(ids, delta, function (res) {
      root._working = false
      if (res.ok !== true) {
        if (root.active) root._error = String(res.message || "")
        else root.noticeRequested(String(res.message || ""), "error", null)
        return
      }
      var text = ids.length === 1 && single
        ? "Shifted \"" + String(single.label || "") + "\" by " + root.amountLabel(delta) + "."
        : "Shifted " + ids.length + " jobs by " + root.amountLabel(delta) + "."
      var used = false
      root.noticeRequested(text, "ok", function () {
        if (used) return
        used = true
        root.service.shift(ids, -delta, function (back) {
          if (back.ok === true) root.noticeRequested("Shift undone.", "ok", null)
          else root.noticeRequested(String(back.message || ""), "error", null)
        })
      })
      root.shifted(ids, delta)
      root.close()
    })
  }

  function handleKey(event) {
    if (!root.active) return false
    var k = event.key
    var mods = event.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier | Qt.MetaModifier)
    if (k === Qt.Key_Escape) { root.close(); return true }
    if (root._working) return true
    if (mods === 0 || mods === Qt.ShiftModifier) {
      if (k === Qt.Key_Left || k === Qt.Key_H) { root._chip = Math.max(0, root._chip - 1); root._error = ""; return true }
      if (k === Qt.Key_Right || k === Qt.Key_L) { root._chip = Math.min(root.amounts.length - 1, root._chip + 1); root._error = ""; return true }
      if (k === Qt.Key_Up || k === Qt.Key_K) { list.contentY = Math.max(0, list.contentY - Style.space(28)); return true }
      if (k === Qt.Key_Down || k === Qt.Key_J) {
        list.contentY = Math.max(0, Math.min(list.contentHeight - list.height, list.contentY + Style.space(28)))
        return true
      }
      if (k === Qt.Key_Return || k === Qt.Key_Enter) { root.apply(); return true }
    }
    return false
  }
  implicitWidth: Style.space(1100)
  implicitHeight: Style.space(600)

  // Modal ground over the view area; swallows the pointer too.
  Rectangle {
    anchors.fill: parent
    color: root.theme.surface

    MouseArea {
      anchors.fill: parent
      acceptedButtons: Qt.AllButtons
      onWheel: function (wheel) { wheel.accepted = true }
    }
  }

  // Same gutter and head height as the other sheets, so the title sits where theirs do.
  Column {
    id: top
    x: 0
    y: 0
    width: root.width
    spacing: Style.space(10)

    Item {
      width: parent.width
      height: Style.space(28)

      Text {
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText
        text: (root._ids.length === 1 ? "Shift 1 job by " : "Shift " + root._ids.length + " jobs by ") + root.amountLabel(root.deltaSec)
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

    Row {
      spacing: Style.space(4)

      Repeater {
        model: root.amounts

        Chip {
          required property var modelData
          required property int index
          theme: root.theme
          text: root.amountLabel(modelData)
          selected: root._chip === index
          hasCursor: root._chip === index || hovered
          tint: modelData < 0 ? root.theme.warnInk : root.theme.accent
          onClicked: { root._chip = index; root._error = "" }
        }
      }
    }

    Text {
      textFormat: Text.PlainText
      text: "Every job moves by the same amount. Jobs bound to a reset stop following it."
      color: root.theme.soft
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
    }
  }

  ListView {
    id: list
    x: 0
    anchors.top: top.bottom
    anchors.topMargin: Style.space(10)
    // As tall as its rows, so the Shift button sits right under the jobs it moves.
    height: Math.max(0, Math.min(contentHeight, root.height - list.y - foot.height - Style.space(14) - Style.space(12)))
    width: root.width
    clip: true
    boundsBehavior: Flickable.StopAtBounds
    model: root.rows

    delegate: Item {
      id: line
      required property var modelData
      width: list.width
      height: Style.space(28)
      // Both times on one day: the day is said once, before them.
      readonly property bool sameDay: line.modelData.before !== null && line.modelData.after !== null
        && Model.dayKey(line.modelData.before * 1000) === Model.dayKey(line.modelData.after * 1000)

      HarnessRail {
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        height: Style.space(18)
        theme: root.theme
        harness: line.modelData.harness
      }

      AgentMark {
        x: Style.space(10)
        anchors.verticalCenter: parent.verticalCenter
        theme: root.theme
        agent: line.modelData.harness
        size: Style.space(12)
      }

      Text {
        x: Style.space(30)
        anchors.verticalCenter: parent.verticalCenter
        width: Math.max(0, line.width - x - times.width - Style.space(20))
        textFormat: Text.PlainText
        text: line.modelData.label
        color: root.theme.strong
        elide: Text.ElideRight
        maximumLineCount: 1
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.data
      }

      Row {
        id: times
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        spacing: Style.space(10)

        Text {
          anchors.verticalCenter: parent.verticalCenter
          visible: line.sameDay && Model.dayDiff(line.modelData.before * 1000, root.nowMs) !== 0
          textFormat: Text.PlainText
          text: line.sameDay ? Model.dateText(line.modelData.before * 1000) : ""
          color: root.theme.soft
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
        }

        Text {
          anchors.verticalCenter: parent.verticalCenter
          textFormat: Text.PlainText
          text: line.modelData.before === null ? "--:--"
            : (line.sameDay ? Model.formatClock(line.modelData.before * 1000) : Model.clockOrDay(line.modelData.before * 1000, root.nowMs))
          color: root.theme.readable
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.data
          font.features: root.theme.type.digits
        }

        Text {
          anchors.verticalCenter: parent.verticalCenter
          textFormat: Text.PlainText
          text: "→"
          color: root.theme.soft
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.data
        }

        Text {
          anchors.verticalCenter: parent.verticalCenter
          textFormat: Text.PlainText
          text: line.modelData.after === null ? "--:--"
            : (line.sameDay ? Model.formatClock(line.modelData.after * 1000) : Model.clockOrDay(line.modelData.after * 1000, root.nowMs))
          color: line.modelData.problem === "" ? root.theme.fg : root.theme.badInk
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.data
          font.features: root.theme.type.digits
          font.bold: true
        }

        Text {
          anchors.verticalCenter: parent.verticalCenter
          width: Style.space(70)
          textFormat: Text.PlainText
          text: line.modelData.problem
          color: line.modelData.problem === "not armed" ? root.theme.soft : root.theme.badInk
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
        }
      }
    }
  }

  Item {
    id: foot
    x: 0
    anchors.top: list.bottom
    anchors.topMargin: Style.space(14)
    width: root.width
    height: Style.spacing.controlHeight

    Text {
      anchors.left: parent.left
      anchors.right: buttons.left
      anchors.rightMargin: Style.space(12)
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: root._error !== "" ? root._error
        : root.blocked ? "Some new times are outside the next 8 days or less than a minute away. Pick another amount."
        : root.validIds.length === 0 ? "None of these jobs is armed any more." : ""
      color: root._error !== "" || root.blocked ? root.theme.badInk : root.theme.soft
      elide: Text.ElideRight
      maximumLineCount: 1
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
    }

    Row {
      id: buttons
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.space(8)

      ActionButton {
        id: applyButton
        theme: root.theme
        primary: true
        enabled: root.canApply
        text: root._working ? "Shifting…" : "Shift " + root.amountLabel(root.deltaSec)
        shortcut: "Enter"
        hasCursor: applyButton.hovered
        onClicked: root.apply()
      }
    }
  }
}
