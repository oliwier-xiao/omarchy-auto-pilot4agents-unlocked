import QtQuick
import Quickshell.Io
import qs.Commons
import qs.Ui
import "lib/Edition.js" as Edition
import "lib/Model.js" as Model
import "lib/Tint.js" as Tint

// The bar slot: one glyph and a short word or countdown that say what state the jobs
// are in, and the way into the panel. It holds no scheduling state of its own. There
// is one of these per monitor, so everything it shows comes from the service, which
// exists once.
//
// States (FEEDBACK-UI 1, first match wins), each a glyph and a word, never colour
// alone: attention in the urgent ink (a failure since the panel was last opened, an
// armed job whose agent is signed out, a helper problem); running in the accent,
// pulsing unless motion is reduced; near in green (under an hour); soon in amber
// (under six hours); later in the bar's own foreground; success, a green check;
// drafts and idle in the soft foreground (solved, not an opacity dim). Inks are solved against the bar surface, so a light theme
// stays readable.
BarWidget {
  id: root
  moduleName: Edition.PLUGIN_ID

  // nf-md-calendar_clock (U+F00F0) at rest, nf-md-play (U+F040A) while a job runs.
  readonly property string glyphIdle: Model.GLYPH.calendar
  readonly property string glyphRunning: Model.GLYPH.play

  readonly property var service: bar && bar.shell ? bar.shell.serviceFor(Edition.PLUGIN_ID) : null
  readonly property bool serviceReady: !!root.service && root.service.ready === true

  // The shell never merges manifest defaults into `settings`, so the fallback
  // repeats the manifest's defaultValue.
  readonly property string labelMode: String(setting("barLabel", "Next run"))

  readonly property bool running: root.serviceReady && root.service.anyRunning === true

  // The service's state; derived from its v1 counts when a service without barState
  // answers (an older build still loaded during a reload).
  readonly property var barState: {
    var s = root.service
    if (!s) return Model.barState({ ready: false })
    var given = s.barState
    if (given && typeof given === "object" && typeof given.state === "string") return given
    return Model.barState({
      ready: s.ready === true,
      setupProblem: typeof s.setupProblem === "string" ? s.setupProblem : "",
      problems: Number(s.attentionCount) || 0,
      running: Number(s.runningCount) || 0,
      nextFireAtMs: s.nextJob && typeof s.nextJob.fireAtMs === "number" ? s.nextJob.fireAtMs : null,
      nowMs: Number(s.nowMs) || Date.now()
    })
  }

  readonly property string glyph: String(root.barState.glyph || root.glyphIdle)
  readonly property string tone: String(root.barState.tone || "")

  readonly property string labelText: {
    if (root.vertical || !root.service) return ""
    if (root.labelMode === "Queued count")
      return root.serviceReady && root.service.armedCount > 0 ? String(root.service.armedCount) : ""
    if (root.labelMode !== "Next run") return ""
    return String(root.barState.text || "")
  }

  // Every digit at its widest, so the bar does not shift under the cursor while a
  // countdown ticks.
  readonly property string labelTemplate: root.labelText.replace(/[0-9]/g, "8")

  readonly property color barSurface: Color.bar.background
  readonly property color urgentColor: root.bar && root.bar.urgent ? root.bar.urgent : Color.urgent
  readonly property bool coloured: root.tone === "bad" || root.tone === "ok" || root.tone === "warn" || root.tone === "accent"
  readonly property color stateInk: {
    var surface = String(root.barSurface)
    if (root.dimmedState) return root.softInk
    if (root.tone === "bad") return Tint.ink(String(root.urgentColor), surface, 4.5)
    if (root.tone === "ok") return Tint.solved(150, 60, surface, 4.5)
    if (root.tone === "warn") return Tint.solved(38, 85, surface, 4.5)
    if (root.tone === "accent") return Tint.ink(String(Color.accent), surface, 4.5)
    return root.urgentColor
  }
  // Drafts and idle are calm, not faded: the bar foreground at the lowest alpha that still
  // reads at 4.5:1 on the bar (the panel's soft ink), never the kit's 45% opacity dim.
  readonly property bool dimmedState: root.barState.state === "drafts" || root.barState.state === "idle"
  readonly property color barForeground: root.bar && root.bar.barForeground !== undefined ? root.bar.barForeground : Color.foreground
  readonly property color softInk: {
    var fg = root.barForeground
    return Qt.rgba(fg.r, fg.g, fg.b, Tint.alphaFor(String(fg), String(root.barSurface), 4.5, 0.6))
  }
  readonly property bool pulsing: root.barState.pulse === true && root.serviceReady
    && !(root.service.settings && root.service.settings.motion === "reduced")

  // Only the state sentence (fixed words, counts and clock times, or a fixed helper
  // message) and counts reach the bar's tooltip; no job label or path is ever put here.
  readonly property string tooltipText: {
    var name = Edition.DISPLAY_NAME
    var first = String(root.barState.sentence || "")
    var s = root.service
    if (!s || !root.serviceReady)
      return typeof s !== "undefined" && s && typeof s.setupProblem === "string" && s.setupProblem !== ""
        ? first + "\n" + name : name + " · starting"
    var line = name + " · " + s.armedCount + " armed"
    if (s.runningCount > 0) line += " · " + s.runningCount + " running"
    if (Number(s.draftCount) > 0) line += " · " + s.draftCount + " drafted"
    return first + "\n" + line
  }

  // Settings live on this half of the plugin; the service is created without them.
  function pushSettings() {
    var s = root.service
    if (!s) return
    s.barLabel = String(setting("barLabel", "Next run"))
  }

  onServiceChanged: { pushSettings(); injectPanel() }
  onSettingsChanged: { pushSettings(); injectPanel() }
  onBarChanged: injectPanel()
  Component.onCompleted: pushSettings()

  // ---- Panel shape contract (Bar.findPanelWidget, requestPopout) -------------

  // The loaded Panel.qml, untyped: its members belong to another file.
  readonly property var panel: panelLoader.item

  readonly property bool opened: root.panel ? root.panel.opened === true : false
  readonly property bool popoutSwitchClosing: root.panel ? root.panel.popoutSwitchClosing === true : false

  // Mounted on first use, then kept. A monitor whose panel is never opened builds
  // no views, sheets or list models and runs none of their bindings. The Loader is
  // synchronous, so the item exists as soon as `active` is set.
  function ensurePanel() {
    if (!panelLoader.active) panelLoader.active = true
    return panelLoader.item
  }

  function open() {
    var p = root.ensurePanel()
    if (p) { root.injectPanel(); p.open() }
  }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function togglePanel() {
    var p = root.ensurePanel()
    if (p) { root.injectPanel(); p.toggle() }
  }
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }

  function openView(view) {
    var p = root.ensurePanel()
    if (!p) return
    root.injectPanel()
    if (typeof p.showView === "function") p.showView(view)
    // On an open panel the view switches in place; opening again would replay the entrance.
    if (!p.opened) p.open()
  }

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("hostWidget" in target) target.hostWidget = root
    if ("service" in target) target.service = root.service
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  readonly property real openPanelIndicatorWidth: root.labelText !== "" ? row.implicitWidth : 0
  readonly property real openPanelIndicatorHeight: Style.bar.iconCanvas

  Loader {
    id: panelLoader
    active: false
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  // Anything running as this user can call these. None of them takes text (IPC
  // arguments are readable in /proc) and none of them changes a job: they only
  // move this panel.
  IpcHandler {
    target: Edition.WIDGET_IPC_TARGET

    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.togglePanel() }
    function compose(): void {
      root.openView("compose")
      var p = panelLoader.item
      if (p && typeof p.newDraft === "function") p.newDraft()
    }
    function queue(): void { root.openView("queue") }
    function history(): void { root.openView("history") }
  }

  // WidgetButton rather than a bare MouseArea: it registers the click target the
  // bar needs while another panel's overlay is mapped, and it provides the hover
  // state the bar's tooltip requires.
  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    labelVisible: false
    hasVisualContent: true
    fontSize: Style.bar.iconFont
    active: root.coloured || root.dimmedState
    activeColor: root.stateInk
    dimmed: false
    tooltipText: root.tooltipText
    fixedWidth: root.vertical
      ? -1
      : (root.labelText !== ""
        ? Math.round(row.implicitWidth + Style.spaceReal(8.5) * 2)
        : Style.bar.iconSlot)
    fixedHeight: root.vertical ? Style.bar.iconSlot : -1

    onPressed: function (b) {
      if (b === Qt.RightButton) root.openView("compose")
      else if (b === Qt.LeftButton) root.togglePanel()
    }

    Row {
      id: row
      anchors.centerIn: parent
      spacing: root.labelText !== "" ? Style.space(2) : 0

      Item {
        anchors.verticalCenter: parent.verticalCenter
        width: root.labelText !== "" ? Style.bar.iconCanvas : Style.bar.iconSlot
        height: Style.bar.iconCanvas

        OpticalGlyph {
          id: glyphItem
          anchors.fill: parent
          text: root.glyph
          fontFamily: button.fontFamily
          fontSize: button.fontSize
          color: button.active && button.useActiveColor ? button.activeColor : button.foreground
        }

        SequentialAnimation {
          running: root.pulsing && root.visible
          loops: Animation.Infinite
          NumberAnimation { target: glyphItem; property: "opacity"; to: 0.5; duration: 800; easing.type: Easing.InOutSine }
          NumberAnimation { target: glyphItem; property: "opacity"; to: 1; duration: 800; easing.type: Easing.InOutSine }
          onRunningChanged: if (!running) glyphItem.opacity = 1
        }
      }

      Text {
        id: figure
        anchors.verticalCenter: parent.verticalCenter
        visible: root.labelText !== ""
        textFormat: Text.PlainText
        text: root.labelText
        color: button.active && button.useActiveColor ? button.activeColor : button.foreground
        font.family: button.fontFamily
        font.pixelSize: Style.font.body
        font.features: ({ "tnum": 1 })
        renderType: Text.NativeRendering
        horizontalAlignment: Text.AlignLeft
        width: figureBox.width

        TextMetrics {
          id: figureBox
          font: figure.font
          text: root.labelTemplate
        }
      }
    }
  }
}
