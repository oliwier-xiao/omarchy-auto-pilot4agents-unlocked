import QtQuick
import Quickshell
import Quickshell.Io
import "lib/Edition.js" as Edition
import "lib/Model.js" as Model

// The one long-lived half of the plugin. Scheduling state lives here and nowhere
// else: the shell creates one service, while bar widgets and panels exist once per
// monitor and come and go with hot reloads.
//
// Nothing in this file schedules anything by itself. Every job fires from a
// transient systemd user timer that the helper creates, so a job still runs when the
// shell is reloaded, the screen is locked or this file is not loaded at all. What
// lives here is a mirror of the job store (read through the `list` verb), a queue of
// requests to the helper, and the clock the countdowns are drawn from.
//
// Every helper call is one BoundedProcess: absolute argv, a cleared environment,
// a byte cap on the answer and a deadline. Reads may run side by side (at most one
// per verb and arguments); changes run one at a time, in order.
Item {
  id: root

  // Injected by the shell's service loader after creation.
  property var shell: null
  property var manifest: null
  property string omarchyPath: ""

  // Pushed by BarWidget from its settings.
  property string barLabel: "Next run"

  // ---------------------------------------------------------------- public state

  readonly property bool ready: root._editionLoaded && root._listLoaded
  readonly property string setupProblem: root._editionError !== ""
    ? root._editionError
    : (root._listLoaded ? "" : root._listError)
  readonly property var edition: root._edition !== null ? root._edition : root._fallbackEdition
  readonly property var levels: root._levels
  readonly property var harnesses: root._harnesses
  readonly property var caps: root._caps
  readonly property var jobs: root._jobs
  readonly property var jobsById: root._jobsById
  readonly property var listMeta: root._listMeta
  readonly property var usage: root._usage
  // Usage v2: every window source the helper read (records and computed resets).
  readonly property var providers: root._usage && Array.isArray(root._usage.providers) ? root._usage.providers : []
  // The fixed message of the last failed usage read, "" once a read succeeds.
  readonly property string usageError: root._usageError
  readonly property var agents: root._agents
  readonly property var sessions: root._sessions
  readonly property var settings: root._settings
  // harness -> Models answer (a failed read stores {ok: false, models: [], reason}).
  readonly property var models: root._models
  // Model.dayKey -> Timeline answer, at most seven days, the oldest read dropped first.
  readonly property var timeline: root._timeline
  readonly property double nowMs: root._nowMs

  readonly property bool loadingJobs: (root._inflightVerbs["list"] || 0) > 0
  readonly property bool loadingUsage: (root._inflightVerbs["usage"] || 0) > 0
  readonly property bool loadingAgents: (root._inflightVerbs["agents"] || 0) > 0
  readonly property bool loadingSessions: (root._inflightVerbs["sessions"] || 0) > 0
  readonly property bool loadingSettings: (root._inflightVerbs["settings-get"] || 0) > 0
  readonly property bool loadingModels: (root._inflightVerbs["models"] || 0) > 0
  readonly property bool loadingTimeline: (root._inflightVerbs["timeline"] || 0) > 0
  readonly property bool busy: root._writeInflight !== null || root._writeQueue.length > 0
  readonly property var lastError: root._lastError

  readonly property var nextJob: {
    var best = null
    for (var i = 0; i < root._jobs.length; i++) {
      var j = root._jobs[i]
      if (j.state.status !== "armed" || typeof j.state.fireAt !== "number") continue
      if (best === null || j.state.fireAt < best.state.fireAt) best = j
    }
    return best
  }
  readonly property int armedCount: root._counts.armed
  readonly property int runningCount: root._counts.running
  readonly property int attentionCount: root._counts.attention
  readonly property bool anyRunning: root.runningCount > 0
  readonly property int viewers: root._viewers
  // Epoch seconds: when the panel was last looked at. settings.lastSeenAt survives a
  // shell restart; opening the panel writes it (at most once a minute) and closing it
  // moves the in-memory mark, so a failure shown while the panel is open still counts
  // as new for the default view of that opening. With nothing stored, failures from
  // the last day are offered again rather than all of them or none.
  readonly property double lastSeenAt: {
    var stored = root._settings && typeof root._settings.lastSeenAt === "number" && isFinite(root._settings.lastSeenAt)
      ? root._settings.lastSeenAt : null
    if (stored === null && root._seenLocal <= 0) return root._bootSeen
    return Math.max(stored === null ? 0 : stored, root._seenLocal)
  }
  // v1 name, kept for callers that still read it.
  readonly property double attentionSeenAt: root.lastSeenAt
  readonly property int draftCount: root._counts.drafts
  readonly property int doneSinceSeen: root._counts.done
  readonly property int problemCount: root._counts.problems
  readonly property int authProblemCount: root._counts.auth
  // The bar widget's state (Model.barState): glyph, word, tone and sentence.
  readonly property var barState: Model.barState({
    ready: root.ready,
    setupProblem: root.setupProblem,
    problems: root._counts.problems,
    failures: root._counts.failures,
    authProblems: root._counts.auth,
    running: root._counts.running,
    runningSinceMs: root._counts.runningSinceMs,
    nextFireAtMs: root.nextJob && typeof root.nextJob.state.fireAt === "number" ? root.nextJob.state.fireAt * 1000 : null,
    nowMs: root._nowMs,
    doneSinceSeen: root._counts.done,
    drafts: root._counts.drafts
  })

  readonly property string pluginDir: {
    var raw = String(Qt.resolvedUrl(".")).replace(/^file:\/\//, "").replace(/\/$/, "")
    try { return decodeURIComponent(raw) } catch (e) { return raw }
  }
  readonly property string helperPath: root.pluginDir + "/" + Edition.HELPER_REL

  signal notice(string text, string kind)
  signal jobsUpdated()

  // ---------------------------------------------------------------- private state

  property bool _editionLoaded: false
  property bool _listLoaded: false
  property bool _agentsLoaded: false
  property double _agentsAtMs: 0
  property string _editionError: ""
  property string _listError: ""
  property var _edition: null
  property var _levels: []
  property var _harnesses: []
  property var _caps: root._defaultCaps
  property var _jobs: []
  property var _jobsById: ({})
  property var _listMeta: ({ killSwitch: false, enabledInShell: null, linger: null, reconciledAt: null, nowMs: null })
  property var _usage: null
  property string _usageError: ""
  property var _agents: ({})
  property var _sessions: ({})
  property var _settings: root._defaultSettings
  property var _models: ({})
  property var _timeline: ({})
  property var _timelineOrder: []
  property double _nowMs: Date.now()
  property var _lastError: null
  property int _viewers: 0
  readonly property double _bootSeen: Math.floor(Date.now() / 1000) - 86400
  property double _seenLocal: 0
  property double _markSeenAtMs: 0
  property int _clockDelay: 60000
  property int _reconcileWaiting: 0
  property var _reconcileJoiners: []
  property double _reconciledAtMs: 0
  property double _dueRefreshAt: 0

  property var _readInflight: ({})
  property var _readPending: ({})
  property var _inflightVerbs: ({})
  property var _writeInflight: null
  property var _writeQueue: []

  readonly property var _fallbackEdition: ({
    pluginId: Edition.PLUGIN_ID,
    displayName: Edition.DISPLAY_NAME,
    unitPrefix: Edition.UNIT_PREFIX,
    widgetIpcTarget: Edition.WIDGET_IPC_TARGET,
    serviceIpcTarget: Edition.SERVICE_IPC_TARGET,
    notifyAppName: Edition.NOTIFY_APP_NAME,
    schemaVersion: 1,
    pluginDir: root.pluginDir,
    version: root.manifest && typeof root.manifest.version === "string" ? root.manifest.version : ""
  })

  // The same numbers the `edition` verb answers with, so a field can be drawn before
  // that answer lands. The helper still enforces its own.
  readonly property var _defaultCaps: ({
    promptBytes: 65536, labelChars: 40, titleChars: 120, maxTurns: [1, 200], budgetUsd: [0.1, 100.0],
    runtimeSec: [300, 14400], marginSec: [60, 540], horizonSec: 691200, uiMinLeadSec: 60,
    shiftMaxIds: 50, shiftRangeSec: 604800
  })

  readonly property var _defaultSettings: ({
    schemaVersion: 1, defaultHarness: "claude", defaultLevel: "plan", resetMarginSec: 120,
    eveningTime: "23:00", morningTime: "07:00", notify: "all", motion: "full",
    defaultAllowPaid: false, limitsShown: "auto", lastSeenAt: null
  })

  // History events that change nothing the user saw: prune deleting an old prompt a day
  // later, a CLI path move, a result that arrived after a disarm.
  readonly property var _housekeepingEvents: ["prompt_deleted", "cli_changed", "late_result"]

  // When a job last changed in a way the user would want to see: the helper's statusAt
  // (its last status event) or the end of its last run, whichever is later. A helper
  // without statusAt falls back to the last event, the run end or the last update, where
  // housekeeping events never count, so a prompt deleted a day after the user looked does
  // not turn the bar red again.
  function _eventAt(j) {
    var st = j.state || {}
    var ended = st.lastRun && typeof st.lastRun.endedAt === "number" ? st.lastRun.endedAt : 0
    if (typeof st.statusAt === "number" && isFinite(st.statusAt)) return Math.max(st.statusAt, ended)
    var at = Math.max(typeof j.updatedAt === "number" ? j.updatedAt : 0, ended)
    if (st.lastEvent && typeof st.lastEvent.at === "number"
        && root._housekeepingEvents.indexOf(String(st.lastEvent.event)) < 0) at = Math.max(at, st.lastEvent.at)
    return at
  }

  readonly property var _counts: {
    var armed = 0, running = 0, attention = 0, drafts = 0, done = 0, problems = 0, failures = 0, auth = 0
    var runningSinceMs = 0
    var seen = root.lastSeenAt
    for (var i = 0; i < root._jobs.length; i++) {
      var j = root._jobs[i]
      var st = j.state.status
      var run = j.state.lastRun
      if (st === "armed") {
        armed++
        var agent = root._agents.hasOwnProperty(String(j.harness)) ? root._agents[j.harness] : null
        if (agent && agent.loggedIn === false) auth++
      } else if (st === "running") {
        running++
        var started = run && typeof run.startedAt === "number" ? run.startedAt * 1000 : 0
        if (started > 0 && (runningSinceMs === 0 || started < runningSinceMs)) runningSinceMs = started
      } else if (st === "draft") drafts++
      if (Model.ATTENTION_STATUSES.indexOf(st) >= 0) attention++
      else if (Model.ENDED_ATTENTION_STATUSES.indexOf(st) >= 0) {
        var ended = run && typeof run.endedAt === "number" ? run.endedAt : j.updatedAt
        if (typeof ended === "number" && ended > seen) attention++
      }
      if (Model.PROBLEM_STATUSES.indexOf(st) >= 0 && root._eventAt(j) > seen) {
        problems++
        if (Model.FAILURE_STATUSES.indexOf(st) >= 0) failures++
      }
      if (run && run.outcome === "done" && typeof run.endedAt === "number" && run.endedAt > seen) done++
    }
    return { armed: armed, running: running, attention: attention, drafts: drafts, done: done,
             problems: problems, failures: failures, auth: auth, runningSinceMs: runningSinceMs }
  }

  // Table 2.4 of the contract: QML deadline = helper deadline + 5 s; answer cap = the
  // helper's own output cap. `lane` read runs concurrently, write runs in order.
  // `mutates` makes a success refresh the job list.
  readonly property var _verbs: ({
    "edition":      { ms: 7000,  cap: 65536,  stdin: false, lane: "read",  mutates: false },
    "list":         { ms: 9000,  cap: 1048576, stdin: false, lane: "read",  mutates: false },
    "job-get":      { ms: 9000,  cap: 524288, stdin: false, lane: "read",  mutates: false },
    "job-create":   { ms: 13000, cap: 65536,  stdin: true,  lane: "write", mutates: true },
    "job-update":   { ms: 30000, cap: 65536,  stdin: true,  lane: "write", mutates: true },
    "job-delete":   { ms: 11000, cap: 65536,  stdin: false, lane: "write", mutates: true },
    "preview":      { ms: 17000, cap: 65536,  stdin: true,  lane: "read",  mutates: false },
    "arm":          { ms: 45000, cap: 65536,  stdin: false, lane: "write", mutates: true },
    "run-now":      { ms: 45000, cap: 65536,  stdin: false, lane: "write", mutates: true },
    "disarm":       { ms: 30000, cap: 65536,  stdin: false, lane: "write", mutates: true },
    "cancel-all":   { ms: 95000, cap: 65536,  stdin: false, lane: "write", mutates: true },
    "reschedule":   { ms: 30000, cap: 65536,  stdin: false, lane: "write", mutates: true },
    "swap":         { ms: 45000, cap: 65536,  stdin: false, lane: "write", mutates: true },
    "shift":        { ms: 90000, cap: 65536,  stdin: false, lane: "write", mutates: true },
    "reconcile":    { ms: 65000, cap: 65536,  stdin: false, lane: "write", mutates: true },
    "settings-get": { ms: 7000,  cap: 16384,  stdin: false, lane: "read",  mutates: false },
    "settings-set": { ms: 9000,  cap: 16384,  stdin: true,  lane: "write", mutates: false },
    "copy-resume":  { ms: 8000,  cap: 16384,  stdin: false, lane: "write", mutates: false },
    "sessions":     { ms: 13000, cap: 921600, stdin: false, lane: "read",  mutates: false },
    "usage":        { ms: 8000,  cap: 131072, stdin: false, lane: "read",  mutates: false },
    "agents":       { ms: 35000, cap: 65536,  stdin: false, lane: "read",  mutates: false },
    "models":       { ms: 30000, cap: 262144, stdin: false, lane: "read",  mutates: false },
    "timeline":     { ms: 9000,  cap: 262144, stdin: false, lane: "read",  mutates: false }
  })

  readonly property var _qmlMessages: ({
    "helper_failed": "The helper did not answer.",
    "helper_timeout": "The helper did not finish in time.",
    "helper_output_too_large": "The helper answered with too much data.",
    "busy_queue": "Too many changes are waiting.",
    "not_ready": Edition.DISPLAY_NAME + " is still starting.",
    "superseded": "A newer preview replaced this one.",
    "bad_args": "The request was not understood."
  })

  readonly property int _writeQueueMax: 16

  // ---------------------------------------------------------------- public functions

  function refresh() { root._refresh(null) }

  function refreshUsage() {
    root._request("usage", [], null, function (res) {
      if (res.ok === true) {
        root._usage = res
        root._usageError = ""
      } else {
        root._usageError = typeof res.message === "string" && res.message !== "" ? res.message : root._qmlMessages["helper_failed"]
        root._noteReadError(res)
      }
    })
  }

  function refreshAgents(checkLogin) {
    root._request("agents", checkLogin === true ? ["--login"] : [], null, function (res) {
      if (res.ok !== true) { root._noteReadError(res); return }
      var map = {}
      var list = Array.isArray(res.agents) ? res.agents : []
      for (var i = 0; i < list.length; i++) {
        var a = list[i]
        if (a && typeof a.harness === "string" && Edition.HARNESS_IDS.indexOf(a.harness) >= 0) map[a.harness] = a
      }
      root._agents = map
      root._agentsLoaded = true
      root._agentsAtMs = Date.now()
    })
  }

  // `cwd` (optional, absolute) scopes Pi's sessions to one folder; the answer is stored
  // under the same key either way.
  function loadSessions(harness, cwd) {
    var h = typeof harness === "string" ? harness : ""
    if (h !== "" && !root._knownHarness(h)) return
    var key = h === "" ? "all" : h
    var args = h === "" ? [] : ["--harness", h]
    if (root._validCwd(cwd)) args = args.concat(["--cwd", cwd])
    root._request("sessions", args, null, function (res) {
      if (res.ok !== true) { root._noteReadError(res); return }
      root._sessions = root._with(root._sessions, key, res)
    })
  }

  // The models an agent offers. A read that fails here (a timeout, a helper failure)
  // is stored as an empty answer with a reason, so a picker never waits forever.
  function loadModels(harness, refresh) {
    var h = typeof harness === "string" ? harness : ""
    if (!root._knownHarness(h)) return
    root._request("models", ["--harness", h].concat(refresh === true ? ["--refresh"] : []), null, function (res) {
      if (res.ok !== true) {
        if (res.code === "superseded") return
        root._noteReadError(res)
        res = { ok: false, harness: h, models: [], reason: res.code === "helper_timeout" ? "timeout" : "failed",
                code: res.code, message: res.message }
      }
      root._models = root._with(root._models, h, res)
    })
  }

  // One local day, midnight to the next midnight, for History's day timeline. Days
  // outside the helper's window (15 days back, 9 ahead) are not asked for.
  function loadTimeline(dayStartMs) {
    var start = Model.localMidnight(Number(dayStartMs))
    if (!isFinite(start)) return false
    var end = new Date(start)
    end.setDate(end.getDate() + 1)
    var from = Math.floor(start / 1000)
    var to = Math.floor(end.getTime() / 1000)
    var nowSec = Math.floor(Date.now() / 1000)
    if (from < nowSec - 15 * 86400 || to > nowSec + 9 * 86400 || to <= from) return false
    var key = Model.dayKey(start)
    root._request("timeline", ["--from", String(from), "--to", String(to)], null, function (res) {
      if (res.ok !== true) { root._noteReadError(res); return }
      var order = root._timelineOrder.filter(function (k) { return k !== key }).concat([key])
      var next = root._with(root._timeline, key, res)
      while (order.length > 7) { next = root._without(next, order[0]); order = order.slice(1) }
      root._timelineOrder = order
      root._timeline = next
    })
    return true
  }

  // Opening the panel counts as looking: attention and success states clear. One
  // settings write a minute at most; the in-memory mark moves once the write answers,
  // after the panel has chosen its view for this opening.
  function markSeen() {
    var now = Date.now()
    if (!root.ready) return false
    if (root._markSeenAtMs > 0 && now - root._markSeenAtMs < 60000) return false
    root._markSeenAtMs = now
    var sec = Math.floor(now / 1000)
    root.setSettings({ lastSeenAt: sec }, function (res) {
      root._seenLocal = Math.max(root._seenLocal, sec)
    })
    return true
  }

  // "auto", or up to 32 source ids in header order.
  function setLimitsShown(value, cb) {
    var ok = value === "auto"
    if (!ok && Array.isArray(value) && value.length <= 32) {
      ok = true
      var seen = {}
      for (var i = 0; i < value.length && ok; i++) {
        if (typeof value[i] !== "string" || !/^[a-z0-9][a-z0-9_-]{0,63}$/.test(value[i]) || seen[value[i]] === true) ok = false
        else seen[value[i]] = true
      }
    }
    if (!ok) { root._local(cb, "bad_args"); return }
    root.setSettings({ limitsShown: value }, cb)
  }

  function getJob(id, cb) {
    if (!root._validId(id)) { root._local(cb, "bad_args"); return }
    root._request("job-get", [id], null, root._wrap(cb, typeof cb === "function"))
  }

  // Latest call wins: an earlier callback still waiting gets `superseded`.
  function preview(draft, cb) {
    root._request("preview", [], root._draftPayload(draft, true), root._wrap(cb, typeof cb === "function"))
  }

  function createAndArm(draft, cb) {
    if (!root.ready) { root._local(cb, "not_ready"); return }
    var id = draft && typeof draft.id === "string" ? draft.id : ""
    if (id !== "" && !root._validId(id)) { root._local(cb, "bad_args"); return }
    var quiet = typeof cb === "function"
    root._request(id !== "" ? "job-update" : "job-create", id !== "" ? [id] : [], root._draftPayload(draft, false),
      root._wrap(function (res) {
        if (res.ok !== true) { if (quiet) cb(res); return }
        var newId = typeof res.id === "string" ? res.id : id
        if (!root._validId(newId) || !root._validDigest(res.digest)) {
          root._finishLocal(cb, "helper_failed", newId)
          return
        }
        // Straight after the save, ahead of anything else waiting, so nothing can
        // change the job between the digest it returned and the arm that uses it.
        root._request("arm", [newId, "--digest", res.digest], null, root._wrap(function (armRes) {
          if (quiet) cb(root._with(armRes, "id", newId))
        }, quiet), true)
      }, quiet))
  }

  function saveDraft(draft, cb) {
    if (!root.ready) { root._local(cb, "not_ready"); return }
    var id = draft && typeof draft.id === "string" ? draft.id : ""
    if (id !== "" && !root._validId(id)) { root._local(cb, "bad_args"); return }
    root._request(id !== "" ? "job-update" : "job-create", id !== "" ? [id] : [], root._draftPayload(draft, false),
      root._wrap(cb, typeof cb === "function"))
  }

  function arm(id, digest, cb) { root._digestVerb("arm", id, digest, cb) }

  function runNow(id, digest, cb) { root._digestVerb("run-now", id, digest, cb) }

  function disarm(id, cb) { root._idVerb("disarm", id, cb) }

  function deleteJob(id, cb) { root._idVerb("job-delete", id, cb) }

  // Not gated on `ready`: this is the way out, and it has to work when nothing else does.
  function cancelAll(cb) {
    root._request("cancel-all", [], null, root._wrap(cb, typeof cb === "function"))
  }

  function reschedule(id, epochSec, cb) {
    if (!root.ready) { root._local(cb, "not_ready"); return }
    var epoch = Math.round(Number(epochSec))
    if (!root._validId(id) || !/^[0-9]{10}$/.test(String(epoch))) { root._local(cb, "bad_args"); return }
    root._request("reschedule", [id, String(epoch)], null, root._wrap(cb, typeof cb === "function"))
  }

  function swap(a, b, cb) {
    if (!root.ready) { root._local(cb, "not_ready"); return }
    if (!root._validId(a) || !root._validId(b) || a === b) { root._local(cb, "bad_args"); return }
    root._request("swap", [a, b], null, root._wrap(cb, typeof cb === "function"))
  }

  function shift(ids, deltaSec, cb) {
    if (!root.ready) { root._local(cb, "not_ready"); return }
    var sec = Math.round(Number(deltaSec))
    var list = Array.isArray(ids) ? ids : []
    var seen = {}
    var ok = list.length >= 1 && list.length <= 50 && sec !== 0 && /^-?[0-9]{1,7}$/.test(String(sec))
    for (var i = 0; ok && i < list.length; i++) {
      if (!root._validId(list[i]) || seen[list[i]] === true) ok = false
      else seen[list[i]] = true
    }
    if (!ok) { root._local(cb, "bad_args"); return }
    root._request("shift", ["--by", String(sec)].concat(list), null, root._wrap(cb, typeof cb === "function"))
  }

  function setSettings(obj, cb) {
    if (!root.ready) { root._local(cb, "not_ready"); return }
    var quiet = typeof cb === "function"
    var partial = obj && typeof obj === "object" && !Array.isArray(obj) ? obj : {}
    root._request("settings-set", [], partial, root._wrap(function (res) {
      if (res.ok === true && res.settings && typeof res.settings === "object") root._settings = res.settings
      if (quiet) cb(res)
    }, quiet))
  }

  // The helper hands back the command text; the clipboard is set here, so no
  // clipboard tool is started and nothing outlives the call.
  function copyResume(id, cb) {
    if (!root.ready) { root._local(cb, "not_ready"); return }
    if (!root._validId(id)) { root._local(cb, "bad_args"); return }
    var quiet = typeof cb === "function"
    root._request("copy-resume", [id], null, root._wrap(function (res) {
      if (res.ok === true && typeof res.command === "string" && res.command !== "") {
        Quickshell.clipboardText = res.command
        root.notice("Resume command copied.", "ok")
      }
      if (quiet) cb(res)
    }, quiet))
  }

  function reconcileNow(cb) {
    var quiet = typeof cb === "function"
    // A reconcile already waiting will read the same store; a second one adds
    // nothing but a queue entry in the serial write lane (and `reconcile` is
    // reachable over IPC). A caller with a callback joins the waiting one and
    // gets its answer.
    if (root._reconcileWaiting > 0) {
      if (quiet) root._reconcileJoiners = root._reconcileJoiners.concat([cb])
      return
    }
    root._reconcileWaiting += 1
    root._request("reconcile", [], null, root._wrap(function (res) {
      root._reconcileWaiting = Math.max(0, root._reconcileWaiting - 1)
      var joiners = root._reconcileJoiners
      root._reconcileJoiners = []
      if (res.ok === true) root._reconciledAtMs = Date.now()
      // A successful reconcile is a change and re-reads the list on its own; a
      // failed one still re-reads, because part of it may have happened.
      if (res.ok !== true) root.refresh()
      if (quiet) cb(res)
      for (var i = 0; i < joiners.length; i++) joiners[i](res)
    }, quiet))
  }

  function viewerOpened() {
    root._viewers = root._viewers + 1
    root._nowMs = Date.now()
    root._armClock()
    // Opening and closing the panel in a row must not stack reconciles in front
    // of the user's next change; the 5-minute timer and every run reconcile too.
    if (!(Date.now() - root._reconciledAtMs < 60000)) root.reconcileNow(function () {})
    root.refreshUsage()
    root.refresh()
    // Read again at most once a minute, so a `codex login` done while the shell was
    // running shows the next time the panel opens.
    if (!root._agentsLoaded || Date.now() - root._agentsAtMs > 60000) root.refreshAgents(false)
    root.markSeen()
  }

  function viewerClosed() {
    root._viewers = Math.max(0, root._viewers - 1)
    root._seenLocal = Math.max(root._seenLocal, Math.floor(Date.now() / 1000))
    root._armClock()
  }

  function levelFor(id) {
    for (var i = 0; i < root._levels.length; i++) {
      if (root._levels[i] && root._levels[i].id === id) return root._levels[i]
    }
    return null
  }

  function agentFor(harness) {
    var key = String(harness || "")
    return root._agents.hasOwnProperty(key) ? root._agents[key] : null
  }

  // ---------------------------------------------------------------- loading

  function _boot() {
    root._loadEdition()
    root._loadSettings()
    root.reconcileNow(function () {})
    root.refresh()
    root.refreshUsage()
  }

  function _loadEdition() {
    root._request("edition", [], null, function (res) {
      if (res.ok !== true || !res.edition || typeof res.edition !== "object") {
        if (res.ok !== true) root._noteReadError(res)
        if (!root._editionLoaded) root._editionError = res.ok === true ? root._qmlMessages["helper_failed"] : res.message
        return
      }
      // The closed enum is enforced by the helper; this only makes sure the panel
      // can never offer a level this build does not know.
      var levels = []
      var raw = Array.isArray(res.levels) ? res.levels : []
      for (var i = 0; i < raw.length; i++) {
        if (raw[i] && Edition.LEVEL_IDS.indexOf(raw[i].id) >= 0) levels.push(raw[i])
      }
      root._edition = res.edition
      root._levels = levels
      root._harnesses = Array.isArray(res.harnesses) ? res.harnesses : []
      root._caps = res.caps && typeof res.caps === "object" ? res.caps : root._defaultCaps
      root._editionLoaded = true
      root._editionError = ""
    })
  }

  function _loadSettings() {
    root._request("settings-get", [], null, function (res) {
      if (res.ok === true && res.settings && typeof res.settings === "object") root._settings = res.settings
      else if (res.ok !== true) root._noteReadError(res)
    })
  }

  function _refresh(after) {
    if (!root._editionLoaded && !((root._inflightVerbs["edition"] || 0) > 0)) root._loadEdition()
    root._request("list", [], null, function (res) {
      if (res.ok === true) root._applyList(res)
      else {
        root._noteReadError(res)
        if (!root._listLoaded) root._listError = res.message
      }
      if (typeof after === "function") after(res)
    })
  }

  function _applyList(res) {
    var raw = Array.isArray(res.jobs) ? res.jobs : []
    var list = []
    var byId = {}
    for (var i = 0; i < raw.length; i++) {
      var j = raw[i]
      if (!j || typeof j !== "object" || !root._validId(j.id) || !j.state || typeof j.state !== "object") continue
      list.push(j)
      byId[j.id] = j
    }
    root._jobs = list
    root._jobsById = byId
    root._listMeta = {
      killSwitch: res.killSwitch === true,
      enabledInShell: typeof res.enabledInShell === "boolean" ? res.enabledInShell : null,
      linger: typeof res.linger === "boolean" ? res.linger : null,
      reconciledAt: typeof res.reconciledAt === "number" ? res.reconciledAt : null,
      nowMs: typeof res.nowMs === "number" ? res.nowMs : null
    }
    root._listLoaded = true
    root._listError = ""
    root._nowMs = Date.now()
    root._armClock()
  }

  // ---------------------------------------------------------------- request plumbing

  function _validId(id) { return typeof id === "string" && /^[0-9a-f]{16}$/.test(id) }

  function _validDigest(d) { return typeof d === "string" && /^[0-9a-f]{64}$/.test(d) }

  function _knownHarness(h) {
    return typeof h === "string" && (Edition.HARNESS_IDS.indexOf(h) >= 0 || Model.HARNESS_NAMES.hasOwnProperty(h))
  }

  function _validCwd(cwd) {
    return typeof cwd === "string" && cwd.charAt(0) === "/" && cwd.length <= 4096 && !/[\x00-\x1f\x7f]/.test(cwd)
  }

  function _qmlError(code) {
    return { ok: false, code: code, message: root._qmlMessages[code] || root._qmlMessages["helper_failed"] }
  }

  // A copy of `obj` with one key set. `var` state is replaced, never edited in place.
  function _with(obj, key, value) {
    var next = {}
    if (obj && typeof obj === "object") for (var k in obj) next[k] = obj[k]
    next[key] = value
    return next
  }

  function _without(obj, key) {
    var next = {}
    if (obj && typeof obj === "object") for (var k in obj) if (k !== key) next[k] = obj[k]
    return next
  }

  function _countVerb(verb, delta) {
    root._inflightVerbs = root._with(root._inflightVerbs, verb, Math.max(0, (root._inflightVerbs[verb] || 0) + delta))
  }

  function _noteReadError(res) {
    if (!res || res.ok === true || res.code === "superseded") return
    root._lastError = { code: res.code, message: res.message }
  }

  // Failure handling shared by every call: lastError always, a notice only when
  // nobody else is going to say anything. `quiet` is whether the public caller
  // passed its own callback; `cb` may be an internal step that is not that caller.
  function _wrap(cb, quiet) {
    return function (res) {
      if (res.ok !== true && res.code !== "superseded") {
        root._lastError = { code: res.code, message: res.message }
        if (quiet !== true) root.notice(res.message, "error")
      }
      if (typeof cb === "function") cb(res)
    }
  }

  function _local(cb, code) {
    var res = root._qmlError(code)
    Qt.callLater(function () { root._wrap(cb, typeof cb === "function")(res) })
  }

  function _finishLocal(cb, code, id) {
    var res = root._with(root._qmlError(code), "id", id)
    root._wrap(cb, typeof cb === "function")(res)
  }

  function _idVerb(verb, id, cb) {
    if (!root.ready) { root._local(cb, "not_ready"); return }
    if (!root._validId(id)) { root._local(cb, "bad_args"); return }
    root._request(verb, [id], null, root._wrap(cb, typeof cb === "function"))
  }

  function _digestVerb(verb, id, digest, cb) {
    if (!root.ready) { root._local(cb, "not_ready"); return }
    if (!root._validId(id) || !root._validDigest(digest)) { root._local(cb, "bad_args"); return }
    root._request(verb, [id, "--digest", digest], null, root._wrap(cb, typeof cb === "function"))
  }

  // Only the Draft keys of CONTRACT 4.4 reach the helper, which refuses unknown ones.
  // A preview never carries the prompt: it does not need it.
  function _draftPayload(draft, forPreview) {
    var d = draft && typeof draft === "object" ? draft : {}
    var out = {}
    if (typeof d.label === "string" && d.label !== "") out.label = d.label
    if (d.harness !== undefined) out.harness = d.harness
    if (d.target && typeof d.target === "object") {
      out.target = {}
      var tk = ["mode", "sessionId", "cwd", "allowNonGit"]
      for (var i = 0; i < tk.length; i++) if (d.target[tk[i]] !== undefined) out.target[tk[i]] = d.target[tk[i]]
      if (typeof d.target.sessionPath === "string" && d.target.sessionPath !== "") out.target.sessionPath = d.target.sessionPath
      else if (d.target.sessionPath === null) out.target.sessionPath = null
    }
    if (d.level !== undefined) out.level = d.level
    // Draft v2. Present only when the draft carries them, so a v1 draft reads as before.
    if (typeof d.allowPaid === "boolean") out.allowPaid = d.allowPaid
    if (typeof d.provider === "string" && d.provider !== "") out.provider = d.provider
    else if (d.provider === null) out.provider = null
    if (d.limits && typeof d.limits === "object") {
      out.limits = {}
      var lk = ["maxTurns", "budgetUsd", "runtimeSec"]
      for (var j = 0; j < lk.length; j++) if (d.limits[lk[j]] !== undefined && d.limits[lk[j]] !== null) out.limits[lk[j]] = d.limits[lk[j]]
    }
    if (d.model !== undefined) out.model = d.model
    if (d.trigger && typeof d.trigger === "object") {
      out.trigger = {}
      var rk = ["kind", "fireAt", "delaySec", "marginSec", "weeklyPolicy"]
      for (var k = 0; k < rk.length; k++) if (d.trigger[rk[k]] !== undefined) out.trigger[rk[k]] = d.trigger[rk[k]]
    }
    if (!forPreview) {
      if (typeof d.prompt === "string") out.prompt = d.prompt
      if (typeof d.expectCommandDigest === "string" && d.expectCommandDigest !== "") out.expectCommandDigest = d.expectCommandDigest
    }
    return out
  }

  function _request(verb, args, payload, cb, front) {
    var spec = root._verbs[verb]
    var argv = Array.isArray(args) ? args.map(function (a) { return String(a) }) : []
    var req = {
      verb: verb,
      args: argv,
      payload: payload === undefined ? null : payload,
      cbs: typeof cb === "function" ? [cb] : [],
      key: verb + " " + argv.join(" ")
    }
    if (spec.lane === "read") root._requestRead(req)
    else root._requestWrite(req, front === true)
  }

  function _deliver(req, res) {
    for (var i = 0; i < req.cbs.length; i++) req.cbs[i](res)
  }

  // Reads: one process per key. A read asked for while the same one runs waits for
  // it and then runs once more (the store may have changed meanwhile), with every
  // waiting callback merged. Previews are different: only the newest one matters.
  function _requestRead(req) {
    if (!root._readInflight.hasOwnProperty(req.key)) {
      root._startRead(req)
      return
    }
    var pending = root._readPending.hasOwnProperty(req.key) ? root._readPending[req.key] : null
    if (req.verb === "preview") {
      if (pending) Qt.callLater(function () { root._deliver(pending, root._qmlError("superseded")) })
      root._readPending = root._with(root._readPending, req.key, req)
      return
    }
    var merged = pending
      ? { verb: pending.verb, args: pending.args, payload: pending.payload, cbs: pending.cbs.concat(req.cbs), key: pending.key }
      : req
    root._readPending = root._with(root._readPending, req.key, merged)
  }

  function _startRead(req) {
    root._readInflight = root._with(root._readInflight, req.key, req)
    root._countVerb(req.verb, 1)
    root._spawn(req, function (res) {
      root._readInflight = root._without(root._readInflight, req.key)
      root._countVerb(req.verb, -1)
      var next = root._readPending.hasOwnProperty(req.key) ? root._readPending[req.key] : null
      if (next) root._readPending = root._without(root._readPending, req.key)
      root._deliver(req, next && req.verb === "preview" ? root._qmlError("superseded") : res)
      if (next) root._startRead(next)
    })
  }

  // Changes: strictly one at a time, first in first out. `front` is only for the arm
  // that completes createAndArm.
  function _requestWrite(req, front) {
    if (root._writeInflight === null && root._writeQueue.length === 0) {
      root._startWrite(req)
      return
    }
    if (root._writeQueue.length >= root._writeQueueMax) {
      // Answered on a later turn, like every other answer, so a caller never
      // sees its callback run before its own call has returned.
      Qt.callLater(function () { root._deliver(req, root._qmlError("busy_queue")) })
      return
    }
    root._writeQueue = front ? [req].concat(root._writeQueue) : root._writeQueue.concat([req])
  }

  function _startWrite(req) {
    root._writeInflight = req
    root._countVerb(req.verb, 1)
    root._spawn(req, function (res) {
      root._writeInflight = null
      root._countVerb(req.verb, -1)
      var spec = root._verbs[req.verb]
      if (res.ok === true && spec.mutates) root._refresh(function () { root.jobsUpdated() })
      root._deliver(req, res)
      if (root._writeInflight === null && root._writeQueue.length > 0) {
        var next = root._writeQueue[0]
        root._writeQueue = root._writeQueue.slice(1)
        root._startWrite(next)
      }
    })
  }

  function _helperEnv() {
    var env = { "LANG": "C.UTF-8", "PATH": "/usr/bin", "PYTHONDONTWRITEBYTECODE": "1" }
    var names = ["HOME", "USER", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"]
    for (var i = 0; i < names.length; i++) {
      var v = Quickshell.env(names[i])
      if (v !== undefined && v !== null && String(v) !== "") env[names[i]] = String(v)
    }
    return env
  }

  function _spawn(req, done) {
    var spec = root._verbs[req.verb]
    var proc = processComponent.createObject(root, {
      command: ["/usr/bin/python3", "-I", "-S", "-B", root.helperPath, req.verb].concat(req.args),
      environment: root._helperEnv(),
      deadlineMs: spec.ms,
      maxStdoutBytes: spec.cap + 1024,
      maxStderrBytes: 4096,
      stdinText: spec.stdin ? JSON.stringify(req.payload || {}) + "\n" : ""
    })
    if (!proc) {
      Qt.callLater(function () { done(root._qmlError("helper_failed")) })
      return
    }
    proc.finished.connect(function (exitCode, overflowed, timedOut, stdoutText, stderrText) {
      Qt.callLater(function () {
        var res = root._parse(overflowed, timedOut, stdoutText)
        proc.destroy()
        done(res)
      })
    })
    if (!proc.start()) {
      proc.destroy()
      Qt.callLater(function () { done(root._qmlError("helper_failed")) })
    }
  }

  // One JSON object, or one of the QML-side failures. Nothing the helper printed is
  // shown unless it came in the fixed `message` field.
  function _parse(overflowed, timedOut, text) {
    if (overflowed) return root._qmlError("helper_output_too_large")
    if (timedOut) return root._qmlError("helper_timeout")
    var s = String(text || "").trim()
    if (s === "") return root._qmlError("helper_failed")
    var obj = null
    try { obj = JSON.parse(s) } catch (e) { return root._qmlError("helper_failed") }
    if (!obj || typeof obj !== "object" || Array.isArray(obj) || typeof obj.ok !== "boolean")
      return root._qmlError("helper_failed")
    if (obj.ok === true) return obj
    if (typeof obj.code !== "string" || obj.code === "") return root._qmlError("helper_failed")
    var err = {
      ok: false,
      code: obj.code,
      message: typeof obj.message === "string" && obj.message !== "" ? obj.message.substr(0, 240) : root._qmlMessages["helper_failed"]
    }
    if (typeof obj.field === "string") err.field = obj.field
    if (obj.detail && typeof obj.detail === "object" && !Array.isArray(obj.detail)) err.detail = obj.detail
    return err
  }

  // ---------------------------------------------------------------- clock

  // One single-shot timer that wakes on the next boundary the display needs:
  // seconds while a panel is open and something is running or due within two
  // minutes, whole minutes otherwise.
  function _armClock() {
    var now = Date.now()
    var fast = false
    // The bar counts the last two minutes in seconds ("1m 32s"), panel or not.
    if (root.nextJob && typeof root.nextJob.fireAtMs === "number" && root.nextJob.fireAtMs - now < 120000
        && root.nextJob.fireAtMs - now > -60000) fast = true
    if (root._viewers > 0 && root.anyRunning) fast = true
    root._clockDelay = fast ? 1000 - (now % 1000) + 5 : 60000 - (now % 60000) + 5
    clock.restart()
  }

  // A job whose time has come changes state on disk (armed, then running) and
  // nobody says so until its run ends. Re-read while the soonest job is overdue,
  // at most every 15 s, so the bar does not sit on "<1m".
  function _refreshIfDue() {
    var next = root.nextJob
    if (!next || typeof next.fireAtMs !== "number" || next.fireAtMs > root._nowMs) return
    if (root._nowMs - root._dueRefreshAt < 15000) return
    root._dueRefreshAt = root._nowMs
    root.refresh()
  }

  Component {
    id: processComponent
    BoundedProcess {}
  }

  Timer {
    id: clock
    interval: Model.clampInterval(root._clockDelay)
    repeat: false
    onTriggered: {
      root._nowMs = Date.now()
      root._refreshIfDue()
      root._armClock()
    }
  }

  // The first calls wait for the session to settle and for the shell to inject
  // `shell`; injection restarts the wait.
  Timer {
    id: startTimer
    interval: 1500
    repeat: false
    onTriggered: root._boot()
  }

  // D18: the list every 30 s while a panel is open, every 5 min otherwise. The
  // runner pings `changed` over IPC after each run, so this is the floor, not the
  // way results normally arrive.
  Timer {
    interval: Model.clampInterval(root._viewers > 0 ? 30000 : 300000)
    repeat: true
    running: true
    onTriggered: root.refresh()
  }

  // D3: reconcile every 5 minutes (it also runs at start, on panel open and at the
  // end of every run).
  Timer {
    interval: 300000
    repeat: true
    running: true
    onTriggered: root.reconcileNow(function () {})
  }

  Timer {
    interval: Model.clampInterval(root._viewers > 0 ? 60000 : 300000)
    repeat: true
    running: true
    onTriggered: root.refreshUsage()
  }

  onShellChanged: startTimer.restart()

  Component.onCompleted: {
    startTimer.restart()
    root._armClock()
  }

  // Callable by anything running as this user, so nothing here takes text and
  // nothing changes a job: `changed` and `reconcile` only re-read what is on disk.
  IpcHandler {
    target: Edition.SERVICE_IPC_TARGET

    function changed(): void { root.refresh() }
    function reconcile(): void { root.reconcileNow(null) }
    function status(): string {
      return JSON.stringify({
        ready: root.ready,
        armed: root.armedCount,
        running: root.runningCount,
        next: root.nextJob ? root.nextJob.state.fireAt : null
      })
    }
  }
}
