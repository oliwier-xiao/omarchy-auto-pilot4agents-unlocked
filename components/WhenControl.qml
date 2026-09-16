pragma ComponentBehavior: Bound

import QtQuick
import qs.Commons
import qs.Ui
import "../lib/Model.js" as Model
import "../lib/Compose.js" as Compose

// When the job fires: one big readout that can be nudged, a row of chips, and a
// resolved line that always says the absolute local time and how far away it is
// (R6 section 3).
//
// The control edits a Draft trigger (CONTRACT 4.4) and nothing else:
//   now                       fires when armed
//   in  + delaySec            "+30m" chips; the delay starts at arm time (D4)
//   at  + fireAt              a clock time: typed, nudged, Tonight, Tomorrow, or moved
//                             to another day in the day strip or by a typed date
//   <reset kind> + marginSec  bound to the agent's reset; nudging moves the buffer
// The helper resolves the real epoch at arm and refuses anything it would not run;
// the resolved line here is the same arithmetic on the same data, for the eye.
Item {
  id: root

  required property var theme

  property var trigger: ({ kind: "now" })
  property double nowMs: 0
  // `service.providers` (Usage v2): the reset chip reads the source bound to this agent.
  property var providers: []
  property string harness: "claude"
  // Pi's provider, the model and OpenCode's billing class decide which reset applies:
  // Pi follows Codex only when signed in with ChatGPT, OpenCode follows the Zen free
  // day for a free Zen model and the Go window for a Go model, Cursor follows none.
  property string provider: ""
  property string model: ""
  property string billing: ""
  property var settings: ({})
  property bool hasCursor: false
  // `ap4a edition` caps (horizonSec), from the service.
  property var caps: ({})
  // The helper's word on this time (a preview's fireAtError or hint), one sentence.
  property string helperNote: ""
  // The arm verb for the footer: "Ctrl+Enter arm" or "Ctrl+Enter run now".
  property string armHint: "Ctrl+Enter arm"

  signal triggerEdited(var trigger)
  // Clicked or scrolled: the section wants the cursor.
  signal focusRequested()

  readonly property var resetKinds: ["claude_5h_reset", "codex_window_reset", "gemini_daily_reset", "zen_free_reset", "go_window_reset"]
  // The agent whose mark and ink a reset wears: the source it reads.
  readonly property var resetOwners: Compose.RESET_OWNERS

  readonly property string kind: root.trigger && typeof root.trigger.kind === "string" ? root.trigger.kind : "now"
  readonly property bool bound: root.resetKinds.indexOf(root.kind) >= 0
  readonly property int settingsMarginSec: root.settings && typeof root.settings.resetMarginSec === "number"
    ? root.settings.resetMarginSec : 120
  readonly property int marginSec: root.bound && root.trigger && typeof root.trigger.marginSec === "number"
    ? root.trigger.marginSec : root.settingsMarginSec
  readonly property double horizonSec: root.caps && typeof root.caps.horizonSec === "number" ? root.caps.horizonSec : 691200

  readonly property var reset: Compose.resetChipFor(root.providers, root.harness, root.provider, root.model, root.billing,
                                                     root.nowMs, root.marginSec)
  // No reset applies to this agent and model: the chip and its stop are gone.
  readonly property bool resetOffered: root.reset.kind !== ""
  readonly property var evening: Model.presetTime(root.settingString("eveningTime", "23:00"), root.nowMs)
  // Always tomorrow, even at 05:00 with a 07:00 morning.
  readonly property var morning: Model.morningPreset(root.settingString("morningTime", "07:00"), root.nowMs)

  readonly property double shownMs: root.msFor(root.trigger, root.reset, root.nowMs)
  readonly property color hue: root.bound ? root.theme.harnessInk(root.resetOwners[root.kind]) : root.theme.accent

  // Keyboard stops, top to bottom and left to right: the two readout segments, the day
  // strip, then every chip.
  readonly property var stops: root.resetOffered
    ? ["hour", "minute", "day", "now", "plus30", "plus60", "plus120", "plus300", "reset", "evening", "morning"]
    : ["hour", "minute", "day", "now", "plus30", "plus60", "plus120", "plus300", "evening", "morning"]

  // Today and every day the helper can still arm on (8 days ahead by default). Rebuilt
  // when the date changes, not on every tick of `nowMs`.
  readonly property int dayCount: Math.max(1, Math.floor(root.horizonSec / 86400) + 1)
  readonly property string todayKey: Model.dayKey(root.nowMs)
  property var days: []
  readonly property string selectedDayKey: isFinite(root.shownMs) ? Model.dayKey(root.shownMs) : ""
  function refreshDays() { root.days = Model.dayStrip(root.nowMs, root.dayCount) }
  onTodayKeyChanged: root.refreshDays()
  onDayCountChanged: root.refreshDays()
  Component.onCompleted: root.refreshDays()
  readonly property var plusChips: [
    { stop: "plus30", text: "+30m", minutes: 30 },
    { stop: "plus60", text: "+1h", minutes: 60 },
    { stop: "plus120", text: "+2h", minutes: 120 },
    { stop: "plus300", text: "+5h", minutes: 300 }
  ]
  readonly property string cursorStop: root.stops[Math.max(0, Math.min(root.stops.length - 1, root._cursor))]
  readonly property bool typing: root._typed !== ""

  property int _cursor: 1
  property string _typed: ""
  property string _note: ""
  property string _noteKind: "soft"
  property real _wheel: 0

  readonly property string hints: {
    if (root.typing) return "0-9 digits  ·  . and a space for a date, 17.09 1830  ·  Enter set  ·  Backspace edit  ·  Esc cancel"
    var arm = root.armHint + "  ·  Esc close"
    if (root.cursorStop === "day")
      return "j/k ±1 day  ·  click a day  ·  0-9 type a date, 17.09 1830  ·  Tab next  ·  " + arm
    var keys = "n now  ·  " + (root.resetOffered ? "r reset  ·  " : "") + "Ctrl+J/K ±1 day  ·  0-9 type a time or a date  ·  Tab next  ·  "
    if (root.bound) return "j/k buffer ±1m  ·  Shift+J/K ±1h  ·  " + keys + arm
    return "j/k ±5m  ·  Shift+J/K ±1h  ·  " + keys + arm
  }

  function settingString(key, fallback) {
    var v = root.settings ? root.settings[key] : undefined
    return typeof v === "string" && v !== "" ? v : fallback
  }

  function msFor(t, chip, now) {
    var k = t && typeof t.kind === "string" ? t.kind : "now"
    if (k === "now") return now
    if (k === "in") return typeof t.delaySec === "number" ? now + t.delaySec * 1000 : NaN
    if (k === "at") return typeof t.fireAt === "number" ? t.fireAt * 1000 : NaN
    if (chip && chip.available === true && chip.kind === k && typeof chip.fireAtMs === "number") return chip.fireAtMs
    return NaN
  }

  function resolvedMs() { return root.shownMs }

  function reasonText(reason) {
    if (reason === "not_open") return "No limit window is open, so the quota is already fresh."
    if (reason === "stale") return "Usage data is old, so the time may shift."
    if (reason === "too_far") return "That reset is more than 8 days away."
    if (reason === "billing_unknown") return "This model's billing is not known yet, so its free reset cannot be used."
    return "No usage data for that reset. Pick a time instead."
  }

  // ---------------------------------------------------------------- display

  // Plain digits are a time; anything with a separator is shown as typed.
  readonly property bool typingDate: root.typing && /[^0-9]/.test(root._typed)

  readonly property string readoutWord: {
    if (root.typing) {
      var t = root._typed
      if (root.typingDate) return t
      return t.length <= 2 ? t + ":__" : t.slice(0, t.length - 2) + ":" + t.slice(t.length - 2)
    }
    if (root.kind === "now") return "Now"
    return "--:--"
  }

  readonly property string clockText: isFinite(root.shownMs) ? Model.formatClock(root.shownMs) : "--:--"

  readonly property string resolvedText: {
    if (root.typing) return root.typingDate ? "Enter sets the date" : "Enter sets the time"
    if (root.kind === "now") return "runs when you press Run now"
    if (!isFinite(root.shownMs)) return root.bound ? "no reset time known" : "no time set"
    return Model.resolvedLine(root.shownMs, root.nowMs)
  }

  readonly property string captionText: {
    if (root._note !== "") return root._note
    if (root.helperNote !== "") return root.helperNote
    if (root.bound) {
      if (root.reset.available !== true || root.reset.kind !== root.kind) return root.reasonText(root.reset.reason)
      var follows = Model.followsLine(root.kind, root.marginSec)
      return root.reset.stale ? follows + ", usage data is old" : follows
    }
    if (root.kind === "in" && typeof root.trigger.delaySec === "number")
      return Model.formatDuration(root.trigger.delaySec * 1000) + " after you arm it"
    return ""
  }

  readonly property color captionColor: (root._note !== "" && root._noteKind === "warn") || (root._note === "" && root.helperNote !== "")
    || (root._note === "" && root.helperNote === "" && root.bound && root.reset.available !== true)
    ? root.theme.warnInk : root.theme.soft

  // ---------------------------------------------------------------- edits

  function earliestMs() { return Math.ceil((root.nowMs + 60000) / 60000) * 60000 }

  function _emit(next) {
    var t = { kind: next.kind, fireAt: null, delaySec: null }
    if (next.kind === "at") t.fireAt = next.fireAt
    if (next.kind === "in") t.delaySec = next.delaySec
    var old = root.trigger && typeof root.trigger === "object" ? root.trigger : {}
    var margin = typeof next.marginSec === "number" ? next.marginSec : old.marginSec
    if (typeof margin === "number") t.marginSec = margin
    var weekly = old.weeklyPolicy
    if (weekly === "defer" || weekly === "skip") t.weeklyPolicy = weekly
    root.triggerEdited(t)
  }

  function setAt(ms, note) {
    var target = Math.round(Number(ms))
    if (!isFinite(target)) return
    var early = root.earliestMs()
    root._note = note || ""
    root._noteKind = "soft"
    if (target < early) {
      target = early
      root._note = "Earliest is " + Model.formatClock(early) + "."
    }
    if (target > root.nowMs + root.horizonSec * 1000) {
      root._note = "That time is more than 8 days away."
      root._noteKind = "warn"
      return
    }
    root._emit({ kind: "at", fireAt: Math.floor(target / 1000) })
  }

  function setDelay(sec) {
    var d = Math.round(Number(sec))
    if (!isFinite(d)) return
    root._note = ""
    root._noteKind = "soft"
    if (d < 60) {
      d = 60
      root._note = "Earliest is in 1 minute."
    }
    if (d > root.horizonSec) {
      root._note = "That time is more than 8 days away."
      root._noteKind = "warn"
      return
    }
    root._emit({ kind: "in", delaySec: d })
  }

  function setNow() {
    root._typed = ""
    root._note = ""
    root._emit({ kind: "now" })
  }

  // "+1h" adds to the time shown: a clock time moves, a delay grows, and a bound
  // reset becomes the clock time it resolved to plus the step.
  function addMinutes(minutes) {
    root._typed = ""
    var add = Math.round(Number(minutes)) * 60
    if (root.kind === "at" && typeof root.trigger.fireAt === "number") {
      root.setAt((root.trigger.fireAt + add) * 1000, "")
      return
    }
    if (root.bound && isFinite(root.shownMs)) {
      root.setAt(root.shownMs + add * 1000, "Unbound from reset.")
      return
    }
    var base = root.kind === "in" && typeof root.trigger.delaySec === "number" ? root.trigger.delaySec : 0
    root.setDelay(base + add)
  }

  function bindReset() {
    root._typed = ""
    root._note = ""
    var chip = root.reset
    if (!chip || chip.kind === "") {
      root._note = "No reset applies to this agent. Pick a time instead."
      root._noteKind = "soft"
      return
    }
    if (chip.available !== true) {
      root._note = root.reasonText(chip.reason)
      root._noteKind = "warn"
      return
    }
    root._emit({ kind: chip.kind, marginSec: root.bound ? root.marginSec : root.settingsMarginSec })
  }

  // While bound, a small step moves the buffer after the reset by one minute,
  // 1 to 9 minutes (CONTRACT C4). Ten minutes or more would start the next
  // 5-hour window later than it needs to, so the control stops at nine and says so.
  function nudgeMargin(dir) {
    var minutes = Math.round(root.marginSec / 60) + dir
    root._noteKind = "soft"
    root._note = ""
    if (minutes > 9) {
      minutes = 9
      root._note = root.kind === "claude_5h_reset"
        ? "The next 5-hour window will start later."
        : "The buffer after a reset is at most 9 minutes."
      root._noteKind = "warn"
    } else if (minutes < 1) {
      minutes = 1
      root._note = "The buffer after a reset is at least 1 minute."
    }
    root._emit({ kind: root.kind, marginSec: minutes * 60 })
  }

  function nudge(stepMin) {
    if (root.typing) root.commitTyping()
    root._note = ""
    if (root.bound) {
      if (Math.abs(stepMin) < 60) {
        root.nudgeMargin(stepMin > 0 ? 1 : -1)
        return
      }
      if (isFinite(root.shownMs)) {
        root.setAt(root.shownMs + stepMin * 60000, "Unbound from reset.")
        return
      }
    }
    if (root.kind === "in") {
      var delay = typeof root.trigger.delaySec === "number" ? root.trigger.delaySec : 0
      root.setDelay(delay + stepMin * 60)
      return
    }
    var from = root.kind === "at" && typeof root.trigger.fireAt === "number" ? root.trigger.fireAt * 1000 : root.nowMs
    // A zero "now" keeps Model's clamp out of the way; setAt clamps and says so.
    root.setAt(Model.nudged(from, stepMin, true, 0), "")
  }

  function nudgeDays(dir) {
    if (root.typing) root.commitTyping()
    root._note = ""
    if (root.kind === "in") {
      var delay = typeof root.trigger.delaySec === "number" ? root.trigger.delaySec : 0
      root.setDelay(delay + dir * 86400)
      return
    }
    var from = root.kind === "at" && typeof root.trigger.fireAt === "number"
      ? root.trigger.fireAt * 1000
      : (isFinite(root.shownMs) ? root.shownMs : root.nowMs)
    root.setAt(Model.nudgedDays(from, dir, 0), root.bound ? "Unbound from reset." : "")
  }

  // The same clock time on another day. With nothing chosen yet (Now), today's clock is
  // kept, and setAt moves a time already gone to the earliest one and says so.
  function setDay(dayMs) {
    root._typed = ""
    var base = root.kind === "now" ? NaN : root.shownMs
    root.setAt(Model.onDay(dayMs, base, root.nowMs), root.bound ? "Unbound from reset." : "")
  }

  function commitTyping() {
    if (root._typed === "") return
    var typed = root._typed
    root._typed = ""
    var parsed = Model.parseTyped(typed, root.nowMs, root.kind === "now" ? NaN : root.shownMs)
    if (!parsed) {
      root._note = "Type a time like 1405, or a date like 17.09 1830."
      root._noteKind = "warn"
      return
    }
    if (parsed.dated) {
      if (parsed.rolled || Model.localMidnight(parsed.ms) < Model.localMidnight(root.nowMs)) {
        root._note = "That date has passed."
        root._noteKind = "warn"
        return
      }
      root.setAt(parsed.ms, root.bound ? "Unbound from reset." : "")
      return
    }
    root.setAt(parsed.ms, parsed.rolled ? Model.formatClock(parsed.ms) + " has passed today. Set for tomorrow." : "")
  }

  function cancelTyping() { root._typed = "" }

  function activate(stop) {
    if (stop === "hour" || stop === "minute" || stop === "day") {
      root.commitTyping()
      return
    }
    if (stop === "now") { root.setNow(); return }
    for (var i = 0; i < root.plusChips.length; i++) {
      if (root.plusChips[i].stop === stop) { root.addMinutes(root.plusChips[i].minutes); return }
    }
    if (stop === "reset") { root.bindReset(); return }
    root._typed = ""
    if (stop === "evening" && root.evening) root.setAt(root.evening.ms, "")
    else if (stop === "morning" && root.morning) root.setAt(root.morning.ms, "")
  }

  function isSelected(stop) {
    if (stop === "now") return root.kind === "now"
    if (stop === "reset") return root.bound
    var at = root.kind === "at" && typeof root.trigger.fireAt === "number" ? root.trigger.fireAt * 1000 : NaN
    if (stop === "evening") return !!root.evening && at === root.evening.ms
    if (stop === "morning") return !!root.morning && at === root.morning.ms
    return false
  }

  function focusStop(stop) {
    var i = root.stops.indexOf(stop)
    if (i >= 0) root._cursor = i
  }

  // ---------------------------------------------------------------- keys

  function handleKey(event) {
    var key = event.key
    var mods = event.modifiers
    var ctrl = (mods & Qt.ControlModifier) !== 0
    var shift = (mods & Qt.ShiftModifier) !== 0
    if ((mods & (Qt.AltModifier | Qt.MetaModifier)) !== 0) return false
    var text = String(event.text || "")

    var digit = text.length === 1 && text >= "0" && text <= "9"
    // A separator continues what is being typed: "." or "/" for a date, "-" for a
    // year-first date, ":" inside a time, a space before the time of a date.
    var separator = root.typing && (text === "." || text === "/" || text === "-" || text === ":" || text === " ")
    if (!ctrl && (digit || separator)) {
      root._note = ""
      if (digit && !root.typingDate && root._typed.length >= 4) root._typed = text
      else if (root._typed.length < 16) root._typed = root._typed + text
      return true
    }
    if (root.typing) {
      if (key === Qt.Key_Backspace) { root._typed = root._typed.slice(0, -1); return true }
      if (!ctrl && (key === Qt.Key_Return || key === Qt.Key_Enter)) { root.commitTyping(); return true }
      if (key === Qt.Key_Escape) { root.cancelTyping(); return true }
    }

    var up = key === Qt.Key_Up || key === Qt.Key_K
    var down = key === Qt.Key_Down || key === Qt.Key_J
    if (up || down) {
      var dir = up ? 1 : -1
      if (ctrl || root.cursorStop === "day") root.nudgeDays(dir)
      else if (shift || root.cursorStop === "hour") root.nudge(60 * dir)
      else root.nudge(5 * dir)
      return true
    }
    if (ctrl) return false

    if (key === Qt.Key_Left || key === Qt.Key_H) {
      root.commitTyping()
      root._cursor = Math.max(0, root._cursor - 1)
      return true
    }
    if (key === Qt.Key_Right || key === Qt.Key_L) {
      root.commitTyping()
      root._cursor = Math.min(root.stops.length - 1, root._cursor + 1)
      return true
    }
    if (key === Qt.Key_Home) { root._cursor = 0; return true }
    if (key === Qt.Key_End) { root._cursor = root.stops.length - 1; return true }
    if (key === Qt.Key_Return || key === Qt.Key_Enter || key === Qt.Key_Space) {
      root.activate(root.cursorStop)
      return true
    }
    if (key === Qt.Key_N) { root.focusStop("now"); root.setNow(); return true }
    if (key === Qt.Key_R) { if (root.resetOffered) root.focusStop("reset"); root.bindReset(); return true }
    return false
  }

  function wheelNudge(wheel) {
    var r = Util.wheelSteps(root._wheel, wheel.angleDelta.y)
    root._wheel = r.remainder
    var ctrl = (wheel.modifiers & Qt.ControlModifier) !== 0
    var shift = (wheel.modifiers & Qt.ShiftModifier) !== 0
    var n = Math.min(12, Math.abs(r.steps))
    for (var i = 0; i < n; i++) {
      var dir = r.steps > 0 ? 1 : -1
      if (ctrl || root.cursorStop === "day") root.nudgeDays(dir)
      else if (shift || root.cursorStop === "hour") root.nudge(60 * dir)
      else root.nudge(5 * dir)
    }
  }

  onHasCursorChanged: if (!root.hasCursor) root.commitTyping()
  // A trigger replaced from outside (a new draft, an edited job) takes the
  // previous sentence with it.
  onTriggerChanged: if (root._note !== "" && root._noteKind === "soft" && root.kind === "now") root._note = ""

  implicitWidth: Style.space(420)
  implicitHeight: column.implicitHeight

  Column {
    id: column
    width: parent.width
    spacing: Style.spacing.lg

    BorderSurface {
      id: readout
      width: parent.width
      height: Style.space(64)
      radius: Style.cornerRadius
      color: Util.alpha(root.hue, root.hasCursor ? 0.13 : 0.08)
      borderSpec: Border.controlSpec(root.hasCursor ? "hover-cursor" : "normal", root.theme.fg, root.theme.accent)

      // Scrolling over the readout nudges it: one notch, one step.
      MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        onClicked: root.focusRequested()
        onWheel: function (wheel) {
          root.focusRequested()
          root.wheelNudge(wheel)
          wheel.accepted = true
        }
      }

      Row {
        id: digits
        anchors.left: parent.left
        anchors.leftMargin: Style.space(14)
        anchors.verticalCenter: parent.verticalCenter
        visible: !root.typing && root.kind !== "now"
        spacing: 0

        Repeater {
          model: [
            { stop: "hour", text: root.clockText.slice(0, 2) },
            { stop: "", text: ":" },
            { stop: "minute", text: root.clockText.slice(3, 5) }
          ]

          delegate: Item {
            id: segment
            required property var modelData
            readonly property bool cursorHere: root.hasCursor && segment.modelData.stop !== ""
              && root.cursorStop === segment.modelData.stop
            width: segText.implicitWidth
            height: segText.implicitHeight + Style.space(4)

            Text {
              id: segText
              textFormat: Text.PlainText
              text: segment.modelData.text
              color: root.theme.fg
              font.family: root.theme.fontFamily
              font.pixelSize: root.theme.type.readout
              font.features: root.theme.type.digits
              font.bold: true
            }

            Rectangle {
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.bottom: parent.bottom
              height: Math.max(1, Style.space(2))
              color: root.hue
              visible: segment.cursorHere
            }

            MouseArea {
              anchors.fill: parent
              enabled: segment.modelData.stop !== ""
              cursorShape: Qt.PointingHandCursor
              onClicked: {
                root.focusStop(segment.modelData.stop)
                root.focusRequested()
              }
            }
          }
        }
      }

      Text {
        anchors.left: parent.left
        anchors.leftMargin: Style.space(14)
        anchors.verticalCenter: parent.verticalCenter
        visible: !digits.visible
        textFormat: Text.PlainText
        text: root.readoutWord
        color: root.typing ? root.theme.accentInk : root.theme.fg
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.readout
        font.bold: true
      }

      Column {
        anchors.right: parent.right
        anchors.rightMargin: Style.space(14)
        anchors.verticalCenter: parent.verticalCenter
        width: parent.width - Style.space(120)
        spacing: Style.space(4)

        Text {
          width: parent.width
          horizontalAlignment: Text.AlignRight
          textFormat: Text.PlainText
          text: root.resolvedText
          color: root.theme.readable
          elide: Text.ElideLeft
          maximumLineCount: 1
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.data
          font.features: root.theme.type.digits
        }

        Text {
          width: parent.width
          horizontalAlignment: Text.AlignRight
          visible: root.captionText !== ""
          textFormat: Text.PlainText
          text: root.captionText
          color: root.captionColor
          elide: Text.ElideRight
          maximumLineCount: 1
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta

          Behavior on color {
            ColorAnimation { duration: root.theme.fadeMs(160) }
          }
        }
      }
    }

    // Every day the job can be set on, so the date is something you see and click rather
    // than something you have to know to scroll. A click keeps the clock time shown.
    Row {
      id: dayRow
      objectName: "dayStrip"
      width: parent.width
      spacing: Style.space(4)
      readonly property int count: Math.max(1, root.days.length)
      readonly property real cellWidth: Math.floor((width - spacing * (count - 1)) / count)
      readonly property color ink: root.bound ? root.hue : root.theme.accentInk

      Repeater {
        model: root.days

        delegate: Item {
          id: dayCell
          required property var modelData
          readonly property bool selected: dayCell.modelData.key === root.selectedDayKey && root.kind !== "now"
          readonly property bool cursorHere: root.hasCursor && root.cursorStop === "day"
            && (dayCell.modelData.key === root.selectedDayKey || (root.selectedDayKey === "" && dayCell.modelData.offset === 0))
          readonly property bool today: dayCell.modelData.offset === 0
          width: dayRow.cellWidth
          height: Style.space(40)

          BorderSurface {
            anchors.fill: parent
            radius: Style.cornerRadius
            color: dayCell.selected ? Util.alpha(root.hue, 0.24)
              : Util.alpha(root.theme.fg, dayHover.hovered ? 0.10 : 0.04)
            borderSpec: Border.controlSpec(dayCell.cursorHere ? "hover-cursor" : "normal", root.theme.fg, root.theme.accent)
          }

          Column {
            anchors.centerIn: parent
            spacing: 0

            Text {
              anchors.horizontalCenter: parent.horizontalCenter
              textFormat: Text.PlainText
              // The first of a month says which month it is; today says so.
              text: dayCell.today ? "Today" : (dayCell.modelData.day === 1 ? dayCell.modelData.month : dayCell.modelData.weekday)
              color: dayCell.selected ? dayRow.ink : root.theme.soft
              font.family: root.theme.fontFamily
              font.pixelSize: root.theme.type.meta
              font.bold: dayCell.today
            }

            Text {
              anchors.horizontalCenter: parent.horizontalCenter
              textFormat: Text.PlainText
              text: String(dayCell.modelData.day)
              color: dayCell.selected ? root.theme.fg : root.theme.readable
              font.family: root.theme.fontFamily
              font.pixelSize: root.theme.type.data
              font.features: root.theme.type.digits
              font.bold: true
            }
          }

          HoverHandler { id: dayHover }

          MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: {
              root.focusRequested()
              root.focusStop("day")
              root.setDay(dayCell.modelData.ms)
            }
            onWheel: function (wheel) {
              root.focusRequested()
              root.focusStop("day")
              root.wheelNudge(wheel)
              wheel.accepted = true
            }
          }
        }
      }
    }

    Flow {
      width: parent.width
      spacing: Style.spacing.sm

      Chip {
        id: nowChip
        theme: root.theme
        text: "Now"
        selected: root.isSelected("now")
        hasCursor: root.hasCursor && root.cursorStop === "now"
        onHoveredChanged: if (nowChip.hovered && root.hasCursor) root.focusStop("now")
        onClicked: { root.focusRequested(); root.focusStop("now"); root.activate("now") }
      }

      Repeater {
        model: root.plusChips

        delegate: Chip {
          id: plusChip
          required property var modelData
          theme: root.theme
          text: plusChip.modelData.text
          hasCursor: root.hasCursor && root.cursorStop === plusChip.modelData.stop
          onHoveredChanged: if (plusChip.hovered && root.hasCursor) root.focusStop(plusChip.modelData.stop)
          onClicked: {
            root.focusRequested()
            root.focusStop(plusChip.modelData.stop)
            root.activate(plusChip.modelData.stop)
          }
        }
      }
    }

    // One row: the reset chip and the two preset times always sit together.
    Row {
      spacing: Style.spacing.sm

      Item {
        visible: root.resetOffered
        width: resetChip.implicitWidth
        height: resetChip.implicitHeight

        Chip {
          id: resetChip
          anchors.fill: parent
          theme: root.theme
          harness: root.resetOwners[root.reset.kind] || ""
          text: root.reset.label !== "" ? root.reset.label : "At reset"
          enabled: root.reset.available === true
          selected: root.isSelected("reset")
          hasCursor: root.hasCursor && root.cursorStop === "reset"
          meterValue: typeof root.reset.percent === "number" ? root.reset.percent : -1
          // The meter shows how full the window is; the header strip gives the percent.
          note: typeof root.reset.percent === "number"
            ? "" : (root.reset.available === true ? "" : (root.reset.reason === "not_open" ? "quota fresh" : "no data"))
          onHoveredChanged: if (resetChip.hovered && root.hasCursor) root.focusStop("reset")
          onClicked: { root.focusRequested(); root.focusStop("reset"); root.activate("reset") }
        }

        // Disabled when there is nothing to bind to; a click still says why.
        MouseArea {
          anchors.fill: parent
          visible: !resetChip.enabled
          cursorShape: Qt.PointingHandCursor
          onClicked: {
            root.focusRequested()
            root.focusStop("reset")
            root.bindReset()
          }
        }
      }

      Chip {
        id: eveningChip
        theme: root.theme
        text: (root.evening && root.evening.tomorrow ? "Tomorrow " : "Tonight ") + root.settingString("eveningTime", "23:00")
        selected: root.isSelected("evening")
        hasCursor: root.hasCursor && root.cursorStop === "evening"
        onHoveredChanged: if (eveningChip.hovered && root.hasCursor) root.focusStop("evening")
        onClicked: { root.focusRequested(); root.focusStop("evening"); root.activate("evening") }
      }

      Chip {
        id: morningChip
        theme: root.theme
        text: "Tomorrow " + root.settingString("morningTime", "07:00")
        selected: root.isSelected("morning")
        hasCursor: root.hasCursor && root.cursorStop === "morning"
        onHoveredChanged: if (morningChip.hovered && root.hasCursor) root.focusStop("morning")
        onClicked: { root.focusRequested(); root.focusStop("morning"); root.activate("morning") }
      }
    }
  }
}
