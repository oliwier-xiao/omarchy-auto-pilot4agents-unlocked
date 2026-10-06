.pragma library
.import "Edition.js" as Edition
.import "Model.js" as Model

// Pure helpers for the Compose view and its sheets: the paid-usage notes, the fixed
// sentences for the helper's gate codes, which reset an agent can follow with this
// model, whether a permission level is offered, the model picker rows and the Will run
// wrapping. Nothing here touches QML, the clock or the disk; "now" is always an
// argument (CONTRACT-V2-DELTA 9.4).

var DAY_MS = 86400000
var HORIZON_SEC = 691200

// ---------------------------------------------------------------- notes

// Gate note codes (paid.NOTES) and their sentences. %1 is filled per code.
var NOTE_SENTENCES = {
  subscription_only: "Runs on your subscription only. If the limit is hit, it waits for the reset.",
  paid_on: "This job may spend usage credits or API dollars.",
  budget_cap: "Budget cap $%1",
  opencode_provider: "OpenCode bills through the provider in your OpenCode config.",
  zen_free: "Free OpenCode Zen model. Free use has a daily limit that resets at %1. Free models may use prompts to improve the model.",
  go_plan: "OpenCode Go plan. If Use balance is on in the OpenCode console, Go can also spend your Zen balance.",
  cursor_on_demand: "Cursor may bill on-demand usage if it is turned on in your Cursor dashboard.",
  pi_subscription: "Draws from your %1 subscription. Extra usage may bill if that account allows it.",
  codex_free_tier: "Runs draw from a small monthly allowance."
}

var NOTE_TONES = { paid_on: "warn", budget_cap: "warn" }

var STATE_NOTES = ["subscription_only", "paid_on"]

// The class notes each agent may carry in each state (CONTRACT-V2-DELTA 6.2). A note
// the helper sent for the other state is dropped when the box has just been flipped.
var CLASS_NOTES = {
  claude: { off: [], on: ["budget_cap"] },
  codex: { off: ["codex_free_tier"], on: [] },
  gemini: { off: [], on: [] },
  opencode: { off: ["zen_free", "go_plan", "opencode_provider"], on: ["zen_free", "go_plan", "opencode_provider"] },
  cursor: { off: ["cursor_on_demand"], on: ["cursor_on_demand"] },
  pi: { off: ["pi_subscription"], on: [] }
}

var MODEL_REASONS = {
  cursor_no_list: "Cursor did not list models. Run cursor-agent login.",
  pi_no_provider: "Pi has no signed-in provider.",
  cli_missing: "The agent command was not found.",
  timeout: "Reading models took too long.",
  too_large: "The model list is too large to read.",
  failed: "The agent did not list models."
}

var PROVIDER_NAMES = {
  "openai-codex": "ChatGPT", "github-copilot": "GitHub Copilot", xai: "xAI", "kimi-coding": "Kimi",
  anthropic: "Anthropic", openrouter: "OpenRouter", radius: "Radius", google: "Google"
}

var PI_PROVIDER_RE = /^[a-z0-9][a-z0-9-]{0,39}$/
var MODEL_MAX = 128
var MODEL_RE = /^[A-Za-z0-9][A-Za-z0-9._:\/@-]{0,127}(?:\[[A-Za-z0-9._:\/=,-]{1,96}\])?$/

function isNum(v) { return typeof v === "number" && isFinite(v) }

function providerName(id) {
  var key = String(id === undefined || id === null ? "" : id)
  return PROVIDER_NAMES.hasOwnProperty(key) ? PROVIDER_NAMES[key] : key
}

// Group headings in the model picker: OpenCode's own providers read as names, not ids.
var GROUP_NAMES = { opencode: "OpenCode Zen", "opencode-go": "OpenCode Go", openai: "OpenAI",
                    "claude-latest": "Newest of each family", "claude-pinned": "Pinned versions" }

function groupName(id) {
  var key = String(id === undefined || id === null ? "" : id)
  return GROUP_NAMES.hasOwnProperty(key) ? GROUP_NAMES[key] : providerName(key)
}

function nextUtcMidnightMs(nowMs) {
  var now = Number(nowMs)
  if (!isFinite(now)) return null
  return (Math.floor(now / DAY_MS) + 1) * DAY_MS
}

