import QtQuick
import qs.Commons
import qs.Ui

// A chip is a pill or a box, and the shape says which kind of thing it is (R6 5.6).
// Pills describe: agents, states, day groups, and filters over those. Boxes are
// pressed: times, levels, buttons, fields. A word that names a state is lowercase.
// State changes fill and border, never the shape.
//
// Visuals follow `hasCursor`, which the view sets from the keyboard cursor or from
// `hovered`, so there is one highlight on screen whichever hand is moving.
Item {
  id: root

  required property var theme

  property string text: ""
  property string glyph: ""
  // Shows the agent's mark before the text, and inks the text in its hue.
  property string harness: ""
  property bool pill: false
  property color tint: root.harness !== "" ? root.theme.harnessInk(root.harness) : root.theme.accent
  // Words and glyph in the tint instead of the neutral ink (agents, state filters).
  property bool inkText: root.harness !== ""
  property bool selected: false
  property bool hasCursor: false
  // A count or caption after the text.
  property string note: ""
  property color noteColor: root.theme.soft
  // 0..1 draws a 24x4 meter inside the chip (the "At reset" chip).
  property real meterValue: -1
  // Ignored: the type system has no italic role. Kept so callers stay valid.
  property bool italic: false

  readonly property bool hovered: mouse.containsMouse
  readonly property bool pressed: mouse.pressed

  signal clicked()

  readonly property real padX: root.pill ? Style.space(10) : Style.space(11)

  readonly property color fillColor: {
    if (root.pill) return Util.alpha(root.tint, root.selected ? 0.26 : 0.13)
    if (root.selected) return Util.alpha(root.tint, 0.26)
    return Util.alpha(root.theme.fg, root.hasCursor ? 0.12 : 0.07)
  }

  readonly property var borderSpec: {
    if (root.pill) return Border.flat(Util.alpha(root.tint, root.hasCursor ? 0.9 : 0.55), 1)
    return Border.controlSpec(root.hasCursor || root.selected ? "hover-cursor" : "normal", root.theme.fg, root.theme.accent)
  }

  readonly property color textColor: {
    if (root.inkText) return root.tint
    return root.selected || root.hasCursor ? root.theme.fg : root.theme.strong
  }

  implicitHeight: root.pill ? Style.space(26) : Style.space(28)
  implicitWidth: row.implicitWidth + root.padX * 2
  opacity: root.enabled ? 1 : 0.45
  scale: mouse.pressed ? 0.97 : 1

  Behavior on scale {
    NumberAnimation { duration: root.theme.moveMs(100); easing.type: Easing.OutCubic }
  }

  // Fill and border change together with the cursor, with no fade between.
  BorderSurface {
    anchors.fill: parent
    radius: root.pill ? height / 2 : Style.cornerRadius
    color: root.fillColor
    borderSpec: root.borderSpec
  }

  Row {
    id: row
    anchors.centerIn: parent
    spacing: Style.space(6)

    AgentMark {
      anchors.verticalCenter: parent.verticalCenter
      visible: root.harness !== ""
      theme: root.theme
      agent: root.harness
      size: Style.space(12)
    }

    Text {
      anchors.verticalCenter: parent.verticalCenter
      visible: root.glyph !== ""
      textFormat: Text.PlainText
      text: root.glyph
      color: root.textColor
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.glyph
    }

    Text {
      anchors.verticalCenter: parent.verticalCenter
      visible: root.text !== ""
      textFormat: Text.PlainText
      text: root.text
      color: root.textColor
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.data
      font.features: root.theme.type.digits
      font.bold: root.pill && root.selected
    }

    Meter {
      anchors.verticalCenter: parent.verticalCenter
      visible: root.meterValue >= 0
      theme: root.theme
      width: Style.space(24)
      thickness: Style.space(4)
      value: root.meterValue
      alarming: root.meterValue >= 0.9
      fill: root.tint
    }

    Text {
      anchors.verticalCenter: parent.verticalCenter
      visible: root.note !== ""
      textFormat: Text.PlainText
      text: root.note
      color: root.noteColor
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
      font.features: root.theme.type.digits
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
