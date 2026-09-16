import QtQuick

// Lint and offscreen-test stand-in for Quickshell.Io.Process. It never starts anything.
// `environment` is var: a JS object literal assigned to the real QVariantHash warns.
QtObject {
  id: root

  property var command: []
  property var environment: ({})
  property bool clearEnvironment: false
  property string workingDirectory: ""
  property bool stdinEnabled: false
  property bool running: false
  property var stdout: null
  property var stderr: null
  readonly property var processId: null

  signal started()
  signal exited(int exitCode, int exitStatus)

  function write(data) {
  }
}