function noteText(code, gate, budgetUsd, nowMs) {
  var g = gate && typeof gate === "object" ? gate : {}
  var s = NOTE_SENTENCES[code]
  if (code === "budget_cap") {
    var b = Number(budgetUsd)
    return s.replace("%1", (isFinite(b) ? b : 5).toFixed(2))
  }
  if (code === "zen_free") {
    var at = isNum(g.resetAtMs) ? g.resetAtMs : nextUtcMidnightMs(nowMs)
    return s.replace("%1", at === null ? "00:00 UTC" : Model.formatClock(at))
  }
  if (code === "pi_subscription") {
    var name = providerName(g.provider)
    return s.replace("%1", name !== "" ? name : "provider")
  }
  return s
}

function classFromBilling(billing) {
  if (billing === "zen_free") return "zen_free"
  if (billing === "go") return "go_plan"
  if (billing === "zen_paid" || billing === "anthropic") return ""
  return "opencode_provider"
}

// [{code, text, tone: "soft"|"warn"}] in display order: the state note, the budget cap,
// then the class note. The helper's own notes win while they describe the state the
// box is in; right after a flip (before the next preview answers) the notes are rebuilt
// from the same table, keeping the class the helper found.
function gateNotes(gate, harness, allowPaid, budgetUsd, nowMs) {
  var h = String(harness || "")
  var on = allowPaid === true
  var g = gate && typeof gate === "object" ? gate : null
  var sent = g && Array.isArray(g.notes) ? g.notes.filter(function (c) { return NOTE_SENTENCES.hasOwnProperty(c) }) : null
  var codes = null
  if (sent !== null && sent.length > 0 && (sent.indexOf("paid_on") >= 0) === on) codes = sent.slice()
  if (codes === null) {
    var allowed = CLASS_NOTES.hasOwnProperty(h) ? CLASS_NOTES[h][on ? "on" : "off"] : []
    codes = [on ? "paid_on" : "subscription_only"]
    if (h === "claude" && on) codes.push("budget_cap")
    var classes = sent !== null ? sent.filter(function (c) { return STATE_NOTES.indexOf(c) < 0 && c !== "budget_cap" }) : []
    if (h === "opencode" && classes.length === 0) {
      var derived = classFromBilling(g ? g.billing : null)
      if (derived !== "") classes.push(derived)
    }
    if (h === "cursor" && classes.indexOf("cursor_on_demand") < 0) classes.push("cursor_on_demand")
    // Cursor off claims "subscription only" only from a fresh record, which only the
    // helper can see; without its answer the on-demand caveat stands alone.
    if (h === "cursor" && !on) codes = []
    for (var i = 0; i < classes.length; i++) {
      if (allowed.indexOf(classes[i]) >= 0 && codes.indexOf(classes[i]) < 0) codes.push(classes[i])
    }
  }
  if (h !== "claude" || !on) codes = codes.filter(function (c) { return c !== "budget_cap" })
  // A free Zen model or the Go plan says more than "subscription only": its class caption
  // takes the place of the generic one instead of stacking under it (FEATURE-PAID R7).
  if (h === "opencode" && !on && (codes.indexOf("zen_free") >= 0 || codes.indexOf("go_plan") >= 0))
    codes = codes.filter(function (c) { return c !== "subscription_only" })
  return codes.map(function (c) {
    return { code: c, text: noteText(c, g, budgetUsd, nowMs), tone: NOTE_TONES.hasOwnProperty(c) ? NOTE_TONES[c] : "soft" }
  })
}

// ---------------------------------------------------------------- gate codes

// errors.MESSAGES for the codes a gate, a Pi draft or a Cursor preflight can raise.
var GATE_MESSAGES = {
  paid_blocked: "This agent is signed in with an API key. Allow paid usage to run it.",
  paid_zen: "This model bills your OpenCode Zen balance. Allow paid usage to run it.",
  paid_opencode_claude: "Claude models in OpenCode bill API or extra usage, not your Claude plan.",
  paid_pi_claude: "Pi bills Claude through extra usage, which is off for this job.",
  paid_pi_key: "Pi uses an API key for this provider. Allow paid usage to run it.",
  pi_slash_prompt: "Pi reads a prompt that starts with / as a command. Start it with a word.",
  pi_model_required: "Pi needs a provider and a model. Pick one from the list.",
  pi_auth_invalid: "Pi could not check the sign-in for that provider.",
  level_unavailable: "That permission level is not offered for this agent.",
  harness_gated: "Cursor support is waiting for a one-time check.",
  cursor_autorun_config: "Cursor is set to Run Everything, which would approve every tool.",
  cursor_network_config: "Cursor's sandbox allows all network access.",
  cursor_project_rules: "This folder has its own Cursor or Claude allow rules, which Cursor would apply.",
  cursor_untrusted: "Cursor does not trust this folder yet. Open cursor-agent in this folder once and choose Trust this workspace.",
  not_logged_in: "The agent is not signed in. Sign in with its own command first."
}

