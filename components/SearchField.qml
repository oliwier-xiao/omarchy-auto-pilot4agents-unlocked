import QtQuick
import qs.Commons
import qs.Ui

// A display of the filter, not an editor. The view's key catcher owns the keyboard
// and sets `text`; a focused TextInput here would eat every key. So the field draws
// what an editor would say: a caret at the insertion point, blinking at the 530 ms
// every reader is calibrated to, and an I-beam under the pointer.
Item {
  id: root

  required property var theme

  property string text: ""
  property string placeholder: "Type to search"
  property bool active: false
  // Right-aligned caption, e.g. "4 armed".
  property string trailing: ""

  readonly property bool blinking: root.active && root.visible && root.theme.animate === true

  implicitHeight: Style.spacing.controlHeight
  implicitWidth: Style.space(320)

  BorderSurface {
    anchors.fill: parent
    radius: Style.cornerRadius
    color: Util.alpha(root.theme.fg, root.active ? 0.10 : 0.05)
    borderSpec: Border.controlSpec(root.active ? "hover-cursor" : "normal", root.theme.fg, root.theme.accent)

    HoverHandler { cursorShape: Qt.IBeamCursor }
  }

  Text {
    id: lens
    anchors.left: parent.left
    anchors.leftMargin: Style.spacing.controlPaddingX
    anchors.verticalCenter: parent.verticalCenter
    textFormat: Text.PlainText
    text: "\uDB80\uDF49"   // md-magnify U+F0349
    color: root.theme.soft
    font.family: root.theme.fontFamily
    // A glyph at the body size on purpose: no icon token equals 12 at defaults.
    font.pixelSize: Style.font.body
  }

  Text {
    id: shown
    anchors.left: lens.right
    anchors.leftMargin: Style.space(10)
    anchors.verticalCenter: parent.verticalCenter
    width: Math.max(0, Math.min(implicitWidth, (tail.visible ? tail.x : root.width) - x - Style.space(12)))
    textFormat: Text.PlainText
    text: root.text !== "" ? root.text : root.placeholder
    color: root.text !== "" ? root.theme.fg : root.theme.soft
    // Elided at the front, so the end being typed stays on screen.
    elide: Text.ElideLeft
    maximumLineCount: 1
    font.family: root.theme.fontFamily
    font.pixelSize: root.theme.type.body
  }

  // At the insertion point: before the placeholder when nothing is typed, after
  // the last character otherwise.
  Rectangle {
    id: caret
    anchors.left: root.text === "" ? shown.left : shown.right
    anchors.leftMargin: root.text === "" ? -Style.space(4) : Style.space(1)
    anchors.verticalCenter: parent.verticalCenter
    width: Math.max(1, Style.space(1))
    height: root.theme.type.body + Style.space(2)
    color: root.theme.accent
    visible: root.active
  }

  SequentialAnimation {
    running: root.blinking
    loops: Animation.Infinite
    PropertyAnimation { target: caret; property: "opacity"; to: 1; duration: 0 }
    PauseAnimation { duration: 530 }
    PropertyAnimation { target: caret; property: "opacity"; to: 0; duration: 0 }
    PauseAnimation { duration: 530 }
    onRunningChanged: if (!running) caret.opacity = 1
  }

  Text {
    id: tail
    anchors.right: parent.right
    anchors.rightMargin: Style.spacing.controlPaddingX
    anchors.verticalCenter: parent.verticalCenter
    visible: root.trailing !== ""
    textFormat: Text.PlainText
    text: root.trailing
    color: root.theme.soft
    font.family: root.theme.fontFamily
    font.pixelSize: root.theme.type.meta
    font.features: root.theme.type.digits
  }
}
