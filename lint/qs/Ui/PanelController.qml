import QtQuick

// Lint and offscreen-test stand-in for qs.Ui PanelController: the open state of a panel.
QtObject {
  id: root

  property bool open: false

  function toggle() { open = !open }
  function show() { if (!open) open = true }
  function hide() { open = false }
}
