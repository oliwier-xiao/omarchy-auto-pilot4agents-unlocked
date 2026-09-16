import QtQuick

// Lint and offscreen-test stand-in for Quickshell.Io.SplitParser.
QtObject {
  id: root

  property string splitMarker: "\n"

  signal read(string data)
}
