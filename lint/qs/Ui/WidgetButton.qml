import QtQuick
import qs.Commons

// Lint and offscreen-test stand-in for qs.Ui WidgetButton, the bar's glyph-and-label button.
Item {
  id: root

  property var bar: null
  property string text: ""
  property string fontFamily: bar && bar.fontFamily ? bar.fontFamily : Style.font.family
  property real fontSize: Style.font.body
  property color foreground: bar && bar.barForeground ? bar.barForeground : Color.foreground
  property color activeColor: bar && bar.urgent ? bar.urgent : Color.urgent
  property bool active: false
  property real horizontalMargin: 8.5
  property real verticalPadding: 6
  property real fixedWidth: -1
  property real fixedHeight: -1
  property real textRotation: 0
  property bool keepSpace: false
  property bool dimmed: false
  property bool concealed: false
  property bool interactive: true
  property bool pressable: true
  property bool useActiveColor: true
  property bool maintainIndicatorReveal: false
  property bool labelVisible: true
  property bool hasVisualContent: text !== ""
  property var revealHost: bar
  property string tooltipText: ""
  property var registeredBar: null

  signal pressed(int button)
  signal wheelMoved(int delta)

  readonly property bool vertical: bar ? bar.vertical === true : false
  readonly property int barSize: bar && bar.barSize ? bar.barSize : Style.bar.sizeHorizontal
  readonly property real scaledHorizontalMargin: Style.spaceReal(horizontalMargin)
  readonly property real scaledVerticalPadding: Style.spaceReal(verticalPadding)
  readonly property bool tooltipHovered: visible && interactive && !concealed && mouseArea.containsMouse
  readonly property real labelWidth: label.visible ? label.implicitWidth : 0

  function triggerPress(button) {
    root.hideOwnTooltip()
    root.pressed(button)
  }

  function hideOwnTooltip() {
    if (root.bar && typeof root.bar.hideTooltip === "function") root.bar.hideTooltip(root)
  }

  visible: hasVisualContent || keepSpace
  opacity: !hasVisualContent || concealed ? 0 : (dimmed ? 0.45 : 1)
  implicitWidth: fixedWidth > 0 ? fixedWidth : (vertical ? barSize : Math.max(12, label.implicitWidth + scaledHorizontalMargin * 2))
  implicitHeight: fixedHeight > 0 ? fixedHeight : (vertical ? Math.max(12, label.implicitHeight + scaledVerticalPadding * 2) : barSize)

  Text {
    id: label
    textFormat: Text.PlainText
    visible: root.labelVisible
    anchors.centerIn: parent
    text: root.text
    color: root.active && root.useActiveColor ? root.activeColor : root.foreground
    font.family: root.fontFamily
    font.pixelSize: root.fontSize
    rotation: root.textRotation
  }

  MouseArea {
    id: mouseArea
    anchors.fill: parent
    acceptedButtons: Qt.LeftButton | Qt.RightButton | Qt.MiddleButton
    enabled: root.interactive
    hoverEnabled: true
    onClicked: function (mouse) { if (root.pressable) root.triggerPress(mouse.button) }
    onWheel: function (wheel) { root.wheelMoved(wheel.angleDelta.y) }
  }
}
