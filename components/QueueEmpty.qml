pragma ComponentBehavior: Bound
import QtQuick
import qs.Commons
import "../lib/Edition.js" as Edition
import "../lib/Model.js" as Model
import "../lib/Compose.js" as Compose

// The queue on first use (R6 8.5): left aligned, it teaches with the real vocabulary
// instead of numbered steps. The time chips and agent pills describe what Compose
// offers (live: the reset time and meter come from the usage record, the agents from
// the helper's discovery). The button and the agent pills open Compose; a time chip
// opens it with that time already picked.
Item {
  id: root

  required property var theme
  required property var service

  // trigger: a Draft trigger for Compose's When section, or null.
  signal composeRequested(var trigger)

  readonly property double nowMs: root.service ? root.service.nowMs : Date.now()
  readonly property var settings: root.service && root.service.settings ? root.service.settings : ({})
  readonly property var agents: root.service && root.service.agents ? root.service.agents : ({})

  readonly property var resetChip: Compose.resetChipFor(root.service ? root.service.providers : [], "claude",
    "", "", "", root.nowMs, typeof root.settings.resetMarginSec === "number" ? root.settings.resetMarginSec : 120)

  readonly property var morning: Model.morningPreset(String(root.settings.morningTime || "07:00"), root.nowMs)
  readonly property int marginSec: typeof root.settings.resetMarginSec === "number" ? root.settings.resetMarginSec : 120

  function agentNote(h) {
    var s = Model.signInState(root.agents[h], h, false)
    if (s.reasonKey === "not_found") return "not found"
    if (s.reasonKey === "not_signed_in") return "not signed in"
    if (s.reasonKey === "untrusted") return "untrusted"
    if (s.reasonKey === "shim") return "shim"
    return ""
  }

  implicitWidth: Style.space(1000)
  implicitHeight: column.implicitHeight

  Column {
    id: column
    width: root.width
    spacing: Style.space(14)

    Column {
      width: parent.width
      spacing: Style.space(6)

      Text {
        textFormat: Text.PlainText
        text: "Nothing armed."
        color: root.theme.strong
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.title
        font.bold: true
      }

      Text {
        width: Math.min(parent.width, root.theme.type.measure)
        textFormat: Text.PlainText
        text: "Write a prompt, pick when it should run, and arm it. It fires even with the screen locked."
        color: root.theme.readable
        wrapMode: Text.WordWrap
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.body
      }
    }

    ActionButton {
      id: composeButton
      theme: root.theme
      primary: true
      text: "Compose"
      shortcut: "Ctrl+1"
      hasCursor: composeButton.hovered
      onClicked: root.composeRequested(null)
    }

    Row {
      spacing: Style.space(8)

      Text {
        anchors.verticalCenter: parent.verticalCenter
        width: Style.space(90)
        textFormat: Text.PlainText
        text: "Try a time"
        color: root.theme.soft
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.label
        font.letterSpacing: root.theme.type.tracking
        font.bold: true
      }

      Chip {
        id: plusTwo
        theme: root.theme
        text: "+2h"
        note: Model.formatClock(root.nowMs + 7200000)
        hasCursor: plusTwo.hovered
        onClicked: root.composeRequested({ kind: "in", delaySec: 7200 })
      }

      Chip {
        id: atReset
        theme: root.theme
        visible: root.resetChip.available === true
        harness: "claude"
        text: root.resetChip.label
        meterValue: typeof root.resetChip.percent === "number" ? root.resetChip.percent : -1
        note: typeof root.resetChip.percent === "number" ? Math.round(root.resetChip.percent * 100) + "%" : ""
        hasCursor: atReset.hovered
        onClicked: root.composeRequested({ kind: root.resetChip.kind, marginSec: root.marginSec })
      }

      Chip {
        id: tomorrow
        theme: root.theme
        visible: !!root.morning
        text: root.morning ? "Tomorrow " + Model.formatClock(root.morning.ms) : ""
        hasCursor: tomorrow.hovered
        onClicked: root.composeRequested(root.morning ? { kind: "at", fireAt: Math.floor(root.morning.ms / 1000) } : null)
      }
    }

    Row {
      spacing: Style.space(8)

      Text {
        anchors.verticalCenter: parent.verticalCenter
        width: Style.space(90)
        textFormat: Text.PlainText
        text: "Sends to"
        color: root.theme.soft
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.label
        font.letterSpacing: root.theme.type.tracking
        font.bold: true
      }

      Repeater {
        model: Edition.HARNESS_IDS

        Chip {
          id: agentChip
          required property var modelData
          readonly property string noteText: root.agentNote(String(modelData))
          theme: root.theme
          pill: true
          harness: String(modelData)
          text: Model.harnessName(modelData)
          note: agentChip.noteText
          noteColor: agentChip.noteText === "" ? root.theme.soft : root.theme.warnInk
          tint: agentChip.noteText === "" ? root.theme.harnessInk(String(modelData)) : root.theme.soft
          hasCursor: agentChip.hovered
          onClicked: root.composeRequested(null)
        }
      }
    }

    Text {
      width: parent.width
      textFormat: Text.PlainText
      text: "If the computer sleeps, the job runs when it wakes (within 15 min, or 3 h for reset jobs)."
      color: root.theme.soft
      wrapMode: Text.WordWrap
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
    }
  }
}
