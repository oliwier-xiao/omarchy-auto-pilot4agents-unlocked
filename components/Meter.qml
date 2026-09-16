import QtQuick
import qs.Commons

// A rounded track showing how much of an allowance is used (the agents widget's
// meter shape). Ordinal data on one hue: the source's own ink, amber from 80 %, the
// urgent ink from 95 % (FEATURE-LIMITS R7). Callers put the percent in words beside it.
Item {
  id: root

  required property var theme

  // 0..1; below zero the fill is hidden and only the track shows. Above 1 the fill
  // stays full.
  property real value: -1
  property bool alarming: false
  // Amber, for a window at 80 % or more that is not yet nearly full.
  property bool warning: false
  property color fill: root.theme.accent
  property real thickness: Style.space(4)

  readonly property real clamped: Math.max(0, Math.min(1, root.value))

  implicitWidth: Style.space(60)
  implicitHeight: root.thickness

  Rectangle {
    id: track
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.verticalCenter: parent.verticalCenter
    height: root.thickness
    radius: height / 2
    color: root.theme.faint
  }

  Rectangle {
    anchors.left: track.left
    anchors.verticalCenter: track.verticalCenter
    visible: root.value >= 0
    height: track.height
    radius: track.radius
    width: track.width * root.clamped
    color: root.alarming ? root.theme.badInk : (root.warning ? root.theme.warnInk : root.fill)

    Behavior on width {
      NumberAnimation { duration: root.theme.moveMs(160); easing.type: Easing.OutCubic }
    }

    Behavior on color {
      ColorAnimation { duration: root.theme.fadeMs(160) }
    }
  }
}
