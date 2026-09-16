import QtQuick
import qs.Commons

// The thin vertical bar at the left edge of a row, in the agent's ink. It is the
// only place a whole row carries the agent's hue; status lives in the pill.
// Gemini's rail is its gradient, the texture that keeps it apart from OpenCode.
Item {
  id: root

  required property var theme

  property string harness: ""
  // Opacity of the rail. Closed and draft jobs step back to .35 or .55.
  property real strength: 1.0
  // A running job breathes. Only while the panel is open and motion is full.
  property bool pulse: false
  // Set true to flash accent into the harness ink once (the arm moment).
  property bool flash: false

  readonly property var stops: root.theme.geminiStops
  readonly property bool gradientRail: root.harness === "gemini" && !!root.stops && root.stops.length === 3
  readonly property bool pulsing: root.pulse && root.visible && root.theme.animate === true && root.theme.reduceMotion !== true

  implicitWidth: Style.space(3)
  implicitHeight: Style.space(38)
  opacity: root.strength

  // A row created with flash already set (the list refreshed after the arm) flashes
  // too. It is a fade only, so reduced motion keeps it (fadeMs caps it at 120 ms).
  function startFlash() {
    if (root.theme.animate !== true) return
    flashFade.restart()
  }
  onFlashChanged: if (root.flash) root.startFlash()
  Component.onCompleted: if (root.flash) root.startFlash()

  Item {
    id: body
    anchors.fill: parent

    Rectangle {
      anchors.fill: parent
      radius: width / 2
      visible: !root.gradientRail
      color: root.theme.harnessFill(root.harness)
    }

    Rectangle {
      anchors.fill: parent
      radius: width / 2
      visible: root.gradientRail
      gradient: Gradient {
        GradientStop { position: 0.0; color: root.gradientRail ? root.stops[0] : "transparent" }
        GradientStop { position: 0.5; color: root.gradientRail ? root.stops[1] : "transparent" }
        GradientStop { position: 1.0; color: root.gradientRail ? root.stops[2] : "transparent" }
      }
    }

    Rectangle {
      id: flashLayer
      anchors.fill: parent
      radius: width / 2
      color: root.theme.accent
      opacity: 0
    }
  }

  NumberAnimation {
    id: flashFade
    target: flashLayer
    property: "opacity"
    from: 1
    to: 0
    duration: root.theme.fadeMs(280)
    easing.type: Easing.OutCubic
  }

  SequentialAnimation {
    running: root.pulsing
    loops: Animation.Infinite
    NumberAnimation { target: body; property: "opacity"; to: 0.45; duration: 800; easing.type: Easing.InOutSine }
    NumberAnimation { target: body; property: "opacity"; to: 1; duration: 800; easing.type: Easing.InOutSine }
    onRunningChanged: if (!running) body.opacity = 1
  }
}
