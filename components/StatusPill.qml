import QtQuick
import qs.Commons
import "../lib/Model.js" as Model

// Lifecycle state as glyph + word on a tinted ground (R6 5.5, CONTRACT 6.4). A pill,
// because it describes. The agent's hue appears here only for "waits for reset",
// where the reset belongs to that agent.
//
// A state change is the system's, so it may fade: ground, glyph and word change colour
// together. The width follows the word at once; the row gives the pill a fixed column,
// so nothing beside it moves.
Item {
  id: root

  required property var theme

  property string status: ""
  property string wait: ""
  property string harness: ""
  // Replaces the word, for the live forms: "running 3m 12s", "re-arming".
  property string text: ""

  readonly property var spec: Model.statusSpec(root.status, root.wait)

  readonly property color ink: {
    switch (root.spec.tone) {
    case "readable": return root.theme.readable
    case "accent": return root.theme.accentInk
    case "harness": return root.theme.harnessInk(root.harness)
    case "ok": return root.theme.okInk
    case "warn": return root.theme.warnInk
    case "bad": return root.theme.badInk
    }
    return root.theme.soft
  }

  readonly property color ground: {
    if (root.status === "disarmed") return "transparent"
    if (root.status === "missed") return Util.alpha(root.theme.warnInk, 0.10)
    switch (root.spec.tone) {
    case "readable": return Util.alpha(root.theme.fg, 0.07)
    case "accent": return Util.alpha(root.theme.accent, 0.22)
    case "harness": return Util.alpha(root.ink, 0.14)
    case "ok": return Util.alpha(root.theme.okInk, 0.14)
    case "warn": return Util.alpha(root.theme.warnInk, 0.14)
    case "bad": return Util.alpha(root.theme.badInk, 0.16)
    }
    return Util.alpha(root.theme.fg, 0.05)
  }

  readonly property bool pulsing: root.status === "running" && root.visible
    && root.theme.animate === true && root.theme.reduceMotion !== true

  implicitHeight: Style.space(18)
  implicitWidth: row.implicitWidth + Style.space(7) * 2

  Rectangle {
    anchors.fill: parent
    radius: height / 2
    color: root.ground
    border.width: root.status === "disarmed" ? 1 : 0
    border.color: root.theme.faint

    Behavior on color {
      ColorAnimation { duration: root.theme.fadeMs(160); easing.type: Easing.InOutQuad }
    }
  }

  Row {
    id: row
    anchors.centerIn: parent
    spacing: Style.space(4)

    Text {
      id: glyphText
      anchors.verticalCenter: parent.verticalCenter
      visible: root.spec.glyph !== ""
      textFormat: Text.PlainText
      text: root.spec.glyph
      color: root.ink
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta

      Behavior on color {
        ColorAnimation { duration: root.theme.fadeMs(160); easing.type: Easing.InOutQuad }
      }
    }

    Text {
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: root.text !== "" ? root.text : root.spec.word
      color: root.ink
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
      font.features: root.theme.type.digits

      Behavior on color {
        ColorAnimation { duration: root.theme.fadeMs(160); easing.type: Easing.InOutQuad }
      }
    }
  }

  SequentialAnimation {
    running: root.pulsing
    loops: Animation.Infinite
    NumberAnimation { target: glyphText; property: "opacity"; to: 0.45; duration: 800; easing.type: Easing.InOutSine }
    NumberAnimation { target: glyphText; property: "opacity"; to: 1; duration: 800; easing.type: Easing.InOutSine }
    onRunningChanged: if (!running) glyphText.opacity = 1
  }
}