var PAID_CODES = ["paid_blocked", "paid_zen", "paid_opencode_claude", "paid_pi_claude", "paid_pi_key"]

function isPaidCode(code) { return PAID_CODES.indexOf(String(code || "")) >= 0 }

// The fixed sentence for a gate code; "" for a code this file does not know. Pi's key
// refusal names the provider when the helper sent a grammar-checked one (DV10).
function paidErrorText(code, detail) {
  var c = String(code || "")
  if (c === "paid_pi_key" && detail && typeof detail.provider === "string" && PI_PROVIDER_RE.test(detail.provider))
    return "Pi uses an API key for " + providerName(detail.provider) + ". Allow paid usage to run it."
  return GATE_MESSAGES.hasOwnProperty(c) ? GATE_MESSAGES[c] : ""
}

// ---------------------------------------------------------------- resets

var RESET_KINDS_FALLBACK = { claude: ["claude_5h_reset"], opencode: ["zen_free_reset", "go_window_reset"],
                             codex: ["codex_window_reset"], gemini: ["gemini_daily_reset"], cursor: [],
                             pi: ["codex_window_reset"] }

var RESET_OWNERS = { claude_5h_reset: "claude", codex_window_reset: "codex", gemini_daily_reset: "gemini",
                     zen_free_reset: "opencode", go_window_reset: "opencode" }

var RESET_SOURCE = { codex_window_reset: "codex", go_window_reset: "opencode-go" }

function kindsTable() {
  var t = Model.RESET_KINDS_FOR
  return t && typeof t === "object" ? t : RESET_KINDS_FALLBACK
}

// The reset kinds a draft may follow (trigger._check_reset_kind and compute_fire_at):
// Pi only with ChatGPT sign-in, the Zen free reset only for a free opencode/ model
// (offered while its billing is still unknown, refused once it is known not free), the
// Go reset only for an opencode-go/ model.
function resetKindsFor(harness, provider, model, billing) {
  var h = String(harness || "")
  var table = kindsTable()
  var kinds = Array.isArray(table[h]) ? table[h] : []
  var m = typeof model === "string" ? model : ""
  var b = typeof billing === "string" && billing !== "" ? billing : null
  return kinds.filter(function (k) {
    if (k === "codex_window_reset" && h === "pi") return provider === "openai-codex"
    if (k === "zen_free_reset") return m.indexOf("opencode/") === 0 && (b === null || b === "zen_free")
    if (k === "go_window_reset") return m.indexOf("opencode-go/") === 0
    return true
  })
}

function providerById(providers, id) {
  var list = Array.isArray(providers) ? providers : []
  for (var i = 0; i < list.length; i++) if (list[i] && list[i].id === id) return list[i]
  return null
}

function windowsOf(p) {
  return p && Array.isArray(p.windows) ? p.windows.filter(function (w) { return !!w && typeof w === "object" }) : []
}

// trigger.reset_window: the latest exhausted open window, else the soonest open one.
function resetWindow(p, nowSec) {
  var open = windowsOf(p).filter(function (w) {
    return w.bindable === true && w.sliding !== true && isNum(w.resetsAt) && w.resetsAt > nowSec
  })
  var full = open.filter(function (w) { return isNum(w.percent) && w.percent >= Model.EXHAUSTED })
  var pool = full.length > 0 ? full : open
  var pick = null
  for (var i = 0; i < pool.length; i++) {
    if (pick === null) pick = pool[i]
    else if (full.length > 0 ? pool[i].resetsAt > pick.resetsAt : pool[i].resetsAt < pick.resetsAt) pick = pool[i]
  }
  return pick
}

