pragma ComponentBehavior: Bound

import QtQuick
import qs.Commons
import qs.Ui
import "../lib/Edition.js" as Edition
import "../lib/Model.js" as Model

// Which agent, and which of its sessions: the agent chips in each product's own
// colour, a summary of the chosen session, and how to use it (resume, fork, new).
//
// An agent the helper cannot run is shown, dimmed, with one alert glyph and no words
// (C10), so all six chips stay on one line. Choosing such a chip raises the sign-in
// sheet, which names the state and the exact command that fixes it: Codex until
// `codex login` is done, a command that was not found or failed a safety check, and
// Gemini until it has run once, because nothing can check its sign-in without starting
// it. Fork is not offered for Gemini, which has none (C7), nor for Cursor, whose CLI
// cannot fork a chat. Cursor stays pickable while it waits for its one-time check
// (drafts and the Will run line work); the helper refuses to arm it.
Item {
  id: root

  required property var theme

  property string harness: "claude"
  // Draft target plus display keys the service drops: title, updatedAtMs, messages.
  property var target: ({ mode: "new", sessionId: null, cwd: null })
  // `service.agents`: harness -> Agents entry; {} until the first answer.
  property var agents: ({})
  property bool hasCursor: false
  property string home: ""
  property double nowMs: 0
  property bool geminiRan: false
  // A session id is known for this agent (picked now or earlier), so resume and
  // fork can be offered.
  property bool sessionKnown: false
  // The preview found a Codex folder without .git and nobody has allowed it yet.
  property bool nonGitWarning: false
  property string armHint: "Ctrl+Enter arm"

  signal harnessPicked(string harness)
  signal pickSessionRequested()
  signal modeRequested(string mode)
  signal focusRequested()
  // An agent that cannot run as it stands was chosen: show how to sign it in.
  signal signInRequested(string harness)
  signal allowNonGitRequested()

  readonly property var rows: ["agent", "session", "mode"]
  readonly property string rowId: root.rows[Math.max(0, Math.min(root.rows.length - 1, root._row))]
  readonly property var modes: root.harness === "gemini" || root.harness === "cursor" ? ["resume", "new"] : ["resume", "fork", "new"]
  readonly property bool canFork: root.modes.indexOf("fork") >= 0
  readonly property var modeLabels: ({ resume: "Resume", fork: "Fork", new: "New session" })
  readonly property var modeKeys: ({ resume: "r", fork: "f", new: "n" })

  property int _row: 0
  property int _modeCursor: 0

  readonly property bool hasSession: !!root.target && typeof root.target.sessionId === "string" && root.target.sessionId !== ""
  readonly property string mode: root.target && typeof root.target.mode === "string" ? root.target.mode : "new"
  readonly property string cwd: root.target && typeof root.target.cwd === "string" ? root.target.cwd : ""

  readonly property string hints: {
    var fork = root.canFork ? "f fork  ·  " : ""
    return "←/→ agent  ·  Enter pick session  ·  r resume  ·  " + fork + "n new  ·  Tab next  ·  "
      + root.armHint + "  ·  Esc close"
  }

  // {enabled, warn, ready, reasonKey}. No words: the chip carries a mark, a name and one
  // alert glyph, and the sign-in sheet turns `reasonKey` into a sentence and a command.
  function agentState(id) {
    var a = root.agents && root.agents[id] ? root.agents[id] : null
    return Model.signInState(a, id, root.geminiRan)
  }

  // Chip labels only. They are short so six agents fit on one line at the card's width;
  // every other place, and the sign-in sheet itself, names the agent in full through
  // Model.harnessName.
  readonly property var shortLabels: ({ claude: "Claude", gemini: "Gemini", cursor: "Cursor" })

  function chipLabel(id) {
    var k = String(id || "")
    return root.shortLabels.hasOwnProperty(k) ? root.shortLabels[k] : Model.harnessName(k)
  }

  function stepHarness(dir) {
    var ids = Edition.HARNESS_IDS
    var i = ids.indexOf(root.harness)
    for (var n = 1; n < ids.length; n++) {
      var j = i + dir * n
      if (j < 0 || j >= ids.length) return
      if (root.agentState(ids[j]).enabled) {
        root.harnessPicked(ids[j])
        return
      }
    }
  }

  function ago(ms) {
    if (typeof ms !== "number" || !isFinite(ms) || root.nowMs <= 0) return ""
    var s = Math.max(0, Math.round((root.nowMs - ms) / 1000))
    if (s < 60) return "just now"
    if (s < 3600) return Math.floor(s / 60) + "m ago"
    if (s < 86400) return Math.floor(s / 3600) + "h ago"
    var d = Math.floor(s / 86400)
    return d === 1 ? "yesterday" : d + "d ago"
  }

  readonly property string title: {
    if (root.hasSession) {
      var t = typeof root.target.title === "string" && root.target.title !== "" ? root.target.title : ""
      return t !== "" ? t : Model.elideMiddle(root.target.sessionId, 13)
    }
    return root.cwd !== "" ? "New session" : "No session picked"
  }

  readonly property string caption: {
    var msgs = root.target && typeof root.target.messages === "number" ? ", " + root.target.messages + " messages" : ""
    if (root.hasSession && root.mode === "resume") return "resume" + msgs
    if (root.hasSession && root.mode === "fork") return "fork: a copy continues, the original stays" + msgs
    if (root.cwd !== "") return "new session in " + Model.shortPath(root.cwd, root.home)
    return "No session yet. Pick one, or a folder for a new session."
  }

  function handleKey(event) {
    var key = event.key
    var mods = event.modifiers
    if ((mods & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)) !== 0) return false
    if (key === Qt.Key_Up || key === Qt.Key_K) { root._row = Math.max(0, root._row - 1); return true }
    if (key === Qt.Key_Down || key === Qt.Key_J) { root._row = Math.min(root.rows.length - 1, root._row + 1); return true }
    if (key === Qt.Key_Left || key === Qt.Key_H) {
      if (root.rowId === "mode") root._modeCursor = Math.max(0, root._modeCursor - 1)
      else root.stepHarness(-1)
      return true
    }
    if (key === Qt.Key_Right || key === Qt.Key_L) {
      if (root.rowId === "mode") root._modeCursor = Math.min(root.modes.length - 1, root._modeCursor + 1)
      else root.stepHarness(1)
      return true
    }
    if (key === Qt.Key_Return || key === Qt.Key_Enter || key === Qt.Key_Space) {
      if (root.rowId === "mode") root.modeRequested(root.modes[Math.min(root._modeCursor, root.modes.length - 1)])
      // The chip under the cursor cannot run as it stands: say how to sign it in first.
      else if (root.rowId === "agent" && !root.agentState(root.harness).ready) root.signInRequested(root.harness)
      else root.pickSessionRequested()
      return true
    }
    if (key === Qt.Key_R) { root.modeRequested("resume"); return true }
    if (key === Qt.Key_F) { if (root.canFork) root.modeRequested("fork"); return true }
    if (key === Qt.Key_N) { root.modeRequested("new"); return true }
    if (key === Qt.Key_G && root.nonGitWarning) { root.allowNonGitRequested(); return true }
    return false
  }

  onTargetChanged: {
    var i = root.modes.indexOf(root.mode)
    root._modeCursor = i >= 0 ? i : 0
  }
  onModesChanged: root._modeCursor = Math.min(root._modeCursor, root.modes.length - 1)

  implicitWidth: Style.space(640)
  implicitHeight: column.implicitHeight

  Column {
    id: column
    width: parent.width
    spacing: Style.spacing.lg

    Flow {
      width: parent.width
      spacing: Style.space(6)

      Repeater {
        model: Edition.HARNESS_IDS

        delegate: Item {
          id: agentCell
          required property string modelData
          readonly property var agentState: root.agentState(agentCell.modelData)
          width: agentChip.implicitWidth
          height: agentChip.implicitHeight

          Chip {
            id: agentChip
            anchors.fill: parent
            theme: root.theme
            pill: true
            harness: agentCell.modelData
            text: root.chipLabel(agentCell.modelData)
            selected: root.harness === agentCell.modelData
            hasCursor: root.hasCursor && root.rowId === "agent" && root.harness === agentCell.modelData
            enabled: agentCell.agentState.enabled
            // One glyph, no words: the reason lives in the sheet the chip raises.
            note: agentCell.agentState.warn ? Model.GLYPH.alert : ""
            noteColor: root.theme.warnInk
            onClicked: {
              root.focusRequested()
              root._row = 0
              root.harnessPicked(agentCell.modelData)
              if (!agentCell.agentState.ready) root.signInRequested(agentCell.modelData)
            }
          }

          // A dimmed chip still answers a click, because its own MouseArea is off:
          // it says how to sign the agent in (someone may not have run `codex login` yet).
          MouseArea {
            anchors.fill: parent
            visible: !agentCell.agentState.enabled
            cursorShape: Qt.PointingHandCursor
            onClicked: {
              root.focusRequested()
              root.signInRequested(agentCell.modelData)
            }
          }
        }
      }
    }

    BorderSurface {
      id: sessionBox
      width: parent.width
      height: Style.space(48)
      radius: Style.cornerRadius
      readonly property bool cursorHere: root.hasCursor && root.rowId === "session"
      color: sessionBox.cursorHere ? Style.hoverFillFor(root.theme.fg, root.theme.accent) : Util.alpha(root.theme.fg, 0.04)
      borderSpec: Border.controlSpec(sessionBox.cursorHere ? "hover-cursor" : "normal", root.theme.fg, root.theme.accent)

      HarnessRail {
        anchors.left: parent.left
        anchors.leftMargin: Style.space(2)
        anchors.top: parent.top
        anchors.topMargin: Style.space(5)
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Style.space(5)
        theme: root.theme
        harness: root.harness
        strength: root.hasSession || root.cwd !== "" ? 1.0 : 0.35
      }

      AgentMark {
        id: sessionMark
        anchors.left: parent.left
        anchors.leftMargin: Style.space(14)
        anchors.top: parent.top
        anchors.topMargin: Style.space(9)
        theme: root.theme
        agent: root.harness
        size: Style.space(14)
        dimmed: !root.hasSession && root.cwd === ""
      }

      // The title, then its folder in the soft ink: two voices, so a long title never
      // pushes the folder out of sight.
      Item {
        id: sessionTitle
        anchors.left: sessionMark.right
        anchors.leftMargin: Style.space(8)
        anchors.right: sessionAge.left
        anchors.rightMargin: Style.space(10)
        anchors.top: parent.top
        anchors.topMargin: Style.space(7)
        height: titleText.implicitHeight
        readonly property string folder: root.hasSession && root.cwd !== "" ? Model.shortPath(root.cwd, root.home) : ""

        Text {
          id: titleText
          width: Math.max(0, Math.min(implicitWidth, sessionTitle.width - (folderText.visible ? folderText.width + Style.space(8) : 0)))
          textFormat: Text.PlainText
          text: root.title
          color: root.hasSession || root.cwd !== "" ? (sessionBox.cursorHere ? root.theme.fg : root.theme.strong) : root.theme.soft
          elide: Text.ElideRight
          maximumLineCount: 1
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.body
        }

        Text {
          id: folderText
          x: titleText.width + Style.space(8)
          anchors.baseline: titleText.baseline
          visible: sessionTitle.folder !== ""
          width: Math.min(implicitWidth, sessionTitle.width, Math.max(Style.space(110), sessionTitle.width - titleText.implicitWidth - Style.space(8)))
          textFormat: Text.PlainText
          text: sessionTitle.folder
          color: root.theme.soft
          elide: Text.ElideMiddle
          maximumLineCount: 1
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
        }
      }

      Text {
        anchors.left: sessionTitle.left
        anchors.right: pickHint.left
        anchors.rightMargin: Style.space(10)
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Style.space(7)
        textFormat: Text.PlainText
        text: root.caption
        color: root.theme.soft
        elide: Text.ElideMiddle
        maximumLineCount: 1
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.meta
      }

      Text {
        id: sessionAge
        anchors.right: parent.right
        anchors.rightMargin: Style.space(12)
        anchors.top: parent.top
        anchors.topMargin: Style.space(8)
        textFormat: Text.PlainText
        text: root.hasSession && root.target ? root.ago(root.target.updatedAtMs) : ""
        color: root.theme.readable
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.meta
        font.features: root.theme.type.digits
      }

      Text {
        id: pickHint
        anchors.right: parent.right
        anchors.rightMargin: Style.space(12)
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Style.space(7)
        textFormat: Text.PlainText
        text: "Enter pick"
        color: sessionBox.cursorHere ? root.theme.accentInk : root.theme.soft
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.meta
      }

      MouseArea {
        anchors.fill: parent
        cursorShape: Qt.PointingHandCursor
        onClicked: {
          root.focusRequested()
          root._row = 1
          root.pickSessionRequested()
        }
      }
    }

    Flow {
      width: parent.width
      spacing: Style.spacing.sm

      Repeater {
        model: root.modes

        delegate: Chip {
          id: modeChip
          required property string modelData
          required property int index
          theme: root.theme
          text: root.modeLabels[modeChip.modelData]
          note: root.modeKeys[modeChip.modelData]
          selected: root.mode === modeChip.modelData && (root.hasSession || modeChip.modelData === "new")
          hasCursor: root.hasCursor && root.rowId === "mode" && root._modeCursor === modeChip.index
          enabled: modeChip.modelData === "new" || root.sessionKnown
          onClicked: {
            root.focusRequested()
            root._row = 2
            root._modeCursor = modeChip.index
            root.modeRequested(modeChip.modelData)
          }
        }
      }
    }

    Row {
      width: parent.width
      spacing: Style.space(8)
      visible: root.nonGitWarning

      Text {
        anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText
        text: "\uDB80\uDC28"   // md-alert_circle U+F0028
        color: root.theme.warnInk
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.glyph
      }

      Text {
        anchors.verticalCenter: parent.verticalCenter
        width: parent.width - allowButton.width - Style.space(40)
        textFormat: Text.PlainText
        text: "This folder is not a git repository. Codex runs here only if you allow it."
        color: root.theme.warnInk
        elide: Text.ElideRight
        maximumLineCount: 1
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.meta
      }

      ActionButton {
        id: allowButton
        anchors.verticalCenter: parent.verticalCenter
        theme: root.theme
        hasCursor: allowButton.hovered
        text: "Allow"
        shortcut: "g"
        onClicked: {
          root.focusRequested()
          root.allowNonGitRequested()
        }
      }
    }
  }
}
