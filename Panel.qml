pragma ComponentBehavior: Bound

import QtQuick
import Quickshell
import qs.Commons
import qs.Ui
import "components"
import "lib/Edition.js" as Edition
import "lib/Model.js" as Model
import "lib/Tint.js" as Tint

// The Auto Pilot card: one fixed 1100 x 800 keyboard panel with three views
// (Compose, Queue, History), in-card sheets, a notice row and the key hints.
//
// This file owns presentation only. It holds no job, timer or schedule: every job
// lives in the service (one per shell) and fires from its own systemd timer. A
// panel exists once per monitor and may be reloaded at any time without losing
// anything.
//
// Keys go through one catcher, in a fixed order (CONTRACT 7.3): an open sheet first,
// then the global Ctrl commands, then the active view, then Esc closes and Tab moves
// to the neighbouring bar panel. Typing never runs a command: every command outside
// the Compose form takes Ctrl.
Panel {
  id: root
  moduleName: Edition.PLUGIN_ID
  ipcTarget: ""
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  property var service: null
  readonly property var barIdentity: root.hostWidget || root

  property string view: "compose"
  property string sheet: ""

  readonly property var views: ["compose", "queue", "history"]
  readonly property string home: {
    var h = Quickshell.env("HOME")
    return h === undefined || h === null ? "" : String(h)
  }
  readonly property bool serviceReady: !!root.service && root.service.ready === true
  // loading | setup | ready
  readonly property string contentState: {
    if (!root.service) return "loading"
    if (typeof root.service.setupProblem === "string" && root.service.setupProblem !== "") return "setup"
    return root.serviceReady ? "ready" : "loading"
  }

  property bool _viewForced: false
  property bool _viewTouched: false
  property bool _viewerCounted: false
  property bool _clickSwitch: false

  // Colour tokens, built once here and handed to every component (CONTRACT 6.1).
  readonly property QtObject theme: QtObject {
    id: theme

    readonly property color surface: Color.popups.background
    readonly property color fg: Color.popups.text
    // Raw accent is for fills, borders, the underline, carets and the now-line.
    // Text and glyphs use accentInk, solved to the text contrast target.
    readonly property color accent: Color.accent
    readonly property color accentInk: Tint.ink(String(Color.accent), String(theme.surface), theme.textTarget)
    // The text ink ladder. Soft is solved per theme: the lowest foreground alpha (never
    // under .60) that still reaches the text contrast target on this surface, so the
    // smallest meta text stays readable on light themes too. Readable and strong keep
    // their steps above it.
    readonly property real softAlpha: Tint.alphaFor(String(theme.fg), String(theme.surface), theme.textTarget, 0.60)
    readonly property real readableAlpha: Math.min(1, Math.max(0.76, theme.softAlpha + 0.08))
    readonly property color strong: Util.alpha(theme.fg, Math.min(1, Math.max(0.88, theme.readableAlpha + 0.06)))
    readonly property color readable: Util.alpha(theme.fg, theme.readableAlpha)
    readonly property color soft: Util.alpha(theme.fg, theme.softAlpha)
    // Hairlines only, never text.
    readonly property color faint: Util.alpha(theme.fg, 0.14)
    // Raised when the platform asks for more contrast (Qt 6.10 accessibility
    // hints); guarded, because not every build exposes them.
    readonly property real textTarget: {
      var hints = Qt.styleHints
      var a = hints ? hints.accessibility : undefined
      if (!a || typeof a.contrastPreference === "undefined") return 4.5
      var high = typeof Qt.HighContrast === "number" ? Qt.HighContrast : 1
      return a.contrastPreference === high ? 7.0 : 4.5
    }
    // One text ink and one mark ink per agent of the edition (Model.harnessOrder is
    // Edition.HARNESS_IDS plus any agent this build names that the list lacks).
    readonly property var inks: {
      var out = {}
      var ids = Model.harnessOrder()
      for (var i = 0; i < ids.length; i++) out[ids[i]] = Tint.harnessInk(ids[i], String(theme.surface), theme.textTarget)
      return out
    }
    readonly property var marks: {
      var out = {}
      var ids = Model.harnessOrder()
      var mono = typeof Tint.harnessMark === "function"
      for (var i = 0; i < ids.length; i++)
        out[ids[i]] = mono ? Tint.harnessMark(ids[i], String(theme.surface), 3.0) : Tint.harnessInk(ids[i], String(theme.surface), 3.0)
      return out
    }
    // Rails, capsules and meter fills: the agent's own hue solved for graphics (3:1). The
    // Cursor and Pi greys belong to their marks alone; a grey rail would read as disabled.
    readonly property var fills: {
      var out = {}
      var ids = Model.harnessOrder()
      for (var i = 0; i < ids.length; i++) out[ids[i]] = Tint.harnessInk(ids[i], String(theme.surface), 3.0)
      return out
    }
    readonly property var geminiStops: Tint.geminiStops(String(theme.surface), 3.0)
    readonly property color okInk: Tint.solved(150, 60, String(theme.surface), theme.textTarget)
    readonly property color warnInk: Tint.solved(38, 85, String(theme.surface), theme.textTarget)
    readonly property color badInk: Tint.ink(String(Color.urgent), String(theme.surface), theme.textTarget)
    readonly property bool reduceMotion: !!root.service && !!root.service.settings && root.service.settings.motion === "reduced"
    // Every endless animation (running pulse, caret blink) stops when this is false.
    readonly property bool animate: root.opened && panel.visible
    readonly property string fontFamily: root.bar && root.bar.fontFamily ? String(root.bar.fontFamily) : Style.font.family
    // Type roles. Components read sizes from here, never from Style.font directly, so
    // one role always means one size, weight rule and tracking across the panel.
    // Regular and Bold only; no italic, no uppercase.
    readonly property QtObject type: QtObject {
      // The one loud element: the When readout.
      readonly property int readout: Style.font.display
      // Brand name, sheet titles, full-card headlines. Bold.
      readonly property int title: Style.font.title
      // Row and session titles, tabs, buttons, fields, prose.
      readonly property int body: Style.font.body
      // Section and card labels. Bold, tracked, soft ink.
      readonly property int label: Style.font.bodySmall
      // Values beside labels: session columns, chips, notices, the command line.
      readonly property int data: Style.font.bodySmall
      // Meta lines, countdowns, notes, pill words, key hints.
      readonly property int meta: Style.font.caption
      readonly property int glyph: Style.font.iconSmall
      readonly property int glyphLarge: Style.font.icon
      readonly property int brandGlyph: Style.font.heading
      readonly property real tracking: Style.spaceReal(0.4)
      // Line height for reading surfaces (the prompt in a job card).
      readonly property real proseLeading: 1.45
      // About 78 characters of body text.
      readonly property real measure: Style.space(560)
      // Tabular figures, so clocks and counts keep their columns in any theme font.
      readonly property var digits: ({ "tnum": 1 })
    }

    function harnessInk(id) { return theme.inks[id] || theme.soft }
    function harnessMark(id) { return theme.marks[id] || theme.soft }
    function harnessFill(id) { return theme.fills[id] || theme.soft }
    // Position, scale and height.
    function moveMs(ms) { return theme.reduceMotion ? 0 : ms }
    // Colour and opacity: gentler, never gone.
    function fadeMs(ms) { return theme.reduceMotion ? Math.min(ms, 120) : ms }
  }

  // ---------------------------------------------------------------- views and sheets

  function viewItem() {
    if (root.view === "queue") return queueView
    if (root.view === "history") return historyView
    return composeView
  }

  function sheetFor(name) {
    if (name === "session") return sessionSheet
    if (name === "settings") return settingsSheet
    if (name === "shift") return shiftSheet
    if (name === "model") return modelSheet
    if (name === "limits") return limitsSheet
    if (name === "signin") return signInSheet
    return null
  }

  function sheetItem() {
    return root.sheet === "" ? null : root.sheetFor(root.sheet)
  }

  // No animation: a keyboard switch swaps the content at once (R6 6.1).
  // keepNotice: the view asked for this switch itself (Compose opening the Queue after
  // arming), so its notice goes along instead of staying behind.
  function showView(v, keepNotice) {
    if (root.views.indexOf(v) < 0) return
    if (keepNotice === true) noticeRow.releaseOrigin()
    else noticeRow.clearIfOriginNot(v)
    if (root.sheet !== "") root.closeSheet()
    root.view = v
    if (root.opened) {
      root._viewTouched = true
      root.activateView()
    } else {
      // BarWidget.openView calls this right before open(): the choice beats the
      // default view for that one opening.
      root._viewForced = true
      Qt.callLater(function () { root._viewForced = false })
    }
  }

  function showViewFocus(v, jobId) {
    root.showView(v, true)
    var item = root.viewItem()
    if (typeof jobId === "string" && jobId !== "" && item && typeof item.focusJob === "function") item.focusJob(jobId)
  }

  function clickView(v) {
    root._clickSwitch = true
    root.showView(v)
    Qt.callLater(function () { root._clickSwitch = false })
  }

  function cycleView(dir) {
    var i = root.views.indexOf(root.view)
    root.showView(root.views[(i + dir + root.views.length) % root.views.length])
  }

  function activateView() {
    if (root.contentState !== "ready") {
      keyCatcher.forceActiveFocus()
      return
    }
    var item = root.viewItem()
    if (item !== composeView || !composeView.editorFocused) keyCatcher.forceActiveFocus()
    if (item && typeof item.activate === "function") item.activate()
  }

  function newDraft() {
    root.showView("compose")
    composeView.newDraft()
  }

  function switchPanel(d) {
    return root.bar && typeof root.bar.switchPanelFrom === "function" ? root.bar.switchPanelFrom(root.barIdentity, d) : false
  }

  // A reset time picked in the Limits sheet: Compose opens with that time and agent.
  function runAtReset(fireAtSec, harness) {
    root.showView("compose")
    composeView.presetTrigger({ kind: "at", fireAt: fireAtSec })
    if (typeof composeView.setHarness === "function" && typeof harness === "string" && harness !== "") composeView.setHarness(harness)
    if (root.sheet !== "") root.closeSheet()
  }

  function openSheet(name, args) {
    if (!root.service) return
    var item = root.sheetFor(name)
    if (!item) return
    keyCatcher.forceActiveFocus()
    root.sheet = name
    if (name === "settings") item.open()
    else item.open(args && typeof args === "object" ? args : {})
  }

  function closeSheet() {
    var item = root.sheetItem()
    root.sheet = ""
    if (item) item.close()
    keyCatcher.forceActiveFocus()
  }

  function retrySetup() {
    if (root.service && typeof root.service.refresh === "function") root.service.refresh()
  }

  function editJob(jobId, mode) {
    var s = root.service
    if (!s || typeof s.getJob !== "function") return
    s.getJob(jobId, function (res) {
      if (!res || res.ok !== true || !res.job) {
        noticeRow.show(res && res.message ? res.message : "That job no longer exists.", "error", null, 0)
        return
      }
      var prompt = typeof res.prompt === "string" ? res.prompt : null
      var d = Model.draftFromJob(res.job, prompt, mode)
      if (d.target && res.job.target && typeof res.job.target.title === "string") {
        var t = {}
        for (var k in d.target) t[k] = d.target[k]
        t.title = res.job.target.title
        d.target = t
      }
      composeView.loadDraft(d, prompt, prompt === null ? "The prompt was deleted after the run. Write it again." : "")
      root.showView("compose")
    })
  }

  // ---------------------------------------------------------------- default view (R6 2.2)

  function eventAt(job) {
    var st = job.state || {}
    var ended = st.lastRun && typeof st.lastRun.endedAt === "number" ? st.lastRun.endedAt : 0
    if (typeof st.statusAt === "number" && isFinite(st.statusAt)) return Math.max(st.statusAt, ended)
    var at = Math.max(typeof job.updatedAt === "number" ? job.updatedAt : 0, ended)
    var house = ["prompt_deleted", "cli_changed", "late_result"]
    if (st.lastEvent && typeof st.lastEvent.at === "number"
        && house.indexOf(String(st.lastEvent.event)) < 0) at = Math.max(at, st.lastEvent.at)
    return at
  }

  // A running job, or something that went wrong since the panel was last closed,
  // opens the list it lives in with the cursor on it; armed jobs open the Queue;
  // otherwise Compose, ready to type.
  function pickDefaultView() {
    if (!root.serviceReady) return { view: "compose", id: "" }
    var s = root.service
    var jobs = Array.isArray(s.jobs) ? s.jobs : []
    var seen = typeof s.attentionSeenAt === "number" ? s.attentionSeenAt : 0
    var needs = Model.ENDED_ATTENTION_STATUSES.concat(Model.ATTENTION_STATUSES)
    var running = null, failure = null, failureAt = -1
    for (var i = 0; i < jobs.length; i++) {
      var j = jobs[i]
      if (!j || !j.state) continue
      if (j.state.status === "running") {
        if (running === null) running = j
        continue
      }
      if (needs.indexOf(j.state.status) >= 0) {
        var at = root.eventAt(j)
        if (at > seen && at > failureAt) { failure = j; failureAt = at }
      }
    }
    if (running !== null) return { view: "queue", id: running.id }
    if (failure !== null) return { view: failure.queue === true ? "queue" : "history", id: failure.id }
    if (s.nextJob && typeof s.nextJob.id === "string") return { view: "queue", id: s.nextJob.id }
    if (s.armedCount > 0) return { view: "queue", id: "" }
    return { view: "compose", id: "" }
  }

  function applyDefaultView() {
    if (root.sheet !== "") root.closeSheet()
    var pick = root.pickDefaultView()
    root.view = pick.view
    activateTimer.pendingJob = pick.id
    activateTimer.restart()
  }

  // ---------------------------------------------------------------- keys

  function handleGlobalKey(event) {
    var mods = event.modifiers
    if ((mods & Qt.ControlModifier) === 0 || (mods & (Qt.AltModifier | Qt.MetaModifier)) !== 0) return false
    var key = event.key
    if (key === Qt.Key_1) { root.showView("compose"); return true }
    if (key === Qt.Key_2) { root.showView("queue"); return true }
    if (key === Qt.Key_3) { root.showView("history"); return true }
    if (key === Qt.Key_PageUp) { root.cycleView(-1); return true }
    if (key === Qt.Key_PageDown) { root.cycleView(1); return true }
    if (key === Qt.Key_Comma) { root.openSheet("settings", null); return true }
    if (key === Qt.Key_L) { root.openSheet("limits", null); return true }
    if (key === Qt.Key_N) { root.newDraft(); return true }
    if (key === Qt.Key_Z && noticeRow.undoAvailable) { noticeRow.undo(); return true }
    return false
  }

  function routeKey(event) {
    // 1. A sheet is modal: it gets every key, Esc closes it, nothing leaks through.
    if (root.sheet !== "") {
      var s = root.sheetItem()
      if (s && s.handleKey(event)) { event.accepted = true; return }
      if (event.key === Qt.Key_Escape) root.closeSheet()
      event.accepted = true
      return
    }
    // The prompt editor owns the keyboard while focused and hands back what the
    // panel needs itself; anything it did not take is not a panel command.
    if (composeView.editorFocused) return
    // 2. Global Ctrl commands.
    if (root.handleGlobalKey(event)) { event.accepted = true; return }
    // 3. The active view, which runs its own Esc ladder first.
    if (root.contentState === "ready") {
      var v = root.viewItem()
      if (v && v.handleKey(event)) { event.accepted = true; return }
    } else if (root.contentState === "setup" && (event.key === Qt.Key_Return || event.key === Qt.Key_Enter)) {
      root.retrySetup()
      event.accepted = true
      return
    }
    // 4. Esc closes the panel.
    if (event.key === Qt.Key_Escape) {
      event.accepted = true
      root.close()
      return
    }
    // 5. Tab moves to the neighbouring bar panel.
    if (event.key === Qt.Key_Tab || event.key === Qt.Key_Backtab) {
      event.accepted = true
      root.switchPanel(event.key === Qt.Key_Backtab || (event.modifiers & Qt.ShiftModifier) !== 0 ? -1 : 1)
    }
  }

  readonly property string footerText: {
    if (root.sheet !== "") {
      var s = root.sheetItem()
      return s && typeof s.hints === "string" ? s.hints : "Esc back"
    }
    if (root.contentState === "loading") return "Ctrl+, settings  ·  Tab next panel  ·  Esc close"
    if (root.contentState === "setup") return "Enter try again  ·  Ctrl+, settings  ·  Esc close"
    var v = root.viewItem()
    return v && typeof v.hints === "string" ? v.hints : ""
  }

  // Conditions that make every arm fail, or a list that stopped updating. Said once,
  // above the view, rather than discovered one refused arm at a time.
  readonly property string systemLine: {
    if (root.contentState !== "ready") return ""
    var m = root.service.listMeta || {}
    if (m.killSwitch === true) return Edition.DISPLAY_NAME + " is switched off by its kill switch file. Nothing fires and nothing can be armed."
    if (m.enabledInShell === false) return Edition.DISPLAY_NAME + " is not enabled in the bar. Nothing fires and nothing can be armed."
    var age = typeof m.nowMs === "number" ? Number(root.service.nowMs) - m.nowMs : 0
    if (age > 330000 && root.service.loadingJobs !== true)
      return "The job list is " + Model.formatDuration(age) + " old. The helper is not answering."
    return ""
  }

  // ---------------------------------------------------------------- life

  onOpenedChanged: {
    if (root.opened) {
      if (root.service && !root._viewerCounted && typeof root.service.viewerOpened === "function") {
        root.service.viewerOpened()
        root._viewerCounted = true
      }
      root._viewTouched = false
      if (root._viewForced) {
        activateTimer.pendingJob = ""
        activateTimer.restart()
      } else {
        root.applyDefaultView()
      }
    } else {
      if (root.sheet !== "") root.closeSheet()
      if (root._viewerCounted && root.service && typeof root.service.viewerClosed === "function") root.service.viewerClosed()
      root._viewerCounted = false
    }
  }

  onServiceChanged: {
    if (root.opened && root.service && !root._viewerCounted && typeof root.service.viewerOpened === "function") {
      root.service.viewerOpened()
      root._viewerCounted = true
    }
  }

  // The first list answered while the panel was already open: choose the view now,
  // unless the user has moved or started writing.
  onServiceReadyChanged: {
    if (root.serviceReady && root.opened && !root._viewTouched && !composeView.dirty) root.applyDefaultView()
  }

  Component.onDestruction: {
    if (root._viewerCounted && root.service && typeof root.service.viewerClosed === "function") root.service.viewerClosed()
  }

  Connections {
    target: root.service
    ignoreUnknownSignals: true
    function onNotice(text, kind) { noticeRow.show(text, kind, null, 0) }
  }

  // Runs after KeyboardPanel has focused the key catcher on open, so a view that
  // focuses its own editor is not overridden a moment later.
  Timer {
    id: activateTimer
    property string pendingJob: ""
    interval: 60
    repeat: false
    onTriggered: {
      if (!root.opened) return
      root.activateView()
      var item = root.viewItem()
      if (activateTimer.pendingJob !== "" && item && typeof item.focusJob === "function") item.focusJob(activateTimer.pendingJob)
      activateTimer.pendingJob = ""
    }
  }

  // ---------------------------------------------------------------- card

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    // One size always; the card never resizes while typing (R6 0.1).
    contentWidth: panel.fittedContentWidth(Style.space(1100))
    contentHeight: panel.fittedContentHeight(Style.space(800), Style.space(800))

    Item {
      id: keyCatcher
      anchors.fill: parent
      focus: true

      Keys.priority: Keys.BeforeItem
      Keys.onPressed: function (event) { root.routeKey(event) }

      // ---- header
      Item {
        id: header
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        height: Style.space(40)

        Row {
          id: brand
          anchors.left: parent.left
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(8)

          Text {
            anchors.verticalCenter: parent.verticalCenter
            textFormat: Text.PlainText
            text: "\uDB80\uDCF0"   // md-calendar_clock U+F00F0, the bar glyph
            color: root.theme.strong
            font.family: root.theme.fontFamily
            font.pixelSize: root.theme.type.brandGlyph
          }

          Text {
            anchors.verticalCenter: parent.verticalCenter
            textFormat: Text.PlainText
            text: Edition.DISPLAY_NAME
            color: root.theme.strong
            font.family: root.theme.fontFamily
            font.pixelSize: root.theme.type.title
            font.bold: true
          }
        }

        Row {
          id: tabs
          anchors.left: brand.right
          anchors.leftMargin: Style.space(28)
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(2)

          Repeater {
            id: tabRepeater
            model: [
              { id: "compose", label: "Compose" },
              { id: "queue", label: "Queue" },
              { id: "history", label: "History" }
            ]

            delegate: Item {
              id: tab
              required property var modelData
              readonly property bool current: root.view === tab.modelData.id
              readonly property int count: tab.modelData.id === "queue" && root.service ? Number(root.service.armedCount) || 0 : 0
              width: tabLabel.implicitWidth + Style.space(12) * 2
              height: Style.space(30)

              Row {
                id: tabLabel
                anchors.centerIn: parent
                spacing: Style.space(6)

                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  textFormat: Text.PlainText
                  text: tab.modelData.label
                  color: tab.current ? root.theme.fg : root.theme.readable
                  font.family: root.theme.fontFamily
                  font.pixelSize: root.theme.type.body
                  font.bold: tab.current
                }

                Text {
                  anchors.verticalCenter: parent.verticalCenter
                  visible: tab.count > 0
                  textFormat: Text.PlainText
                  text: String(tab.count)
                  color: tab.current ? root.theme.accentInk : root.theme.soft
                  font.family: root.theme.fontFamily
                  font.pixelSize: root.theme.type.data
                  font.features: root.theme.type.digits
                  font.bold: true
                }
              }

              MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: root.clickView(tab.modelData.id)
              }
            }
          }
        }

        // The active tab's underline. It jumps on a keyboard switch and slides
        // on a click, where the eye is following the pointer anyway.
        Rectangle {
          id: underline
          readonly property Item currentTab: tabRepeater.count === root.views.length
            ? tabRepeater.itemAt(Math.max(0, root.views.indexOf(root.view))) : null
          x: tabs.x + (underline.currentTab ? underline.currentTab.x : 0)
          y: tabs.y + tabs.height - height
          width: underline.currentTab ? underline.currentTab.width : 0
          height: Math.max(2, Style.space(2))
          color: root.theme.accent

          Behavior on x {
            enabled: root._clickSwitch
            NumberAnimation { duration: root.theme.moveMs(140); easing.type: Easing.OutCubic }
          }
          Behavior on width {
            enabled: root._clickSwitch
            NumberAnimation { duration: root.theme.moveMs(140); easing.type: Easing.OutCubic }
          }
        }

        LimitStrip {
          id: limitStrip
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          width: Math.max(0, Math.min(implicitWidth, parent.width - tabs.x - tabs.width - Style.space(20)))
          clip: true
          theme: root.theme
          providers: root.service && Array.isArray(root.service.providers) ? root.service.providers : []
          shownIds: Model.shownSources(limitStrip.providers,
            root.service && root.service.settings ? root.service.settings.limitsShown : "auto")
          errorText: root.service && typeof root.service.usageError === "string" ? root.service.usageError : ""
          nowMs: root.service ? Number(root.service.nowMs) || 0 : 0
          onSheetRequested: root.openSheet("limits", null)
        }
      }

      Rectangle {
        id: hairline
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: header.bottom
        height: Math.max(1, Style.space(1))
        color: root.theme.faint
      }

      Item {
        id: systemStrip
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: hairline.bottom
        height: root.systemLine !== "" ? Style.space(26) : 0
        visible: root.systemLine !== ""

        Row {
          anchors.left: parent.left
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(8)

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
            width: parent.width - Style.space(30)
            textFormat: Text.PlainText
            text: root.systemLine
            color: root.theme.warnInk
            elide: Text.ElideRight
            maximumLineCount: 1
            font.family: root.theme.fontFamily
            font.pixelSize: root.theme.type.meta
          }
        }
      }

      // ---- view area
      Item {
        id: viewArea
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: systemStrip.bottom
        anchors.topMargin: Style.space(12)
        anchors.bottom: noticeRow.top
        anchors.bottomMargin: Style.space(4)

        ComposeView {
          id: composeView
          anchors.fill: parent
          visible: root.contentState === "ready" && root.view === "compose"
          active: composeView.visible && root.opened && root.sheet === ""
          theme: root.theme
          service: root.service
          home: root.home
          focusReturn: keyCatcher
          onNoticeRequested: function (text, kind, undo) { noticeRow.show(text, kind, undo, 0, "compose") }
          onViewRequested: function (view, jobId) { root.showViewFocus(view, jobId) }
          onArmed: function (jobId) { queueView.flashJob(jobId) }
          onSheetRequested: function (sheet, args) { root.openSheet(sheet, args) }
          onGlobalKey: function (event) { root.handleGlobalKey(event) }
        }

        QueueView {
          id: queueView
          anchors.fill: parent
          visible: root.contentState === "ready" && root.view === "queue"
          active: queueView.visible && root.opened && root.sheet === ""
          theme: root.theme
          service: root.service
          home: root.home
          onNoticeRequested: function (text, kind, undo) { noticeRow.show(text, kind, undo, 0) }
          onEditRequested: function (jobId, mode) { root.editJob(jobId, mode) }
          onSheetRequested: function (sheet, args) { root.openSheet(sheet, args) }
          onViewRequested: function (view, jobId) { root.showViewFocus(view, jobId) }
          onComposeRequested: function (trigger) {
            root.showView("compose")
            if (trigger) composeView.presetTrigger(trigger)
          }
        }

        HistoryView {
          id: historyView
          anchors.fill: parent
          visible: root.contentState === "ready" && root.view === "history"
          active: historyView.visible && root.opened && root.sheet === ""
          theme: root.theme
          service: root.service
          home: root.home
          onNoticeRequested: function (text, kind, undo) { noticeRow.show(text, kind, undo, 0) }
          onEditRequested: function (jobId, mode) { root.editJob(jobId, mode) }
        }

        // Never "0 jobs" before the first answer (ASM rule).
        Column {
          anchors.left: parent.left
          anchors.top: parent.top
          anchors.topMargin: Style.space(24)
          width: parent.width
          spacing: Style.space(8)
          visible: root.contentState === "loading"

          Text {
            textFormat: Text.PlainText
            text: "Reading jobs…"
            color: root.theme.strong
            font.family: root.theme.fontFamily
            font.pixelSize: root.theme.type.title
            font.bold: true
          }

          Text {
            width: Math.min(parent.width, root.theme.type.measure)
            textFormat: Text.PlainText
            text: Edition.DISPLAY_NAME + " is asking its helper for the job list. Armed jobs fire on their own timers meanwhile."
            color: root.theme.readable
            wrapMode: Text.WordWrap
            font.family: root.theme.fontFamily
            font.pixelSize: root.theme.type.body
          }
        }

        ProblemCard {
          anchors.left: viewArea.left
          anchors.top: viewArea.top
          anchors.topMargin: Style.space(24)
          width: Math.min(Style.space(640), viewArea.width)
          visible: root.contentState === "setup"
          theme: root.theme
          message: root.service && typeof root.service.setupProblem === "string" ? root.service.setupProblem : ""
          detail: "Nothing was armed. Jobs that were already armed keep their own timers."
          onRetryRequested: root.retrySetup()
        }

        // ---- sheets: in-card overlays over the view area only
        SessionSheet {
          id: sessionSheet
          anchors.fill: parent
          visible: root.sheet === "session"
          active: sessionSheet.visible && root.opened
          theme: root.theme
          service: root.service
          home: root.home
          onPicked: function (selection) {
            composeView.applySession(selection)
            root.closeSheet()
          }
          onClosed: if (root.sheet === "session") root.sheet = ""
          onNoticeRequested: function (text, kind, undo) { noticeRow.show(text, kind, undo, 0) }
        }

        SettingsSheet {
          id: settingsSheet
          anchors.fill: parent
          visible: root.sheet === "settings"
          active: settingsSheet.visible && root.opened
          theme: root.theme
          service: root.service
          onClosed: if (root.sheet === "settings") root.sheet = ""
          onNoticeRequested: function (text, kind, undo) { noticeRow.show(text, kind, undo, 0) }
        }

        ShiftSheet {
          id: shiftSheet
          anchors.fill: parent
          visible: root.sheet === "shift"
          active: shiftSheet.visible && root.opened
          theme: root.theme
          service: root.service
          onClosed: if (root.sheet === "shift") root.sheet = ""
          onNoticeRequested: function (text, kind, undo) { noticeRow.show(text, kind, undo, 0) }
        }

        // Compose asks for it with sheetRequested("model", args).
        ModelSheet {
          id: modelSheet
          anchors.fill: parent
          visible: root.sheet === "model"
          active: modelSheet.visible && root.opened
          theme: root.theme
          service: root.service
          onPicked: function (selection) {
            if (typeof composeView.applyModel === "function") composeView.applyModel(selection)
            root.sheet = ""
            keyCatcher.forceActiveFocus()
          }
          onClosed: if (root.sheet === "model") root.sheet = ""
        }

        Connections {
          target: modelSheet
          ignoreUnknownSignals: true
          function onNoticeRequested(text, kind, undo) { noticeRow.show(text, kind, undo, 0) }
        }

        LimitsSheet {
          id: limitsSheet
          anchors.fill: parent
          visible: root.sheet === "limits"
          active: limitsSheet.visible && root.opened
          theme: root.theme
          service: root.service
          onClosed: if (root.sheet === "limits") root.sheet = ""
          onNoticeRequested: function (text, kind, undo) { noticeRow.show(text, kind, undo, 0) }
          onRunAtRequested: function (fireAtSec, harness) { root.runAtReset(fireAtSec, harness) }
        }

        // Compose asks for it with sheetRequested("signin", { harness }) when a chip's
        // agent cannot run as it stands.
        SignInSheet {
          id: signInSheet
          anchors.fill: parent
          visible: root.sheet === "signin"
          active: signInSheet.visible && root.opened
          theme: root.theme
          service: root.service
          onClosed: if (root.sheet === "signin") root.sheet = ""
          onNoticeRequested: function (text, kind, undo) { noticeRow.show(text, kind, undo, 0) }
        }
      }

      NoticeRow {
        id: noticeRow
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: footer.top
        height: Style.space(28)
        theme: root.theme
      }

      FooterHints {
        id: footer
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        height: Style.space(34)
        theme: root.theme
        hints: root.footerText
      }
    }
  }
}
