import QtQuick
import qs.Commons

// The chip that opens a group of jobs: "Today  Mon 14 Sep", "Now", "Needs attention".
// A pill, because it describes. Today and the live groups carry a tint so the eye
// finds the present first; a day the pointer is dragging a job onto fills with the
// accent, which is the only way this header ever looks pressed.
Item {
  id: root

  required property var theme

  property string groupKey: ""
  property string word: ""
  property string date: ""
  property int count: 0
  property bool dropHere: false

  readonly property color hue: {
    if (root.groupKey === "attention") return root.theme.warnInk
    if (root.groupKey === "now") return root.theme.accentInk
    if (root.word === "Today") return root.theme.accentInk
    return root.theme.fg
  }
  readonly property bool tinted: root.groupKey === "attention" || root.groupKey === "now" || root.word === "Today"
  // "Thu" beside "Thu 17 Sep" says the weekday twice. The word only adds something for
  // Today, Tomorrow, Yesterday and the named groups; otherwise the date takes its place.
  readonly property bool wordRepeats: root.word !== "" && root.date.indexOf(root.word + " ") === 0
  readonly property string headWord: root.wordRepeats ? root.date : root.word

  implicitHeight: Style.space(30)
  implicitWidth: chip.width

  Rectangle {
    id: chip
    anchors.left: parent.left
    anchors.leftMargin: Style.space(8)
    anchors.verticalCenter: parent.verticalCenter
    height: Style.space(20)
    width: label.implicitWidth + Style.space(10) * 2
    radius: height / 2
    color: root.dropHere ? Util.alpha(root.theme.accent, 0.26)
      : (root.tinted ? Util.alpha(root.hue, 0.13) : Util.alpha(root.theme.fg, 0.08))
    border.width: root.dropHere || root.tinted ? 1 : 0
    border.color: root.dropHere ? root.theme.accent : Util.alpha(root.hue, 0.55)

    Behavior on color {
      ColorAnimation { duration: root.theme.fadeMs(160) }
    }

    Row {
      id: label
      anchors.centerIn: parent
      spacing: Style.space(8)

      Text {
        anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText
        text: root.headWord
        color: root.tinted ? root.hue : root.theme.strong
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.label
        font.bold: true
        font.letterSpacing: root.theme.type.tracking
      }

      Text {
        anchors.verticalCenter: parent.verticalCenter
        visible: root.date !== "" && !root.wordRepeats
        textFormat: Text.PlainText
        text: root.date
        color: root.theme.soft
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.meta
      }

      // "·  3 jobs": the count is set apart from the date, so it never reads as part of it.
      Text {
        anchors.verticalCenter: parent.verticalCenter
        visible: root.count > 1
        textFormat: Text.PlainText
        text: "·"
        color: root.theme.soft
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.meta
      }

      Text {
        anchors.verticalCenter: parent.verticalCenter
        visible: root.count > 1
        textFormat: Text.PlainText
        text: root.count + " jobs"
        color: root.theme.readable
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.meta
        font.features: root.theme.type.digits
      }
    }
  }

  // The day rule: a hairline from the chip to the right edge.
  Rectangle {
    anchors.left: chip.right
    anchors.leftMargin: Style.space(10)
    anchors.right: parent.right
    anchors.rightMargin: Style.space(8)
    anchors.verticalCenter: parent.verticalCenter
    height: Math.max(1, Style.space(1))
    color: root.theme.faint
  }
}
