import QtQuick
import qs.Commons
import qs.Ui

// A pressed thing, so a box. `primary` is the one action a view leads with (Arm);
// `danger` inks it with the failure colour; `armed` is the first press of a
// two-press guard, lit until the second press or until the guard lapses. `quiet` is
// an action that sits inside a line of text (Undo in the notice row): accent words and
// no box until it has the cursor, so it never reads as a third main button.
Item {
  id: root

  required property var theme

  property string text: ""
  property string glyph: ""
  // Shown after the text in the soft ink, e.g. "Ctrl+Enter".
  property string shortcut: ""
  property bool primary: false
  property bool danger: false
  property bool hasCursor: false
  property bool armed: false
  property bool quiet: false
  // The widest text this button ever shows ("Run now" for a button that also says Arm),
  // so a change of words never moves what sits beside it.
  property string widthTemplate: ""

  readonly property bool hovered: mouse.containsMouse
  readonly property bool pressed: mouse.pressed

  signal clicked()

  readonly property color tint: root.danger ? root.theme.badInk : root.theme.accent
  // Visuals follow the cursor only (the CursorSurface rule): the view moves its
  // cursor here on hover, so keyboard and mouse never light two things at once.
  readonly property bool hot: root.hasCursor

  readonly property color fillColor: {
    if (root.quiet) return root.hot ? Util.alpha(root.theme.fg, 0.12) : "transparent"
    if (root.armed) return Util.alpha(root.tint, 0.46)
    if (root.primary) return Util.alpha(root.tint, root.hot ? 0.46 : 0.30)
    return Util.alpha(root.theme.fg, root.hot ? 0.12 : 0.07)
  }

  readonly property var borderSpec: {
    if (root.quiet && !root.hot) return Border.none()
    if (root.armed) return Border.flat(root.tint, Math.max(1, Style.space(2)))
    if (root.danger && root.hasCursor) return Border.flat(Util.alpha(root.tint, 0.9), 1)
    return Border.controlSpec(root.hasCursor || root.primary ? "hover-cursor" : "normal", root.theme.fg, root.theme.accent)
  }

  readonly property color textColor: root.quiet ? root.theme.accentInk
    : root.danger ? root.theme.badInk
    : (root.primary || root.armed || root.hasCursor ? root.theme.fg : root.theme.strong)

  implicitHeight: Style.spacing.controlHeight
  // Whole pixels: a fractional width lets a right-aligned row of buttons spill one pixel
  // past its column, and that pixel shows beside an open sheet.
  implicitWidth: Math.ceil(Math.max(row.implicitWidth,
                                    row.implicitWidth - label.implicitWidth + (root.widthTemplate !== "" ? widthMetrics.advanceWidth : 0)))
    + Style.space(12) * 2
  opacity: root.enabled ? 1 : 0.45
  scale: mouse.pressed ? 0.97 : 1

  Behavior on scale {
    NumberAnimation { duration: root.theme.moveMs(100); easing.type: Easing.OutCubic }
  }

  TextMetrics {
    id: widthMetrics
    font: label.font
    text: root.widthTemplate
  }

  // Fill and border change together with the cursor: no fade, so a key press never
  // shows half a highlight.
  BorderSurface {
    anchors.fill: parent
    radius: Style.cornerRadius
    color: root.fillColor
    borderSpec: root.borderSpec
  }

  Row {
    id: row
    anchors.centerIn: parent
    spacing: Style.space(6)

    Text {
      anchors.verticalCenter: parent.verticalCenter
      visible: root.glyph !== ""
      textFormat: Text.PlainText
      text: root.glyph
      color: root.textColor
      font.family: root.theme.fontFamily
      // A glyph at the body size on purpose: no icon token equals 12 at defaults.
      font.pixelSize: Style.font.body
    }

    Text {
      id: label
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: root.text
      color: root.textColor
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.body
      font.bold: root.primary
    }

    Text {
      anchors.verticalCenter: parent.verticalCenter
      visible: root.shortcut !== ""
      textFormat: Text.PlainText
      text: root.shortcut
      color: root.theme.soft
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
    onClicked: root.clicked()
  }
}
