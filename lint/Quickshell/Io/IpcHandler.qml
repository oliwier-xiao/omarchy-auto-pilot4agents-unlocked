import QtQuick

// Lint and offscreen-test stand-in for Quickshell.Io.IpcHandler. Handler functions are
// declared at each use site.
QtObject {
  id: root

  property string target: ""
  property bool enabled: true
}