// What the "At reset" chip offers for this agent and model right now, from Usage v2
// providers. Same shape as Model.resetChip: {kind, label, fireAtMs, percent, stale,
// available, reason}; kind "" means the chip is hidden.
function resetChipFor(providers, harness, provider, model, billing, nowMs, marginSec) {
  var kinds = resetKindsFor(harness, provider, model, billing)
  var kind = kinds.length > 0 ? kinds[0] : ""
  var margin = Number(marginSec)
  if (!isFinite(margin)) margin = 120
  var now = Number(nowMs)
  var nowSec = Math.floor(now / 1000)
  var out = { kind: kind, label: "", fireAtMs: null, percent: null, stale: false, available: false, reason: "no_data" }
  if (kind === "" || !isFinite(now)) return out

  // One chip wording for every agent (R6 3.1): "At reset HH:MM". The mark and ink say
  // whose reset it is, and the readout's "follows ... reset" line names it.
  function finish(resetMs) {
    out.label = "At reset " + Model.formatClock(resetMs)
    out.fireAtMs = resetMs + margin * 1000
    if (out.fireAtMs - now > HORIZON_SEC * 1000) {
      out.reason = "too_far"
      return out
    }
    out.available = true
    out.reason = out.stale ? "stale" : null
    return out
  }

  if (kind === "claude_5h_reset") {
    out.label = "At reset"
    var c = providerById(providers, "claude")
    if (!c || c.readable !== true) return out
    out.stale = c.stale === true
    var session = null
    var ws = windowsOf(c)
    for (var i = 0; i < ws.length; i++) if (ws[i].kind === "session" && ws[i].sliding !== true) { session = ws[i]; break }
    if (session && isNum(session.percent)) out.percent = session.percent
    if (!session || !isNum(session.resetsAt) || session.resetsAt <= nowSec) {
      out.reason = "not_open"
      return out
    }
    return finish(session.resetsAt * 1000)
  }

  if (kind === "codex_window_reset" || kind === "go_window_reset") {
    out.label = "At reset"
    var p = providerById(providers, RESET_SOURCE[kind])
    if (!p || p.readable !== true) return out
    out.stale = p.stale === true
    var w = resetWindow(p, nowSec)
    if (w === null) return out
    out.percent = isNum(w.percent) ? w.percent : null
    return finish(w.resetsAt * 1000)
  }

  if (kind === "gemini_daily_reset") {
    var g = providerById(providers, "gemini")
    var soonest = null
    if (g && g.readable === true && g.source === "record") {
      var dws = windowsOf(g)
      for (var j = 0; j < dws.length; j++) {
        var dw = dws[j]
        if (dw.kind === "daily" && dw.bindable === true && isNum(dw.resetsAt) && dw.resetsAt > nowSec
            && (soonest === null || dw.resetsAt < soonest)) soonest = dw.resetsAt
      }
    }
    var mid = soonest !== null ? soonest * 1000 : Model.nextLaMidnightMs(now)
    if (mid === null) return out
    return finish(mid)
  }

  // zen_free_reset: the next 00:00 UTC, for a model whose billing is known to be free.
  var midnight = nextUtcMidnightMs(now)
  out.label = "At reset " + Model.formatClock(midnight)
  if (billing !== "zen_free") {
    out.reason = "billing_unknown"
    return out
  }
  return finish(midnight)
}

// ---------------------------------------------------------------- levels

// {available, reason}: the helper's own sentence when a level is not offered for this
// agent (LEVELS[level].unavailable). A level table that names agents but not this one
// is not offered either. No table yet: nothing is refused here, the helper decides.
function levelState(levels, levelId, harness) {
  var list = Array.isArray(levels) ? levels : []
  var h = String(harness || "")
  for (var i = 0; i < list.length; i++) {
    var e = list[i]
    if (!e || e.id !== levelId) continue
    var un = e.unavailable && typeof e.unavailable === "object" ? e.unavailable : null
    if (un && typeof un[h] === "string" && un[h] !== "") return { available: false, reason: un[h] }
    var hs = e.harness && typeof e.harness === "object" ? e.harness : null
    if (hs && Object.keys(hs).length > 0 && !hs.hasOwnProperty(h)) return { available: false, reason: GATE_MESSAGES.level_unavailable }
    return { available: true, reason: "" }
  }
  return { available: true, reason: "" }
}

// ---------------------------------------------------------------- models

// consts.MODEL_RE and MODEL_MAX, mirrored so a bad id is caught before a round trip.
function modelOk(text) {
  return typeof text === "string" && text.length > 0 && text.length <= MODEL_MAX && MODEL_RE.test(text)
}

