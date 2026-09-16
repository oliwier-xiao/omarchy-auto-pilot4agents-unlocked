import QtQuick
import qs.Commons
import qs.Ui
import "../lib/Edition.js" as Edition
import "../lib/Model.js" as Model

// One job on two lines, 48 px high (R6 2.4). Left to right: the agent's rail, its
// mark, the label with how the job runs, the session it goes to, and a right
// cluster of fixed width (clock, countdown, level, status) so the columns never move
// between rows.
//
// The row paints the cursor, a mark, the first press of a guard and a drop target,
// and reports the pointer; the view decides what any of it means.
Item {
  id: root

  required property var theme

  property var job: null
  // "queue" shows when a job fires; "history" shows when it ran.
  property string mode: "queue"
  property double nowMs: 0
  property string home: ""

  property bool hasCursor: false
  property bool marked: false
  // The arm moment: the rail flashes once (HarnessRail.flash).
  property bool flash: false
  property bool guard: false
  property bool dropHere: false
  // The source slot of a drag stays in the list at low opacity.
  property bool placeholder: false
  // The copy that follows the pointer.
  property bool ghost: false
  property string dropPreview: ""
  // Epoch seconds the job is being moved to while the helper re-arms it, or null.
  property var pendingFireAt: null
  property bool draggable: false
  // The job's model as the models verb names it (Model.modelLabel), "" for the agent's default.
  property string modelLabel: ""

  signal hovered()
  signal activated(int modifiers)
  signal dragBegin(var area, real x, real y)
  signal dragMove(var area, real x, real y)
  signal dragEnd()
  signal dragCancel()
  signal wheelNudge(int steps)

  readonly property var st: root.job && root.job.state ? root.job.state : ({})
  readonly property string status: String(root.st.status || "")
  readonly property string harness: root.job ? String(root.job.harness || "") : ""
  readonly property var target: root.job && root.job.target ? root.job.target : ({})
  readonly property var run: root.st.lastRun || null
  readonly property bool pending: root.pendingFireAt !== null && root.pendingFireAt !== undefined
  readonly property var fireAtSec: root.pending ? root.pendingFireAt
    : (typeof root.st.fireAt === "number" ? root.st.fireAt : null)

  readonly property real railStrength: {
    switch (root.status) {
    case "draft": case "disarmed": return 0.35
    case "done": case "skipped": case "missed": return 0.55
    }
    return 1.0
  }

  readonly property string levelLabel: root.job && Edition.LEVEL_LABELS.hasOwnProperty(String(root.job.level))
    ? Edition.LEVEL_LABELS[root.job.level] : ""

  function modeWord(m) {
    if (m === "resume") return "resume"
    if (m === "fork") return "fork"
    return "new session"
  }

  // Paid usage allowed for this job: a "paid" pill leads the meta line. Without it no
  // dollar amount is shown, because none can be spent.
  readonly property bool paid: !!root.job && root.job.allowPaid === true

  // "resume, Big Pickle, 15 turns, $5.00, 1h 30m max": what the run is allowed to do.
  readonly property string metaLine: {
    if (!root.job) return ""
    var parts = [root.modeWord(root.target.mode)]
    if (root.modelLabel !== "") parts.push(root.modelLabel)
    var lim = root.job.limits || {}
    if (root.harness === "claude") {
      if (typeof lim.maxTurns === "number") parts.push(lim.maxTurns + " turns")
      if (root.paid && typeof lim.budgetUsd === "number") parts.push("$" + lim.budgetUsd.toFixed(2))
    }
    if (typeof lim.runtimeSec === "number") parts.push(Model.formatDuration(lim.runtimeSec * 1000) + " max")
    return parts.join(", ")
  }

  readonly property string sessionLine: {
    if (!root.job) return ""
    var title = String(root.target.title || "")
    var where = Model.shortPath(root.target.cwd || "", root.home)
    if (title !== "" && where !== "" && title !== where) return title + "  " + where
    return title !== "" ? title : where
  }

  // The session column in two inks: the title readable, its folder soft. When the title
  // is only the folder's own name, the path is drawn whole with that last part in the
  // readable ink ("~/proj/" then "web").
  readonly property string sessionFolder: root.job ? Model.shortPath(root.target.cwd || "", root.home) : ""
  readonly property string sessionName: root.job ? String(root.target.title || "") : ""
  readonly property string folderBase: {
    var f = root.sessionFolder.replace(/\/+$/, "")
    return f.slice(f.lastIndexOf("/") + 1)
  }
  readonly property bool folderNamed: root.sessionFolder !== "" && (root.sessionName === "" || root.sessionName === root.folderBase)
  readonly property string sessionTitle: root.folderNamed ? root.folderBase : root.sessionName
  readonly property string sessionPath: {
    if (!root.folderNamed) return root.sessionFolder
    var f = root.sessionFolder.replace(/\/+$/, "")
    var i = f.lastIndexOf("/")
    return i >= 0 ? f.slice(0, i + 1) : ""
  }

  // A draft that cannot be armed as it stands says why instead of promising a run: Cursor
  // waiting for its one-time check, or a Claude model through OpenCode while paid usage
  // is off (the helper's fixed sentences).
  readonly property string refusalLine: {
    if (!root.job || root.mode === "history" || root.status !== "draft") return ""
    if (root.job.gated === true) return Model.reasonSentence("harness_gated")
    var model = typeof root.job.model === "string" ? root.job.model : ""
    if (root.harness === "opencode" && root.job.allowPaid !== true && model.indexOf("anthropic/") === 0)
      return "Claude models in OpenCode bill API or extra usage, not your Claude plan."
    return ""
  }

  // Second line of the session column: how the time was chosen, or what happened.
  readonly property string triggerLine: {
    if (!root.job) return ""
    if (root.pending) return "Re-arming for " + Model.formatClock(root.fireAtSec * 1000) + "."
    if (root.refusalLine !== "") return root.refusalLine
    if (root.mode === "history") return Model.outcomeSentence(root.job, root.nowMs)
    var tr = root.job.trigger || {}
    if (root.status === "armed" && root.st.wait) return Model.outcomeSentence(root.job, root.nowMs)
    if (root.status !== "armed" && root.status !== "draft" && root.status !== "running")
      return Model.outcomeSentence(root.job, root.nowMs)
    switch (String(tr.kind || "")) {
    case "claude_5h_reset": case "codex_window_reset": case "gemini_daily_reset":
    case "zen_free_reset": case "go_window_reset":
      return Model.followsLine(String(tr.kind), tr.marginSec)
    case "now": return root.status === "draft" ? "runs right away once armed" : "run now"
    case "in": return typeof tr.delaySec === "number" ? Model.formatDuration(tr.delaySec * 1000) + " after arming" : "after a delay"
    case "at": return ""
    }
    return ""
  }

  readonly property string clockText: {
    if (!root.job) return ""
    if (root.mode === "history") {
      if (root.run && typeof root.run.startedAt === "number") {
        var a = Model.formatClock(root.run.startedAt * 1000)
        return typeof root.run.endedAt === "number" ? a + " → " + Model.formatClock(root.run.endedAt * 1000) : a
      }
      return root.fireAtSec !== null ? Model.formatClock(root.fireAtSec * 1000) : Model.formatClock(Model.endedAtMs(root.job))
    }
    if (root.status === "running")
      return root.run && typeof root.run.startedAt === "number" ? "started " + Model.formatClock(root.run.startedAt * 1000) : "started"
    if (root.fireAtSec === null) return ""
    return Model.formatClock(root.fireAtSec * 1000)
  }

  readonly property string countdownText: {
    if (!root.job) return ""
    // History says how long a run took in its outcome sentence; the pill says draft.
    if (root.mode === "history") return ""
    if (root.status === "draft") return ""
    if (root.status === "running") return ""
    if (root.fireAtSec === null) return ""
    if (root.status === "armed") return Model.formatCountdown(root.fireAtSec * 1000, root.nowMs)
    return root.fireAtSec * 1000 <= root.nowMs ? "was due" : Model.formatCountdown(root.fireAtSec * 1000, root.nowMs)
  }

  readonly property string pillText: {
    if (root.pending) return "re-arming"
    if (root.status === "running" && root.run && typeof root.run.startedAt === "number" && root.mode === "queue")
      return Model.formatRunning(root.run.startedAt * 1000, root.nowMs)
    return ""
  }

  readonly property color outcomeInk: {
    if (root.refusalLine !== "") return root.theme.warnInk
    if (root.mode !== "history" && !root.pending) {
      var spec = Model.statusSpec(root.status, root.st.wait || "")
      if (spec.tone === "warn") return root.theme.warnInk
      if (spec.tone === "harness") return root.theme.harnessInk(root.harness)
      return root.theme.soft
    }
    if (root.pending) return root.theme.readable
    switch (root.status) {
    case "failed": case "gave_up": return root.theme.badInk
    case "limit": case "missed": case "interrupted": case "busy": return root.theme.warnInk
    case "done": return root.theme.okInk
    }
    return root.theme.soft
  }

  readonly property real clusterWidth: Style.space(384)
  readonly property real textLeft: Style.space(40)
  readonly property real middleWidth: Math.max(0, root.width - root.textLeft - root.clusterWidth - Style.space(24))

  implicitHeight: Style.space(48)
  implicitWidth: Style.space(1000)
  opacity: root.placeholder ? 0.35 : (root.ghost ? 0.92 : 1)
  scale: root.ghost ? 1.03 : 1

  Behavior on opacity {
    NumberAnimation { duration: root.theme.fadeMs(120) }
  }

  BorderSurface {
    anchors.fill: parent
    radius: Style.cornerRadius
    color: {
      if (root.ghost) return root.theme.surface
      if (root.dropHere) return Util.alpha(root.theme.accent, 0.12)
      if (root.hasCursor) return Util.alpha(root.theme.fg, 0.08)
      if (root.marked) return Util.alpha(root.theme.accent, 0.10)
      return "transparent"
    }
    borderSpec: {
      if (root.guard) return Border.flat(root.theme.accent, Math.max(1, Style.space(2)))
      if (root.ghost || root.dropHere) return Border.flat(root.theme.accent, 1)
      if (root.hasCursor) return Border.controlSpec("hover-cursor", root.theme.fg, root.theme.accent)
      return Border.none()
    }
  }

  // Ghost ground: the ghost sits over other rows, so it needs its own opaque layer
  // under the tint above.
  Rectangle {
    anchors.fill: parent
    z: -1
    visible: root.ghost
    color: root.theme.surface
  }

  HarnessRail {
    id: rail
    anchors.left: parent.left
    anchors.leftMargin: Style.space(2)
    anchors.top: parent.top
    anchors.topMargin: Style.space(5)
    anchors.bottom: parent.bottom
    anchors.bottomMargin: Style.space(5)
    theme: root.theme
    flash: root.flash
    harness: root.harness
    strength: root.railStrength
    pulse: root.status === "running"
  }

  // Gutter: a check when the row is marked, the drag grip when it can be dragged
  // and the pointer is over it.
  Text {
    anchors.left: rail.right
    anchors.leftMargin: Style.space(2)
    anchors.verticalCenter: parent.verticalCenter
    textFormat: Text.PlainText
    visible: root.marked || (root.draggable && rowMouse.containsMouse && !root.ghost)
    text: root.marked ? "\uDB80\uDD2C" : "\uDB80\uDDDD"   // md-check U+F012C, md-drag_vertical U+F01DD
    color: root.marked ? root.theme.accentInk : root.theme.soft
    font.family: root.theme.fontFamily
    font.pixelSize: root.theme.type.meta
  }

  AgentMark {
    x: Style.space(20)
    y: Style.space(8)
    theme: root.theme
    agent: root.harness
    size: Style.space(14)
    dimmed: root.status === "draft" || root.status === "disarmed"
  }

  Column {
    x: root.textLeft
    anchors.verticalCenter: parent.verticalCenter
    width: root.middleWidth * 0.52
    spacing: Style.space(2)

    Text {
      width: parent.width
      textFormat: Text.PlainText
      text: root.job ? String(root.job.label || "") : ""
      color: root.hasCursor || root.ghost ? root.theme.fg : root.theme.strong
      elide: Text.ElideRight
      maximumLineCount: 1
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.body
    }

    Item {
      width: parent.width
      height: metaText.implicitHeight

      Rectangle {
        id: paidChip
        anchors.verticalCenter: parent.verticalCenter
        visible: root.paid
        width: root.paid ? paidText.implicitWidth + Style.space(6) * 2 : 0
        height: Math.max(paidText.implicitHeight, Style.space(14))
        radius: height / 2
        color: Util.alpha(root.theme.warnInk, 0.14)
        border.width: 1
        border.color: Util.alpha(root.theme.warnInk, 0.55)

        Text {
          id: paidText
          anchors.centerIn: parent
          textFormat: Text.PlainText
          text: "paid"
          color: root.theme.warnInk
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
        }
      }

      Text {
        id: metaText
        x: root.paid ? paidChip.width + Style.space(6) : 0
        width: Math.max(0, parent.width - x)
        textFormat: Text.PlainText
        text: root.metaLine
        color: root.theme.soft
        elide: Text.ElideRight
        maximumLineCount: 1
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.meta
      }
    }
  }

  Column {
    x: root.textLeft + root.middleWidth * 0.52 + Style.space(12)
    anchors.verticalCenter: parent.verticalCenter
    width: root.middleWidth * 0.48
    spacing: Style.space(2)

    Item {
      id: sessionRow
      width: parent.width
      height: sessionTitleText.implicitHeight
      readonly property real gap: root.folderNamed || root.sessionTitle === "" || root.sessionPath === "" ? 0 : Style.space(8)

      Text {
        id: sessionTitleText
        x: root.folderNamed ? sessionPathText.width : 0
        width: Math.max(0, Math.min(implicitWidth, sessionRow.width - sessionPathText.width - sessionRow.gap))
        textFormat: Text.PlainText
        text: root.sessionTitle
        color: root.theme.readable
        elide: Text.ElideRight
        maximumLineCount: 1
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.data
      }

      Text {
        id: sessionPathText
        x: root.folderNamed ? 0 : sessionTitleText.width + sessionRow.gap
        anchors.baseline: sessionTitleText.baseline
        visible: root.sessionPath !== ""
        width: root.sessionPath === "" ? 0 : Math.min(implicitWidth, sessionRow.width, root.folderNamed
          ? Math.max(0, sessionRow.width - sessionTitleText.implicitWidth)
          : Math.max(Style.space(110), sessionRow.width - sessionTitleText.implicitWidth - sessionRow.gap))
        textFormat: Text.PlainText
        text: root.sessionPath
        color: root.theme.soft
        elide: Text.ElideMiddle
        maximumLineCount: 1
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.meta
      }
    }

    Text {
      width: parent.width
      textFormat: Text.PlainText
      text: root.triggerLine
      color: root.outcomeInk
      elide: Text.ElideRight
      maximumLineCount: 1
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
    }
  }

  // Fixed right cluster.
  Item {
    id: cluster
    anchors.right: parent.right
    anchors.rightMargin: Style.space(10)
    anchors.top: parent.top
    anchors.bottom: parent.bottom
    width: root.clusterWidth

    Text {
      id: clock
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      width: root.mode === "history" ? Style.space(112) : Style.space(88)
      textFormat: Text.PlainText
      text: root.dropPreview !== "" ? root.dropPreview : root.clockText
      color: root.dropPreview !== "" ? root.theme.accentInk : (root.hasCursor ? root.theme.fg : root.theme.readable)
      horizontalAlignment: Text.AlignRight
      elide: Text.ElideLeft
      maximumLineCount: 1
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.data
      font.features: root.theme.type.digits
      font.bold: true
    }

    Text {
      anchors.left: clock.right
      anchors.leftMargin: root.mode === "history" ? 0 : Style.space(10)
      anchors.verticalCenter: parent.verticalCenter
      width: root.mode === "history" ? 0 : Style.space(76)
      textFormat: Text.PlainText
      text: root.countdownText
      color: root.theme.soft
      elide: Text.ElideRight
      maximumLineCount: 1
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
      font.features: root.theme.type.digits
    }

    // The state pill gets a fixed column, as wide as the longest word this list can show,
    // so the level words line up down the list whatever each row's state is.
    TextMetrics {
      id: pillMetrics
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
      font.features: root.theme.type.digits
      text: root.mode === "history" ? "session busy" : "waits for reset"
    }

    Item {
      id: pillColumn
      anchors.right: parent.right
      anchors.top: parent.top
      anchors.bottom: parent.bottom
      width: Math.ceil(pillMetrics.advanceWidth) + Style.space(7) * 2 + Style.space(18)
    }

    // Plan is the quiet default and reads as plain words; Unattended keeps its amber pill.
    Rectangle {
      id: levelChip
      anchors.right: pillColumn.left
      anchors.rightMargin: Style.space(8)
      anchors.verticalCenter: parent.verticalCenter
      visible: root.levelLabel !== ""
      height: Style.space(18)
      width: levelText.implicitWidth + Style.space(7) * 2
      radius: height / 2
      readonly property string tone: !!root.job ? (Edition.LEVEL_TONES[root.job.level] || "") : ""
      readonly property bool loud: levelChip.tone === "warn" || levelChip.tone === "bad"
      readonly property color ink: levelChip.tone === "bad" ? root.theme.badInk
        : (levelChip.tone === "warn" ? root.theme.warnInk : root.theme.soft)
      color: levelChip.loud ? Util.alpha(levelChip.ink, 0.12) : "transparent"

      Text {
        id: levelText
        anchors.centerIn: parent
        textFormat: Text.PlainText
        text: root.levelLabel
        color: levelChip.ink
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.meta
      }
    }

    StatusPill {
      id: pill
      anchors.right: pillColumn.right
      anchors.verticalCenter: parent.verticalCenter
      theme: root.theme
      status: root.pending ? "armed" : root.status
      wait: root.pending ? "transient" : String(root.st.wait || "")
      harness: root.harness
      text: root.pillText
    }

    // Wheel over the time nudges it, like the When readout in Compose. Clicks pass
    // through to the row.
    MouseArea {
      anchors.left: parent.left
      anchors.top: parent.top
      anchors.bottom: parent.bottom
      width: clock.width + Style.space(90)
      acceptedButtons: Qt.NoButton
      enabled: root.mode === "queue" && root.status === "armed" && !root.ghost
      property real acc: 0
      onWheel: function (wheel) {
        var r = Util.wheelSteps(acc, wheel.angleDelta.y)
        acc = r.remainder
        if (r.steps !== 0) root.wheelNudge(r.steps)
        wheel.accepted = true
      }
    }
  }

  // Press and move past 8 px to drag (ASM DND lane); a click moves the cursor.
  MouseArea {
    id: rowMouse
    anchors.fill: parent
    z: -1
    enabled: !root.ghost
    hoverEnabled: true
    cursorShape: root.draggable ? Qt.OpenHandCursor : Qt.PointingHandCursor
    preventStealing: root.draggable

    property bool armedDrag: false
    property bool dragging: false
    property bool wasDrag: false
    property real pressX: 0
    property real pressY: 0

    onEntered: root.hovered()
    onPressed: function (mouse) {
      armedDrag = root.draggable && mouse.button === Qt.LeftButton
      dragging = false
      wasDrag = false
      pressX = mouse.x
      pressY = mouse.y
    }
    onPositionChanged: function (mouse) {
      if (!armedDrag) return
      if (!dragging) {
        if (Math.hypot(mouse.x - pressX, mouse.y - pressY) < 8) return
        dragging = true
        wasDrag = true
        root.dragBegin(rowMouse, mouse.x, mouse.y)
      }
      root.dragMove(rowMouse, mouse.x, mouse.y)
    }
    onReleased: {
      var was = dragging
      armedDrag = false
      dragging = false
      if (was) root.dragEnd()
    }
    onCanceled: {
      var was = dragging
      armedDrag = false
      dragging = false
      if (was) root.dragCancel()
    }
    onClicked: function (mouse) {
      if (wasDrag) { wasDrag = false; return }
      root.activated(mouse.modifiers)
    }
  }
}
