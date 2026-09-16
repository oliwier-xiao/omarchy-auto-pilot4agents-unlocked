import QtQuick
import qs.Commons
import "../lib/Edition.js" as Edition
import "../lib/Model.js" as Model
import "../lib/Compose.js" as Compose

// Compose: write a prompt now, choose where it goes and when, see the exact command,
// then arm it (R6 8.1). A form of four sections, Prompt, Send to, When and Options,
// that Tab walks through; the prompt editor is section 0.
//
// Nothing here schedules anything. The draft lives in this view only until it is
// handed to the service, which saves it through the helper (prompt on stdin), gets
// back a digest and arms exactly that. The "Will run" line is the helper's own
// rendering of the command for this draft (`ap4a preview`), and the arm carries its
// digest, so what was shown is what is armed or the helper refuses.
//
// The preview also answers with the gate arm would apply (paid usage, Cursor's one-time
// check and preflights, sign-in). While that gate says no, Arm is off and the reason is
// shown where it can be fixed: a paid refusal under the Allow paid usage box, anything
// else beside the buttons. Pi needs an explicit provider and model, and a prompt that
// does not start with "/".
Item {
  id: root

  required property var theme
  required property var service
  property bool active: false
  property string home: ""
  // The panel's key catcher: focus returns here when the editor is left.
  property Item focusReturn: null

  signal noticeRequested(string text, string kind, var undo)
  signal viewRequested(string view, string jobId)
  // The job just armed, sent before the view moves to the Queue (its rail flashes).
  signal armed(string jobId)
  signal sheetRequested(string sheet, var args)
  // Ctrl+1/2/3, Ctrl+, Ctrl+N and Ctrl+PgUp/PgDn pressed inside the editor, for the
  // panel's global step.
  signal globalKey(var event)

  // Helper sentences for a preview's fireAtError and fireAtHint (CONTRACT 2.5, 4.6).
  readonly property var fireErrors: ({
    time_past: "That time has already passed.",
    time_too_far: "That time is more than 8 days away.",
    no_reset_data: "No usage data for that reset. Pick a time instead.",
    reset_not_open: "No limit window is open, so the quota is already fresh.",
    weekly_exhausted: "The weekly limit is used up.",
    trigger_unsupported: "That reset does not apply to this agent."
  })
  readonly property var fireHints: ({
    usage_stale: "Usage data is old, so the time may shift.",
    weekly_deferred: "The weekly limit is used up, so it waits for the weekly reset.",
    clock_unsynced: "The clock is not synced, so it waits 2 minutes more."
  })

  property int section: 0

  property var _draft: ({ harness: Edition.HARNESS_IDS[0],
                          target: { mode: "new", sessionId: null, cwd: null, allowNonGit: false, sessionPath: null },
                          level: Edition.DEFAULT_LEVEL, limits: {}, model: null, allowPaid: false, provider: null,
                          trigger: { kind: "now" } })
  property bool _touched: false
  property var _lastSession: null
  property var _preview: null
  property string _previewHarness: ""
  // Agent, provider and model the last preview answered for (its notes describe that model).
  property string _previewModelKey: ""
  property string _previewError: ""
  property string _previewDoneKey: ""
  property bool _previewPending: false
  property bool _armQueued: false
  property bool _arming: false
  property bool _saving: false
  property bool _guardArmed: false
  property double _tickMs: 0
  // A preview that skipped the OpenCode catalogue asks for the model list once, then
  // previews again when it lands (DV11).
  property string _modelsWaitKey: ""
  property string _modelsAskedKey: ""
  property var _modelsAsked: ({})

  // ---------------------------------------------------------------- derived

  readonly property bool ready: !!root.service && root.service.ready === true
  readonly property var settings: root.service && root.service.settings ? root.service.settings : ({})
  readonly property var agents: root.service && root.service.agents ? root.service.agents : ({})
  readonly property var levels: root.service && Array.isArray(root.service.levels) ? root.service.levels : []
  readonly property var models: root.service && root.service.models && typeof root.service.models === "object" ? root.service.models : ({})
  readonly property var providers: root.service && Array.isArray(root.service.providers) ? root.service.providers : []
  readonly property double nowMs: Math.max(root.service ? Number(root.service.nowMs) || 0 : 0, root._tickMs)

  // The Draft the service receives (CONTRACT 4.4 plus `id` for an update). Extra
  // display keys on target (title, updatedAtMs, messages) are dropped by the service.
  readonly property var draft: {
    var out = {}
    var d = root._draft || {}
    for (var k in d) out[k] = d[k]
    out.prompt = editor.text
    return out
  }
  readonly property bool dirty: editor.text !== "" || root._touched
  readonly property bool editorFocused: editor.editing

  readonly property string harness: typeof root._draft.harness === "string" ? root._draft.harness : Edition.HARNESS_IDS[0]
  readonly property var target: root._draft.target ? root._draft.target : ({ mode: "new", sessionId: null, cwd: null })
  readonly property var trigger: root._draft.trigger ? root._draft.trigger : ({ kind: "now" })
  readonly property string level: typeof root._draft.level === "string" ? root._draft.level : Edition.DEFAULT_LEVEL
  readonly property bool allowPaid: root._draft.allowPaid === true
  readonly property string provider: root.harness === "pi" && typeof root._draft.provider === "string" ? root._draft.provider : ""
  readonly property string model: typeof root._draft.model === "string" ? root._draft.model : ""
  readonly property bool isRunNow: root.trigger.kind === "now"
  readonly property string armHint: root.isRunNow ? "Ctrl+Enter run now" : "Ctrl+Enter arm"
  readonly property bool hasSession: typeof root.target.sessionId === "string" && root.target.sessionId !== ""
  readonly property bool targetComplete: root.target.mode === "new"
    ? (typeof root.target.cwd === "string" && root.target.cwd !== "")
    : root.hasSession

  // What the preview depends on. The prompt is not part of it.
  readonly property string draftKey: JSON.stringify({
    id: root._draft.id || null, harness: root.harness,
    target: { mode: root.target.mode, sessionId: root.target.sessionId || null, cwd: root.target.cwd || null,
              allowNonGit: root.target.allowNonGit === true, sessionPath: root.target.sessionPath || null },
    level: root.level, limits: root._draft.limits || {}, model: root._draft.model || null,
    allowPaid: root.allowPaid, provider: root.provider || null, trigger: root.trigger
  })
  readonly property bool previewCurrent: root._previewDoneKey === root.draftKey && !root._previewPending

  readonly property bool geminiRan: {
    var jobs = root.service && Array.isArray(root.service.jobs) ? root.service.jobs : []
    for (var i = 0; i < jobs.length; i++) {
      if (jobs[i] && jobs[i].harness === "gemini" && jobs[i].state && jobs[i].state.lastRun) return true
    }
    return false
  }

  readonly property string levelCaption: {
    for (var i = 0; i < root.levels.length; i++) {
      if (root.levels[i] && root.levels[i].id === root.level) {
        var h = root.levels[i].harness ? root.levels[i].harness[root.harness] : null
        return h && typeof h.caption === "string" ? h.caption : ""
      }
    }
    return ""
  }

  // The last preview's gate for this agent, and whether it answers for the draft as it is.
  readonly property var gate: root._preview && root._preview.gate && typeof root._preview.gate === "object"
    && root._previewHarness === root.harness ? root._preview.gate : null
  readonly property bool gateCurrent: root.previewCurrent && root.gate !== null
  readonly property string gateCode: root.gateCurrent && root.gate.ok === false && typeof root.gate.code === "string" ? root.gate.code : ""
  readonly property string gateText: root.gateCode === ""
    ? "" : (Compose.paidErrorText(root.gateCode, root.gate.detail) || "The helper would not arm this job yet.")
  // The gate the paid notes read: the last preview's while it answered for this agent and
  // model, else only the billing class the model list knows, so a note about the previous
  // model never lingers while the next preview is on its way.
  readonly property var notesGate: root.gate !== null && root._previewModelKey === root.harness + "|" + root.provider + "|" + root.model
    ? root.gate : ({ billing: root.billing !== "" ? root.billing : null, provider: root.provider !== "" ? root.provider : null, resetAtMs: null })
  // Gate refusals that are not about paid usage, shown beside the buttons.
  readonly property string gateLine: root.gateCode !== "" && !Compose.isPaidCode(root.gateCode) ? root.gateText : ""

  // OpenCode's billing class for the chosen model: the gate's when it answers for this
  // draft, else the model list's, else unknown ("").
  readonly property string billing: root.billingOf(root._draft)

  readonly property bool piNeedsModel: root.harness === "pi" && (root.provider === "" || root.model === "")
  readonly property bool piSlashPrompt: root.harness === "pi" && /^\s*\//.test(editor.text)
  readonly property string blockText: root.piSlashPrompt ? Compose.GATE_MESSAGES.pi_slash_prompt
    : (root.piNeedsModel ? Compose.GATE_MESSAGES.pi_model_required : root.gateText)
  readonly property bool armBlocked: root.blockText !== ""

  readonly property bool previewShown: !!root._preview && root.targetComplete && !root.piNeedsModel
  readonly property string argvDisplay: root.previewShown && typeof root._preview.display === "string"
    ? root._preview.display
    : (!root.targetComplete ? "Pick a session or a folder first. The exact command shows here."
      : (root.piNeedsModel ? "Pick a Pi model first. The exact command shows here." : ""))
  readonly property string argvError: root.targetComplete && !root.piNeedsModel && root._previewError !== ""
    && root._previewDoneKey === root.draftKey ? root._previewError : ""

  readonly property string helperFireNote: {
    var p = root._preview
    if (!p || root._previewDoneKey !== root.draftKey) return ""
    if (typeof p.fireAtError === "string" && root.fireErrors.hasOwnProperty(p.fireAtError)) return root.fireErrors[p.fireAtError]
    if (typeof p.fireAtHint === "string" && root.fireHints.hasOwnProperty(p.fireAtHint)) return root.fireHints[p.fireAtHint]
    return ""
  }

  readonly property bool nonGitWarning: root.previewShown && root._previewDoneKey === root.draftKey
    && Array.isArray(root._preview.warnings) && root._preview.warnings.indexOf("non_git_dir") >= 0
    && root.target.allowNonGit !== true

  readonly property string hints: {
    if (root._guardArmed) return "Press Ctrl+Enter again to run now."
    var arm = root.armHint
    if (root.section === 0) {
      if (editor.editing) return "Esc leave the editor  ·  Tab next  ·  " + arm + "  ·  Ctrl+S save draft"
      return "Type to write  ·  Tab next  ·  " + arm + "  ·  Ctrl+S save draft  ·  Esc close"
    }
    if (root.section === 1) return sendTo.hints
    if (root.section === 2) return whenControl.hints
    if (root.section === 3) return options.hints
    return "Tab next  ·  " + arm + "  ·  Ctrl+S save draft  ·  Esc close"
  }

  // ---------------------------------------------------------------- helpers

  function copy(obj) {
    var out = {}
    if (obj && typeof obj === "object") for (var k in obj) out[k] = obj[k]
    return out
  }

  function withKey(obj, key, value) {
    var out = root.copy(obj)
    out[key] = value
    return out
  }

  function basename(path) {
    var p = String(path || "").replace(/\/+$/, "")
    var i = p.lastIndexOf("/")
    return i >= 0 ? p.slice(i + 1) : p
  }

  function freshDraft() {
    var d = Model.draftFromSettings(root.service ? root.service.settings : null, root.service ? root.service.agents : null)
    var out = root.copy(d)
    delete out.prompt
    if (out.allowPaid !== true) out.allowPaid = false
    if (out.provider === undefined) out.provider = null
    return out
  }

  function billingOf(d) {
    if (!d || d.harness !== "opencode") return ""
    var m = typeof d.model === "string" ? d.model : ""
    var g = root._preview && root._preview.gate && root._previewHarness === "opencode" ? root._preview.gate : null
    if (g && typeof g.billing === "string" && g.billing !== "" && root._previewDoneKey === root.draftKey && d === root._draft) return g.billing
    var e = Compose.modelEntry(root.models.opencode, "opencode", "", m)
    return e && typeof e.billing === "string" ? e.billing : ""
  }

  // A reset kind the draft's agent and model no longer follow becomes the one it does,
  // or Now with a sentence saying so.
  function fixTrigger(d, notes) {
    var tr = d.trigger && typeof d.trigger === "object" ? d.trigger : { kind: "now" }
    if (!Model.RESET_NAMES.hasOwnProperty(tr.kind)) return d
    var kinds = Compose.resetKindsFor(d.harness, d.provider, d.model, root.billingOf(d))
    if (kinds.indexOf(tr.kind) >= 0) return d
    if (kinds.length > 0) {
      d.trigger = root.withKey(tr, "kind", kinds[0])
      return d
    }
    d.trigger = { kind: "now" }
    if (notes) notes.push("No reset applies to " + Model.harnessName(d.harness) + (d.model ? " with this model" : "") + ", so the time is set to Now.")
    return d
  }

  // The draft moved to agent `h`: its model belongs to the old agent, a level the new one
  // does not offer falls back to Plan, and the trigger is checked again.
  function switchedHarness(draft, h, notes) {
    var d = root.copy(draft)
    var hadModel = typeof d.model === "string" && d.model !== ""
    d.harness = h
    d.model = null
    d.provider = null
    var lv = Compose.levelState(root.levels, d.level, h)
    if (!lv.available) {
      d.level = Edition.DEFAULT_LEVEL
      if (notes) notes.push(lv.reason)
    }
    if (notes && h === "pi") notes.push(Compose.GATE_MESSAGES.pi_model_required)
    else if (notes && hadModel) notes.push(Model.harnessName(h) + " starts with its own default model.")
    return root.fixTrigger(d, notes)
  }

  function askModels(h) {
    var s = root.service
    if (!s || typeof s.loadModels !== "function" || root._modelsAsked[h] === true) return
    if (s.models && s.models[h]) return
    root._modelsAsked = root.withKey(root._modelsAsked, h, true)
    s.loadModels(h, false)
  }

  // ---------------------------------------------------------------- public API

  function activate() {
    if (editor.text === "") {
      root.section = 0
      editor.focusEditor()
    }
    if (!root.previewCurrent && !root._previewPending) debounce.restart()
  }

  function newDraft() {
    if (root.dirty) {
      var snapshot = { draft: root._draft, prompt: editor.text }
      root.reset()
      root.noticeRequested("New draft.", "info", function () { root.loadDraft(snapshot.draft, snapshot.prompt, "") })
    } else {
      root.reset()
    }
    if (root.active) editor.focusEditor()
  }

  function loadDraft(draft, prompt, notice) {
    var d = root.copy(draft)
    delete d.prompt
    if (Edition.HARNESS_IDS.indexOf(d.harness) < 0) d.harness = Edition.HARNESS_IDS[0]
    if (!d.target || typeof d.target !== "object") d.target = { mode: "new", sessionId: null, cwd: null, allowNonGit: false }
    d.target = root.copy(d.target)
    d.target.sessionPath = d.harness === "pi" && typeof d.target.sessionPath === "string" && d.target.sessionPath !== "" ? d.target.sessionPath : null
    if (!d.trigger || typeof d.trigger !== "object") d.trigger = { kind: "now" }
    if (!d.limits || typeof d.limits !== "object") d.limits = {}
    d.allowPaid = d.allowPaid === true
    d.provider = d.harness === "pi" && typeof d.provider === "string" && d.provider !== "" ? d.provider : null
    root.clearGuard()
    root._armQueued = false
    root._draft = d
    root._touched = true
    root._lastSession = typeof d.target.sessionId === "string" && d.target.sessionId !== ""
      ? { harness: d.harness, sessionId: d.target.sessionId, cwd: d.target.cwd, title: d.target.title || "",
          sessionPath: d.target.sessionPath } : null
    editor.text = prompt === null || prompt === undefined ? "" : String(prompt)
    root.section = 0
    if ((d.harness === "opencode" || d.harness === "pi") && typeof d.model === "string" && d.model !== "") root.askModels(d.harness)
    if (typeof notice === "string" && notice !== "") root.noticeRequested(notice, "warn", null)
    debounce.restart()
  }

  function applySession(selection) {
    var sel = selection && typeof selection === "object" ? selection : {}
    var h = Edition.HARNESS_IDS.indexOf(sel.harness) >= 0 ? sel.harness : root.harness
    var mode = sel.mode === "resume" || sel.mode === "fork" || sel.mode === "new" ? sel.mode : "resume"
    if (mode === "fork" && (h === "gemini" || h === "cursor")) mode = "resume"
    var sid = typeof sel.sessionId === "string" && sel.sessionId !== "" ? sel.sessionId : null
    if (sid === null && mode !== "new") mode = "new"
    var cwd = typeof sel.cwd === "string" && sel.cwd !== "" ? sel.cwd : null
    var spath = h === "pi" && sid !== null && typeof sel.sessionPath === "string" && sel.sessionPath !== "" ? sel.sessionPath : null
    var t = {
      mode: mode, sessionId: sid, cwd: cwd, allowNonGit: false, sessionPath: spath,
      title: typeof sel.title === "string" && sel.title !== "" ? sel.title : root.basename(cwd)
    }
    if (typeof sel.updatedAtMs === "number") t.updatedAtMs = sel.updatedAtMs
    if (typeof sel.messages === "number") t.messages = sel.messages
    var d = root.withKey(root._draft, "target", t)
    var notes = []
    if (h !== root.harness) d = root.switchedHarness(d, h, notes)
    if (sid !== null)
      root._lastSession = { harness: h, sessionId: sid, cwd: cwd, title: t.title, updatedAtMs: t.updatedAtMs,
                            messages: t.messages, sessionPath: spath }
    root._draft = d
    root._touched = true
    root.section = 1
    if (notes.length > 0) root.noticeRequested(notes.join(" "), "info", null)
  }

  // The model sheet's pick. Agent default is a null model; Pi's pick carries its provider.
  function applyModel(selection) {
    var sel = selection && typeof selection === "object" ? selection : {}
    if (Edition.HARNESS_IDS.indexOf(sel.harness) >= 0 && sel.harness !== root.harness) root.setHarness(sel.harness)
    var d = root.copy(root._draft)
    var m = typeof sel.model === "string" && sel.model !== "" ? sel.model : null
    if (m !== null && !Compose.modelOk(m)) {
      root.noticeRequested("The model name is not valid.", "warn", null)
      return
    }
    d.model = m
    d.provider = d.harness === "pi" && m !== null && typeof sel.provider === "string" && sel.provider !== "" ? sel.provider : null
    var notes = []
    d = root.fixTrigger(d, notes)
    root._draft = d
    root._touched = true
    root.section = 3
    options.focusRow("model")
    if (notes.length > 0) root.noticeRequested(notes.join(" "), "info", null)
  }

  // ---------------------------------------------------------------- edits

  function reset() {
    root.clearGuard()
    root._armQueued = false
    root._draft = root.freshDraft()
    root._touched = false
    root._lastSession = null
    root._preview = null
    root._previewHarness = ""
    root._previewError = ""
    root._previewDoneKey = ""
    editor.text = ""
    root.section = 0
  }

  function setHarness(h) {
    if (Edition.HARNESS_IDS.indexOf(h) < 0 || h === root.harness) return
    var notes = []
    if (root.hasSession) {
      notes.push("That session belongs to " + Model.harnessName(root.harness) + ". Pick one for "
                 + Model.harnessName(h) + ", or start a new session.")
    }
    var t = root.target
    var d = root.withKey(root._draft, "target", {
      mode: "new", sessionId: null, cwd: typeof t.cwd === "string" ? t.cwd : null, allowNonGit: false, sessionPath: null,
      title: typeof t.cwd === "string" ? root.basename(t.cwd) : ""
    })
    d = root.switchedHarness(d, h, notes)
    root._draft = d
    root._touched = true
    if (notes.length > 0) root.noticeRequested(notes.join(" "), "info", null)
  }

  function setMode(mode) {
    var t = root.target
    if (mode === "new") {
      root._draft = root.withKey(root._draft, "target", {
        mode: "new", sessionId: null, cwd: typeof t.cwd === "string" ? t.cwd : null, allowNonGit: t.allowNonGit === true,
        sessionPath: null, title: typeof t.cwd === "string" ? root.basename(t.cwd) : ""
      })
      root._touched = true
      return
    }
    if (mode === "fork" && (root.harness === "gemini" || root.harness === "cursor")) return
    var src = root.hasSession ? t
      : (root._lastSession && root._lastSession.harness === root.harness ? root._lastSession : null)
    if (!src) {
      root.openSessionSheet()
      return
    }
    var next = { mode: mode, sessionId: src.sessionId, cwd: src.cwd || null, allowNonGit: t.allowNonGit === true,
                 sessionPath: root.harness === "pi" && typeof src.sessionPath === "string" ? src.sessionPath : null,
                 title: src.title || "" }
    if (typeof src.updatedAtMs === "number") next.updatedAtMs = src.updatedAtMs
    if (typeof src.messages === "number") next.messages = src.messages
    root._draft = root.withKey(root._draft, "target", next)
    root._touched = true
  }

  function openSessionSheet() {
    root.leaveEditor()
    root.sheetRequested("session", { harness: root.harness, cwd: typeof root.target.cwd === "string" ? root.target.cwd : "" })
  }

  function openModelSheet(query) {
    root.leaveEditor()
    root.setSection(3)
    root.sheetRequested("model", { harness: root.harness, provider: root.provider, model: root.model,
                                   allowPaid: root.allowPaid, query: typeof query === "string" ? query : "",
                                   // The row the list drops out of, in this view's own
                                   // coordinates — the picker fills the same item, so it
                                   // reads the rect as given. It centres itself when absent.
                                   anchor: options.modelAnchorRect(root) })
  }

  // A time picked outside Compose (the empty Queue's "Try a time" chips, the limits
  // sheet's Run at reset). A reset this agent and model do not follow is left out: the
  // When section offers the right one.
  function presetTrigger(trigger) {
    if (!trigger || typeof trigger !== "object" || typeof trigger.kind !== "string") return
    var kind = trigger.kind
    if (Model.RESET_NAMES.hasOwnProperty(kind)
        && Compose.resetKindsFor(root.harness, root.provider, root.model, root.billing).indexOf(kind) < 0) return
    var old = root._draft && root._draft.trigger && typeof root._draft.trigger === "object" ? root._draft.trigger : {}
    var t = { kind: kind, fireAt: typeof trigger.fireAt === "number" ? trigger.fireAt : null,
              delaySec: typeof trigger.delaySec === "number" ? trigger.delaySec : null }
    if (typeof trigger.marginSec === "number") t.marginSec = trigger.marginSec
    else if (typeof old.marginSec === "number") t.marginSec = old.marginSec
    if (old.weeklyPolicy === "defer" || old.weeklyPolicy === "skip") t.weeklyPolicy = old.weeklyPolicy
    root.applyPatch({ trigger: t })
  }

  function applyPatch(patch) {
    var d = root.copy(root._draft)
    for (var k in patch) d[k] = patch[k]
    root._draft = d
    root._touched = true
  }

  // ---------------------------------------------------------------- sections

  function leaveEditor() {
    if (!editor.editing) return
    editor.leave()
    if (root.focusReturn) root.focusReturn.forceActiveFocus()
  }

  function setSection(i) {
    var next = Math.max(0, Math.min(3, i))
    if (next !== 0) root.leaveEditor()
    root.section = next
  }

  function moveSection(dir) {
    var next = (root.section + dir + 4) % 4
    root.setSection(next)
    if (next === 0) editor.focusEditor()
  }

  function editorKey(event) {
    var key = event.key
    var shift = (event.modifiers & Qt.ShiftModifier) !== 0
    if (key === Qt.Key_Tab || key === Qt.Key_Backtab) {
      root.moveSection(key === Qt.Key_Backtab || shift ? -1 : 1)
      return
    }
    if ((event.modifiers & Qt.ControlModifier) !== 0 && key === Qt.Key_S) {
      root.saveDraft()
      return
    }
    root.leaveEditor()
    root.globalKey(event)
  }

  function handleKey(event) {
    var key = event.key
    var mods = event.modifiers
    var ctrl = (mods & Qt.ControlModifier) !== 0
    var alt = (mods & (Qt.AltModifier | Qt.MetaModifier)) !== 0
    var submit = ctrl && (key === Qt.Key_Return || key === Qt.Key_Enter)
    var hadGuard = root._guardArmed
    if (hadGuard && !submit) root.clearGuard()

    if (submit) { root.submit(); return true }
    if (ctrl && key === Qt.Key_S) { root.saveDraft(); return true }
    if (key === Qt.Key_Tab || key === Qt.Key_Backtab) {
      root.moveSection(key === Qt.Key_Backtab || (mods & Qt.ShiftModifier) !== 0 ? -1 : 1)
      return true
    }
    if (key === Qt.Key_Escape && hadGuard) return true

    if (root.section === 0) {
      if (ctrl || alt) return false
      if (key === Qt.Key_Return || key === Qt.Key_Enter) { editor.focusEditor(); return true }
      var text = String(event.text || "")
      if (text.length >= 1 && text.charCodeAt(0) >= 32 && text.charCodeAt(0) !== 127) {
        editor.focusEditor()
        editor.insertText(text)
        return true
      }
      return false
    }
    if (root.section === 1) return sendTo.handleKey(event)
    if (root.section === 2) return whenControl.handleKey(event)
    return options.handleKey(event)
  }

  // ---------------------------------------------------------------- preview

  function requestPreview() {
    debounce.stop()
    var s = root.service
    if (!s || s.ready !== true || typeof s.preview !== "function") return
    if (!root.targetComplete || root.piNeedsModel) {
      root._preview = null
      root._previewError = ""
      root._previewDoneKey = ""
      root._previewPending = false
      return
    }
    var key = root.draftKey
    var h = root.harness
    var modelKey = h + "|" + root.provider + "|" + root.model
    // The preview needs no prompt, so it is never handed one.
    var payload = root.copy(root.draft)
    delete payload.prompt
    root._previewPending = true
    s.preview(payload, function (res) {
      if (res && res.code === "superseded") return
      // An answer for a draft that has changed since: the newer request is on its way.
      if (key !== root.draftKey) return
      root._previewPending = false
      root._previewDoneKey = key
      if (res && res.ok === true && res.preview && typeof res.preview === "object") {
        root._preview = res.preview
        root._previewHarness = h
        root._previewModelKey = modelKey
        root._previewError = ""
        var g = res.preview.gate
        if (g && typeof g === "object" && g.pending === true && h === "opencode" && root._modelsAskedKey !== key
            && typeof s.loadModels === "function") {
          root._modelsAskedKey = key
          root._modelsWaitKey = key
          s.loadModels("opencode", false)
        }
      } else {
        root._preview = null
        root._previewHarness = ""
        root._previewError = res && typeof res.message === "string" && res.message !== "" ? res.message : "The helper did not answer. Nothing was armed."
      }
      if (root._armQueued) {
        root._armQueued = false
        if (!root._preview) root.noticeRequested(root._previewError, "error", null)
        else if (root.gateCode !== "") root.refuseForGate()
        else root.doArm()
      }
    })
  }

  // ---------------------------------------------------------------- arm and save

  function clearGuard() {
    root._guardArmed = false
    guardTimer.stop()
  }

  function refuseForGate() {
    root.noticeRequested(root.gateText, "warn", null)
    if (Compose.isPaidCode(root.gateCode)) {
      root.setSection(3)
      options.focusRow("paid")
    }
  }

  function submit() {
    if (root._arming || !root.service) return
    if (editor.text.trim() === "") {
      root.clearGuard()
      root.noticeRequested("Write a prompt first.", "warn", null)
      root.section = 0
      editor.focusEditor()
      return
    }
    if (!root.targetComplete) {
      root.clearGuard()
      root.noticeRequested("Pick a session or a folder first.", "warn", null)
      root.setSection(1)
      return
    }
    if (root.piSlashPrompt) {
      root.clearGuard()
      root.noticeRequested(root.blockText, "warn", null)
      root.section = 0
      editor.focusEditor()
      return
    }
    if (root.piNeedsModel) {
      root.clearGuard()
      root.noticeRequested(root.blockText, "warn", null)
      root.setSection(3)
      options.focusRow("model")
      return
    }
    if (root.gateCode !== "") {
      root.clearGuard()
      root.refuseForGate()
      return
    }
    // Running a prompt costs quota, so Run now is never one stray key.
    if (root.isRunNow && !root._guardArmed) {
      root._guardArmed = true
      guardTimer.restart()
      return
    }
    root.clearGuard()
    if (!root.previewCurrent) {
      root._armQueued = true
      root.requestPreview()
      return
    }
    if (!root._preview) {
      root.noticeRequested(root._previewError !== "" ? root._previewError : "The helper did not answer. Nothing was armed.", "error", null)
      return
    }
    root.doArm()
  }

  function armedSentence(res, kind) {
    var ms = typeof res.fireAt === "number" ? res.fireAt * 1000 : NaN
    var text
    if (res.immediate === true || kind === "now") text = "Armed. It runs now."
    else if (!isFinite(ms)) text = "Armed."
    else if (Model.RESET_NAMES.hasOwnProperty(kind)) text = "Armed. Fires at the " + Model.RESET_NAMES[kind] + ", " + Model.formatClock(ms) + "."
    else {
      var day = Model.dayKey(ms) === Model.dayKey(root.nowMs) ? "" : Model.dayLabel(ms, root.nowMs).date + " "
      text = "Armed. Fires " + day + Model.formatClock(ms) + " (" + Model.formatCountdown(ms, root.nowMs) + ")."
    }
    if (res.hint === "usage_stale") text += " Usage data is old, so the time may shift."
    else if (res.hint === "weekly_deferred") text += " It waits for the weekly reset."
    else if (res.hint === "clock_unsynced") text += " The clock is not synced, so it waits 2 minutes more."
    return text
  }

  function doArm() {
    var s = root.service
    if (!s || root._arming || !root._preview) return
    var d = root.withKey(root.draft, "expectCommandDigest", String(root._preview.commandDigest || ""))
    if (d.expectCommandDigest === "") delete d.expectCommandDigest
    var kind = root.trigger.kind
    root._arming = true
    s.createAndArm(d, function (res) {
      root._arming = false
      if (!res || res.ok !== true) {
        // Saved but not armed: keep the id, so the next try updates that job.
        if (res && typeof res.id === "string" && /^[0-9a-f]{16}$/.test(res.id)) root._draft = root.withKey(root._draft, "id", res.id)
        if (res && (res.code === "preview_stale" || res.code === "digest_mismatch")) root.requestPreview()
        var fixed = res ? Compose.paidErrorText(res.code, res.detail) : ""
        root.noticeRequested(fixed !== "" ? fixed : (res && res.message ? res.message : "The helper did not answer. Try again."), "error", null)
        return
      }
      var id = String(res.id || "")
      root.noticeRequested(root.armedSentence(res, kind), "ok", function () {
        s.disarm(id, function (r) {
          if (r && r.ok === true) root.noticeRequested("Disarmed.", "ok", null)
          else root.noticeRequested(r && r.message ? r.message : "The helper did not answer. Try again.", "error", null)
        })
      })
      root.reset()
      root.armed(id)
      root.viewRequested("queue", id)
    })
  }

  function saveDraft() {
    var s = root.service
    if (!s || root._saving) return
    if (editor.text.trim() === "") {
      root.noticeRequested("Write a prompt first.", "warn", null)
      return
    }
    if (root.piSlashPrompt) {
      root.noticeRequested(root.blockText, "warn", null)
      return
    }
    root._saving = true
    s.saveDraft(root.draft, function (res) {
      root._saving = false
      if (!res || res.ok !== true) {
        var fixed = res ? Compose.paidErrorText(res.code, res.detail) : ""
        root.noticeRequested(fixed !== "" ? fixed : (res && res.message ? res.message : "The helper did not answer. Try again."), "error", null)
        return
      }
      if (typeof res.id === "string" && /^[0-9a-f]{16}$/.test(res.id)) root._draft = root.withKey(root._draft, "id", res.id)
      root.noticeRequested("Draft saved. It waits in the Queue until you arm it.", "ok", null)
    })
  }

  // ---------------------------------------------------------------- life

  // Always through the debounce: this handler can run before the bindings that derive
  // from the same draft (targetComplete, piNeedsModel) have caught up, so the decision
  // whether a preview can be asked for is made when the timer fires. The Will run line
  // itself follows those bindings at once.
  onDraftKeyChanged: {
    var d = root._draft || {}
    if ((d.harness === "opencode" || d.harness === "pi") && typeof d.model === "string" && d.model !== "") root.askModels(d.harness)
    debounce.restart()
  }

  onActiveChanged: {
    if (!root.active) {
      root.clearGuard()
      root.leaveEditor()
    }
  }

  Component.onCompleted: root._draft = root.freshDraft()

  Connections {
    target: root.service
    ignoreUnknownSignals: true

    // Defaults that arrive after the view was built replace an untouched draft.
    function onSettingsChanged() { if (!root.dirty) root._draft = root.freshDraft() }
    function onAgentsChanged() { if (!root.dirty) root._draft = root.freshDraft() }
    function onReadyChanged() { if (root.service && root.service.ready === true) debounce.restart() }
    // The model list a pending gate waited for has landed: preview the same draft again.
    function onModelsChanged() {
      if (root._modelsWaitKey !== "" && root._modelsWaitKey === root.draftKey) {
        root._modelsWaitKey = ""
        debounce.restart()
      }
    }
  }

  Timer {
    id: debounce
    interval: 300
    repeat: false
    onTriggered: root.requestPreview()
  }

  Timer {
    id: guardTimer
    interval: 3000
    repeat: false
    onTriggered: root._guardArmed = false
  }

  // Seconds for the resolved line only while the chosen time is under two minutes
  // away and the view is on screen.
  Timer {
    interval: 1000
    repeat: true
    running: root.active && !root.isRunNow && isFinite(whenControl.shownMs)
      && whenControl.shownMs - (root.service ? Number(root.service.nowMs) || 0 : 0) < 125000
    onTriggered: root._tickMs = Date.now()
  }

  // ---------------------------------------------------------------- layout

  readonly property real gap: Style.space(14)
  readonly property real labelGap: Style.space(6)

  Item {
    id: bottomBand
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.bottom: parent.bottom
    height: Math.max(argv.implicitHeight, actions.height)

    ArgvLine {
      id: argv
      anchors.left: parent.left
      anchors.right: actions.left
      anchors.rightMargin: root.gap
      anchors.bottom: parent.bottom
      theme: root.theme
      display: root.argvDisplay
      cwd: root.previewShown && typeof root._preview.cwd === "string" ? Model.shortPath(root._preview.cwd, root.home) : ""
      binary: root.previewShown && typeof root._preview.binary === "string" ? Model.shortPath(root._preview.binary, root.home) : ""
      caption: root.previewShown && typeof root._preview.levelCaption === "string" ? root._preview.levelCaption : root.levelCaption
      loading: root._previewPending
      errorText: root.argvError
      maxLines: 3
    }

    Row {
      id: actions
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      spacing: Style.space(8)

      ActionButton {
        id: saveButton
        theme: root.theme
        hasCursor: saveButton.hovered
        enabled: !root._saving && root.ready
        text: "Save draft"
        shortcut: "Ctrl+S"
        onClicked: {
          root.leaveEditor()
          root.saveDraft()
        }
      }

      ActionButton {
        id: armButton
        theme: root.theme
        primary: true
        hasCursor: armButton.hovered
        armed: root._guardArmed
        enabled: !root._arming && root.ready && !root.armBlocked
        glyph: "󰒊"   // md-send U+F048A
        text: root._arming ? "Arming…" : (root.isRunNow ? "Run now" : "Arm")
        widthTemplate: "Run now"
        shortcut: "Ctrl+Enter"
        onClicked: {
          root.leaveEditor()
          root.submit()
        }
      }
    }
  }

  Item {
    id: leftColumn
    anchors.left: parent.left
    anchors.top: parent.top
    anchors.bottom: bottomBand.top
    anchors.bottomMargin: root.gap
    width: parent.width - rightColumn.width - root.gap

    Text {
      id: promptLabel
      anchors.left: parent.left
      anchors.top: parent.top
      textFormat: Text.PlainText
      text: "Prompt"
      color: root.active && root.section === 0 ? root.theme.accentInk : root.theme.soft
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.label
      font.bold: true
      font.letterSpacing: root.theme.type.tracking

      MouseArea {
        anchors.fill: parent
        onClicked: {
          root.section = 0
          editor.focusEditor()
        }
      }
    }

    PromptEditor {
      id: editor
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.top: promptLabel.bottom
      anchors.topMargin: root.labelGap
      anchors.bottom: sendToLabel.top
      anchors.bottomMargin: root.gap
      theme: root.theme
      hasCursor: root.active && root.section === 0 && !editor.editing
      maxBytes: root.service && root.service.caps && typeof root.service.caps.promptBytes === "number"
        ? root.service.caps.promptBytes : 65536
      problemText: root.piSlashPrompt ? Compose.GATE_MESSAGES.pi_slash_prompt : ""
      onEditingChanged: if (editor.editing) root.section = 0
      onSubmitRequested: root.submit()
      onEscapePressed: root.leaveEditor()
      onGlobalKey: function (event) { root.editorKey(event) }
      onCapRefused: root.noticeRequested("The prompt is longer than 64 KiB.", "warn", null)
    }

    Text {
      id: sendToLabel
      anchors.left: parent.left
      anchors.bottom: sendTo.top
      anchors.bottomMargin: root.labelGap
      textFormat: Text.PlainText
      text: "Send to"
      color: root.active && root.section === 1 ? root.theme.accentInk : root.theme.soft
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.label
      font.bold: true
      font.letterSpacing: root.theme.type.tracking

      MouseArea {
        anchors.fill: parent
        onClicked: root.setSection(1)
      }
    }

    SendToSection {
      id: sendTo
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      height: sendTo.implicitHeight
      theme: root.theme
      harness: root.harness
      target: root.target
      agents: root.agents
      hasCursor: root.active && root.section === 1
      home: root.home
      nowMs: root.nowMs
      geminiRan: root.geminiRan
      sessionKnown: root.hasSession || (!!root._lastSession && root._lastSession.harness === root.harness)
      nonGitWarning: root.nonGitWarning
      armHint: root.armHint
      onFocusRequested: root.setSection(1)
      onHarnessPicked: function (harness) { root.setHarness(harness) }
      onPickSessionRequested: root.openSessionSheet()
      onModeRequested: function (mode) { root.setMode(mode) }
      // A chip whose agent cannot run as it stands: the sheet says how to sign it in.
      onSignInRequested: function (harness) { root.sheetRequested("signin", { harness: harness }) }
      onAllowNonGitRequested: {
        root._draft = root.withKey(root._draft, "target", root.withKey(root.target, "allowNonGit", true))
        root._touched = true
      }
    }
  }

  Item {
    id: rightColumn
    anchors.right: parent.right
    anchors.top: parent.top
    anchors.bottom: bottomBand.top
    anchors.bottomMargin: root.gap
    width: Math.min(Style.space(420), parent.width * 0.42)

    Column {
      id: formColumn
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.top: parent.top
      spacing: root.labelGap

      Text {
        textFormat: Text.PlainText
        text: "When"
        color: root.active && root.section === 2 ? root.theme.accentInk : root.theme.soft
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.label
        font.bold: true
        font.letterSpacing: root.theme.type.tracking

        MouseArea {
          anchors.fill: parent
          onClicked: root.setSection(2)
        }
      }

      WhenControl {
        id: whenControl
        width: parent.width
        theme: root.theme
        trigger: root.trigger
        nowMs: root.nowMs
        providers: root.providers
        harness: root.harness
        provider: root.provider
        model: root.model
        billing: root.billing
        settings: root.settings
        caps: root.service && root.service.caps ? root.service.caps : ({})
        hasCursor: root.active && root.section === 2
        helperNote: root.helperFireNote
        armHint: root.armHint
        onFocusRequested: root.setSection(2)
        onTriggerEdited: function (trigger) { root.applyPatch({ trigger: trigger }) }
      }

      Item {
        width: 1
        height: root.gap - root.labelGap
      }

      Text {
        textFormat: Text.PlainText
        text: "Options"
        color: root.active && root.section === 3 ? root.theme.accentInk : root.theme.soft
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.label
        font.bold: true
        font.letterSpacing: root.theme.type.tracking

        MouseArea {
          anchors.fill: parent
          onClicked: root.setSection(3)
        }
      }

      OptionsSection {
        id: options
        width: parent.width
        theme: root.theme
        level: root.level
        limits: root._draft.limits || ({})
        model: root._draft.model === undefined ? null : root._draft.model
        harness: root.harness
        levels: root.levels
        caps: root.service && root.service.caps ? root.service.caps : ({})
        hasCursor: root.active && root.section === 3
        armHint: root.armHint
        allowPaid: root.allowPaid
        gate: root.notesGate
        gateCurrent: root.gateCurrent
        provider: root.provider
        models: root.models
        billing: root.billing
        nowMs: root.nowMs
        onFocusRequested: root.setSection(3)
        onEdited: function (patch) {
          var d = root.copy(root._draft)
          for (var k in patch) d[k] = patch[k]
          var notes = []
          if (patch.hasOwnProperty("model") || patch.hasOwnProperty("provider")) d = root.fixTrigger(d, notes)
          root._draft = d
          root._touched = true
          if (notes.length > 0) root.noticeRequested(notes.join(" "), "info", null)
        }
        onModelSheetRequested: function (query) { root.openModelSheet(query) }
      }
    }

    Column {
      id: footColumn
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.bottom: parent.bottom
      spacing: Style.space(3)

      // Why Arm is off, when the gate says no for a reason other than paid usage.
      Row {
        id: gateRow
        width: parent.width
        spacing: Style.space(6)
        visible: root.gateLine !== ""

        Text {
          id: gateGlyph
          textFormat: Text.PlainText
          text: "󰀨"   // md-alert_circle U+F0028
          color: root.theme.warnInk
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
        }

        Text {
          width: parent.width - gateGlyph.width - parent.spacing
          textFormat: Text.PlainText
          text: root.gateLine
          color: root.theme.warnInk
          wrapMode: Text.WordWrap
          maximumLineCount: 2
          elide: Text.ElideRight
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
        }
      }

      Row {
        width: parent.width
        spacing: Style.space(6)
        // Room permitting: a tall Options section keeps its rows instead.
        visible: formColumn.y + formColumn.height + root.gap < footColumn.y + (gateRow.visible ? gateRow.height + footColumn.spacing : 0)

        Text {
          id: lockGlyph
          textFormat: Text.PlainText
          text: "󰌾"   // md-lock U+F033E
          color: root.theme.okInk
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
        }

        // One sentence for both facts; the Queue's empty state and the README carry the numbers.
        Text {
          width: parent.width - lockGlyph.width - parent.spacing
          textFormat: Text.PlainText
          text: "Fires while the screen is locked. If the computer sleeps, it runs when it wakes."
          color: root.theme.readable
          wrapMode: Text.WordWrap
          maximumLineCount: 2
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
        }
      }
    }
  }
}
