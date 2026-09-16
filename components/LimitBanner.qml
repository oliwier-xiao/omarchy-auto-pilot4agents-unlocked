import QtQuick
import qs.Commons
import "../lib/Model.js" as Model

// The waiting state above the queue (R6 8.6). Waiting for a reset is not a failure,
// so the card wears the agent's tint rather than the urgent colour: which limit is
// full, how many jobs wait for it, when it resets, and the meters behind it. The
// source is the one the waiting job draws from (PublicJob.limitSource), whatever the
// agent. The view owns the two-press guard for the disarm button.
Item {
  id: root

  required property var theme

  // Queue jobs (PublicJob) in list order.
  property var jobs: []
  // Usage v2 providers.
  property var providers: []
  property double nowMs: 0
  property bool guard: false
  property bool working: false

  signal disarmRequested(var ids)

  // The source each agent draws from when a job does not say.
  readonly property var defaultSource: ({ claude: "claude", codex: "codex", cursor: "cursor", gemini: "gemini-daily" })

  readonly property var headlines: ({
    claude: "Claude session limit reached.",
    codex: "Codex limit reached.",
    cursor: "Cursor included usage is used up.",
    "opencode-go": "OpenCode Go limit reached.",
    "zen-free": "OpenCode Zen free daily limit reached.",
    gemini: "Gemini daily limit reached.",
    "gemini-daily": "Gemini daily limit reached."
  })

  function windowOfKind(provider, kind) {
    var ws = provider && Array.isArray(provider.windows) ? provider.windows : []
    for (var i = 0; i < ws.length; i++) if (ws[i] && ws[i].kind === kind) return ws[i]
    return null
  }

  readonly property var claude: Model.providerById(root.providers, "claude")
  readonly property var session: root.windowOfKind(root.claude, "session")
  readonly property bool sessionFull: !!root.session && typeof root.session.percent === "number"
    && root.session.percent >= 0.99

  readonly property var waiting: {
    var out = []
    var list = Array.isArray(root.jobs) ? root.jobs : []
    for (var i = 0; i < list.length; i++) {
      var j = list[i]
      if (!j || !j.state || j.state.status !== "armed") continue
      var kind = j.trigger ? j.trigger.kind : ""
      if (j.state.wait === "limit" || (root.sessionFull && kind === "claude_5h_reset" && j.harness === "claude")) out.push(j)
    }
    return out
  }

  readonly property var ids: root.waiting.map(function (j) { return j.id })
  readonly property string harness: root.waiting.length > 0 ? String(root.waiting[0].harness || "") : ""
  readonly property string source: {
    if (root.waiting.length === 0) return ""
    var j = root.waiting[0]
    if (typeof j.limitSource === "string" && j.limitSource !== "") return j.limitSource
    return root.defaultSource.hasOwnProperty(root.harness) ? root.defaultSource[root.harness] : ""
  }
  readonly property var provider: root.source !== "" ? Model.providerById(root.providers, root.source) : null
  readonly property var mainWindow: {
    if (!root.provider) return null
    if (root.source === "claude") return root.session
    return Model.windowByKey(root.provider, root.provider.headlineKey)
  }
  readonly property var weekly: root.source === "claude" ? root.windowOfKind(root.claude, "weekly") : null

  readonly property double resetMs: {
    var w = root.mainWindow
    if (w && typeof w.resetsAt === "number" && w.resetsAt * 1000 > root.nowMs) return w.resetsAt * 1000
    var best = 0
    for (var i = 0; i < root.waiting.length; i++) {
      var f = root.waiting[i].state.fireAt
      if (typeof f === "number" && (best === 0 || f * 1000 < best)) best = f * 1000
    }
    return best
  }

  readonly property string headline: {
    var n = root.waiting.length
    if (n === 0) return ""
    var what = root.headlines.hasOwnProperty(root.source) ? root.headlines[root.source]
      : root.harness !== "" ? Model.harnessName(root.harness) + " limit reached." : "Usage limit reached."
    var count = n === 1 ? "1 job waits" : n + " jobs wait"
    if (root.resetMs <= 0) return what + " " + count + " for the reset."
    return what + " " + count + " for the reset at " + Model.formatClock(root.resetMs) + ", "
      + Model.formatCountdown(root.resetMs, root.nowMs) + "."
  }

  readonly property string staleLine: {
    var p = root.provider
    if (!p || p.stale !== true || p.source === "computed") return ""
    var name = typeof p.name === "string" && p.name !== "" ? p.name : p.id
    var age = typeof p.ageSec === "number" ? " is " + Model.formatAge(p.ageSec) + " old." : " age is unknown."
    return name + " usage" + age + " Times may shift."
  }

  readonly property color ink: root.theme.harnessInk(root.harness)

  function windowText(w) {
    if (!w) return ""
    var label = typeof w.shortLabel === "string" && w.shortLabel !== "" ? w.shortLabel : "Usage"
    return label + " " + Model.percentText(w.percent, w.over === true)
  }

  visible: root.waiting.length > 0
  implicitWidth: Style.space(1000)
  implicitHeight: root.visible ? content.implicitHeight + Style.space(10) * 2 : 0

  Rectangle {
    anchors.fill: parent
    radius: Style.cornerRadius
    color: Util.alpha(root.ink, 0.12)
    border.width: 1
    border.color: Util.alpha(root.ink, 0.45)
  }

  Column {
    id: content
    x: Style.space(12)
    y: Style.space(10)
    width: Math.max(0, root.width - Style.space(12) * 2 - button.width - Style.space(12))
    spacing: Style.space(6)

    Row {
      spacing: Style.space(8)
      width: parent.width

      AgentMark {
        anchors.verticalCenter: parent.verticalCenter
        theme: root.theme
        agent: root.harness
        size: Style.space(14)
      }

      Text {
        anchors.verticalCenter: parent.verticalCenter
        width: parent.width - Style.space(22)
        textFormat: Text.PlainText
        text: root.headline
        color: root.theme.fg
        elide: Text.ElideRight
        maximumLineCount: 1
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.body
        font.bold: true
      }
    }

    Row {
      visible: !!root.mainWindow && typeof root.mainWindow.percent === "number"
      spacing: Style.space(10)
      leftPadding: Style.space(22)

      Text {
        anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText
        text: root.windowText(root.mainWindow)
        color: root.theme.readable
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.meta
        font.features: root.theme.type.digits
      }

      Meter {
        anchors.verticalCenter: parent.verticalCenter
        width: Style.space(200)
        theme: root.theme
        value: root.mainWindow && typeof root.mainWindow.percent === "number" ? root.mainWindow.percent : -1
        warning: Model.meterTone(root.mainWindow ? root.mainWindow.percent : null, false) === "warn"
        alarming: Model.meterTone(root.mainWindow ? root.mainWindow.percent : null, !!root.mainWindow && root.mainWindow.over === true) === "bad"
        fill: root.ink
      }

      Text {
        anchors.verticalCenter: parent.verticalCenter
        visible: !!root.weekly
        textFormat: Text.PlainText
        text: root.windowText(root.weekly)
        color: root.theme.readable
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.meta
        font.features: root.theme.type.digits
      }

      Meter {
        anchors.verticalCenter: parent.verticalCenter
        visible: !!root.weekly
        width: Style.space(120)
        theme: root.theme
        value: root.weekly && typeof root.weekly.percent === "number" ? root.weekly.percent : -1
        warning: Model.meterTone(root.weekly ? root.weekly.percent : null, false) === "warn"
        alarming: Model.meterTone(root.weekly ? root.weekly.percent : null, !!root.weekly && root.weekly.over === true) === "bad"
        fill: root.ink
      }
    }

    Text {
      visible: root.staleLine !== ""
      leftPadding: Style.space(22)
      textFormat: Text.PlainText
      text: root.staleLine
      color: root.theme.soft
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
    }
  }

  ActionButton {
    id: button
    anchors.right: parent.right
    anchors.rightMargin: Style.space(12)
    anchors.verticalCenter: parent.verticalCenter
    theme: root.theme
    danger: true
    armed: root.guard
    enabled: !root.working && root.ids.length > 0
    hasCursor: button.hovered
    text: root.ids.length === 1 ? "Disarm" : root.ids.length === 2 ? "Disarm both" : "Disarm all " + root.ids.length
    onClicked: root.disarmRequested(root.ids)
  }
}
