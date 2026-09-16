pragma ComponentBehavior: Bound
import QtQuick
import qs.Commons
import "../lib/Model.js" as Model
import "../lib/Timeline.js" as Timeline

// The next day at a glance (R6 4.3): a strip from an hour ago to 23 hours ahead with
// a tick every three hours, the present as an accent line, every fixed limit reset of
// every source as a dashed marker in its agent's ink (a 5-hour window also as a band
// ending at its reset), and every job as a capsule in its agent's ink. A running
// job's capsule breathes; the cursor's capsule stands taller with a hairline down
// toward the list. Read only: a click moves the cursor to that job.
//
// Labels share one row above the track and never overlap (FEEDBACK-UI 2): "now" wins
// over a reset label, a reset label over "+N later", and hour numbers give way to all
// of them. Resets too close to fit collapse into one "+N resets" count.
Item {
  id: root

  required property var theme

  // [{id, harness, fireAtMs, running, pending}] in list order.
  property var items: []
  property double nowMs: 0
  // Usage v2 providers.
  property var providers: []
  property string cursorId: ""

  signal jobClicked(string id)

  readonly property double startMs: root.nowMs - 3600000
  readonly property double spanMs: 86400000
  readonly property double endMs: root.startMs + root.spanMs
  readonly property real labelHeight: Style.space(13)
  readonly property real trackTop: root.labelHeight
  readonly property real trackHeight: Math.max(0, root.height - root.trackTop)
  readonly property real trackWidth: Math.max(0, root.width)
  readonly property real labelGap: Style.space(6)

  function xFor(ms) {
    return (Number(ms) - root.startMs) / root.spanMs * root.trackWidth
  }

  readonly property var ticks: Timeline.ticks(root.startMs, root.endMs + 1, 3)

  // Fixed resets still ahead inside the strip. Sliding windows have no fixed reset
  // and are never drawn.
  readonly property var markers: Timeline.ribbonMarkers(root.providers, root.nowMs, root.endMs)

  // The open 5-hour window of the first source that has one.
  readonly property var band: {
    for (var i = 0; i < root.markers.length; i++) {
      var m = root.markers[i]
      if (m.kind === "session" && m.shortLabel === "5-hour")
        return { fromMs: Math.max(root.startMs, m.atMs - 5 * 3600000), resetMs: m.atMs, harness: m.harness }
    }
    return null
  }

  readonly property int laterCount: {
    var n = 0
    var list = Array.isArray(root.items) ? root.items : []
    for (var i = 0; i < list.length; i++) if (list[i] && list[i].fireAtMs > root.endMs) n++
    return n
  }

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

  // [{id, text, kind, harness, stale, x, altX, width, priority, row}]
  readonly property var labelSpecs: {
    var out = []
    if (root.trackWidth <= 0) return out
    var gap = Style.space(3)
    var nowX = root.xFor(root.nowMs)
    var nowW = Math.ceil(plainMetrics.advanceWidth("now"))
    out.push({ id: "now", text: "now", kind: "now", harness: "", stale: false, x: nowX + gap, altX: nowX - nowW - gap,
               width: nowW, priority: Timeline.PRIORITY.now, row: 0 })
    for (var i = 0; i < root.markers.length; i++) {
      var m = root.markers[i]
      var text = (m.shortLabel !== "" ? m.shortLabel : "reset") + " " + Model.formatClock(m.atMs)
      var w = Math.ceil(boldMetrics.advanceWidth(text))
      var mx = root.xFor(m.atMs)
      out.push({ id: "reset:" + m.key, text: text, kind: "reset", harness: m.harness, stale: m.stale,
                 x: mx + gap, altX: mx - w - gap, anchorX: mx, width: w, priority: Timeline.PRIORITY.reset, row: 0 })
    }
    if (root.laterCount > 0) {
      var later = "+" + root.laterCount + " later"
      var lw = Math.ceil(plainMetrics.advanceWidth(later))
      out.push({ id: "later", text: later, kind: "later", harness: "", stale: false, x: root.trackWidth - lw,
                 width: lw, priority: Timeline.PRIORITY.later, row: 0 })
    }
    for (var t = 0; t < root.ticks.length; t++) {
      var tick = root.ticks[t]
      var tw = Math.ceil(plainMetrics.advanceWidth(tick.label))
      var tx = root.xFor(tick.ms)
      out.push({ id: "tick:" + tick.ms, text: tick.label, kind: "tick", harness: "", stale: false,
                 x: tx + gap, altX: tx - tw - gap, width: tw, priority: Timeline.PRIORITY.tick, row: 0 })
    }
    return out
  }

  readonly property real countWidth: Math.ceil(plainMetrics.advanceWidth("+88 resets"))

  readonly property var labels: {
    var specs = root.labelSpecs
    var laid = Timeline.layoutLabels(specs, root.trackWidth, root.countWidth, root.labelGap)
    var out = []
    for (var i = 0; i < laid.length; i++) {
      var l = laid[i]
      if (!l.visible) continue
      if (l.id === "count") {
        var text = "+" + l.collapsed + (l.collapsed === 1 ? " reset" : " resets")
        out.push({ id: "count", text: text, kind: "count", harness: "", stale: false, x: l.x, collapsed: l.collapsed,
                   anchorX: l.anchorX })
        continue
      }
      var s = specs[i]
      out.push({ id: s.id, text: s.text, kind: s.kind, harness: s.harness, stale: s.stale, x: l.x, collapsed: 0 })
    }
    return out
  }

  // Visible label boxes in this item's coordinates: [{id, kind, text, x, y, w, h}].
  function labelRects() {
    var out = []
    for (var i = 0; i < labelRepeater.count; i++) {
      var it = labelRepeater.itemAt(i)
      if (!it || !it.visible) continue
      out.push({ id: String(it.modelData.id), kind: String(it.modelData.kind), text: it.text,
                 x: it.x, y: it.y, w: it.implicitWidth, h: it.implicitHeight })
    }
    return out
  }

  implicitHeight: Style.space(40)
  implicitWidth: Style.space(1000)
  clip: true

  Rectangle {
    x: 0
    y: root.trackTop
    width: root.trackWidth
    height: root.trackHeight
    radius: Style.cornerRadius
    color: Util.alpha(root.theme.fg, 0.04)
  }

  Repeater {
    model: root.ticks

    Rectangle {
      required property var modelData
      x: root.xFor(modelData.ms)
      y: root.trackTop
      width: 1
      height: root.trackHeight
      color: root.theme.faint
    }
  }

  // The open 5-hour window.
  Rectangle {
    visible: root.band !== null
    x: root.band ? root.xFor(root.band.fromMs) : 0
    y: root.trackTop
    width: root.band ? Math.max(0, root.xFor(root.band.resetMs) - x) : 0
    height: root.trackHeight
    color: Util.alpha(root.theme.harnessInk(root.band ? root.band.harness : ""), 0.14)
  }

  // Dashed reset markers, one per fixed reset.
  Repeater {
    model: root.markers

    Column {
      id: marker
      required property var modelData
      x: Math.round(root.xFor(marker.modelData.atMs))
      y: root.trackTop
      spacing: Style.space(2)
      opacity: marker.modelData.stale ? 0.55 : 1

      Repeater {
        model: Math.max(0, Math.floor(root.trackHeight / Style.space(5)))
        Rectangle {
          width: Math.max(1, Style.space(1))
          height: Style.space(3)
          color: marker.modelData.harness !== "" ? root.theme.harnessInk(marker.modelData.harness) : root.theme.soft
        }
      }
    }
  }

  // Now.
  Rectangle {
    x: root.xFor(root.nowMs) - width / 2
    y: root.trackTop
    width: Math.max(2, Style.space(2))
    height: root.trackHeight
    color: root.theme.accent
  }

  // Every label of the strip, laid out by Timeline.layoutLabels.
  Repeater {
    id: labelRepeater
    model: root.labels

    Text {
      id: stripLabel
      required property var modelData
      // A "+N resets" count that had to move away from the marker it stands for draws a
      // leader back to it, so it never reads as belonging to its new neighbour.
      readonly property real leaderTo: modelData.kind === "count" && typeof modelData.anchorX === "number"
        ? modelData.anchorX - stripLabel.x : stripLabel.implicitWidth / 2
      readonly property bool leader: Math.abs(stripLabel.leaderTo - stripLabel.implicitWidth / 2) > Style.space(12)
      // "+N later" keeps its right edge on the strip's, whatever the rendered width.
      x: modelData.kind === "later" ? Math.max(0, root.trackWidth - implicitWidth) : modelData.x
      y: 0
      textFormat: Text.PlainText
      text: modelData.text
      opacity: modelData.stale ? 0.6 : 1
      color: modelData.kind === "now" ? root.theme.accentInk
        : modelData.kind === "reset" ? (modelData.harness !== "" ? root.theme.harnessInk(modelData.harness) : root.theme.readable)
        : modelData.kind === "count" ? root.theme.readable
        : root.theme.soft
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
      font.features: root.theme.type.digits
      font.bold: modelData.kind === "reset"

      Rectangle {
        visible: stripLabel.leader
        x: Math.min(stripLabel.implicitWidth / 2, stripLabel.leaderTo)
        y: stripLabel.implicitHeight
        width: Math.abs(stripLabel.leaderTo - stripLabel.implicitWidth / 2)
        height: 1
        color: root.theme.soft
      }

      Rectangle {
        visible: stripLabel.leader
        x: stripLabel.leaderTo
        y: stripLabel.implicitHeight
        width: 1
        height: Style.space(4)
        color: root.theme.soft
      }
    }
  }

  Repeater {
    model: root.items

    Item {
      id: cap
      required property var modelData
      readonly property bool inRange: modelData.fireAtMs >= root.startMs && modelData.fireAtMs <= root.endMs
      readonly property bool isCursor: modelData.id === root.cursorId
      readonly property bool gradient: modelData.harness === "gemini" && !!root.theme.geminiStops
        && root.theme.geminiStops.length === 3
      readonly property real capHeight: cap.isCursor ? Style.space(22) : Style.space(16)

      visible: cap.inRange
      x: root.xFor(modelData.fireAtMs) - width / 2
      y: root.trackTop
      width: Style.space(14)
      height: root.trackHeight
      z: cap.isCursor ? 2 : 1

      Rectangle {
        id: pill
        anchors.horizontalCenter: parent.horizontalCenter
        y: (root.trackHeight - cap.capHeight) / 2
        width: Style.space(6)
        height: cap.capHeight
        radius: width / 2
        opacity: cap.modelData.pending ? 0.55 : 1
        color: cap.gradient ? "transparent" : root.theme.harnessFill(cap.modelData.harness)
        gradient: cap.gradient ? stops : null
        border.width: cap.isCursor ? 1 : 0
        border.color: root.theme.fg

        Gradient {
          id: stops
          GradientStop { position: 0.0; color: cap.gradient ? root.theme.geminiStops[0] : "transparent" }
          GradientStop { position: 0.5; color: cap.gradient ? root.theme.geminiStops[1] : "transparent" }
          GradientStop { position: 1.0; color: cap.gradient ? root.theme.geminiStops[2] : "transparent" }
        }
      }

      // Hairline toward the list for the cursor's job.
      Rectangle {
        visible: cap.isCursor
        anchors.horizontalCenter: parent.horizontalCenter
        y: pill.y + pill.height
        width: 1
        height: Math.max(0, root.trackHeight - y)
        color: root.theme.accent
      }

      SequentialAnimation {
        running: cap.modelData.running === true && cap.visible && root.visible
          && root.theme.animate === true && root.theme.reduceMotion !== true
        loops: Animation.Infinite
        NumberAnimation { target: pill; property: "opacity"; to: 0.45; duration: 800; easing.type: Easing.InOutSine }
        NumberAnimation { target: pill; property: "opacity"; to: 1; duration: 800; easing.type: Easing.InOutSine }
        onRunningChanged: if (!running) pill.opacity = Qt.binding(function () { return cap.modelData.pending ? 0.55 : 1 })
      }

      MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: root.jobClicked(String(cap.modelData.id))
      }
    }
  }
}
