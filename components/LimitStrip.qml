pragma ComponentBehavior: Bound
import QtQuick
import qs.Commons
import "../lib/Model.js" as Model

// The header's limits (FEATURE-LIMITS R7): one chip per shown window source, whatever
// agent it belongs to. A chip shows the window closest to blocking (Cursor: its
// Included usage), as the source's mark, the window's short name, a meter in the
// source's ink (amber from 80 %, the urgent ink from 95 %), the percent in words and
// when it resets. A computed reset (Gemini's day, OpenCode Zen's free day) shows only
// its time. A reading older than it should be is drawn faded and says its age.
//
// Chips have one fixed width and never overlap: the ones that do not fit collapse
// into a "+N" chip. Any chip opens the Limits sheet, which lists every source.
// The numbers are records other tools keep; this plugin never runs a collector.
Item {
  id: root

  required property var theme

  // Usage v2 providers, and the ids to show in header order (Model.shownSources).
  property var providers: []
  property var shownIds: []
  property double nowMs: 0
  // Narrower chips, for tight places.
  property bool compact: false
  // Set while usage reads fail. With no source at all the strip says so instead of
  // staying blank.
  property string errorText: ""

  signal sheetRequested()

  readonly property var chips: {
    var out = []
    var ids = Array.isArray(root.shownIds) ? root.shownIds : []
    for (var i = 0; i < ids.length; i++) {
      var p = Model.providerById(root.providers, ids[i])
      if (p) out.push(Model.chipFor(p, root.nowMs))
    }
    // A bare "Weekly" beside a blue mark could be OpenCode Go or Zen free. A source that is
    // not the agent's own record, or that shares its mark with another shown chip, leads
    // with its name: "OpenCode Go · Weekly".
    var perMark = {}
    for (var j = 0; j < out.length; j++) perMark[out[j].harness] = (perMark[out[j].harness] || 0) + 1
    for (var k = 0; k < out.length; k++) {
      var c = out[k]
      var named = c.harness === "" || perMark[c.harness] > 1 || (c.id !== c.harness && !c.computed)
      c.label = named && c.name !== "" && c.shortLabel.indexOf(c.name) < 0 ? c.name + " · " + c.shortLabel : c.shortLabel
    }
    return out
  }

  readonly property bool hasProviders: Array.isArray(root.providers) && root.providers.length > 0
  readonly property real chipWidth: root.compact ? Style.space(150) : Style.space(220)
  readonly property real chipHeight: Style.space(24)
  readonly property real gap: Style.space(6)
  readonly property real pad: Style.space(8)
  readonly property real allChipsWidth: root.chips.length > 0 ? root.chips.length * root.chipWidth + (root.chips.length - 1) * root.gap : 0

  // The room a "+N" chip needs, from its widest form, so how many chips fit never
  // depends on how many did.
  readonly property real overflowSlotWidth: Math.ceil(slotMetrics.advanceWidth) + Style.space(9) * 2

  // How many chips fit beside a "+N" chip in the width the header gives.
  readonly property int fitCount: {
    var n = root.chips.length
    if (n === 0) return 0
    var avail = root.width > 0 ? root.width : root.allChipsWidth
    if (root.allChipsWidth <= avail + 0.5) return n
    var k = Math.floor((avail - root.overflowSlotWidth) / (root.chipWidth + root.gap))
    return Math.max(0, Math.min(n, k))
  }
  readonly property int hiddenCount: root.chips.length - root.fitCount
  readonly property var visibleChips: root.chips.slice(0, root.fitCount)
  // "+N" for the chips that did not fit; "Limits" when sources exist but none is shown.
  readonly property string moreText: root.chips.length === 0
    ? (root.hasProviders ? "Limits" : "")
    : (root.hiddenCount > 0 ? "+" + root.hiddenCount : "")
  readonly property real moreWidth: Math.ceil(moreMetrics.advanceWidth) + Style.space(9) * 2

  implicitHeight: Style.space(30)
  implicitWidth: root.chips.length > 0 ? root.allChipsWidth
    : (root.hasProviders ? root.moreWidth : (failNote.visible ? failNote.implicitWidth : 0))

  TextMetrics {
    id: slotMetrics
    font.family: root.theme.fontFamily
    font.pixelSize: root.theme.type.meta
    font.features: root.theme.type.digits
    text: "+88"
  }

  // Chip rectangles in this item's coordinates, visible ones only (for tests and for
  // anything that lines up with the strip).
  function chipRects() {
    var out = []
    for (var i = 0; i < chipRepeater.count; i++) {
      var it = chipRepeater.itemAt(i)
      if (!it || !it.visible) continue
      var p = it.mapToItem(root, 0, 0)
      out.push({ id: String(it.modelData.id), x: p.x, y: p.y, w: it.width, h: it.height })
    }
    if (moreChip.visible) {
      var q = moreChip.mapToItem(root, 0, 0)
      out.push({ id: "more", x: q.x, y: q.y, w: moreChip.width, h: moreChip.height })
    }
    return out
  }

  TextMetrics {
    id: moreMetrics
    font.family: root.theme.fontFamily
    font.pixelSize: root.theme.type.meta
    font.features: root.theme.type.digits
    text: root.moreText === "" ? "+8" : root.moreText.replace(/[0-9]/g, "8")
  }

  // Fixed columns, sized from the widest text each can hold, so figures that tick do
  // not move the rest of the chip.
  TextMetrics {
    id: percentMetrics
    font.family: root.theme.fontFamily
    font.pixelSize: root.theme.type.meta
    font.features: root.theme.type.digits
    text: "100%+"
  }

  TextMetrics {
    id: clockMetrics
    font.family: root.theme.fontFamily
    font.pixelSize: root.theme.type.meta
    font.features: root.theme.type.digits
    text: "resets 88:88"
  }

  TextMetrics {
    id: tailMetrics
    font.family: root.theme.fontFamily
    font.pixelSize: root.theme.type.meta
    font.features: root.theme.type.digits
    text: root.compact ? "88h 88m" : "88h 88m old"
  }

  Text {
    id: failNote
    anchors.right: parent.right
    anchors.verticalCenter: parent.verticalCenter
    visible: !root.hasProviders && root.errorText !== ""
    textFormat: Text.PlainText
    text: "Limits could not be read."
    color: root.theme.soft
    font.family: root.theme.fontFamily
    font.pixelSize: root.theme.type.meta
  }

  Row {
    id: row
    anchors.right: parent.right
    anchors.verticalCenter: parent.verticalCenter
    spacing: root.gap

    Repeater {
      id: chipRepeater
      model: root.visibleChips

      Item {
        id: chip
        required property var modelData
        readonly property bool meterShown: !chip.modelData.computed && chip.modelData.percent !== null
        readonly property color ink: chip.modelData.harness !== "" ? root.theme.harnessInk(chip.modelData.harness) : root.theme.readable
        readonly property color valueInk: chip.modelData.tone === "bad" ? root.theme.badInk
          : chip.modelData.tone === "warn" ? root.theme.warnInk : root.theme.readable
        // A computed chip already says its reset clock time, so it has no countdown and
        // its name gets the room.
        readonly property string tail: chip.modelData.stale
          ? (chip.modelData.ageSec !== null ? Model.formatAge(chip.modelData.ageSec) + " old" : "age unknown")
          : (chip.meterShown ? chip.modelData.resetText : "")

        width: root.chipWidth
        height: root.chipHeight
        opacity: chip.modelData.stale ? 0.6 : 1

        Rectangle {
          anchors.fill: parent
          radius: height / 2
          color: Util.alpha(chip.ink, chipMouse.containsMouse ? 0.18 : 0.10)
          border.width: 1
          border.color: Util.alpha(chip.ink, chipMouse.containsMouse ? 0.75 : 0.40)
        }

        AgentMark {
          id: mark
          x: root.pad
          anchors.verticalCenter: parent.verticalCenter
          visible: chip.modelData.harness !== ""
          theme: root.theme
          agent: chip.modelData.harness
          size: Style.space(12)
        }

        Text {
          x: mark.visible ? mark.x + mark.width + Style.space(5) : root.pad
          anchors.verticalCenter: parent.verticalCenter
          width: Math.max(0, (chip.meterShown ? meter.x : value.x) - x - Style.space(5))
          textFormat: Text.PlainText
          text: chip.modelData.label
          color: chip.ink
          elide: Text.ElideRight
          maximumLineCount: 1
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
        }

        Meter {
          id: meter
          anchors.right: value.left
          anchors.rightMargin: Style.space(5)
          anchors.verticalCenter: parent.verticalCenter
          visible: chip.meterShown
          width: root.compact ? Style.space(20) : Style.space(28)
          theme: root.theme
          value: chip.modelData.percent !== null ? chip.modelData.percent : -1
          warning: chip.modelData.tone === "warn"
          alarming: chip.modelData.tone === "bad"
          fill: root.theme.harnessFill(chip.modelData.harness)
        }

        Text {
          id: value
          anchors.right: tailText.left
          anchors.rightMargin: chip.tail !== "" ? Style.space(5) : 0
          anchors.verticalCenter: parent.verticalCenter
          width: Math.ceil(chip.meterShown ? percentMetrics.advanceWidth : clockMetrics.advanceWidth)
          textFormat: Text.PlainText
          text: chip.modelData.text
          color: chip.valueInk
          horizontalAlignment: chip.meterShown ? Text.AlignRight : Text.AlignLeft
          elide: Text.ElideRight
          maximumLineCount: 1
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
          font.features: root.theme.type.digits
        }

        Text {
          id: tailText
          anchors.right: parent.right
          anchors.rightMargin: root.pad
          anchors.verticalCenter: parent.verticalCenter
          width: chip.tail !== "" ? Math.ceil(tailMetrics.advanceWidth) : 0
          textFormat: Text.PlainText
          text: chip.tail
          color: chip.modelData.stale ? root.theme.warnInk : root.theme.soft
          horizontalAlignment: Text.AlignRight
          elide: Text.ElideRight
          maximumLineCount: 1
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
          font.features: root.theme.type.digits
        }

        MouseArea {
          id: chipMouse
          anchors.fill: parent
          hoverEnabled: true
          cursorShape: Qt.PointingHandCursor
          onClicked: root.sheetRequested()
        }
      }
    }

    Item {
      id: moreChip
      visible: root.moreText !== ""
      width: root.moreWidth
      height: root.chipHeight

      Rectangle {
        anchors.fill: parent
        radius: height / 2
        color: Util.alpha(root.theme.fg, moreMouse.containsMouse ? 0.14 : 0.07)
        border.width: 1
        border.color: moreMouse.containsMouse ? root.theme.accent : root.theme.faint
      }

      Text {
        anchors.centerIn: parent
        textFormat: Text.PlainText
        text: root.moreText
        color: root.theme.readable
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.meta
        font.features: root.theme.type.digits
      }

      MouseArea {
        id: moreMouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.sheetRequested()
      }
    }
  }
}
