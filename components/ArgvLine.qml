import QtQuick
import qs.Commons
import "../lib/Compose.js" as Compose

// "Will run": the exact command the runner will start, as the helper's `preview` verb renders it
// from the same templates the runner uses, with the prompt shown as <stdin> because
// that is the only way it ever reaches the agent. Under it, where it runs, which
// binary, and what the permission level means for this agent.
//
// The command wraps only between tokens: every token carries word joiners, so a flag
// or a path is never split after its "-" or "/" (Compose.argvWrapText). A command longer
// than `maxLines` keeps its head and its last token, "<stdin>", with "…" between
// (Compose.fitArgv), because the stdin tail is the one part that must stay in sight.
Item {
  id: root

  required property var theme

  property string label: "Will run"
  property string display: ""
  property string cwd: ""
  property string binary: ""
  // The level's caption for this agent, from the helper's `edition` verb.
  property string caption: ""
  property bool loading: false
  property string errorText: ""
  property int maxLines: 3

  readonly property real labelWidth: Style.space(64)
  // Monospace cells per line of the command column.
  readonly property int columns: cell.advanceWidth > 0 ? Math.floor(command.width / (cell.advanceWidth / 10)) : 0
  // The display as it is laid out: fitted to the lines, before the word joiners.
  readonly property string fitted: root.display === "" ? "" : Compose.fitArgv(root.display, root.columns, Math.max(1, root.maxLines))
  readonly property int lineCount: command.lineCount

  implicitWidth: Style.space(640)
  implicitHeight: column.implicitHeight

  TextMetrics {
    id: cell
    font: command.font
    text: "0000000000"
  }

  Column {
    id: column
    width: parent.width
    spacing: Style.space(3)

    Item {
      width: parent.width
      implicitHeight: Math.max(labelText.implicitHeight, command.implicitHeight)

      Text {
        id: labelText
        anchors.left: parent.left
        anchors.top: parent.top
        width: root.labelWidth
        textFormat: Text.PlainText
        text: root.label
        color: root.theme.soft
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.label
        font.letterSpacing: root.theme.type.tracking
        font.bold: true
      }

      Text {
        id: command
        anchors.left: labelText.right
        anchors.right: parent.right
        anchors.top: parent.top
        textFormat: Text.PlainText
        text: root.errorText !== ""
          ? root.errorText
          : (root.display !== "" ? Compose.argvWrapText(root.fitted) : (root.loading ? "Checking the command…" : ""))
        color: root.errorText !== "" ? root.theme.badInk : root.theme.readable
        opacity: root.loading && root.errorText === "" && root.display !== "" ? 0.6 : 1
        wrapMode: root.errorText !== "" ? Text.WordWrap : Text.Wrap
        maximumLineCount: Math.max(1, root.maxLines)
        elide: Text.ElideRight
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.data

        Behavior on opacity {
          NumberAnimation { duration: root.theme.fadeMs(120) }
        }
      }
    }

    Row {
      x: root.labelWidth
      width: parent.width - root.labelWidth
      spacing: Style.space(14)
      visible: root.errorText === "" && (root.cwd !== "" || root.binary !== "")

      Text {
        visible: root.cwd !== ""
        width: Math.min(implicitWidth, root.binary !== "" ? parent.width * 0.5 : parent.width)
        textFormat: Text.PlainText
        text: "in " + root.cwd
        color: root.theme.soft
        elide: Text.ElideMiddle
        maximumLineCount: 1
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.meta
      }

      Text {
        visible: root.binary !== ""
        width: Math.min(implicitWidth, root.cwd !== "" ? parent.width * 0.5 - Style.space(14) : parent.width)
        textFormat: Text.PlainText
        text: root.binary
        color: root.theme.soft
        elide: Text.ElideMiddle
        maximumLineCount: 1
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.meta
      }
    }

    Text {
      x: root.labelWidth
      width: parent.width - root.labelWidth
      visible: root.caption !== "" && root.errorText === ""
      textFormat: Text.PlainText
      text: root.caption
      color: root.theme.readable
      wrapMode: Text.WordWrap
      maximumLineCount: 2
      elide: Text.ElideRight
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
      // Level captions can name CLI flags; a coding font's ligature would join a flag's
      // two leading dashes into one long dash.
      font.features: ({ "liga": 0, "calt": 0 })
    }
  }
}
