import QtQuick

// Lint and offscreen-test stand-in for qs.Ui PanelKeyCatcher: keys in, semantic signals out.
Item {
  id: root

  property bool blocked: false

  signal moveRequested(int dx, int dy)
  signal activateRequested()
  signal returnRequested()
  signal closeRequested()
  signal deleteRequested()
  signal tabRequested(int direction)
  signal textKey(string text)

  focus: true
  Keys.priority: Keys.BeforeItem
  Keys.onPressed: function (event) {
    if (root.blocked) return
    if (event.key === Qt.Key_Escape) { root.closeRequested(); event.accepted = true; return }
    if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) {
      root.tabRequested((event.modifiers & Qt.ShiftModifier) || event.key === Qt.Key_Backtab ? -1 : 1)
      event.accepted = true
      return
    }
    if (event.key === Qt.Key_Down) { root.moveRequested(0, 1); event.accepted = true; return }
    if (event.key === Qt.Key_Up) { root.moveRequested(0, -1); event.accepted = true; return }
    if (event.key === Qt.Key_Right) { root.moveRequested(1, 0); event.accepted = true; return }
    if (event.key === Qt.Key_Left) { root.moveRequested(-1, 0); event.accepted = true; return }
    if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
      root.returnRequested()
      root.activateRequested()
      event.accepted = true
      return
    }
    if (event.text && event.text.length === 1) root.textKey(event.text)
  }
}
