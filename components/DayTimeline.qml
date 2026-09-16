pragma ComponentBehavior: Bound
import QtQuick
import qs.Commons
import "../lib/Model.js" as Model
import "../lib/Timeline.js" as Timeline

// One day of History, 00:00 to 24:00 local (FEATURE-LIMITS R7, R7-SYNTHESIS 5.6). One
// lane per limit source that had a reset or a run that day, then "No limit data" for
// runs that drew from none. Each run is a capsule from start to end in its agent's
// ink with its outcome glyph; an armed job due that day is an outline. Each reset is
// a dashed marker in the source's ink; a reset seen when a run hit the limit has a
// hollow head. Labels ("5-hour 13:00", "now") share a row above the lanes and hour
// numbers a row below, and never overlap; resets too close to label collapse into a
// "+N resets" count that lists them on hover. Read only: a click moves the History
// cursor to that job.
Item {
  id: root

  required property var theme

  // The Timeline answer for the day, or null while it is being read.
  property var timeline: null
  property double dayStartMs: 0
  property double nowMs: 0
  property string cursorJobId: ""

  signal jobClicked(string id)

  readonly property double dayEndMs: Timeline.nextDayMs(root.dayStartMs)
  readonly property var lanes: Timeline.dayLanes(root.timeline, root.dayStartMs, root.dayEndMs, root.nowMs)
  readonly property int laneCount: root.lanes.length
  readonly property bool loaded: !!root.timeline && typeof root.timeline === "object"
  readonly property bool empty: root.loaded && root.lanes.length === 0
  readonly property bool isToday: root.nowMs >= root.dayStartMs && root.nowMs < root.dayEndMs
  readonly property string emptyText: "No runs or resets recorded on " + Model.dateText(root.dayStartMs) + "."

  readonly property int maxLanes: 5
  readonly property var shownLanes: root.lanes.slice(0, root.maxLanes)
  readonly property int moreLanes: Math.max(0, root.lanes.length - root.maxLanes)

  readonly property real nameWidth: Style.space(150)
  readonly property real markerRow: Style.space(14)
  readonly property real laneHeight: Style.space(20)
  readonly property real laneGap: Style.space(3)
  readonly property real tickRow: Style.space(14)
  readonly property real trackX: root.nameWidth
  readonly property real trackWidth: Math.max(0, root.width - root.nameWidth - Style.space(18))
  readonly property real lanesTop: root.markerRow + Style.space(3)
  readonly property real lanesHeight: root.shownLanes.length > 0
    ? root.shownLanes.length * (root.laneHeight + root.laneGap) - root.laneGap : 0
  readonly property real tickTop: root.lanesTop + root.lanesHeight + Style.space(2)

  implicitWidth: Style.space(1000)
  implicitHeight: !root.loaded || root.empty ? Style.space(30)
    : root.tickTop + root.tickRow + (root.moreLanes > 0 ? Style.space(14) : 0)

  function xFor(ms) {
    var span = root.dayEndMs - root.dayStartMs
    if (!(span > 0)) return root.trackX
    return root.trackX + Math.max(0, Math.min(1, (Number(ms) - root.dayStartMs) / span)) * root.trackWidth
  }

  function laneY(i) { return root.lanesTop + i * (root.laneHeight + root.laneGap) }

  function outcomeGlyph(outcome) {
    switch (String(outcome || "")) {
    case "done": return Model.GLYPH.check
    case "failed": case "gave_up": return Model.GLYPH.close
    case "limit": return Model.GLYPH.alert
    case "skipped": return Model.GLYPH.skip
    case "missed": return Model.GLYPH.history
    case "interrupted": return Model.GLYPH.close
    case "running": return Model.GLYPH.play
    case "armed": return Model.GLYPH.clock
    }
    return ""
  }

  function outcomeInk(outcome) {
    switch (String(outcome || "")) {
    case "done": return root.theme.okInk
    case "failed": case "gave_up": return root.theme.badInk
    case "limit": case "missed": case "interrupted": return root.theme.warnInk
    case "running": return root.theme.accentInk
    }
    return root.theme.soft
  }

  readonly property var ticks: Timeline.ticks(root.dayStartMs, root.dayEndMs, 3)

  FontMetrics {
    id: plainMetrics
    font.family: root.theme.fontFamily
    font.pixelSize: root.theme.type.meta
    font.features: root.theme.type.digits
  }

  FontMetrics {
    id: boldMetrics
    font.family: root.theme.fontFamily
    font.pixelSize: root.theme.type.meta
    font.features: root.theme.type.digits
    font.bold: true
  }

  // Marker labels and "now" on row 0, hour numbers on row 1; x relative to the track.
  readonly property var labelSpecs: {
    var out = []
    if (root.trackWidth <= 0 || !root.loaded || root.empty) return out
    var gap = Style.space(3)
    function rel(ms) { return root.xFor(ms) - root.trackX }
    if (root.isToday) {
      var nw = Math.ceil(plainMetrics.advanceWidth("now"))
      var nx = rel(root.nowMs)
      out.push({ id: "now", text: "now", kind: "now", harness: "", hollow: false, x: nx + gap, altX: nx - nw - gap,
                 width: nw, priority: Timeline.PRIORITY.now, row: 0 })
    }
    for (var i = 0; i < root.shownLanes.length; i++) {
      var lane = root.shownLanes[i]
      for (var j = 0; j < lane.markers.length; j++) {
        var m = lane.markers[j]
        var text = (m.shortLabel !== "" ? m.shortLabel : "reset") + " " + Model.formatClock(m.atMs)
        var w = Math.ceil(boldMetrics.advanceWidth(text))
        var mx = rel(m.atMs)
        out.push({ id: "reset:" + lane.source + ":" + m.atMs + ":" + j, text: text, kind: "reset", harness: lane.harness,
                   hollow: m.hollow, x: mx + gap, altX: mx - w - gap, anchorX: mx, width: w, priority: Timeline.PRIORITY.reset, row: 0 })
      }
    }
    for (var t = 0; t < root.ticks.length; t++) {
      var tick = root.ticks[t]
      var tw = Math.ceil(plainMetrics.advanceWidth(tick.label))
      out.push({ id: "tick:" + tick.ms, text: tick.label, kind: "tick", harness: "", hollow: false,
                 x: rel(tick.ms) - tw / 2, width: tw, priority: Timeline.PRIORITY.tick, row: 1 })
    }
    return out
  }

  readonly property real countWidth: Math.ceil(plainMetrics.advanceWidth("+88 resets")) + Style.space(8)

  readonly property var labels: {
    var specs = root.labelSpecs
    var laid = Timeline.layoutLabels(specs, root.trackWidth, root.countWidth, Style.space(6))
    var out = []
    var crowd = []
    for (var i = 0; i < laid.length; i++) {
      var l = laid[i]
      if (l.id !== "count" && !l.visible && specs[i].kind === "reset") crowd.push({ text: specs[i].text, harness: specs[i].harness })
    }
    for (var k = 0; k < laid.length; k++) {
      var e = laid[k]
      if (!e.visible) continue
      if (e.id === "count") {
        out.push({ id: "count", text: "+" + e.collapsed + (e.collapsed === 1 ? " reset" : " resets"), kind: "count",
                   harness: "", x: root.trackX + e.x, row: e.row, collapsed: e.collapsed, crowd: crowd,
                   anchorX: root.trackX + e.anchorX })
        continue
      }
      var s = specs[k]
      out.push({ id: s.id, text: s.text, kind: s.kind, harness: s.harness, x: root.trackX + e.x, row: e.row,
                 collapsed: 0, crowd: [] })
    }
    return out
  }

  // Visible label boxes in this item's coordinates: [{id, kind, text, x, y, w, h, collapsed}].
  function labelRects() {
    var out = []
    for (var i = 0; i < labelRepeater.count; i++) {
      var it = labelRepeater.itemAt(i)
      if (!it || !it.visible) continue
      out.push({ id: String(it.modelData.id), kind: String(it.modelData.kind), text: it.modelData.text,
                 x: it.x, y: it.y, w: it.width, h: it.height, collapsed: it.modelData.collapsed })
    }
    return out
  }

  Text {
    y: Style.space(6)
    x: Style.space(8)
    width: Math.max(0, root.width - Style.space(16))
    visible: !root.loaded || root.empty
    textFormat: Text.PlainText
    text: root.loaded ? root.emptyText : "Reading the day…"
    color: root.loaded ? root.theme.readable : root.theme.soft
    elide: Text.ElideRight
    maximumLineCount: 1
    font.family: root.theme.fontFamily
    font.pixelSize: root.theme.type.body
  }

  Item {
    id: body
    anchors.fill: parent
    visible: root.loaded && !root.empty

    // Hour grid behind the lanes.
    Repeater {
      model: root.ticks

      Rectangle {
        required property var modelData
        x: Math.round(root.xFor(modelData.ms))
        y: root.lanesTop
        width: 1
        height: root.lanesHeight
        color: root.theme.faint
      }
    }

    Repeater {
      model: root.shownLanes

      Item {
        id: lane
        required property var modelData
        required property int index
        readonly property color ink: lane.modelData.harness !== "" ? root.theme.harnessInk(lane.modelData.harness) : root.theme.soft
        x: 0
        y: root.laneY(lane.index)
        width: root.width
        height: root.laneHeight

        AgentMark {
          id: laneMark
          x: Style.space(8)
          anchors.verticalCenter: parent.verticalCenter
          visible: lane.modelData.harness !== "" && lane.modelData.source !== null
          theme: root.theme
          agent: lane.modelData.harness
          size: Style.space(12)
        }

        Text {
          x: Style.space(26)
          anchors.verticalCenter: parent.verticalCenter
          width: Math.max(0, root.nameWidth - Style.space(32))
          textFormat: Text.PlainText
          text: lane.modelData.name
          color: lane.modelData.source === null ? root.theme.soft : lane.ink
          elide: Text.ElideRight
          maximumLineCount: 1
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
        }

        Rectangle {
          x: root.trackX
          width: root.trackWidth
          height: parent.height
          radius: Style.cornerRadius
          color: Util.alpha(lane.modelData.source === null ? root.theme.fg : lane.ink, 0.05)
        }

        // Reset markers: a dashed line through the lane, a head at its top.
        Repeater {
          model: lane.modelData.markers

          Item {
            id: marker
            required property var modelData
            x: Math.round(root.xFor(marker.modelData.atMs))
            y: 0
            width: 1
            height: lane.height

            Column {
              y: Style.space(4)
              spacing: Style.space(2)
              Repeater {
                model: Math.max(0, Math.floor((lane.height - Style.space(4)) / Style.space(5)))
                Rectangle {
                  width: Math.max(1, Style.space(1))
                  height: Style.space(3)
                  color: lane.ink
                }
              }
            }

            Rectangle {
              x: -width / 2 + 0.5
              y: 0
              width: Style.space(6)
              height: width
              radius: width / 2
              color: marker.modelData.hollow ? root.theme.surface : lane.ink
              border.width: marker.modelData.hollow ? 1 : 0
              border.color: lane.ink
            }
          }
        }

        // Runs and armed jobs.
        Repeater {
          model: lane.modelData.capsules

          Item {
            id: capsule
            required property var modelData
            readonly property bool isCursor: capsule.modelData.jobId !== "" && capsule.modelData.jobId === root.cursorJobId
            readonly property bool armed: capsule.modelData.outcome === "armed"
            readonly property color mark: root.theme.harnessFill(capsule.modelData.harness)
            x: root.xFor(capsule.modelData.startMs) - (capsule.armed ? pill.width / 2 : 0)
            y: 0
            width: pill.width + (outcome.text !== "" ? outcome.implicitWidth : 0) + Style.space(2)
            height: lane.height
            z: capsule.isCursor ? 3 : 2

            Rectangle {
              id: pill
              y: Style.space(4)
              width: Math.max(Style.space(10), root.xFor(capsule.modelData.endMs) - root.xFor(capsule.modelData.startMs))
              height: lane.height - Style.space(8)
              radius: height / 2
              color: capsule.armed ? "transparent" : Util.alpha(capsule.mark, 0.45)
              border.width: capsule.isCursor ? Math.max(2, Style.space(2)) : 1
              border.color: capsule.isCursor ? root.theme.fg : capsule.mark
            }

            Text {
              id: outcome
              x: pill.width + Style.space(2)
              anchors.verticalCenter: pill.verticalCenter
              visible: text !== ""
              textFormat: Text.PlainText
              text: root.outcomeGlyph(capsule.modelData.outcome)
              color: root.outcomeInk(capsule.modelData.outcome)
              font.family: root.theme.fontFamily
              font.pixelSize: root.theme.type.meta
            }

            MouseArea {
              anchors.fill: parent
              enabled: capsule.modelData.jobId !== ""
              cursorShape: Qt.PointingHandCursor
              onClicked: root.jobClicked(capsule.modelData.jobId)
            }
          }
        }
      }
    }

    // Now, on today only.
    Rectangle {
      visible: root.isToday
      x: root.xFor(root.nowMs) - width / 2
      y: root.lanesTop - Style.space(2)
      width: Math.max(2, Style.space(2))
      height: root.lanesHeight + Style.space(4)
      color: root.theme.accent
      z: 4
    }

    Repeater {
      id: labelRepeater
      model: root.labels

      Item {
        id: label
        required property var modelData
        // A count that had to move away from the reset it stands for draws a leader back
        // to that marker, so it never reads as belonging to its new neighbour.
        readonly property real leaderTo: label.modelData.kind === "count" && typeof label.modelData.anchorX === "number"
          ? label.modelData.anchorX - label.x : label.width / 2
        readonly property bool leader: Math.abs(label.leaderTo - label.width / 2) > Style.space(12)
        x: label.modelData.x
        y: label.modelData.row === 1 ? root.tickTop : 0
        width: label.modelData.kind === "count" ? root.countWidth : labelText.implicitWidth
        height: labelText.implicitHeight
        z: 5

        Rectangle {
          anchors.fill: parent
          visible: label.modelData.kind === "count"
          radius: height / 2
          color: Util.alpha(root.theme.fg, countMouse.containsMouse ? 0.16 : 0.08)
        }

        Rectangle {
          visible: label.leader
          x: Math.min(label.width / 2, label.leaderTo)
          y: label.height
          width: Math.abs(label.leaderTo - label.width / 2)
          height: 1
          color: root.theme.soft
        }

        Rectangle {
          visible: label.leader
          x: label.leaderTo
          y: label.height
          width: 1
          height: Style.space(4)
          color: root.theme.soft
        }

        Text {
          id: labelText
          anchors.horizontalCenter: label.modelData.kind === "count" ? parent.horizontalCenter : undefined
          textFormat: Text.PlainText
          text: label.modelData.text
          color: label.modelData.kind === "now" ? root.theme.accentInk
            : label.modelData.kind === "reset" ? (label.modelData.harness !== "" ? root.theme.harnessInk(label.modelData.harness) : root.theme.readable)
            : label.modelData.kind === "count" ? root.theme.readable
            : root.theme.soft
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
          font.features: root.theme.type.digits
          font.bold: label.modelData.kind === "reset"
        }

        MouseArea {
          id: countMouse
          anchors.fill: parent
          enabled: label.modelData.kind === "count"
          hoverEnabled: true
        }

        // The resets a count stands for, while the pointer is on it.
        Rectangle {
          visible: label.modelData.kind === "count" && countMouse.containsMouse
          x: Math.min(0, root.width - label.x - width)
          y: parent.height + Style.space(2)
          width: crowdColumn.implicitWidth + Style.space(8) * 2
          height: crowdColumn.implicitHeight + Style.space(6) * 2
          radius: Style.cornerRadius
          color: root.theme.surface
          border.width: 1
          border.color: root.theme.faint

          Column {
            id: crowdColumn
            x: Style.space(8)
            y: Style.space(6)
            spacing: Style.space(2)

            Repeater {
              model: label.modelData.crowd
              Text {
                required property var modelData
                textFormat: Text.PlainText
                text: modelData.text
                color: modelData.harness !== "" ? root.theme.harnessInk(modelData.harness) : root.theme.readable
                font.family: root.theme.fontFamily
                font.pixelSize: root.theme.type.meta
                font.features: root.theme.type.digits
              }
            }
          }
        }
      }
    }

    Text {
      x: root.trackX
      y: root.tickTop + root.tickRow
      visible: root.moreLanes > 0
      textFormat: Text.PlainText
      text: "+" + root.moreLanes + (root.moreLanes === 1 ? " more source" : " more sources")
      color: root.theme.soft
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
    }
  }
}
