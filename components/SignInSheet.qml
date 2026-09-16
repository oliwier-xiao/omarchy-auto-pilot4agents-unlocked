pragma ComponentBehavior: Bound

import QtQuick
import qs.Commons
import qs.Ui
import "../lib/Model.js" as Model

// "Sign in to ...": the sheet a Send to chip raises when its agent cannot run as it
// stands. The chips carry a mark, a name and one alert glyph and no words, so this is
// where the reason and the recovery live: what state the agent is in, the exact command
// to type in a terminal, and what to expect from it.
//
// Nothing here runs a command and nothing is ever typed for you. Enter only asks the
// helper to look at the agent again (the same check the panel makes when it opens), and
// once the agent answers as ready the sheet closes and says so.
Item {
  id: root

  required property var theme
  required property var service

  property bool active: false

  // The agent this sheet was opened for; "" until `open` names one.
  property string harness: ""

  readonly property string hints: root._checking
    ? "Checking…  ·  Esc back"
    : "Enter check again  ·  Esc back"

  signal noticeRequested(string text, string kind, var undo)
  signal closed()

  property bool _checking: false
  property string _note: ""

  readonly property var agents: root.service && root.service.agents ? root.service.agents : ({})
  // Gemini alone cannot be asked whether it is signed in, so a finished Gemini run is
  // the only proof there is.
  readonly property bool geminiRan: {
    var jobs = root.service && Array.isArray(root.service.jobs) ? root.service.jobs : []
    for (var i = 0; i < jobs.length; i++) {
      if (jobs[i] && jobs[i].harness === "gemini" && jobs[i].state && jobs[i].state.lastRun) return true
    }
    return false
  }

  // The same reading the chips use (Model.signInState), so the sheet and the chip that
  // raised it never disagree.
  readonly property var signIn: Model.signInState(root.agents[root.harness], root.harness, root.geminiRan)
  readonly property string reasonKey: String(root.signIn.reasonKey)
  readonly property bool ready: root.signIn.ready === true
  readonly property string agentName: Model.harnessName(root.harness)

  readonly property var steps: Model.SIGN_IN_STEPS.hasOwnProperty(root.harness) ? Model.SIGN_IN_STEPS[root.harness] : null
  readonly property string command: root.steps ? String(root.steps.command) : ""
  readonly property string note: root.steps ? String(root.steps.note) : ""

  // One sentence per state, in the same voice as the History captions: the fact first,
  // never a command. The command is the box below.
  readonly property var reasonSentences: ({
    not_found: "Its command was not found on this computer.",
    shim: "Its command is a stand-in, not the agent itself.",
    untrusted: "Its command failed a safety check, so it is never run.",
    not_signed_in: "It is not signed in yet.",
    sign_in_unknown: "Its sign-in cannot be checked without a run.",
    gated: "It is waiting for a one-time check.",
    unavailable: "It cannot run yet."
  })

  readonly property string reasonLine: {
    if (root.harness === "") return ""
    if (root.ready) return "It is signed in and ready to run."
    return root.reasonSentences.hasOwnProperty(root.reasonKey) ? root.reasonSentences[root.reasonKey] : "It cannot run yet."
  }

  function open(args) {
    var h = args && typeof args.harness === "string" ? args.harness : ""
    root.harness = Model.SIGN_IN_STEPS.hasOwnProperty(h) ? h : ""
    root._checking = false
    root._note = ""
  }

  // `active` and `visible` belong to the panel (CONTRACT 6.6): the sheet only asks to
  // be closed, as the other sheets do.
  function close() {
    root.closed()
  }

  function signedIn() {
    root._checking = false
    root._note = ""
    root.noticeRequested(root.agentName + " is signed in.", "info", null)
    root.close()
  }

  // Enter: ask the helper to read the agent again, sign-in included.
  function recheck() {
    if (root._checking || root.harness === "") return
    if (root.ready) {
      root.signedIn()
      return
    }
    if (!root.service || typeof root.service.refreshAgents !== "function") return
    root._checking = true
    root._note = ""
    root.service.refreshAgents(true)
    waitTimer.restart()
  }

  function handleKey(event) {
    if (!root.active) return false
    var k = event.key
    var mods = event.modifiers & (Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier | Qt.MetaModifier)
    if (k === Qt.Key_Escape) { root.close(); return true }
    if (mods !== 0) return false
    if (k === Qt.Key_Return || k === Qt.Key_Enter) { root.recheck(); return true }
    return false
  }

  implicitWidth: Style.space(1100)
  implicitHeight: Style.space(600)

  // The answer landed: either the agent is ready now, or it is not and the sheet says so
  // without closing, so the command stays in sight.
  Connections {
    target: root.service
    ignoreUnknownSignals: true

    function onAgentsChanged() {
      if (!root._checking) return
      if (root.ready) {
        root.signedIn()
        return
      }
      root._checking = false
      root._note = "Still not ready. " + root.reasonLine
    }
  }

  // The agents verb has a 35 second deadline; past that no answer is coming.
  Timer {
    id: waitTimer
    interval: 40000
    repeat: false
    onTriggered: {
      if (!root._checking) return
      if (root.ready) {
        root.signedIn()
        return
      }
      root._checking = false
      root._note = "The helper did not answer. Try again."
    }
  }

  // Modal ground over the view area; swallows the pointer too.
  Rectangle {
    anchors.fill: parent
    color: root.theme.surface

    MouseArea {
      anchors.fill: parent
      acceptedButtons: Qt.AllButtons
      onWheel: function (wheel) { wheel.accepted = true }
    }
  }

  // Same gutter and head height as the other sheets, so the title sits where theirs do.
  Column {
    id: top
    x: 0
    y: 0
    width: root.width
    spacing: Style.space(10)

    Item {
      width: parent.width
      height: Style.space(28)

      Text {
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText
        text: root.harness === "" ? "Sign in" : "Sign in to " + root.agentName
        color: root.theme.strong
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.title
        font.bold: true
      }

      ActionButton {
        id: backButton
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        theme: root.theme
        hasCursor: backButton.hovered
        glyph: "󰁍"   // md-arrow_left U+F004D
        text: "Back"
        shortcut: "Esc"
        onClicked: root.close()
      }
    }
  }

  Column {
    id: body
    x: 0
    anchors.top: top.bottom
    anchors.topMargin: Style.space(18)
    width: root.width
    spacing: Style.space(12)

    // Which agent, in its own mark and hue, so the sheet is tied to the chip that raised it.
    Row {
      spacing: Style.space(8)

      AgentMark {
        anchors.verticalCenter: parent.verticalCenter
        theme: root.theme
        agent: root.harness
        size: Style.space(16)
      }

      Text {
        anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText
        text: root.agentName
        color: root.theme.harnessInk(root.harness)
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.body
        font.bold: true
      }

      Text {
        anchors.verticalCenter: parent.verticalCenter
        visible: !root.ready && root.harness !== ""
        textFormat: Text.PlainText
        text: Model.GLYPH.alert
        color: root.theme.warnInk
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.glyph
      }
    }

    Text {
      width: Math.min(parent.width, root.theme.type.measure)
      textFormat: Text.PlainText
      text: root.reasonLine
      color: root.ready ? root.theme.readable : root.theme.warnInk
      wrapMode: Text.WordWrap
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.body
    }

    Text {
      textFormat: Text.PlainText
      text: "Type this in a terminal:"
      color: root.theme.soft
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.label
      font.letterSpacing: root.theme.type.tracking
      font.bold: true
    }

    // The command exactly as it is typed, in a box of its own so it is never read as prose.
    BorderSurface {
      width: Math.min(root.width, commandText.implicitWidth + Style.space(24))
      height: commandText.implicitHeight + Style.space(16)
      visible: root.command !== ""
      radius: Style.cornerRadius
      color: Util.alpha(root.theme.fg, 0.06)
      borderSpec: Border.flat(Util.alpha(root.theme.fg, 0.22), 1)

      Text {
        id: commandText
        anchors.left: parent.left
        anchors.leftMargin: Style.space(12)
        anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText
        text: root.command
        color: root.theme.fg
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.data
        // A command may carry flags; a coding font's ligature would join two dashes into one.
        font.features: ({ "liga": 0, "calt": 0 })
      }
    }

    Text {
      width: Math.min(parent.width, root.theme.type.measure)
      visible: root.note !== ""
      textFormat: Text.PlainText
      text: root.note
      color: root.theme.soft
      wrapMode: Text.WordWrap
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
    }
  }

  Item {
    id: foot
    x: 0
    anchors.top: body.bottom
    anchors.topMargin: Style.space(18)
    width: root.width
    height: Style.spacing.controlHeight

    Text {
      anchors.left: parent.left
      anchors.right: buttons.left
      anchors.rightMargin: Style.space(12)
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: root._checking ? "Checking " + root.agentName + "…" : root._note
      color: root._note !== "" && !root._checking ? root.theme.warnInk : root.theme.soft
      elide: Text.ElideRight
      maximumLineCount: 1
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
    }

    Row {
      id: buttons
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      spacing: Style.space(8)

      ActionButton {
        id: checkButton
        theme: root.theme
        primary: true
        enabled: !root._checking && root.harness !== ""
        hasCursor: checkButton.hovered
        text: root._checking ? "Checking…" : "Check again"
        widthTemplate: "Check again"
        shortcut: "Enter"
        onClicked: root.recheck()
      }
    }
  }
}
