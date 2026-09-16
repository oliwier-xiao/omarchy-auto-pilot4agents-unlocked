import QtQuick
import qs.Commons
import qs.Ui

// A checkbox row: a box that fills and shows a check when on, the words beside it and
// a caption under them. A checkbox is pressed, so its shape is a box (R6 5.6). Space
// toggles while the row has the cursor (the owner routes the key to `handleKey`), and
// a click anywhere on the row toggles too.
//
// The row never changes its own state: it asks with `toggled(next)` and shows what
// the owner binds back, so a refused change never looks accepted.
Item {
  id: root

  required property var theme

  property string text: ""
  property bool checked: false
  property bool hasCursor: false
  property string caption: ""
  // soft | readable | warn | ok | bad
  property string captionTone: "soft"
  // The filled box. Allow paid usage passes the warning amber.
  property color tint: root.theme.accent
  // A small pill after the words, such as an OpenCode model's billing class.
  property string badge: ""
  property color badgeColor: root.theme.readable

  readonly property bool hovered: mouse.containsMouse

  signal toggled(bool checked)

  readonly property color captionColor: {
    if (root.captionTone === "warn") return root.theme.warnInk
    if (root.captionTone === "ok") return root.theme.okInk
    if (root.captionTone === "bad") return root.theme.badInk
    if (root.captionTone === "readable") return root.theme.readable
    return root.theme.soft
  }

  function toggle() {
    if (root.enabled) root.toggled(!root.checked)
  }

  function handleKey(event) {
    if ((event.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)) !== 0) return false
    if (event.key === Qt.Key_Space) {
      root.toggle()
      return true
    }
    return false
  }

  implicitWidth: Style.space(420)
  implicitHeight: line.height + (captionText.visible ? captionText.implicitHeight + Style.space(2) : 0)
  opacity: root.enabled ? 1 : 0.45

  Item {
    id: line
    width: parent.width
    height: Style.space(28)

    BorderSurface {
      id: box
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: Style.space(16)
      height: Style.space(16)
      radius: Math.min(Style.cornerRadius, Style.space(3))
      color: root.checked ? root.tint : Util.alpha(root.theme.fg, root.hasCursor || root.hovered ? 0.12 : 0.06)
      borderSpec: root.checked
        ? Border.flat(root.tint, 1)
        : Border.controlSpec(root.hasCursor ? "hover-cursor" : "normal", root.theme.fg, root.theme.accent)

      Text {
        anchors.centerIn: parent
        visible: root.checked
        textFormat: Text.PlainText
        text: "󰄬"   // md-check U+F012C
        color: root.theme.surface
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.glyph
      }
    }

    Text {
      id: label
      anchors.left: box.right
      anchors.leftMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      width: Math.min(implicitWidth, line.width - x - (badgePill.visible ? badgePill.width + Style.space(8) : 0))
      textFormat: Text.PlainText
      text: root.text
      color: root.hasCursor ? root.theme.fg : root.theme.strong
      elide: Text.ElideRight
      maximumLineCount: 1
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.data
    }

    Rectangle {
      id: badgePill
      anchors.left: label.right
      anchors.leftMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      visible: root.badge !== ""
      width: badgeText.implicitWidth + Style.space(12)
      height: Style.space(18)
      radius: height / 2
      color: Util.alpha(root.badgeColor, 0.14)
      border.width: 1
      border.color: Util.alpha(root.badgeColor, 0.55)

      Text {
        id: badgeText
        anchors.centerIn: parent
        textFormat: Text.PlainText
        text: root.badge
        color: root.badgeColor
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.meta
      }
    }

    MouseArea {
      id: mouse
      anchors.fill: parent
      enabled: root.enabled
      hoverEnabled: true
      cursorShape: Qt.PointingHandCursor
      onClicked: root.toggle()
    }
  }

  Text {
    id: captionText
    anchors.top: line.bottom
    anchors.topMargin: Style.space(2)
    x: label.x
    width: Math.max(0, parent.width - x)
    visible: root.caption !== ""
    textFormat: Text.PlainText
    text: root.caption
    color: root.captionColor
    wrapMode: Text.WordWrap
    maximumLineCount: 3
    elide: Text.ElideRight
    font.family: root.theme.fontFamily
    font.pixelSize: root.theme.type.meta
  }
}
