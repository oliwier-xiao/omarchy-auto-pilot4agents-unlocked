pragma ComponentBehavior: Bound
import QtQuick
import qs.Commons

// The key hints at the bottom of the card. The active view or sheet composes the
// sentence (already joined with "  ·  "); this draws it. Each hint is a key and a verb,
// inked apart: the key in the readable ink, the verb in the soft one, so the eye finds
// the keys first. A lone sentence with no key (the second press of a guard) is the one
// thing that matters at that moment, so it reads in the readable ink as a whole.
//
// The pieces carry their own spaces, so the drawn line is exactly as wide as the joined
// string and stays centred on one line wherever the string would.
Item {
  id: root

  required property var theme

  property string hints: ""

  // [{key, verb}]; key is "" for a piece that does not start with a key.
  readonly property var segments: {
    var text = String(root.hints || "")
    if (text === "") return []
    var parts = text.split("  ·  ")
    var out = []
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i]
      if (p === "") continue
      var m = /^(Ctrl\+\S+|Shift\+\S+|Shift|Esc|Enter|Tab|Delete|Backspace|Space|0-9|j\/k|←\/→|↑\/↓|[a-z])\s(.+)$/.exec(p)
      out.push(m ? { key: m[1], verb: m[2] } : { key: "", verb: p })
    }
    return out
  }
  readonly property bool sentence: root.segments.length === 1 && root.segments[0].key === ""

  implicitWidth: Style.space(1100)
  implicitHeight: Style.space(34)
  clip: true

  TextMetrics {
    id: oneLine
    font.family: root.theme.fontFamily
    font.pixelSize: root.theme.type.meta
    font.features: root.theme.type.digits
    text: root.hints
  }

  Text {
    anchors.fill: parent
    anchors.leftMargin: Style.space(8)
    anchors.rightMargin: Style.space(8)
    visible: root.sentence
    textFormat: Text.PlainText
    text: root.sentence ? root.segments[0].verb : ""
    color: root.theme.readable
    horizontalAlignment: Text.AlignHCenter
    verticalAlignment: Text.AlignVCenter
    wrapMode: Text.WordWrap
    maximumLineCount: 2
    elide: Text.ElideRight
    font.family: root.theme.fontFamily
    font.pixelSize: root.theme.type.meta
    font.features: root.theme.type.digits
  }

  Flow {
    anchors.centerIn: parent
    visible: !root.sentence
    width: Math.max(0, Math.min(root.width - Style.space(16), Math.ceil(oneLine.advanceWidth) + Style.space(4)))

    Repeater {
      model: root.sentence ? [] : root.segments

      Row {
        id: piece
        required property var modelData
        required property int index

        Text {
          visible: piece.index > 0
          // A piece that wrapped to the start of the second line drops its dot.
          opacity: piece.x > 0 ? 1 : 0
          textFormat: Text.PlainText
          text: "  ·"
          color: root.theme.soft
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
          font.features: root.theme.type.digits
        }

        Text {
          visible: piece.modelData.key !== ""
          textFormat: Text.PlainText
          text: (piece.index > 0 ? "  " : "") + piece.modelData.key
          color: root.theme.readable
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
          font.features: root.theme.type.digits
        }

        Text {
          textFormat: Text.PlainText
          text: (piece.modelData.key !== "" ? " " : (piece.index > 0 ? "  " : "")) + piece.modelData.verb
          color: root.theme.soft
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
          font.features: root.theme.type.digits
        }
      }
    }
  }
}