function badgeFor(billing, allowPaid) {
  if (billing === "zen_free") return { text: "free", tone: "ok" }
  if (billing === "zen_paid") return { text: "Zen", tone: allowPaid === true ? "readable" : "warn" }
  if (billing === "go") return { text: "Go", tone: "accent" }
  // Claude through OpenCode bills API or extra usage: refused while paid usage is off, so
  // the picker says so before the pick rather than after it.
  if (billing === "anthropic") return { text: "paid", tone: allowPaid === true ? "readable" : "warn" }
  return { text: "", tone: "" }
}

function rowFor(kind, id, label, group) {
  return { kind: kind, id: id, label: label, group: group, badge: "", badgeTone: "", isDefault: false,
           provider: null, modelId: null, note: "" }
}

// The picker's rows: "Agent default" first (never for Pi, whose fallback would try a
// paid provider), then models grouped under "group" rows: by provider for OpenCode and
// Pi, newest-of-family aliases and pinned versions for Claude. Cursor's "auto" is never
// offered. `query` is words that must all appear in the id, label, note or group.
function modelRows(result, harness, query, allowPaid) {
  var h = String(harness || "")
  var list = result && Array.isArray(result.models) ? result.models : []
  var words = String(query || "").toLowerCase().split(/\s+/).filter(function (w) { return w !== "" })
  function matches(hay) {
    var s = hay.toLowerCase()
    for (var i = 0; i < words.length; i++) if (s.indexOf(words[i]) < 0) return false
    return true
  }
  var out = []
  if (h !== "pi" && matches("agent default")) out.push(rowFor("default", "", "Agent default", ""))
  var grouped = h === "opencode" || h === "pi" || h === "claude"
  var order = []
  var buckets = {}
  for (var i = 0; i < list.length; i++) {
    var r = list[i]
    if (!r || typeof r.id !== "string" || !modelOk(r.id)) continue
    if (h === "cursor" && r.id === "auto") continue
    var label = typeof r.label === "string" && r.label !== "" ? r.label : r.id
    var group = grouped && typeof r.group === "string" ? r.group : ""
    var row = rowFor("model", r.id, label, group)
    row.note = typeof r.note === "string" ? r.note : ""
    if (h === "pi") {
      var slash = r.id.indexOf("/")
      row.provider = typeof r.provider === "string" && r.provider !== "" ? r.provider : (slash > 0 ? r.id.slice(0, slash) : "")
      row.modelId = typeof r.modelId === "string" && r.modelId !== "" ? r.modelId : (slash > 0 ? r.id.slice(slash + 1) : "")
      if (!PI_PROVIDER_RE.test(row.provider) || !modelOk(row.modelId)) continue
      if (group === "") row.group = group = row.provider
    }
    if (words.length > 0 && !matches([r.id, label, row.note, group, providerName(group)].join("\n"))) continue
    var badge = h === "opencode" ? badgeFor(r.billing, allowPaid) : { text: "", tone: "" }
    row.badge = badge.text
    row.badgeTone = badge.tone
    row.isDefault = h !== "pi" && r["default"] === true
    if (!buckets.hasOwnProperty(group)) { buckets[group] = []; order.push(group) }
    buckets[group].push(row)
  }
  for (var g = 0; g < order.length; g++) {
    if (order[g] !== "") out.push(rowFor("group", "group:" + order[g], groupName(order[g]) || order[g], order[g]))
    out = out.concat(buckets[order[g]])
  }
  return out
}

// The row the models verb has for a draft's model, or null.
function modelEntry(result, harness, provider, model) {
  var list = result && Array.isArray(result.models) ? result.models : []
  var m = typeof model === "string" ? model : ""
  if (m === "") return null
  for (var i = 0; i < list.length; i++) {
    var r = list[i]
    if (!r) continue
    if (harness === "pi") {
      if ((r.provider === provider && r.modelId === m) || r.id === provider + "/" + m) return r
    } else if (r.id === m) {
      return r
    }
  }
  return null
}

// ---------------------------------------------------------------- will run

var WORD_JOINER = "⁠"

function codePoints(text) {
  return String(text).match(/[\uD800-\uDBFF][\uDC00-\uDFFF]|[\s\S]/g) || []
}

