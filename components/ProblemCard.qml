import QtQuick
import qs.Commons
import qs.Ui

// Shown instead of a view when the helper cannot be used at all: it did not
// answer, answered with something that is not its protocol, or refused its own
// state folder. The message is the helper's fixed sentence (or the service's own
// for a helper that never answered); nothing the helper printed besides it is shown.
Item {
  id: root

  required property var theme

  property string message: ""
  property string detail: ""

  signal retryRequested()

  readonly property real pad: Style.space(16)

  implicitWidth: Style.space(560)
  implicitHeight: column.implicitHeight + root.pad * 2

  BorderSurface {
    anchors.fill: parent
    radius: Style.cornerRadius
    color: Util.alpha(root.theme.badInk, 0.08)
    borderSpec: Border.flat(Util.alpha(root.theme.badInk, 0.55), 1)
  }

  Rectangle {
    anchors.left: parent.left
    anchors.top: parent.top
    anchors.bottom: parent.bottom
    anchors.leftMargin: Style.space(2)
    anchors.topMargin: Style.space(5)
    anchors.bottomMargin: Style.space(5)
    width: Style.space(3)
    radius: width / 2
    color: root.theme.badInk
  }

  Column {
    id: column
    x: root.pad + Style.space(4)
    y: root.pad
    width: root.width - x - root.pad
    spacing: Style.space(10)

    Row {
      width: parent.width
      spacing: Style.space(8)

      Text {
        id: glyph
        textFormat: Text.PlainText
        text: "\uDB80\uDC28"   // md-alert_circle U+F0028
        color: root.theme.badInk
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.glyphLarge
      }

      Text {
        width: parent.width - glyph.width - parent.spacing
        textFormat: Text.PlainText
        text: root.message
        color: root.theme.strong
        wrapMode: Text.WordWrap
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.title
        font.bold: true
      }
    }

    Text {
      width: Math.min(parent.width, root.theme.type.measure)
      visible: root.detail !== ""
      textFormat: Text.PlainText
      text: root.detail
      color: root.theme.readable
      wrapMode: Text.WordWrap
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.body
    }

    ActionButton {
      id: retry
      theme: root.theme
      // The card has no keyboard cursor of its own; the pointer is its cursor.
      hasCursor: retry.hovered
      text: "Try again"
      glyph: "\uDB81\uDC50"   // md-refresh U+F0450
      onClicked: root.retryRequested()
    }
  }
}
