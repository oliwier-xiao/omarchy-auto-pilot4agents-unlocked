import QtQuick
import qs.Commons
import "../lib/Model.js" as Model

// The one line under the view where the panel reports what just happened, with an
// Undo for ten seconds when the action can be taken back. It keeps its height when
// empty, so nothing below it jumps when a notice arrives.
//
// A notice raised inside one view (Compose's "New draft." with its undo) belongs to
// that view and goes away when the user switches to another. Notices from the Queue,
// History, the sheets and the service stay wherever the user goes.
Item {
  id: root

  required property var theme

  readonly property bool undoAvailable: root._shown && root._undo !== null
  readonly property string text: root._shown ? root._text : ""

  signal undoInvoked()

  property string _text: ""
  property string _kind: "info"
  property var _undo: null
  property bool _shown: false
  property int _timeoutMs: 4000
  // "global", or the view that raised the notice.
  property string _origin: "global"
  // The notice fading out had an Undo: the button fades with the words instead of
  // vanishing a frame before them.
  property bool _hadUndo: false

  // kind: info | ok | warn | error. undoFn: a function or null. timeoutMs 0 means
  // 10 s with an undo, 4 s without. origin: the view that raised it, or omitted.
  function show(message, kind, undoFn, timeoutMs, origin) {
    var t = String(message === undefined || message === null ? "" : message)
    if (t === "") { root.clear(); return }
    // Rises into place when it arrives; a notice replacing one on screen changes in place.
    if (!root._shown) liftIn.restart()
    root._text = t
    root._kind = ["info", "ok", "warn", "error"].indexOf(kind) >= 0 ? kind : "info"
    root._undo = typeof undoFn === "function" ? undoFn : null
    root._hadUndo = root._undo !== null
    root._origin = typeof origin === "string" && origin !== "" ? origin : "global"
    var ms = Math.round(Number(timeoutMs) || 0)
    root._timeoutMs = ms > 0 ? ms : (root._undo !== null ? 10000 : 4000)
    root._shown = true
    dismiss.restart()
  }

  // Runs the undo once. False when there is nothing to undo.
  function undo() {
    if (!root.undoAvailable) return false
    var fn = root._undo
    root.clear()
    fn()
    root.undoInvoked()
    return true
  }

  function clear() {
    dismiss.stop()
    root._undo = null
    root._shown = false
  }

  // Called before the panel switches to `view`.
  function clearIfOriginNot(view) {
    if (root._shown && root._origin !== "global" && root._origin !== view) root.clear()
  }

  // The view that raised the notice sent the user to another view on purpose (Compose
  // opens the Queue after arming): the notice goes along.
  function releaseOrigin() {
    root._origin = "global"
  }

  readonly property color ink: {
    switch (root._kind) {
    case "ok": return root.theme.okInk
    case "warn": return root.theme.warnInk
    case "error": return root.theme.badInk
    }
    return root.theme.readable
  }

  readonly property string glyph: {
    switch (root._kind) {
    case "ok": return "\uDB80\uDD2C"      // md-check
    case "warn": return "\uDB80\uDC28"    // md-alert_circle
    case "error": return "\uDB80\uDC28"
    }
    return ""
  }

  implicitHeight: Style.space(28)
  implicitWidth: Style.space(400)

  Timer {
    id: dismiss
    interval: Model.clampInterval(root._timeoutMs)
    repeat: false
    onTriggered: root.clear()
  }

  Item {
    id: content
    anchors.fill: parent
    opacity: root._shown ? 1 : 0
    visible: opacity > 0
    onOpacityChanged: if (content.opacity === 0) root._hadUndo = false

    // Entry only: the notice settles up from 4 px below. Leaving is a fade in place.
    transform: Translate { id: lift; y: 0 }

    NumberAnimation {
      id: liftIn
      target: lift
      property: "y"
      from: Style.space(4)
      to: 0
      duration: root.theme.moveMs(180)
      easing.type: Easing.BezierSpline
      easing.bezierCurve: [0.23, 1, 0.32, 1, 1, 1]
    }

    Behavior on opacity {
      NumberAnimation { duration: root._shown ? root.theme.fadeMs(180) : root.theme.fadeMs(120); easing.type: Easing.OutCubic }
    }

    Text {
      id: glyphText
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      visible: root.glyph !== ""
      textFormat: Text.PlainText
      text: root.glyph
      color: root.ink
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.glyph
    }

    Text {
      anchors.left: glyphText.right
      anchors.leftMargin: glyphText.visible ? Style.space(6) : 0
      anchors.right: undoButton.visible ? undoButton.left : parent.right
      anchors.rightMargin: undoButton.visible ? Style.space(10) : 0
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: root._text
      color: root.ink
      elide: Text.ElideRight
      maximumLineCount: 1
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.data
      font.features: root.theme.type.digits
    }

    ActionButton {
      id: undoButton
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      theme: root.theme
      quiet: true
      visible: root.undoAvailable || (content.opacity > 0 && root._hadUndo)
      // The row has no keyboard cursor of its own; the pointer is its cursor.
      hasCursor: undoButton.hovered
      text: "Undo"
      shortcut: "Ctrl+Z"
      onClicked: root.undo()
    }
  }
}
