import QtQuick
import qs.Commons

// Lint and offscreen-test stand-in for qs.Ui PanelSeparator: a one pixel rule.
Rectangle {
  id: root

  property color foreground: Color.foreground
  property real strength: 0.12

  implicitWidth: 100
  implicitHeight: 1
  height: 1
  color: Qt.rgba(foreground.r, foreground.g, foreground.b, strength)
}
