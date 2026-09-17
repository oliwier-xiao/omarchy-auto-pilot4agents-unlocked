import QtQuick
import qs.Commons

// Lint and offscreen-test stand-in for qs.Ui KeyboardPanel. Upstream it is a layer-shell
// PanelWindow anchored to the bar; here it is a plain Item with the same public members,
// so a panel can be linted and instantiated offscreen without a compositor.
Item {
  id: root

  required property Item anchorItem
  required property var bar
  property var owner: null
  property int margin: Style.gapsOut
  property int padding: Style.spacing.popupPadding
  property int contentWidth: Style.space(280)
  property int contentHeight: Style.space(200)
  property var borderSpec: Border.surfaceSpec("popups", "border", Color.popups.border, Math.max(1, Style.space(2)))
  property bool centerOnBar: false
  property bool open: false
  property int gap: Style.gapsOut
  property bool popoutSwitching: false
  property bool popoutSwitchClosing: false
  property bool focusPrimed: false
  property Item focusTarget: null

  default property alias contentItem: contentHolder.children

  readonly property var coordinatorKey: owner || root
  readonly property string barPos: bar && bar.position ? String(bar.position) : "top"
  readonly property real availableCardWidth: 0
  readonly property real availableCardHeight: 0
  readonly property real verticalContentInset: padding * 2 + Border.top(borderSpec) + Border.bottom(borderSpec)

  function close() {
    open = false
  }

  function fittedContentWidth(width, cap) {
    var desired = Math.max(1, Number(width) || 1)
    var maxWidth = root.availableCardWidth > 0 ? root.availableCardWidth : desired
    if (cap !== undefined && Number(cap) > 0) maxWidth = Math.min(maxWidth, Number(cap))
    return Math.round(Math.min(desired, maxWidth))
  }

  function fittedContentHeight(implicitHeight, cap) {
    var desired = Math.max(root.verticalContentInset, (Number(implicitHeight) || 0) + root.verticalContentInset)
    var maxHeight = root.availableCardHeight > 0 ? root.availableCardHeight : desired
    if (cap !== undefined && Number(cap) > 0) maxHeight = Math.min(maxHeight, Number(cap))
    return Math.round(Math.min(desired, maxHeight))
  }

  function cappedContentHeight(height) {
    return root.fittedContentHeight(height)
  }

  visible: open
  // As upstream: the card is exactly contentWidth x contentHeight and the content
  // sits inside the padding and the border (BorderSurface's content insets), so an
  // offscreen test lays a panel out at the size it gets on screen.
  width: contentWidth
  height: contentHeight

  Rectangle {
    anchors.fill: parent
    z: -1
    color: Color.popups.background
    border.color: Border.color(root.borderSpec)
    border.width: Border.uniformWidth(root.borderSpec)
  }

  onOpenChanged: {
    if (open && focusTarget) Qt.callLater(function () { if (root.focusTarget) root.focusTarget.forceActiveFocus() })
  }

  Item {
    id: contentHolder
    x: root.padding + Border.left(root.borderSpec)
    y: root.padding + Border.top(root.borderSpec)
    width: Math.max(0, root.contentWidth - root.padding * 2 - Border.left(root.borderSpec) - Border.right(root.borderSpec))
    height: Math.max(0, root.contentHeight - root.verticalContentInset)
  }
}
