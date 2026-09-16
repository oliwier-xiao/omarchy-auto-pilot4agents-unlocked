pragma Singleton
import QtQuick

// Lint and offscreen-test stand-in for omarchy-shell's qs.Commons Util singleton.
// Hand-written, pure helpers only. The real singleton also has helpers that start
// detached processes; this plugin must never call them, so they are absent here and
// any call to one fails the lint run.
QtObject {
  id: root

  function clamp(value, min, max) {
    var n = Number(value)
    if (!isFinite(n)) return min
    return Math.max(min, Math.min(max, n))
  }

  function clampAlpha(value) {
    return root.clamp(value, 0, 1)
  }

  // One wheel notch is one step; smaller touchpad deltas accumulate.
  function wheelSteps(accumulator, delta) {
    var d = Math.max(-120, Math.min(120, Number(delta) || 0))
    var acc = Number(accumulator) || 0
    if (acc * d < 0) acc = 0
    var total = acc + d
    var steps = total < 0 ? Math.ceil(total / 120) : Math.floor(total / 120)
    return { steps: steps, remainder: total - steps * 120 }
  }

  function alpha(c, opacity) {
    var a = root.clampAlpha(opacity)
    if (!c) return Qt.rgba(0, 0, 0, a)
    var col = typeof c === "string" ? Qt.color(c) : c
    return Qt.rgba(col.r, col.g, col.b, a)
  }

  function isPlainObject(value) {
    return value !== null && typeof value === "object" && !Array.isArray(value)
  }

  function cloneJson(value) {
    return JSON.parse(JSON.stringify(value === undefined ? null : value))
  }

  // Backspace, Ctrl+Backspace and Ctrl+U edit a filter; true only when the text would change.
  function editsFilter(event, text) {
    if (!text) return false
    if (event.modifiers & (Qt.AltModifier | Qt.MetaModifier)) return false
    if (event.key === Qt.Key_U) return event.modifiers === Qt.ControlModifier
    return event.key === Qt.Key_Backspace
  }

  function editedFilter(event, text) {
    if (event.key === Qt.Key_U) return ""
    if (event.modifiers & Qt.ControlModifier) return text.replace(/\s+$/, "").replace(/\S+$/, "")
    return text.slice(0, -1)
  }
}
