pragma Singleton
import QtQuick

// Lint and offscreen-test stand-in for omarchy-shell's qs.Commons Border singleton.
// Hand-written: the spec constructors and readers with their real names. A spec here
// is always a flat uniform border; the real singleton also resolves theme gradients.
QtObject {
  id: root

  function none() {
    return { widths: { top: 0, right: 0, bottom: 0, left: 0 }, color: "transparent", gradient: { enabled: false, colors: [], angle: 0 } }
  }

  function flat(color, width) {
    var w = Math.max(0, Number(width) || 0)
    return { widths: { top: w, right: w, bottom: w, left: w }, color: color === undefined ? "transparent" : color, gradient: { enabled: false, colors: [], angle: 0 } }
  }

  function controlSpec(state, foreground, accent, urgent) {
    if (state === "selected") return root.flat(accent, 1)
    if (state === "hover-cursor") return root.flat(Qt.rgba(0, 0, 0, 0), 1)
    return root.flat(foreground, 1)
  }

  function surfaceSpec(section, token, fallbackColor, fallbackWidth, alphaKey) {
    return root.flat(fallbackColor, fallbackWidth)
  }

  function withWidth(spec, width) {
    return root.flat(root.color(spec), width)
  }

  function isNone(spec) { return root.uniformWidth(spec) <= 0 }
  function needsOverlay(spec) { return false }
  function canUseNative(spec) { return true }
  function top(spec) { return spec && spec.widths ? Number(spec.widths.top) || 0 : 0 }
  function right(spec) { return spec && spec.widths ? Number(spec.widths.right) || 0 : 0 }
  function bottom(spec) { return spec && spec.widths ? Number(spec.widths.bottom) || 0 : 0 }
  function left(spec) { return spec && spec.widths ? Number(spec.widths.left) || 0 : 0 }
  function uniformWidth(spec) { return root.top(spec) }
  function color(spec) { return spec && spec.color !== undefined ? spec.color : "transparent" }
}
