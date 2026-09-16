pragma Singleton
import QtQuick

// Lint and offscreen-test stand-in for the Quickshell application singleton.
// Only the members this plugin reads or writes. The detached-process launchers of the
// real singleton are deliberately absent: a call to one fails the lint run.
QtObject {
  id: root

  property var screens: []
  property string shellDir: ""
  property string clipboardText: ""

  function env(name) {
    return ""
  }
}
