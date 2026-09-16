.pragma library

// Contrast-solved inks. The six agents keep their own hues everywhere in the panel,
// because a Claude mark that turned green under a green theme would say something
// untrue. What is allowed to move is lightness, and only when the theme would
// otherwise make the ink unreadable: the hue and saturation stay, the lightness is
// bisected until the ink reaches the contrast target against the popup surface.
//
// lumOf, contrast, toHsl, hslHex and toneFor are the recipe from
// opencode-config-manager/lib/Palette.js (MIT, same author), with one change: the
// parser also takes "#aarrggbb", which is how a QML color with alpha prints.

// ---------------------------------------------------------------- brand

// Cursor and Pi ship no brand hue of their own (both marks are plain black and white),
// so their words take two free places on the wheel: purple at 285 and lime at 80, each
// at least 38 degrees from Claude, Gemini, the warning amber and the success green. Pi
// left pink (330) because every theme's urgent red sits 330 to 15, and a pink chip beside
// "Cancel all jobs" read as an error. Two greys would read as soft text and as each other
// (CONTRACT-V2-DELTA DV7).
var BRAND = { claude: "#D97757", opencode: "#5C9CF5", codex: "#10A37F", gemini: "#847ACE",
              cursor: "#BB64D8", pi: "#97C639" }

// Marks only: the monochrome colours agent-skills-manager draws these two in (its
// AgentMark.qml, MIT, same author). Text never uses them.
var MARK_BRAND = { cursor: "#8A8A8A", pi: "#C9C9C9" }

// Gemini CLI's own gradient. The middle stop is the text ink; the outer two only
// ever appear as texture (mark fill, rail), never as the colour of words, because
// the rose stop sits next to Claude and to most themes' red.
var GEMINI_STOPS = ["#4796E4", "#847ACE", "#C3677F"]

// An unknown agent id still gets something readable instead of nothing.
var UNKNOWN_INK = "#8b949e"

// ---------------------------------------------------------------- luminance

function channelLum(c) {
  return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)
}

