import QtQuick
import Quickshell.Io
import qs.Commons

// Lint and offscreen-test stand-in for qs.Ui Panel, the base of plugin popups: open
// state, open/close/toggle, popout switching and the optional IPC handler.
Item {
  id: root

  property var bar: null
  property string moduleName: ""
  property var settings: ({})
  property string ipcTarget: ""
  property bool manageIpc: true
  property alias controller: panelController
  property bool popoutSwitching: false
  property bool popoutSwitchClosing: false

  readonly property bool opened: panelController.open
  readonly property color barForeground: bar && bar.barForeground ? bar.barForeground : Color.foreground

  function open() { panelController.show() }
  function close() { panelController.hide() }

  function closeForPopoutSwitch() {
    popoutSwitchClosing = true
    close()
    Qt.callLater(function () { root.popoutSwitchClosing = false })
  }

  function toggle() {
    if (opened) close()
    else open()
  }

  function switchPanel(direction) {
    if (bar && typeof bar.switchPanelFrom === "function") return bar.switchPanelFrom(root, direction)
    return false
  }

  function setting(name, fallback) {
    var value = settings ? settings[name] : undefined
    return value === undefined || value === null ? fallback : value
  }

  PanelController {
    id: panelController
  }

  IpcHandler {
    enabled: root.manageIpc && root.ipcTarget !== ""
    target: root.ipcTarget

    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.toggle() }
  }
}
