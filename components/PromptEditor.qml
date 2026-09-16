import QtQuick
import QtQuick.Controls
import qs.Commons
import qs.Ui
import "../lib/Model.js" as Model

// The prompt, typed in a real editor: selection, the mouse, paste, the input method
// (Polish diacritics through fcitx5) and undo all come from Qt's own TextArea.
//
// It is the one place in the panel that owns the keyboard while it has focus. Keys
// that mean something to the panel are handed back instead of being typed: Esc
// leaves the editor, Ctrl+Enter arms, Tab moves to the next section, and the global
// Ctrl commands go back through `globalKey`.
//
// Seam for a fallback editor (CONTRACT C13): ComposeView only uses `text`, `bytes`,
// `editing`, `focusEditor()`, `leave()`, `insertText()` and the three signals. If a
// live check shows the input method or paste failing inside the layer-shell panel,
// only the inside of this file changes (a key-catcher-fed text plus a Paste action);
// nothing that uses it does.
Item {
  id: root

  required property var theme

  // The prompt as plain text. It reaches the helper only on its stdin.
  property alias text: area.text
  // The helper refuses more than 64 KiB of UTF-8, so the editor never holds more.
  property int maxBytes: 65536
  property bool hasCursor: false
  property string placeholder: "Write the prompt. It goes to the agent on stdin when the job fires."
  // One sentence about the prompt as typed, shown under it; "" hides the line.
  property string problemText: ""

  readonly property int bytes: root._bytes
  readonly property bool editing: area.activeFocus

  signal submitRequested()
  signal escapePressed()
  signal globalKey(var event)
  // An edit was refused because it would pass maxBytes.
  signal capRefused()

  property int _bytes: 0
  property string _accepted: ""
  property int _acceptedCursor: 0
  property bool _reverting: false

  function focusEditor() {
    if (!area.activeFocus) area.forceActiveFocus()
  }

  // Gives the keyboard back. The caller moves focus to the panel's key catcher.
  function leave() {
    if (area.activeFocus) area.focus = false
  }

  // For the first key typed while the section had the cursor but not the focus.
  function insertText(value) {
    var t = String(value === undefined || value === null ? "" : value)
    if (t === "") return
    area.insert(area.cursorPosition, t)
  }

  // Nothing under 1 KiB, where the size cannot matter; then "12 of 64 KiB", rounded up.
  readonly property string counterText: {
    if (root._bytes < 1024) return ""
    return Math.ceil(root._bytes / 1024) + " of " + Math.round(root.maxBytes / 1024) + " KiB"
  }

  implicitWidth: Style.space(640)
  implicitHeight: Style.space(300)

  BorderSurface {
    anchors.fill: parent
    radius: Style.cornerRadius
    color: Util.alpha(root.theme.fg, root.editing ? 0.07 : (root.hasCursor ? 0.06 : 0.04))
    borderSpec: Border.controlSpec(root.editing ? "focus" : (root.hasCursor ? "hover-cursor" : "normal"),
                                   root.theme.fg, root.theme.accent)
  }

  ScrollView {
    id: scroll
    anchors.fill: parent
    anchors.leftMargin: Style.space(12)
    anchors.rightMargin: Style.space(6)
    anchors.topMargin: Style.space(10)
    anchors.bottomMargin: Math.max(counter.height, root.problemText !== "" ? problemRow.height : 0) + Style.space(10)
    clip: true
    ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

    TextArea {
      id: area
      textFormat: TextEdit.PlainText
      wrapMode: TextEdit.Wrap
      selectByMouse: true
      persistentSelection: true
      padding: 0
      rightPadding: Style.space(8)
      background: null
      color: root.theme.fg
      selectionColor: Util.alpha(root.theme.accent, 0.35)
      selectedTextColor: root.theme.fg
      placeholderText: root.placeholder
      placeholderTextColor: root.theme.soft
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.body

      // An edit that would pass the cap is taken back whole, cursor included, so
      // a large paste never leaves half of itself behind.
      onTextChanged: {
        if (root._reverting) return
        var n = Model.utf8Bytes(area.text)
        if (n > root.maxBytes) {
          root._reverting = true
          area.text = root._accepted
          area.cursorPosition = Math.min(root._acceptedCursor, area.length)
          root._reverting = false
          root.capRefused()
          return
        }
        root._accepted = area.text
        root._bytes = n
      }

      onCursorPositionChanged: if (!root._reverting) root._acceptedCursor = area.cursorPosition

      Keys.onPressed: function (event) {
        var ctrl = (event.modifiers & Qt.ControlModifier) !== 0
        var key = event.key
        if (key === Qt.Key_Escape) {
          event.accepted = true
          root.escapePressed()
          return
        }
        if (ctrl && (key === Qt.Key_Return || key === Qt.Key_Enter)) {
          event.accepted = true
          root.submitRequested()
          return
        }
        // Tab moves on through the form; a prompt has no use for a tab character.
        if (key === Qt.Key_Tab || key === Qt.Key_Backtab) {
          event.accepted = true
          root.globalKey(event)
          return
        }
        if (ctrl && (key === Qt.Key_1 || key === Qt.Key_2 || key === Qt.Key_3 || key === Qt.Key_Comma
                     || key === Qt.Key_N || key === Qt.Key_S || key === Qt.Key_PageUp || key === Qt.Key_PageDown)) {
          event.accepted = true
          root.globalKey(event)
        }
      }
    }
  }

  // Clicks below the last line still land in the editor.
  MouseArea {
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.bottom: parent.bottom
    height: counter.height + Style.space(10)
    cursorShape: Qt.IBeamCursor
    onClicked: {
      root.focusEditor()
      area.cursorPosition = area.length
    }
  }

  // A prompt the chosen agent would misread (Pi takes a leading "/" as a command): the
  // fact and the fix, in the warning ink beside its glyph, where the text is typed.
  Row {
    id: problemRow
    anchors.left: parent.left
    anchors.leftMargin: Style.space(12)
    anchors.right: counter.visible ? counter.left : parent.right
    anchors.rightMargin: Style.space(10)
    anchors.bottom: parent.bottom
    anchors.bottomMargin: Style.space(6)
    visible: root.problemText !== ""
    spacing: Style.space(6)

    Text {
      id: problemGlyph
      textFormat: Text.PlainText
      text: "󰀨"   // md-alert_circle U+F0028
      color: root.theme.warnInk
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
    }

    Text {
      width: Math.max(0, problemRow.width - problemGlyph.width - problemRow.spacing)
      textFormat: Text.PlainText
      text: root.problemText
      color: root.theme.warnInk
      elide: Text.ElideRight
      maximumLineCount: 1
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
    }
  }

  Text {
    id: counter
    visible: root.counterText !== ""
    anchors.right: parent.right
    anchors.bottom: parent.bottom
    anchors.rightMargin: Style.space(10)
    anchors.bottomMargin: Style.space(6)
    textFormat: Text.PlainText
    text: root.counterText
    color: root._bytes > root.maxBytes * 0.9 ? root.theme.warnInk : root.theme.soft
    font.family: root.theme.fontFamily
    font.pixelSize: root.theme.type.meta
    font.features: root.theme.type.digits
  }
}
