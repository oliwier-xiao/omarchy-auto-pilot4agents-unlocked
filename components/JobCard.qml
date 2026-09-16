pragma ComponentBehavior: Bound
import QtQuick
import qs.Commons
import "../lib/Model.js" as Model

// The card a row opens into (R6 2.4 item 7, 8.3). It shows what the list row has no
// room for: the full prompt (read only), the exact command the runner will start,
// and for a finished job why it ended and the last lines it printed. Everything
// shown comes from `service.getJob`; the agent's output is plain text, capped.
//
// Actions are boxes. The view owns the guards: `guardAction` lights the button
// whose second press is awaited.
Item {
  id: root

  required property var theme

  property var job: null
  property string mode: "queue"
  property double nowMs: 0
  property string home: ""
  // job-get result: {job, prompt, promptAvailable, preview, runs, lastLines}, or null.
  property var detail: null
  property bool loading: false
  property string errorText: ""
  property string guardAction: ""
  // Pointer cursor inside the card, so hover lights one button at a time.
  property string hoverAction: ""
  // The job's model as the models verb names it (Model.modelLabel), "" for the agent's default.
  property string modelLabel: ""

  readonly property bool paid: !!root.job && root.job.allowPaid === true

  signal actionRequested(string action)

  readonly property var st: root.job && root.job.state ? root.job.state : ({})
  readonly property string status: String(root.st.status || "")
  readonly property int lastLinesMax: 8

  readonly property var actions: {
    var j = root.job
    if (!j) return []
    if (root.mode === "history") {
      var h = [{ id: "rearm", text: "Edit and re-arm", shortcut: "Ctrl+E", enabled: true, danger: false }]
      h.push({ id: "run", text: "Run again", shortcut: "Ctrl+Enter", enabled: j.canRunNow === true, danger: false })
      h.push({ id: "copy", text: "Copy resume command", shortcut: "Ctrl+C", enabled: root.hasSession, danger: false })
      return h
    }
    var q = [{ id: "edit", text: "Edit", shortcut: "Ctrl+E", enabled: j.canEdit === true, danger: false }]
    q.push({ id: "run", text: "Run now", shortcut: "Ctrl+Enter", enabled: j.canRunNow === true, danger: false })
    q.push({ id: "duplicate", text: "Duplicate", shortcut: "Ctrl+D", enabled: true, danger: false })
    if (j.canDisarm === true) q.push({ id: "disarm", text: "Disarm", shortcut: "Delete", enabled: true, danger: true })
    else q.push({ id: "delete", text: "Delete", shortcut: "Delete", enabled: j.canDelete === true, danger: true })
    return q
  }

  readonly property bool hasSession: {
    var j = root.job
    if (!j) return false
    return (typeof j.state.runSessionId === "string" && j.state.runSessionId !== "")
      || (j.target && typeof j.target.sessionId === "string" && j.target.sessionId !== "")
  }

  readonly property string outcome: {
    if (!root.job) return ""
    if (root.mode === "history") return Model.outcomeSentence(root.job, root.nowMs)
    if (root.status === "armed" && !root.st.wait) return ""
    if (root.status === "draft") return ""
    return Model.outcomeSentence(root.job, root.nowMs)
  }

  readonly property color outcomeInk: {
    switch (root.status) {
    case "failed": case "gave_up": return root.theme.badInk
    case "done": return root.theme.okInk
    case "limit": case "missed": case "interrupted": case "busy": case "paused": case "needs_confirm": return root.theme.warnInk
    case "running": return root.theme.accentInk
    case "armed": return root.theme.harnessInk(root.job ? root.job.harness : "")
    }
    return root.theme.readable
  }

  readonly property string promptText: root.detail && typeof root.detail.prompt === "string" ? root.detail.prompt : ""
  readonly property bool promptGone: !!root.detail && root.detail.prompt === null

  readonly property var lastLines: {
    var l = root.detail && Array.isArray(root.detail.lastLines) ? root.detail.lastLines : []
    return l.length > root.lastLinesMax ? l.slice(l.length - root.lastLinesMax) : l
  }

  readonly property var preview: root.detail && root.detail.preview ? root.detail.preview : null

  readonly property string whenLine: {
    if (!root.job || root.mode !== "queue") return ""
    if (root.status === "armed" && typeof root.st.fireAt === "number")
      return "Fires " + Model.resolvedLine(root.st.fireAt * 1000, root.nowMs) + ", " + Model.formatClock(root.st.fireAt * 1000) + "."
    if (root.status === "running") return "Running now."
    return ""
  }

  implicitWidth: Style.space(1000)
  implicitHeight: body.implicitHeight + Style.space(12) * 2

  Rectangle {
    anchors.fill: parent
    anchors.leftMargin: Style.space(8)
    anchors.rightMargin: Style.space(8)
    radius: Style.cornerRadius
    color: Util.alpha(root.theme.fg, 0.04)
    border.width: 1
    border.color: root.theme.faint
  }

  Column {
    id: body
    x: Style.space(22)
    y: Style.space(12)
    width: Math.max(0, root.width - Style.space(22) * 2)
    spacing: Style.space(8)

    Text {
      width: parent.width
      visible: root.outcome !== ""
      textFormat: Text.PlainText
      text: root.outcome
      color: root.outcomeInk
      wrapMode: Text.WordWrap
      maximumLineCount: 2
      elide: Text.ElideRight
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.data
    }

    Text {
      width: parent.width
      visible: root.whenLine !== ""
      textFormat: Text.PlainText
      text: root.whenLine
      color: root.theme.readable
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
    }

    // Which model, and whether this job may spend paid usage.
    Row {
      width: parent.width
      visible: root.modelLabel !== "" || root.paid
      spacing: Style.space(14)

      Text {
        visible: root.modelLabel !== ""
        textFormat: Text.PlainText
        text: "Model " + root.modelLabel
        color: root.theme.readable
        elide: Text.ElideRight
        maximumLineCount: 1
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.meta
      }

      Row {
        visible: root.paid
        spacing: Style.space(5)

        Text {
          anchors.verticalCenter: parent.verticalCenter
          textFormat: Text.PlainText
          text: Model.GLYPH.alert
          color: root.theme.warnInk
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
        }

        Text {
          anchors.verticalCenter: parent.verticalCenter
          textFormat: Text.PlainText
          text: "Paid usage is on for this job. It may spend usage credits or API dollars."
          color: root.theme.warnInk
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
        }
      }
    }

    Text {
      width: parent.width
      visible: root.loading || root.errorText !== ""
      textFormat: Text.PlainText
      text: root.errorText !== "" ? root.errorText : "Reading job…"
      color: root.errorText !== "" ? root.theme.badInk : root.theme.soft
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
    }

    // Prompt, read only: eight lines, then it scrolls.
    Column {
      width: parent.width
      spacing: Style.space(3)
      visible: !!root.detail && (root.promptText !== "" || root.promptGone)

      Text {
        textFormat: Text.PlainText
        text: "Prompt"
        color: root.theme.soft
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.label
        font.bold: true
        font.letterSpacing: root.theme.type.tracking
      }

      Flickable {
        id: promptFlick
        width: parent.width
        height: Math.min(promptBody.implicitHeight, promptBody.lineHeightPx * 8)
        contentWidth: width
        contentHeight: promptBody.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height

        // A reading surface: body size on a measure of about 78 characters, prose
        // leading, and whole lines, so the eighth line is never cut through.
        Text {
          id: promptBody
          readonly property real lineHeightPx: Math.ceil(root.theme.type.body * root.theme.type.proseLeading)
          width: Math.min(promptFlick.width, root.theme.type.measure)
          textFormat: Text.PlainText
          text: root.promptGone ? "The prompt was deleted after the run." : root.promptText
          color: root.promptGone ? root.theme.soft : root.theme.strong
          wrapMode: Text.Wrap
          lineHeightMode: Text.FixedHeight
          lineHeight: promptBody.lineHeightPx
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.body
        }
      }
    }

    ArgvLine {
      width: parent.width
      visible: root.mode === "queue" && !!root.detail
      theme: root.theme
      display: root.preview ? String(root.preview.display || "") : ""
      cwd: root.preview ? Model.shortPath(root.preview.cwd || "", root.home) : ""
      binary: root.preview ? String(root.preview.binary || "") : ""
      caption: root.preview ? String(root.preview.levelCaption || "") : ""
      loading: root.loading
      errorText: !!root.detail && !root.preview ? "The agent command could not be found, so there is nothing to show." : ""
    }

    Column {
      width: parent.width
      spacing: Style.space(3)
      visible: root.mode === "history" && !!root.detail

      Text {
        textFormat: Text.PlainText
        text: "Last lines"
        color: root.theme.soft
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.label
        font.bold: true
        font.letterSpacing: root.theme.type.tracking
      }

      Text {
        visible: root.lastLines.length === 0
        textFormat: Text.PlainText
        text: "No output was kept for this run."
        color: root.theme.soft
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.meta
      }

      Repeater {
        model: root.lastLines

        Text {
          required property var modelData
          width: body.width
          textFormat: Text.PlainText
          text: String(modelData)
          color: root.theme.readable
          elide: Text.ElideRight
          maximumLineCount: 1
          leftPadding: Style.space(10)
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
        }
      }
    }

    Row {
      spacing: Style.space(8)

      Repeater {
        model: root.actions

        ActionButton {
          required property var modelData
          theme: root.theme
          text: modelData.text
          shortcut: modelData.shortcut
          danger: modelData.danger === true
          enabled: modelData.enabled === true
          armed: root.guardAction === modelData.id
          hasCursor: hovered
          onClicked: root.actionRequested(modelData.id)
        }
      }
    }
  }
}
