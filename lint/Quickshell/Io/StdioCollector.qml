import QtQuick

// Lint and offscreen-test stand-in for Quickshell.Io.StdioCollector.
QtObject {
  id: root

  property bool waitForEnd: true
  readonly property string text: ""

  signal streamFinished()
}
