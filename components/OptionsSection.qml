pragma ComponentBehavior: Bound

import QtQuick
import qs.Commons
import qs.Ui
import "../lib/Edition.js" as Edition
import "../lib/Compose.js" as Compose

// The permission level, paid usage, the limits and the model of one job.
//
// The level chips come from the closed table `ap4a edition` answers with (plan and
// unattended, CONTRACT 0.1), and the caption under them is that table's own sentence
// for the chosen agent (C1). A level the table does not offer for this agent (Cursor
// and Pi are Plan only) stays visible, dimmed, with the table's reason.
//
// Allow paid usage is a real checkbox, off by default: off, a job may only draw on the
// subscription; on, it may spend credits or API dollars, and Claude gets its budget
// row. The notes under the box are the helper's gate notes for this agent and model
// (Compose.gateNotes). Max turns exist only for Claude (DC8); every agent has a run
// time. The model is picked from the agent's own list (ModelSheet), never typed.
Item {
  id: root

  required property var theme

  property string level: Edition.DEFAULT_LEVEL
  // Draft limits: only the keys the user set. Absent keys take the helper's defaults.
  property var limits: ({})
  property var model: null
  property string harness: "claude"
  // `service.levels`: LEVELS entries verbatim, `unavailable` included.
  property var levels: []
  property bool hasCursor: false
  // `service.caps`: maxTurns, budgetUsd and runtimeSec ranges.
  property var caps: ({})
  property string armHint: "Ctrl+Enter arm"
  property bool allowPaid: false
  // The last preview's gate for this agent (CONTRACT-V2-DELTA 3.5), or null.
  property var gate: null
  // The gate answers for the draft as it is now (its refusal applies).
  property bool gateCurrent: false
  // Pi's provider; "" for every other agent.
  property string provider: ""
  // `service.models`: harness -> Models result.
  property var models: ({})
  // The OpenCode billing class of the chosen model, "" when unknown.
  property string billing: ""
  property double nowMs: 0

  signal edited(var patch)
  signal focusRequested()
  // Enter on the model row, or a letter typed there (the picker's first filter text).
  signal modelSheetRequested(string query)

  // Where the model row's box sits, so the picker can hang off it the way a dropdown
  // does instead of covering the card. Measured in `space`'s coordinates — the picker
  // fills the same item the view does, so mapping to the window instead would push the
  // card down by the height of everything above the view. Null until the row exists.
  property Item modelAnchorItem: null
  function modelAnchorRect(space) {
    var it = root.modelAnchorItem
    if (!it || !it.visible || it.width <= 0) return null
    var p = it.mapToItem(space || null, 0, 0)
    if (!p) return null
    return { x: p.x, y: p.y, width: it.width, height: it.height }
  }

  readonly property var levelList: {
    var out = []
    var src = Array.isArray(root.levels) ? root.levels : []
    for (var i = 0; i < src.length; i++) {
      if (src[i] && Edition.LEVEL_IDS.indexOf(src[i].id) >= 0) out.push(src[i])
    }
    if (out.length > 0) return out
    // Before the helper answers: labels only, no caption and no default numbers.
    return Edition.LEVEL_IDS.map(function (id) {
      return { id: id, label: Edition.LEVEL_LABELS[id] || id, summary: "", defaultMaxTurns: null, harness: {} }
    })
  }

  readonly property var levelEntry: {
    for (var i = 0; i < root.levelList.length; i++) {
      if (root.levelList[i].id === root.level) return root.levelList[i]
    }
    return null
  }

  readonly property string caption: {
    var e = root.levelEntry
    var h = e && e.harness && e.harness[root.harness] ? e.harness[root.harness] : null
    if (h && typeof h.caption === "string") return h.caption
    return e && typeof e.summary === "string" ? e.summary : ""
  }

  // The table's reasons for every level it does not offer this agent, in level order.
  readonly property string unavailableLine: {
    var out = []
    for (var i = 0; i < root.levelList.length; i++) {
      var st = Compose.levelState(root.levels, root.levelList[i].id, root.harness)
      if (!st.available && st.reason !== "" && out.indexOf(st.reason) < 0) out.push(st.reason)
    }
    return out.join(" ")
  }

  readonly property bool claudeLimits: root.harness === "claude"
  readonly property var rows: {
    var out = ["level", "paid"]
    if (root.claudeLimits && root.allowPaid) out.push("budget")
    if (root.claudeLimits) out.push("turns")
    out.push("runtime")
    out.push("model")
    return out
  }
  readonly property var valueRows: root.rows.filter(function (r) { return r !== "level" && r !== "paid" })
  readonly property string rowId: root.rows[Math.max(0, Math.min(root.rows.length - 1, root._row))]

  readonly property var turnsRange: root.range("maxTurns", 1, 200)
  readonly property var budgetRange: root.range("budgetUsd", 0.1, 100)
  readonly property var runtimeRange: root.range("runtimeSec", 300, 14400)

  readonly property bool turnsSet: !!root.limits && typeof root.limits.maxTurns === "number"
  readonly property int levelTurns: root.levelEntry && typeof root.levelEntry.defaultMaxTurns === "number" ? root.levelEntry.defaultMaxTurns : 0
  readonly property int turns: root.turnsSet ? root.limits.maxTurns : root.levelTurns
  readonly property real budget: !!root.limits && typeof root.limits.budgetUsd === "number" ? root.limits.budgetUsd : 5.0
  readonly property int runtimeSec: !!root.limits && typeof root.limits.runtimeSec === "number" ? root.limits.runtimeSec : 5400

  readonly property var notes: Compose.gateNotes(root.gate, root.harness, root.allowPaid, root.budget, root.nowMs)
  readonly property var extraNotes: root.notes.slice(1)
  // A paid refusal the helper would raise at arm, in its fixed sentence. Only while the
  // box is off: checking it is the way out, and it sits right above.
  readonly property string paidError: root.gateCurrent && !root.allowPaid && !!root.gate && Compose.isPaidCode(root.gate.code)
    ? Compose.paidErrorText(root.gate.code, root.gate.detail) : ""
  readonly property var badge: root.harness === "opencode" ? Compose.badgeFor(root.billing, root.allowPaid) : ({ text: "", tone: "" })

  readonly property var modelResult: root.models && typeof root.models === "object" && root.models[root.harness]
    ? root.models[root.harness] : null
  readonly property string modelId: typeof root.model === "string" ? root.model : ""
  readonly property bool hasModel: root.modelId !== "" && (root.harness !== "pi" || root.provider !== "")
  readonly property var modelEntry: Compose.modelEntry(root.modelResult, root.harness, root.provider, root.modelId)
  readonly property string modelValue: {
    if (!root.hasModel) return root.harness === "pi" ? "Pick a model" : "Agent default"
    if (root.modelEntry && typeof root.modelEntry.label === "string" && root.modelEntry.label !== "") return root.modelEntry.label
    return root.harness === "pi" ? root.provider + "/" + root.modelId : root.modelId
  }

  readonly property bool typing: root._typed !== ""

  property int _row: 0
  property string _typed: ""
  property string _typedRow: ""
  property string _note: ""
  property string _noteKind: "soft"

  readonly property string hints: {
    if (root._typed !== "") return "Enter set  ·  Backspace edit  ·  Esc cancel"
    var tail = "↑/↓ row  ·  Tab next  ·  " + root.armHint + "  ·  Esc close"
    if (root.rowId === "level") return "←/→ level  ·  " + tail
    if (root.rowId === "paid") return (root.allowPaid ? "Space turn paid usage off" : "Space allow paid usage") + "  ·  " + tail
    if (root.rowId === "model")
      return "Enter pick a model  ·  " + (root.hasModel && root.harness !== "pi" ? "Delete agent default  ·  " : "") + tail
    return "←/→ change  ·  Shift+←/→ bigger steps  ·  0-9 type a number  ·  " + tail
  }

  function range(key, lo, hi) {
    var r = root.caps && Array.isArray(root.caps[key]) ? root.caps[key] : null
    return r && r.length === 2 && typeof r[0] === "number" && typeof r[1] === "number" ? r : [lo, hi]
  }

  function money(v) { return "$" + Number(v).toFixed(2) }

  function toneInk(tone) {
    if (tone === "warn") return root.theme.warnInk
    if (tone === "ok") return root.theme.okInk
    if (tone === "bad") return root.theme.badInk
    if (tone === "accent") return root.theme.accentInk
    if (tone === "readable") return root.theme.readable
    return root.theme.soft
  }

  function rangeNote(id) {
    if (id === "turns") return "Max turns go from " + root.turnsRange[0] + " to " + root.turnsRange[1] + "."
    if (id === "budget") return "The budget goes from " + root.money(root.budgetRange[0]) + " to " + root.money(root.budgetRange[1]) + "."
    return "The run time goes from " + Math.round(root.runtimeRange[0] / 60) + " to " + Math.round(root.runtimeRange[1] / 60) + " minutes."
  }

  function levelOffered(id) {
    return Compose.levelState(root.levels, id, root.harness).available
  }

  function setLevel(id) {
    if (Edition.LEVEL_IDS.indexOf(id) < 0 || id === root.level) return
    var st = Compose.levelState(root.levels, id, root.harness)
    if (!st.available) {
      root._note = st.reason
      root._noteKind = "warn"
      return
    }
    root._note = ""
    root.edited({ level: id })
  }

  function stepLevel(dir) {
    var i = -1
    for (var n = 0; n < root.levelList.length; n++) if (root.levelList[n].id === root.level) i = n
    for (var j = i + dir; j >= 0 && j < root.levelList.length; j += dir) {
      if (root.levelOffered(root.levelList[j].id)) {
        root.setLevel(root.levelList[j].id)
        return
      }
    }
    // Nothing further that way; a refused neighbour still says why.
    var k = i + dir
    if (k >= 0 && k < root.levelList.length) root.setLevel(root.levelList[k].id)
  }

  function togglePaid() {
    root._note = ""
    root.edited({ allowPaid: !root.allowPaid })
  }

  function setLimit(key, value) {
    var next = {}
    var src = root.limits && typeof root.limits === "object" ? root.limits : {}
    for (var k in src) next[k] = src[k]
    next[key] = value
    root.edited({ limits: next })
  }

  // One step of a value row; `big` is Shift. Clamps at the helper's range and
  // says where the range ends.
  function stepValue(dir, big) {
    var id = root.rowId
    root._note = ""
    if (id === "level") { root.stepLevel(dir); return }
    var lo, hi, next
    if (id === "turns") {
      lo = root.turnsRange[0]; hi = root.turnsRange[1]
      next = (root.turns > 0 ? root.turns : lo) + dir * (big ? 10 : 1)
      if (next < lo || next > hi) { root._note = root.rangeNote(id); next = Math.max(lo, Math.min(hi, next)) }
      root.setLimit("maxTurns", next)
    } else if (id === "budget") {
      lo = root.budgetRange[0]; hi = root.budgetRange[1]
      next = Math.round((root.budget + dir * (big ? 5 : 0.5)) * 100) / 100
      if (next < lo || next > hi) { root._note = root.rangeNote(id); next = Math.max(lo, Math.min(hi, next)) }
      root.setLimit("budgetUsd", next)
    } else if (id === "runtime") {
      lo = root.runtimeRange[0]; hi = root.runtimeRange[1]
      next = (Math.round(root.runtimeSec / 60) + dir * (big ? 30 : 5)) * 60
      if (next < lo || next > hi) { root._note = root.rangeNote(id); next = Math.max(lo, Math.min(hi, next)) }
      root.setLimit("runtimeSec", next)
    }
  }

  function commitTyping() {
    if (root._typed === "") return
    var typed = root._typed
    var id = root._typedRow
    root._typed = ""
    root._typedRow = ""
    var n = Number(typed)
    root._noteKind = "warn"
    if (!isFinite(n) || typed === ".") { root._note = root.rangeNote(id); return }
    if (id === "turns") {
      n = Math.round(n)
      if (n < root.turnsRange[0] || n > root.turnsRange[1]) { root._note = root.rangeNote(id); return }
      root._note = ""
      root.setLimit("maxTurns", n)
    } else if (id === "budget") {
      n = Math.round(n * 100) / 100
      if (n < root.budgetRange[0] || n > root.budgetRange[1]) { root._note = root.rangeNote(id); return }
      root._note = ""
      root.setLimit("budgetUsd", n)
    } else if (id === "runtime") {
      var sec = Math.round(n) * 60
      if (sec < root.runtimeRange[0] || sec > root.runtimeRange[1]) { root._note = root.rangeNote(id); return }
      root._note = ""
      root.setLimit("runtimeSec", sec)
    }
  }

  function cancelTyping() {
    root._typed = ""
    root._typedRow = ""
  }

  function moveRow(dir) {
    root.commitTyping()
    root._row = Math.max(0, Math.min(root.rows.length - 1, root._row + dir))
  }

  function focusRow(id) {
    var i = root.rows.indexOf(id)
    if (i >= 0) root._row = i
  }

  function valueText(id) {
    if (id === root._typedRow && root._typed !== "") return root._typed
    if (id === "turns") return root.turns > 0 ? String(root.turns) : "default"
    if (id === "budget") return root.money(root.budget)
    if (id === "runtime") return Math.round(root.runtimeSec / 60) + " min"
    if (id === "model") return root.modelValue
    return ""
  }

  function labelText(id) {
    if (id === "turns") return "Max turns"
    if (id === "budget") return "Budget"
    if (id === "runtime") return "Run time"
    return "Model"
  }

  function noteText(id) {
    if (id === "turns") return root.turnsSet ? "" : (Edition.LEVEL_LABELS[root.level] || "Level") + " default"
    if (id === "budget") return "Claude's estimate"
    if (id === "runtime") return "hard stop"
    if (root.harness === "pi" && !root.hasModel) return "required"
    return root.hasModel && root.modelEntry && root.modelEntry["default"] === true ? "default" : ""
  }

  function handleKey(event) {
    var key = event.key
    var mods = event.modifiers
    var ctrl = (mods & Qt.ControlModifier) !== 0
    var shift = (mods & Qt.ShiftModifier) !== 0
    if ((mods & (Qt.AltModifier | Qt.MetaModifier)) !== 0) return false
    var text = String(event.text || "")
    var printable = !ctrl && text.length === 1 && text.charCodeAt(0) > 32 && text.charCodeAt(0) !== 127
    var id = root.rowId

    if (id === "paid" && !ctrl && key === Qt.Key_Space) {
      root.togglePaid()
      return true
    }

    if (id === "model" && !ctrl) {
      if (key === Qt.Key_Return || key === Qt.Key_Enter || key === Qt.Key_Space) { root.modelSheetRequested(""); return true }
      if ((key === Qt.Key_Delete || key === Qt.Key_Backspace) && root.hasModel && root.harness !== "pi") {
        root.edited({ model: null, provider: null })
        return true
      }
      // A letter starts the picker's filter; the row keys j, k, h and l still move.
      if (printable && "jkhl".indexOf(text) < 0) { root.modelSheetRequested(text); return true }
    }

    if (id === "turns" || id === "budget" || id === "runtime") {
      var numeric = printable && ((text >= "0" && text <= "9") || (text === "." && id === "budget"))
      if (numeric) {
        if (root._typedRow !== id) root._typed = ""
        root._typedRow = id
        if (root._typed.length < 7) root._typed = root._typed + text
        root._note = ""
        return true
      }
      if (root._typed !== "") {
        if (key === Qt.Key_Backspace) { root._typed = root._typed.slice(0, -1); return true }
        if (!ctrl && (key === Qt.Key_Return || key === Qt.Key_Enter)) { root.commitTyping(); return true }
        if (key === Qt.Key_Escape) { root.cancelTyping(); return true }
      }
    }

    if (ctrl) return false
    if (key === Qt.Key_Up || key === Qt.Key_K) { root.moveRow(-1); return true }
    if (key === Qt.Key_Down || key === Qt.Key_J) { root.moveRow(1); return true }
    if (key === Qt.Key_Left || key === Qt.Key_H) { root.commitTyping(); root.stepValue(-1, shift); return true }
    if (key === Qt.Key_Right || key === Qt.Key_L) { root.commitTyping(); root.stepValue(1, shift); return true }
    if ((key === Qt.Key_Return || key === Qt.Key_Enter || key === Qt.Key_Space) && id === "level") {
      var i = root.levelList.length > 0 && root.levelList[0].id === root.level ? 1 : 0
      if (i < root.levelList.length) root.setLevel(root.levelList[i].id)
      return true
    }
    return false
  }

  onHasCursorChanged: if (!root.hasCursor) root.commitTyping()
  onRowsChanged: root._row = Math.min(root._row, root.rows.length - 1)
  onHarnessChanged: if (root._noteKind === "warn") root._note = ""

  implicitWidth: Style.space(420)
  implicitHeight: column.implicitHeight

  Column {
    id: column
    width: parent.width
    spacing: Style.spacing.lg

    Flow {
      width: parent.width
      spacing: Style.spacing.sm

      Repeater {
        model: root.levelList

        delegate: Item {
          id: levelCell
          required property var modelData
          readonly property var levelState: Compose.levelState(root.levels, levelCell.modelData.id, root.harness)
          width: levelChip.implicitWidth
          height: levelChip.implicitHeight

          Chip {
            id: levelChip
            anchors.fill: parent
            theme: root.theme
            text: levelCell.modelData.label
            // Unattended runs with nobody there; its selected fill is amber, not accent.
            tint: levelCell.modelData.id === "unattended" ? root.theme.warnInk : root.theme.accent
            selected: root.level === levelCell.modelData.id
            hasCursor: root.hasCursor && root.rowId === "level" && root.level === levelCell.modelData.id
            enabled: levelCell.levelState.available
            note: levelCell.levelState.available ? "" : "not offered"
            onClicked: {
              root.focusRequested()
              root._row = 0
              root.setLevel(levelCell.modelData.id)
            }
          }

          // A level this agent does not offer still answers a click with the reason.
          MouseArea {
            anchors.fill: parent
            visible: !levelCell.levelState.available
            cursorShape: Qt.PointingHandCursor
            onClicked: {
              root.focusRequested()
              root._row = 0
              root.setLevel(levelCell.modelData.id)
            }
          }
        }
      }
    }

    // While the level is being chosen. At rest the Will run line carries this sentence.
    Text {
      width: parent.width
      visible: root.caption !== "" && root.hasCursor && root.rowId === "level"
      textFormat: Text.PlainText
      text: root.caption
      color: root.theme.readable
      wrapMode: Text.WordWrap
      maximumLineCount: 3
      elide: Text.ElideRight
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
      // The level caption can name CLI flags: no ligature may join their dashes.
      font.features: ({ "liga": 0, "calt": 0 })
    }

    Text {
      id: unavailableText
      width: parent.width
      visible: root.unavailableLine !== ""
      textFormat: Text.PlainText
      text: root.unavailableLine
      color: root.theme.soft
      wrapMode: Text.WordWrap
      maximumLineCount: 2
      elide: Text.ElideRight
      // The reason a level is not offered can name a CLI flag, so no ligature joins its dashes.
      font.features: ({ "liga": 0, "calt": 0 })
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
    }

    Column {
      id: paidBlock
      width: parent.width
      spacing: Style.space(2)

      CheckRow {
        id: paidRow
        width: parent.width
        theme: root.theme
        text: "Allow paid usage"
        checked: root.allowPaid
        hasCursor: root.hasCursor && root.rowId === "paid"
        tint: root.theme.warnInk
        caption: root.notes.length > 0 ? root.notes[0].text : ""
        captionTone: root.notes.length > 0 ? root.notes[0].tone : "soft"
        onToggled: {
          root.focusRequested()
          root.focusRow("paid")
          root.togglePaid()
        }
      }

      Repeater {
        model: root.extraNotes

        delegate: Text {
          id: extraNote
          required property var modelData
          x: Style.space(24)
          width: paidBlock.width - x
          textFormat: Text.PlainText
          text: extraNote.modelData.text
          color: root.toneInk(extraNote.modelData.tone)
          wrapMode: Text.WordWrap
          maximumLineCount: 3
          elide: Text.ElideRight
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
        }
      }

      Row {
        id: paidErrorRow
        x: Style.space(24)
        width: paidBlock.width - x
        visible: root.paidError !== ""
        spacing: Style.space(6)

        Text {
          id: paidErrorGlyph
          textFormat: Text.PlainText
          text: "󰀨"   // md-alert_circle U+F0028
          color: root.theme.warnInk
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
        }

        Text {
          width: Math.max(0, paidErrorRow.width - paidErrorGlyph.width - paidErrorRow.spacing)
          textFormat: Text.PlainText
          text: root.paidError
          color: root.theme.warnInk
          wrapMode: Text.WordWrap
          maximumLineCount: 2
          elide: Text.ElideRight
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
        }
      }
    }

    Repeater {
      model: root.valueRows

      delegate: Item {
        id: valueRow
        required property string modelData
        readonly property bool cursorHere: root.hasCursor && root.rowId === valueRow.modelData
        readonly property bool editingHere: root._typedRow === valueRow.modelData && root._typed !== ""
        readonly property bool picker: valueRow.modelData === "model"
        width: column.width
        height: Style.space(28)

        Text {
          id: rowLabel
          anchors.left: parent.left
          anchors.verticalCenter: parent.verticalCenter
          width: Style.space(84)
          textFormat: Text.PlainText
          text: root.labelText(valueRow.modelData)
          color: valueRow.cursorHere ? root.theme.fg : root.theme.soft
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.data
        }

        BorderSurface {
          id: valueBox
          anchors.left: rowLabel.right
          anchors.verticalCenter: parent.verticalCenter
          width: valueRow.picker ? Math.min(Style.space(220), valueRow.width - rowLabel.width - Style.space(64)) : Style.space(96)
          height: Style.space(26)
          radius: Style.cornerRadius
          color: Util.alpha(root.theme.fg, valueRow.cursorHere ? 0.12 : 0.06)
          borderSpec: Border.controlSpec(valueRow.editingHere ? "focus" : (valueRow.cursorHere ? "hover-cursor" : "normal"),
                                         root.theme.fg, root.theme.accent)

          // The model row is the one the picker drops out of.
          Component.onCompleted: if (valueRow.picker) root.modelAnchorItem = valueBox

          Text {
            id: valueText
            anchors.left: parent.left
            anchors.leftMargin: Style.space(8)
            anchors.right: modelBadgePill.visible ? modelBadgePill.left : (caret.visible ? caret.left : parent.right)
            anchors.rightMargin: Style.space(8)
            anchors.verticalCenter: parent.verticalCenter
            textFormat: Text.PlainText
            text: root.valueText(valueRow.modelData)
            color: valueRow.picker && !root.hasModel
              ? (root.harness === "pi" ? root.theme.warnInk : root.theme.readable)
              : (valueRow.cursorHere ? root.theme.fg : root.theme.strong)
            elide: valueRow.picker ? Text.ElideRight : Text.ElideLeft
            maximumLineCount: 1
            font.family: root.theme.fontFamily
            font.pixelSize: root.theme.type.data
            font.features: root.theme.type.digits
          }

          // OpenCode's billing class on the model it describes: [Big Pickle  free ⌄].
          Rectangle {
            id: modelBadgePill
            readonly property color ink: root.toneInk(root.badge.tone)
            anchors.right: caret.left
            anchors.rightMargin: Style.space(6)
            anchors.verticalCenter: parent.verticalCenter
            visible: valueRow.picker && root.badge.text !== ""
            width: modelBadge.implicitWidth + Style.space(10)
            height: Style.space(16)
            radius: height / 2
            color: Util.alpha(modelBadgePill.ink, 0.14)
            border.width: 1
            border.color: Util.alpha(modelBadgePill.ink, 0.55)

            Text {
              id: modelBadge
              objectName: "modelBadge"
              anchors.centerIn: parent
              visible: modelBadgePill.visible
              textFormat: Text.PlainText
              text: root.badge.text
              color: modelBadgePill.ink
              font.family: root.theme.fontFamily
              font.pixelSize: root.theme.type.meta
            }
          }

          Text {
            id: caret
            anchors.right: parent.right
            anchors.rightMargin: Style.space(8)
            anchors.verticalCenter: parent.verticalCenter
            visible: valueRow.picker
            textFormat: Text.PlainText
            text: "󰅀"   // md-chevron_down U+F0140
            color: valueRow.cursorHere ? root.theme.accentInk : root.theme.soft
            font.family: root.theme.fontFamily
            font.pixelSize: root.theme.type.glyph
          }

          Rectangle {
            visible: valueRow.editingHere
            x: Math.min(valueText.x + valueText.contentWidth + Style.space(1), valueBox.width - Style.space(6))
            anchors.verticalCenter: parent.verticalCenter
            width: Math.max(1, Style.space(1))
            height: root.theme.type.data + Style.space(2)
            color: root.theme.accent
          }

          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: {
              root.focusRequested()
              root.commitTyping()
              root.focusRow(valueRow.modelData)
              if (valueRow.picker) root.modelSheetRequested("")
            }
            onWheel: function (wheel) {
              root.focusRequested()
              root.focusRow(valueRow.modelData)
              if (!valueRow.picker && wheel.angleDelta.y !== 0)
                root.stepValue(wheel.angleDelta.y > 0 ? 1 : -1, (wheel.modifiers & Qt.ShiftModifier) !== 0)
              wheel.accepted = true
            }
          }
        }

        Text {
          anchors.left: valueBox.right
          anchors.leftMargin: Style.space(8)
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          textFormat: Text.PlainText
          text: root.noteText(valueRow.modelData)
          color: valueRow.picker && root.harness === "pi" && !root.hasModel ? root.theme.warnInk
            : (valueRow.picker && root.hasModel ? root.theme.accentInk : root.theme.soft)
          elide: Text.ElideRight
          maximumLineCount: 1
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
        }
      }
    }

    Text {
      width: parent.width
      visible: root._note !== "" || !root.claudeLimits
      textFormat: Text.PlainText
      text: root._note !== "" ? root._note : "Max turns and budget apply to Claude Code only. Other agents stop at the run time."
      color: root._note !== "" && root._noteKind === "warn" ? root.theme.warnInk : root.theme.soft
      wrapMode: Text.WordWrap
      maximumLineCount: 2
      elide: Text.ElideRight
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
    }
  }
}