// "#rgb", "#rrggbb" and "#aarrggbb" (alpha dropped: contrast is about the ink,
// and the caller decides how much of it to lay down).
function hexRgb(value) {
  var s = String(value === undefined || value === null ? "" : value).trim().replace(/^#/, "")
  if (s.length === 8) s = s.substr(2)
  if (s.length === 3) s = s[0] + s[0] + s[1] + s[1] + s[2] + s[2]
  if (!/^[0-9a-fA-F]{6}$/.test(s)) return { r: 0, g: 0, b: 0 }
  return {
    r: parseInt(s.substr(0, 2), 16) / 255,
    g: parseInt(s.substr(2, 2), 16) / 255,
    b: parseInt(s.substr(4, 2), 16) / 255
  }
}

// A QML color (which has .r/.g/.b in 0..1) or any of the string forms above.
function rgbOf(c) {
  return (c && typeof c === "object" && c.r !== undefined) ? c : hexRgb(c)
}

function lumOf(c) {
  var rgb = rgbOf(c)
  return 0.2126 * channelLum(rgb.r) + 0.7152 * channelLum(rgb.g) + 0.0722 * channelLum(rgb.b)
}

function contrast(a, b) {
  var la = lumOf(a), lb = lumOf(b)
  var hi = Math.max(la, lb), lo = Math.min(la, lb)
  return (hi + 0.05) / (lo + 0.05)
}

// The lowest alpha, from `floor` up in steps of 0.01, at which `fg` laid over `surface`
// reaches `target`. 1 when no alpha does: the text then uses the full foreground.
// The mix is done in the same sRGB space the compositor blends in.
function alphaFor(fg, surface, target, floor) {
  var t = targetOf(target, 4.5)
  var f = rgbOf(fg), s = rgbOf(surface)
  var start = Math.max(0, Math.min(1, Number(floor)))
  if (!isFinite(start)) start = 0
  for (var step = Math.round(start * 100); step <= 100; step++) {
    var a = step / 100
    var mix = { r: f.r * a + s.r * (1 - a), g: f.g * a + s.g * (1 - a), b: f.b * a + s.b * (1 - a) }
    if (contrast(mix, s) >= t) return a
  }
  return 1
}

function isDarkSurface(c) { return lumOf(c) < 0.5 }

// ---------------------------------------------------------------- hsl

function toHsl(c) {
  var rgb = rgbOf(c)
  var r = rgb.r, g = rgb.g, b = rgb.b
  var max = Math.max(r, g, b), min = Math.min(r, g, b)
  var l = (max + min) / 2, h = 0, s = 0
  if (max !== min) {
    var d = max - min
    s = l > 0.5 ? d / (2 - max - min) : d / (max + min)
    if (max === r) h = ((g - b) / d + (g < b ? 6 : 0))
    else if (max === g) h = (b - r) / d + 2
    else h = (r - g) / d + 4
    h *= 60
  }
  return { h: h, s: s * 100, l: l * 100 }
}

function hslHex(h, s, l) {
  var hh = ((h % 360) + 360) % 360 / 360
  var ss = Math.max(0, Math.min(100, s)) / 100
  var ll = Math.max(0, Math.min(100, l)) / 100
  function hue2(p, q, t) {
    if (t < 0) t += 1
    if (t > 1) t -= 1
    if (t < 1 / 6) return p + (q - p) * 6 * t
    if (t < 1 / 2) return q
    if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6
    return p
  }
  var r, g, b
  if (ss === 0) { r = g = b = ll }
  else {
    var q = ll < 0.5 ? ll * (1 + ss) : ll + ss - ll * ss
    var p = 2 * ll - q
    r = hue2(p, q, hh + 1 / 3); g = hue2(p, q, hh); b = hue2(p, q, hh - 1 / 3)
  }
  function hx(v) {
    var n = Math.round(Math.max(0, Math.min(1, v)) * 255).toString(16)
    return n.length === 1 ? "0" + n : n
  }
  return "#" + hx(r) + hx(g) + hx(b)
}

// Lightness is solved, not assigned: HSL lightness is not luminance (a saturated
// green at L=44 is bright), so bisect for the L that hits the target on this surface.
// The search keeps the last midpoint that met the target, so the answer is never
// the one step short of it that a plain bisection can end on.
function toneFor(h, s, surface, target) {
  var dark = isDarkSurface(surface)
  var lo = dark ? 20 : 4
  var hi = dark ? 96 : 70
  var best = dark ? hi : lo
  for (var i = 0; i < 22; i++) {
    var mid = (lo + hi) / 2
    var c = contrast(hslHex(h, s, mid), surface)
    if (c < target) { if (dark) lo = mid; else hi = mid }
    else { best = mid; if (dark) hi = mid; else lo = mid }
  }
  return best
}

// ---------------------------------------------------------------- inks

// "#rrggbb", lower case, whatever form came in.
function normalHex(value) {
  var rgb = rgbOf(value)
  return "#" + hex2(rgb.r) + hex2(rgb.g) + hex2(rgb.b)
}

function hex2(v) {
  var n = Math.round(Math.max(0, Math.min(1, v)) * 255).toString(16)
  return n.length === 1 ? "0" + n : n
}

function targetOf(target, fallback) {
  var t = Number(target)
  return isFinite(t) && t > 1 ? t : fallback
}

// Identity stays the tool's own hue; only lightness moves, and only when the
// theme would otherwise make it unreadable.
function ink(hex, surface, target) {
  var t = targetOf(target, 4.5)
  var base = normalHex(hex)
  if (contrast(base, surface) >= t) return base
  var c = toHsl(base)
  return hslHex(c.h, c.s, toneFor(c.h, c.s, surface, t))
}

function harnessInk(id, surface, target) {
  var key = String(id || "")
  return ink(BRAND.hasOwnProperty(key) ? BRAND[key] : UNKNOWN_INK, surface, targetOf(target, 4.5))
}

// The colour an agent's mark is filled with: the monochrome mark colour where the
// product has one, else its brand ink. Solved to `target` (3.0 by default: a mark is
// a graphic, not text).
function harnessMark(id, surface, target) {
  var key = String(id || "")
  var hex = MARK_BRAND.hasOwnProperty(key) ? MARK_BRAND[key] : (BRAND.hasOwnProperty(key) ? BRAND[key] : UNKNOWN_INK)
  return ink(hex, surface, targetOf(target, 3.0))
}

function geminiStops(surface, target) {
  var t = targetOf(target, 3.0)
  return [ink(GEMINI_STOPS[0], surface, t), ink(GEMINI_STOPS[1], surface, t), ink(GEMINI_STOPS[2], surface, t)]
}

// Semantic hues the theme does not provide (Color.qml has no success or warning
// token): a fixed hue and saturation, lightness solved for this surface.
function solved(h, s, surface, target) {
  var t = targetOf(target, 4.5)
  return hslHex(h, s, toneFor(h, s, surface, t))
}