// The display with a word joiner between the characters of every token, so Text.Wrap
// breaks only at the spaces between tokens (never after a "-" or "/" inside a flag or a
// path). Combining marks keep their base character.
function argvWrapText(display) {
  return String(display === undefined || display === null ? "" : display).split(" ").map(function (token) {
    var cps = codePoints(token)
    var out = ""
    for (var i = 0; i < cps.length; i++) {
      if (i > 0 && !/^[̀-ͯ]/.test(cps[i])) out += WORD_JOINER
      out += cps[i]
    }
    return out
  }).join(" ")
}

// How many lines `tokens` take when wrapped at spaces into `cols` cells per line; a
// token wider than a line breaks anywhere, as Text.Wrap does.
function lineCount(tokens, cols) {
  var lines = 0
  var used = -1
  for (var i = 0; i < tokens.length; i++) {
    var len = codePoints(tokens[i]).length
    if (used >= 0 && used + 1 + len <= cols) {
      used += 1 + len
      continue
    }
    lines += Math.max(1, Math.ceil(len / cols))
    used = len % cols === 0 && len > 0 ? cols : len % cols
  }
  return lines
}

// Flags whose value is a path, shortened before anything is cut (the folder is on the line
// below anyway), and flags whose value is never cut: the model and the budget are what the
// Will run line is there to show (FEATURE-PAID).
var ARGV_PATH_FLAGS = ["--dir", "--workspace", "--session", "--cwd", "--add-dir", "-C"]
var ARGV_KEEP_FLAGS = ["--model", "-m", "--max-budget-usd"]
var ARGV_PATH_MIN = 16

// "/tmp/claude-1000/…/scratchpad": the first and last characters of a token around "…",
// `keep` characters in all.
function middleElide(token, keep) {
  var cps = codePoints(token)
  if (cps.length <= keep) return String(token)
  var tailN = Math.floor((keep - 1) / 2)
  var headN = keep - 1 - tailN
  return cps.slice(0, headN).join("") + "…" + cps.slice(cps.length - tailN).join("")
}

// The display fitted into `maxLines` lines of `cols` cells: whole when it fits; else with
// long path values shortened from the middle; else the leading tokens that fit, "…", the
// model and budget flags that were cut, and the last token, so neither the model nor the
// <stdin> tail ever scrolls out of sight.
function fitArgv(display, cols, maxLines) {
  var text = String(display === undefined || display === null ? "" : display)
  var c = Math.floor(Number(cols))
  var n = Math.floor(Number(maxLines))
  if (!isFinite(c) || c < 8 || !isFinite(n) || n < 1) return text
  var tokens = text.split(" ").filter(function (t) { return t !== "" })
  if (tokens.length < 2 || lineCount(tokens, c) <= n) return text

  var paths = []
  for (var p = 0; p < tokens.length - 1; p++) {
    var isPath = (p > 0 && ARGV_PATH_FLAGS.indexOf(tokens[p - 1]) >= 0) || /^(\/|~\/)/.test(tokens[p])
    if (isPath && codePoints(tokens[p]).length > ARGV_PATH_MIN) paths.push(p)
  }
  for (var keep = 48; paths.length > 0 && keep >= ARGV_PATH_MIN; keep -= 8) {
    var shorter = tokens.slice()
    for (var q = 0; q < paths.length; q++) shorter[paths[q]] = middleElide(tokens[paths[q]], keep)
    if (lineCount(shorter, c) <= n) return shorter.join(" ")
    if (keep - 8 < ARGV_PATH_MIN) tokens = shorter
  }

  var tail = tokens[tokens.length - 1]
  var kept = {}
  for (var k = 0; k < tokens.length - 1; k++) {
    if (ARGV_KEEP_FLAGS.indexOf(tokens[k]) < 0) continue
    kept[k] = true
    if (k + 1 < tokens.length - 1) kept[k + 1] = true
  }
  function keptFrom(start) {
    var out = []
    for (var j = start; j < tokens.length - 1; j++) if (kept[j] === true) out.push(tokens[j])
    return out
  }
  var head = []
  for (var i = 0; i < tokens.length - 1; i++) {
    if (lineCount(head.concat([tokens[i], "…"], keptFrom(i + 1), [tail]), c) > n) break
    head.push(tokens[i])
  }
  return head.concat(["…"], keptFrom(head.length), [tail]).join(" ")
}
