#!/bin/bash
# tests/q6_qml.test.sh: the Where to run picker (components/SessionSheet.qml) and the No project
# caption in Send to.
#
# 1. qmllint (Qt 6) on SessionSheet.qml and SendToSection.qml with the repository stand-ins.
# 2. One offscreen Qt 6 engine run: SessionSheet and SendToSection against a stand-in service
#    that answers `dirs`, `sessions --in`, `workspace`, `folder` and `find-dirs` the way
#    Service.qml does, and
#    records every call. Every case prints one CASE line; the suite needs all of them, and an
#    engine warning from these files fails it.
#
# No helper, no process, no state folder: nothing here touches the machine.
set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QML=/usr/lib/qt6/bin/qml
QMLLINT=/usr/lib/qt6/bin/qmllint

CASES="places_then_tree_from_home tree_keys_open_close_and_hidden folder_place_reads_its_own_list
no_project_is_made_when_picked home_itself_is_browsable_not_a_working_folder fork_and_the_chosen_session
typed_path_opens_the_tree_down_to_it hints_follow_the_focused_pane sendto_no_project_caption
reopening_starts_clean a_refused_no_project_folder_is_never_used links_skipped_names_and_cut_listings
late_answers_keep_the_cursor_the_place_and_the_highlight no_project_is_made_once_and_late_answers_are_dropped
hover_counts_only_when_the_pointer_moves folders_closed_or_hidden_under_the_cursor typed_paths_and_refusals
the_tree_scrolls_to_a_revealed_folder new_session_button_names_its_folder_and_agent
a_typed_folder_that_is_not_there_suggests_real_ones a_folder_the_helper_refuses_is_said_before_a_pick
a_new_session_waits_for_its_folder_check sendto_starts_with_no_agent_and_says_what_is_missing
typing_words_finds_folders_anywhere the_newest_search_wins_and_an_earlier_open_is_dropped"

export QT_QPA_PLATFORM=offscreen
export QT_FORCE_STDERR_LOGGING=1
export QML_DISABLE_DISK_CACHE=1

pass=0; fail=0
ok() { printf '  ok   %s\n' "$1"; pass=$((pass+1)); }
no() { printf '  FAIL %s\n         %s\n' "$1" "$2"; fail=$((fail+1)); }

T="$(mktemp -d "${TMPDIR:-/tmp}/ap4a-q6.XXXXXX")" || exit 2
trap 'rm -rf "$T"' EXIT INT TERM

echo "=== qmllint (Where to run) ==="
if [ -x "$QMLLINT" ]; then
  for f in components/SessionSheet.qml components/SendToSection.qml; do
    out="$(cd "$REPO" && "$QMLLINT" -I lint "$f" 2>&1)"
    warn="$(printf '%s\n' "$out" | grep -E '^(Warning|Error)' || true)"
    if [ -z "$warn" ]; then ok "qmllint $f"
    else no "qmllint $f" "$(printf '%s' "$warn" | head -4 | tr '\n' ' ')"; fi
  done
else
  echo "  skip qmllint (no $QMLLINT)"
fi

echo "=== Where to run in a Qt 6 engine ==="
if [ ! -x "$QML" ]; then
  echo "  skip the engine run (no Qt 6 qml runtime at $QML)"
  printf '\n%d passed\n' "$pass"
  [ "$fail" -eq 0 ]; exit
fi

W="$T/engine"
mkdir -p "$W"
cp -r "$REPO/lib" "$REPO/components" "$W/"
cp -r "$REPO/lint/qs" "$W/qs"

cat > "$W/Q6Smoke.qml" <<'QML'
import QtQuick
import "components"
import "lib/Edition.js" as Edition
import "lib/Model.js" as Model
import "lib/Tint.js" as Tint

Item {
  id: root
  width: 1100
  height: 1100

  readonly property double nowMs: Date.now()
  readonly property string home: "/home/tester"
  readonly property var caseNames: __CASES__

  property var results: ({})
  property var notices: []
  property var picks: []
  property var steps: []
  property int si: 0
  property int tries: 0

  // ---------------------------------------------------------------- harness

  function check(name, cond, label) {
    var r = root.results[name] || { n: 0, fail: "" }
    r.n++
    if (!cond && r.fail === "") r.fail = String(label)
    var next = {}
    for (var k in root.results) next[k] = root.results[k]
    next[name] = r
    root.results = next
  }
  function ev(k, mods, text) { return { key: k, modifiers: mods || 0, text: text || "", accepted: false } }
  function key(k, mods, text) { return sheet.handleKey(root.ev(k, mods, text)) }
  function typeText(s) { for (var i = 0; i < s.length; i++) sheet.handleKey(root.ev(0, 0, s[i])) }
  function calls(name) { return svc.calls.filter(function (c) { return c.name === name }) }
  function last(name) { var l = root.calls(name); return l.length ? l[l.length - 1] : null }
  function lastNotice() { return root.notices.length ? root.notices[root.notices.length - 1] : { text: "", kind: "" } }
  function lastPick() { return root.picks.length ? root.picks[root.picks.length - 1] : null }
  function leftIndex(kind, path) {
    for (var i = 0; i < sheet.leftRows.length; i++) {
      var r = sheet.leftRows[i]
      if (r.kind === kind && (r.path === path || r.id === path)) return i
    }
    return -1
  }
  function leftRow() { return sheet.leftRows[sheet.leftCursor] || {} }
  function find(item, pred) {
    if (!item) return null
    if (pred(item)) return item
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) {
      var hit = root.find(kids[i], pred)
      if (hit) return hit
    }
    return null
  }
  function findAll(item, pred, out) {
    out = out || []
    if (!item) return out
    if (pred(item)) out.push(item)
    var kids = item.children || []
    for (var i = 0; i < kids.length; i++) root.findAll(kids[i], pred, out)
    return out
  }
  property double mark: 0
  function since(ms) { return Date.now() - root.mark >= ms }
  function step(name, run, until) { root.steps.push({ name: name, run: run, until: until || null }) }
  function fresh(args, hold) {
    svc.hold = hold || {}
    svc.queue = []
    svc.calls = []
    svc.sessions = ({})
    svc.workspaceExists = false
    svc.workspaceRefused = false
    svc.workspace = { path: root.home + "/AutoPilot", exists: false }
    svc.folderStates = ({})
    root.notices = []
    root.picks = []
    sheet.open(args || {})
  }

  // ---------------------------------------------------------------- theme

  readonly property QtObject theme: QtObject {
    id: theme
    readonly property color surface: "#1a1b26"
    readonly property color fg: "#a9b1d6"
    readonly property color accent: "#7aa2f7"
    readonly property color accentInk: Tint.ink("#7aa2f7", "#1a1b26", 4.5)
    readonly property color strong: Qt.rgba(theme.fg.r, theme.fg.g, theme.fg.b, 0.88)
    readonly property color readable: Qt.rgba(theme.fg.r, theme.fg.g, theme.fg.b, 0.77)
    readonly property color soft: Qt.rgba(theme.fg.r, theme.fg.g, theme.fg.b, 0.69)
    readonly property color faint: Qt.rgba(theme.fg.r, theme.fg.g, theme.fg.b, 0.14)
    readonly property real textTarget: 4.5
    readonly property var inks: {
      var out = {}
      for (var i = 0; i < Edition.HARNESS_IDS.length; i++) out[Edition.HARNESS_IDS[i]] = Tint.harnessInk(Edition.HARNESS_IDS[i], "#1a1b26", 4.5)
      return out
    }
    readonly property var marks: {
      var out = {}
      for (var i = 0; i < Edition.HARNESS_IDS.length; i++) out[Edition.HARNESS_IDS[i]] = Tint.harnessMark(Edition.HARNESS_IDS[i], "#1a1b26", 3.0)
      return out
    }
    readonly property var geminiStops: Tint.geminiStops("#1a1b26", 3.0)
    readonly property color okInk: Tint.solved(150, 60, "#1a1b26", 4.5)
    readonly property color warnInk: Tint.solved(38, 85, "#1a1b26", 4.5)
    readonly property color badInk: Tint.ink("#f7768e", "#1a1b26", 4.5)
    property bool reduceMotion: false
    readonly property bool animate: false
    readonly property string fontFamily: "monospace"
    readonly property QtObject type: QtObject {
      readonly property int readout: 24
      readonly property int title: 14
      readonly property int body: 12
      readonly property int label: 11
      readonly property int data: 11
      readonly property int meta: 10
      readonly property int glyph: 11
      readonly property int glyphLarge: 14
      readonly property int brandGlyph: 16
      readonly property real tracking: 0.4
      readonly property real proseLeading: 1.45
      readonly property real measure: 560
      readonly property var digits: ({ "tnum": 1 })
    }
    function harnessInk(id) { return theme.inks[id] || theme.soft }
    function harnessMark(id) { return theme.marks[id] || theme.soft }
    function harnessFill(id) { return Tint.harnessInk(id, "#1a1b26", 3.0) }
    function moveMs(ms) { return theme.reduceMotion ? 0 : ms }
    function fadeMs(ms) { return theme.reduceMotion ? Math.min(ms, 120) : ms }
  }

  // ---------------------------------------------------------------- stand-in service

  function row(harness, id, title, cwd, agoMin, messages) {
    return { harness: harness, id: id, title: title, cwd: cwd, updatedAtMs: root.nowMs - agoMin * 60000, messages: messages,
             canFork: harness !== "gemini" && harness !== "cursor", path: harness === "pi" ? cwd + "/s.jsonl" : null }
  }
  readonly property var recentRows: [
    root.row("claude", "3f2a0c19-0000-4000-8000-000000000001", "Fix the flaky test", "/home/tester/code/api", 120, 41),
    root.row("gemini", "5b2a0c19-0000-4000-8000-000000000002", "Docs pass", "/home/tester/code/api", 240, 9),
    root.row("opencode", "ses_abcdefgh1234", "Regenerate the client", "/home/tester/code/web", 300, 36),
    root.row("claude", "3f2a0c19-0000-4000-8000-000000000003", "Weekly report", "/home/tester/AutoPilot", 600, 7),
    root.row("claude", "3f2a0c19-0000-4000-8000-000000000004", "Started in home", "/home/tester", 700, 3)
  ]
  function entry(name, extra) {
    var e = { name: name, hidden: name.charAt(0) === ".", git: false, own: true, link: false, target: null }
    for (var k in extra) e[k] = extra[k]
    return e
  }
  readonly property var tree: ({
    "/home/tester": [root.entry("AutoPilot"), root.entry("code"), root.entry("many"), root.entry("notes"), root.entry(".config")],
    "/home/tester/many": (function () {
      var out = []
      for (var n = 0; n < 40; n++) out.push(root.entry("d" + (n < 10 ? "0" : "") + n))
      return out
    })(),
    "/home/tester/code": [root.entry("api", { git: true }), root.entry("web", { git: true }),
                          root.entry("out", { link: true, outside: true }), root.entry("vendor", { own: false })],
    "/home/tester/code/api": [],
    "/home/tester/notes": [root.entry("odd")],
    "/home/tester/notes/odd": [root.entry("odd-link", { link: true, outside: false })],
    "/home/tester/.config": [root.entry("app")]
  })
  // dirs answers that say more than their rows: names left out, a cut listing.
  readonly property var dirExtras: ({ "/home/tester/notes/odd": { skipped: 2, truncated: true } })

  QtObject {
    id: svc
    property var calls: []
    property var sessions: ({})
    property var dirs: ({})
    property var workspace: null
    property bool workspaceExists: false
    property bool workspaceRefused: false
    property bool loadingSessions: false
    property bool loadingDirs: false
    property double nowMs: root.nowMs
    property var agents: ({})
    function rec(name, args) { svc.calls = svc.calls.concat([{ name: name, args: args }]) }
    // Held answers wait in the queue until flush(name), so tests can reorder what lands when.
    property var hold: ({})
    property var queue: []
    function later(name, fn) {
      if (svc.hold[name] === true) svc.queue = svc.queue.concat([{ name: name, fn: fn }])
      else fn()
    }
    function flush(name) {
      var run = svc.queue.filter(function (q) { return q.name === name })
      svc.queue = svc.queue.filter(function (q) { return q.name !== name })
      for (var i = 0; i < run.length; i++) run[i].fn()
    }
    function agentFor(h) { return svc.agents[h] || null }
    function answer(rows, extra) {
      var a = { ok: true, harness: null, nowMs: root.nowMs, sessions: rows, counts: {}, truncated: {}, errors: {},
                limitDays: 90, perHarnessCap: 50, cwd: null, needsCwd: ["pi"] }
      for (var k in extra) a[k] = extra[k]
      return a
    }
    function put(obj, key, value) {
      var next = {}
      for (var k in obj) next[k] = obj[k]
      next[key] = value
      return next
    }
    function loadSessions(harness, cwd) {
      svc.rec("loadSessions", [harness, cwd === undefined ? null : cwd])
      svc.later("loadSessions", function () { svc.sessions = svc.put(svc.sessions, "all", svc.answer(root.recentRows, {})) })
    }
    function loadFolderSessions(path) {
      svc.rec("loadFolderSessions", [path])
      var rows = root.recentRows.filter(function (r) { return r.cwd === path })
      if (path === "/home/tester/code/api")
        rows = rows.concat([root.row("codex", "019a0c19-0000-7000-8000-000000000009", "Older than the recent list", path, 60 * 24 * 40, 12)])
      if (path === "/home/tester/code/web")
        rows = rows.concat([root.row("claude", "3f2a0c19-0000-4000-8000-000000000010", "Fresh web session", path, 10, 2)])
      svc.later("loadFolderSessions", function () {
        svc.sessions = svc.put(svc.sessions, "in", svc.answer(rows, { cwd: path, "in": path, needsCwd: [] }))
      })
    }
    function loadDirs(path, hidden) {
      svc.rec("loadDirs", [path, hidden === true])
      svc.later("loadDirs", function () { svc.answerDirs(path, hidden) })
    }
    function answerDirs(path, hidden) {
      var entries = root.tree[path]
      if (entries === undefined) {
        svc.dirs = svc.put(svc.dirs, path, { ok: true, path: path, home: root.home, state: "missing", entries: [], truncated: false })
        return
      }
      // Like the helper: hidden names only when asked for, otherwise just counted.
      var shown = entries.filter(function (e) { return hidden === true || !e.hidden })
      var a = { ok: true, path: path, home: root.home, state: "ok", entries: shown, truncated: false, skipped: 0,
                hidden: hidden === true, hiddenCount: entries.length - entries.filter(function (e) { return !e.hidden }).length }
      var extra = root.dirExtras[path] || {}
      for (var k in extra) a[k] = extra[k]
      svc.dirs = svc.put(svc.dirs, path, a)
    }
    function clearDirs() { svc.rec("clearDirs", []); svc.dirs = ({}) }
    // `ap4a find-dirs`: the folders of the tree whose names hold every word, best first. Its
    // ranking is the helper's (tests/test_folders.py); this only answers the way it does.
    function findDirs(q, known, cb) {
      svc.rec("findDirs", [q, known])
      svc.later("findDirs", function () {
        var words = q.toLowerCase().split(/\s+/).filter(function (w) { return w !== "" })
        var out = []
        for (var dir in root.tree) {
          var list = root.tree[dir]
          for (var i = 0; i < list.length; i++) {
            var e = list[i]
            var name = e.name.toLowerCase()
            if (e.hidden || e.link || !words.every(function (w) { return name.indexOf(w) >= 0 })) continue
            var at = name.indexOf(words[0])
            var path = dir + "/" + e.name
            out.push({ path: path, name: e.name, depth: path.split("/").length - 3, git: e.git === true, own: e.own !== false,
                       score: 1000 - at, marks: [[at, at + words[0].length]] })
          }
        }
        out.sort(function (a, b) { return b.score - a.score || a.depth - b.depth })
        cb({ ok: true, q: q, entries: out, truncated: false, reason: null })
      })
    }
    // `ap4a folder`: a folder of the tree, or one listed in its parent, is there; folderStates
    // says otherwise for the folders a case refuses.
    property var folderStates: ({})
    function checkFolder(path, cb) {
      svc.rec("checkFolder", [path])
      svc.later("checkFolder", function () {
        var known = svc.folderStates[path]
        if (known) {
          cb({ ok: true, path: path, real: path, state: known.state, reason: known.reason })
          return
        }
        var slash = path.lastIndexOf("/")
        var parent = root.tree[path.slice(0, slash)] || []
        var there = root.tree[path] !== undefined || parent.some(function (e) { return e.name === path.slice(slash + 1) })
        cb({ ok: true, path: path, real: path, state: there ? "ok" : "missing", reason: there ? null : "missing" })
      })
    }
    function loadWorkspace(create, cb) {
      svc.rec("loadWorkspace", [create === true])
      svc.later("loadWorkspace", function () { svc.answerWorkspace(create, cb) })
    }
    function answerWorkspace(create, cb) {
      if (svc.workspaceRefused) {
        svc.workspace = { path: root.home + "/AutoPilot", exists: false, refused: true }
        if (typeof cb === "function") cb(create === true ? { ok: false, code: "invalid_cwd" } : { ok: true, exists: false, refused: true })
        return
      }
      if (create === true) svc.workspaceExists = true
      svc.workspace = { path: root.home + "/AutoPilot", exists: svc.workspaceExists, refused: false }
      if (typeof cb === "function") cb({ ok: true, path: root.home + "/AutoPilot", exists: svc.workspaceExists, created: create === true })
    }
  }

  SessionSheet {
    id: sheet
    x: 0
    y: 0
    width: 1100
    height: 640
    theme: root.theme
    service: svc
    home: root.home
    active: true
    onPicked: function (selection) { root.picks = root.picks.concat([selection]) }
    onNoticeRequested: function (text, kind, undo) { root.notices = root.notices.concat([{ text: text, kind: kind }]) }
  }

  SendToSection {
    id: sendTo
    x: 0
    y: 700
    width: 1000
    theme: root.theme
    home: root.home
    nowMs: root.nowMs
  }

  // ---------------------------------------------------------------- cases

  Component.onCompleted: {
    var C = ""

    C = "places_then_tree_from_home"
    step(C, function (C) {
      fresh({})
      check(C, last("clearDirs") !== null && last("loadDirs").args[0] === "/home/tester", "the tree reads the home folder first")
      check(C, last("loadWorkspace").args[0] === false, "asks whether No project exists, without making it")
      check(C, last("loadSessions").args[0] === "" && last("loadSessions").args[1] === "/home/tester", "recent sessions as before")
      var kinds = sheet.leftRows.slice(0, 2).map(function (r) { return r.kind + ":" + r.id })
      check(C, JSON.stringify(kinds) === JSON.stringify(["place:workspace", "place:recent"]), "No project, then All recent sessions: " + JSON.stringify(kinds))
      check(C, sheet.placeKind === "recent" && sheet.focusPane === "list" && root.leftRow().id === "recent", "starts on All recent sessions, cursor in the sessions")
      check(C, sheet.rows.length === 5 && sheet.cursor === 1, "the recent list, cursor on the newest: " + sheet.rows.length)
      var rec = sheet.leftRows.filter(function (r) { return r.kind === "recent" }).map(function (r) { return r.path })
      check(C, JSON.stringify(rec) === JSON.stringify(["/home/tester/code/api", "/home/tester/code/web"]),
            "recent folders leave out home and No project: " + JSON.stringify(rec))
      var root0 = sheet.leftRows[root.leftIndex("dir", "/home/tester")]
      check(C, root0 && root0.depth === 0 && root0.expanded === true && root0.name === "~", "the tree starts open at ~")
      var names = sheet.leftRows.filter(function (r) { return r.kind === "dir" && r.depth === 1 }).map(function (r) { return r.name })
      check(C, JSON.stringify(names) === JSON.stringify(["AutoPilot", "code", "many", "notes"]), "hidden folders stay hidden: " + JSON.stringify(names))
      check(C, root.calls("loadDirs").every(function (c) { return c.args[1] === false }), "hidden names are not asked for")
      check(C, JSON.stringify(sheet.marksFor("/home/tester/code/api")) === JSON.stringify([{ harness: "claude", n: 1 }, { harness: "gemini", n: 1 }]),
            "agent marks per folder: " + JSON.stringify(sheet.marksFor("/home/tester/code/api")))
      check(C, sheet.countBelow("/home/tester/code") === 3, "sessions in the folders below: " + sheet.countBelow("/home/tester/code"))
    })

    C = "tree_keys_open_close_and_hidden"
    step(C, function (C) {
      fresh({})
      key(Qt.Key_Tab)
      check(C, sheet.focusPane === "places", "Tab moves to the folders")
      sheet.leftCursor = root.leftIndex("dir", "/home/tester/code")
      key(Qt.Key_Right)
      check(C, sheet.expanded["/home/tester/code"] === true && last("loadDirs").args[0] === "/home/tester/code", "Right opens and reads it")
      var kids = sheet.leftRows.filter(function (r) { return r.kind === "dir" && r.depth === 2 })
      check(C, kids.map(function (r) { return r.name }).join() === "api,web,out,vendor", "its subfolders: " + kids.map(function (r) { return r.name }).join())
      var out = kids[2]
      check(C, out.outside === true && out.closed === true && out.path === "" && !sheet.leftTakesCursor(out), "a link out of home is shown, never entered")
      check(C, kids[3].own === false, "another user's folder is marked")
      key(Qt.Key_Right)
      check(C, root.leftRow().path === "/home/tester/code/api" && sheet.placePath === "/home/tester/code/api", "Right again steps into the first child, and the place follows")
      key(Qt.Key_Left)
      check(C, root.leftRow().path === "/home/tester/code", "Left on a closed folder goes to its parent")
      key(Qt.Key_Left)
      check(C, sheet.expanded["/home/tester/code"] !== true, "Left on an open folder closes it")
      check(C, root.calls("loadDirs").every(function (c) { return c.args[1] === false }), "no hidden names before Ctrl+H")
      key(Qt.Key_H, Qt.ControlModifier)
      var names = sheet.leftRows.filter(function (r) { return r.kind === "dir" && r.depth === 1 }).map(function (r) { return r.name })
      check(C, sheet.showHidden === true && names.indexOf(".config") === names.length - 1, "Ctrl+H shows hidden folders, last: " + names.join())
      check(C, root.calls("loadDirs").some(function (c) { return c.args[0] === "/home/tester" && c.args[1] === true }), "and only then asks the helper for hidden names")
      key(Qt.Key_H, Qt.ControlModifier)
      check(C, sheet.leftRows.every(function (r) { return r.name !== ".config" }), "Ctrl+H again hides them")
      key(Qt.Key_H, Qt.ControlModifier)
      key(Qt.Key_Down)
      key(Qt.Key_Return)
      check(C, sheet.focusPane === "list", "Enter moves to the sessions")
      check(C, sheet.query === "", "keys in the tree never typed into the filter")
    })

    C = "folder_place_reads_its_own_list"
    step(C, function (C) {
      fresh({})
      sheet.focusPane = "places"
      sheet.selectLeft(root.leftIndex("recent", "/home/tester/code/api"))
      check(C, sheet.placeKind === "folder" && sheet.activePath === "/home/tester/code/api", "the recent folder is the place")
      check(C, sheet.rows.length === 2 && sheet.rows.every(function (r) { return r.cwd === "/home/tester/code/api" }), "its recent rows at once: " + sheet.rows.length)
      check(C, sheet.placeRowIndex === sheet.leftCursor, "one row marks the place")
    }, null)
    step(C, function (C) {
      check(C, last("loadFolderSessions").args[0] === "/home/tester/code/api", "then its own list is read")
      check(C, sheet.inResult !== null && sheet.rows.length === 3, "the folder's own list replaces the recent rows: " + sheet.rows.length)
      check(C, sheet.rows[2].title === "Older than the recent list", "older sessions of the folder appear")
      check(C, sheet.placeFact === "3 sessions", "facts: " + sheet.placeFact)
      check(C, sheet.piLine === "", "a folder's own list needs no Pi line")
      check(C, sheet.marksFor("/home/tester/code/api").length === 3, "its marks count the older session")
      var reads = root.calls("loadFolderSessions").length
      sheet.leftCursor = root.leftIndex("dir", "/home/tester/code")
      check(C, sheet.placePath === "/home/tester/code/api" && root.calls("loadFolderSessions").length === reads, "moving the highlight alone reads nothing")
      key(Qt.Key_Down)
      check(C, sheet.placePath !== "/home/tester/code/api", "a key moves the place")
      sheet.selectLeft(root.leftIndex("recent", "/home/tester/code/api"))
      check(C, sheet.rows.length === 3, "coming back is instant")
      key(Qt.Key_Tab)
      key(Qt.Key_Up)
      key(Qt.Key_Return)
      var p = root.lastPick()
      check(C, p && p.mode === "new" && p.cwd === "/home/tester/code/api" && p.harness === "claude", "row 0 starts a new session here: " + JSON.stringify(p))
    }, function () { return root.last("loadFolderSessions") !== null })

    C = "no_project_is_made_when_picked"
    step(C, function (C) {
      fresh({ harness: "claude" })
      sheet.selectLeft(root.leftIndex("place", "workspace"))
      check(C, sheet.activeKind === "workspace" && sheet.placeTitle === "No project", "the No project place")
      check(C, sheet.placeFact.indexOf("Made when you start a session here") >= 0, "says it is made on first use: " + sheet.placeFact)
      check(C, sheet.rows.length === 1 && sheet.rows[0].title === "Weekly report", "its earlier sessions can be resumed")
      key(Qt.Key_Home)
      key(Qt.Key_Return)
      check(C, root.calls("loadWorkspace").filter(function (c) { return c.args[0] === true }).length === 1, "the folder is made first")
      var p = root.lastPick()
      check(C, p && p.mode === "new" && p.cwd === "/home/tester/AutoPilot" && p.title === "No project", "then the pick: " + JSON.stringify(p))
      fresh({ cwd: "/home/tester/AutoPilot" })
      check(C, sheet.placeKind === "workspace" && root.leftRow().id === "workspace", "a draft in No project opens on it")
    })

    C = "home_itself_is_browsable_not_a_working_folder"
    step(C, function (C) {
      fresh({})
      sheet.focusPane = "places"
      sheet.selectLeft(root.leftIndex("dir", "/home/tester"))
      check(C, sheet.activeKind === "home" && sheet.rows.length === 0, "home lists no sessions to resume")
      check(C, sheet.placeFact === "Agents do not start in your home folder itself. Pick a folder inside it, or No project.", "says why: " + sheet.placeFact)
      key(Qt.Key_N, Qt.ControlModifier)
      check(C, root.picks.length === 0 && lastNotice().text === "Your home folder itself is not allowed. Pick a folder inside it, or No project.", "Ctrl+N refuses: " + lastNotice().text)
      root.mark = Date.now()
    })
    step(C, function (C) {
      check(C, root.calls("loadFolderSessions").length === 0, "home is never read as a folder list, even after the folder timer")
    }, function () { return root.since(450) })

    C = "fork_and_the_chosen_session"
    step(C, function (C) {
      fresh({ harness: "claude", cwd: "/home/tester/code/api", sessionId: "3f2a0c19-0000-4000-8000-000000000001" })
      check(C, sheet.placeKind === "folder" && sheet.placePath === "/home/tester/code/api", "opens on the draft's folder")
      check(C, sheet.filter === "claude" && sheet.rows.length === 1, "the agent filter as before")
      var marks = root.findAll(sheet, function (i) { return i.text !== undefined && String(i.text).indexOf(" now") > 0 && i.visible })
      check(C, marks.length === 1, "the draft's session carries the check: " + marks.length)
      key(Qt.Key_F, Qt.ControlModifier)
      var p = root.lastPick()
      check(C, p && p.mode === "fork" && p.sessionId === "3f2a0c19-0000-4000-8000-000000000001", "Ctrl+F forks: " + JSON.stringify(p))
      key(Qt.Key_Right)
      key(Qt.Key_Right)
      check(C, sheet.filter === "codex" || sheet.filter === "gemini", "Right still steps the agent filter: " + sheet.filter)
      while (sheet.filter !== "gemini") key(Qt.Key_Right)
      key(Qt.Key_F, Qt.ControlModifier)
      check(C, root.picks.length === 1 && lastNotice().text === "Gemini CLI sessions cannot be forked. Enter resumes it, or Ctrl+N starts a new one.", "Gemini cannot fork: " + lastNotice().text)
      key(Qt.Key_Home)
      key(Qt.Key_F, Qt.ControlModifier)
      check(C, lastNotice().text === "Ctrl+F forks the highlighted session. Pick one with ↑/↓ first.", "nothing highlighted")
    })

    C = "typed_path_opens_the_tree_down_to_it"
    step(C, function (C) {
      fresh({})
      root.typeText("~/code/api")
      check(C, sheet.queryPath === "/home/tester/code/api" && sheet.activeKind === "folder" && sheet.cursor === 0, "a typed path is the place, row 0 new")
      check(C, sheet.rows.length === 2, "its recent rows: " + sheet.rows.length)
    })
    step(C, function (C) {
      check(C, sheet.expanded["/home/tester/code"] === true, "the tree opened down to it")
      check(C, root.leftRow().path === "/home/tester/code/api", "the left cursor is on it")
      check(C, sheet.placeRowIndex === sheet.leftCursor, "and marks it")
      check(C, last("loadSessions").args[1] === "/home/tester/code/api", "Pi sessions there, as before")
      key(Qt.Key_U, Qt.ControlModifier, "")
      check(C, sheet.activeKind === "recent" && sheet.rows.length === 5, "clearing the path goes back to the place before")
    }, function () { return root.last("loadFolderSessions") !== null })

    C = "hints_follow_the_focused_pane"
    step(C, function (C) {
      fresh({})
      check(C, sheet.hints.indexOf("Tab folders") > 0 && sheet.hints.indexOf("Ctrl+F fork") > 0 && sheet.hints.indexOf("←/→ agent") > 0, "sessions hints: " + sheet.hints)
      key(Qt.Key_Tab)
      check(C, sheet.hints.indexOf("Tab sessions") > 0 && sheet.hints.indexOf("Ctrl+H hidden folders") > 0 && sheet.hints.indexOf("←/→ close or open") > 0, "folder hints: " + sheet.hints)
      root.typeText("fix")
      check(C, key(Qt.Key_Escape) === true && sheet.query === "", "Esc clears the filter first")
      check(C, key(Qt.Key_Escape) === false, "then returns false so the panel closes the sheet")
    })

    C = "reopening_starts_clean"
    step(C, function (C) {
      fresh({ cwd: "/home/tester/code/api" })
      check(C, root.leftRow().path === "/home/tester/code/api", "the draft's folder has the left cursor")
      fresh({})
      check(C, sheet.placeKind === "recent" && root.leftRow().id === "recent", "a fresh open starts on All recent sessions")
    })
    step(C, function (C) {
      check(C, root.leftRow().id === "recent" && sheet.placeRowIndex === sheet.leftCursor, "and stays there when the tree answers")
      check(C, sheet.expanded["/home/tester/code"] !== true, "the last open's folders are closed again")
    })

    C = "a_refused_no_project_folder_is_never_used"
    step(C, function (C) {
      svc.calls = []
      root.notices = []
      root.picks = []
      svc.workspaceRefused = true
      sheet.open({})
      sheet.selectLeft(root.leftIndex("place", "workspace"))
      var refusal = "~/AutoPilot cannot be used: it has to be a folder of yours, not a link, that nobody else can write. Fix it, or pick another folder."
      check(C, sheet.placeFact === refusal, "says why: " + sheet.placeFact)
      key(Qt.Key_Home)
      key(Qt.Key_Return)
      check(C, root.picks.length === 0 && lastNotice().text === refusal, "Enter refuses with the same sentence")
      check(C, root.calls("loadWorkspace").every(function (c) { return c.args[0] === false }), "nothing is made")
      svc.workspaceRefused = false
    })

    C = "links_skipped_names_and_cut_listings"
    step(C, function (C) {
      fresh({})
      sheet.setExpanded("/home/tester/notes", true)
      sheet.setExpanded("/home/tester/notes/odd", true)
      var link = sheet.leftRows.filter(function (r) { return r.name === "odd-link" })[0]
      check(C, link && link.closed === true && link.outside === false && !sheet.leftTakesCursor(link), "a link the helper could not resolve cleanly stays closed")
      var notes = sheet.leftRows.filter(function (r) { return r.kind === "note" }).map(function (r) { return r.text })
      check(C, notes.indexOf("2 folders with names that cannot be shown are left out.") >= 0, "left-out names are counted: " + notes.join(" | "))
      check(C, notes.indexOf("Not every folder here is listed. Type a path to reach one.") >= 0, "a cut listing says so")
      root.typeText("~/.config/app")
    })
    step(C, function (C) {
      check(C, sheet.showHidden === true && sheet.expanded["/home/tester/.config"] === true, "a typed path inside a hidden folder shows hidden folders")
      check(C, root.leftRow().path === "/home/tester/.config/app", "and reaches it")
    }, function () { return root.leftRow().path === "/home/tester/.config/app" })

    C = "late_answers_keep_the_cursor_the_place_and_the_highlight"
    step(C, function (C) {
      fresh({}, { loadSessions: true })
      sheet.focusPane = "places"
      sheet.selectLeft(root.leftIndex("dir", "/home/tester/code"))
      check(C, sheet.leftRows.every(function (r) { return r.kind !== "recent" }), "no recent folders yet")
      svc.flush("loadSessions")
      check(C, sheet.leftRows.some(function (r) { return r.kind === "recent" }), "recent folders arrive above the tree")
      check(C, root.leftRow().path === "/home/tester/code" && sheet.placeRowIndex === sheet.leftCursor, "the cursor stays on its folder")
      fresh({}, { loadFolderSessions: true })
      sheet.selectLeft(root.leftIndex("recent", "/home/tester/code/web"))
      key(Qt.Key_Down)
      check(C, sheet.cursor === 1 && sheet.rows[0].title === "Regenerate the client", "the recent row is highlighted")
      root.mark = Date.now()
    })
    step(C, function (C) {
      svc.flush("loadFolderSessions")
      check(C, sheet.rows.length === 2 && sheet.rows[0].title === "Fresh web session", "a newer session lands above it")
      check(C, sheet.rows[sheet.cursor - 1].title === "Regenerate the client", "the highlight stays on the same session")
      sheet.selectLeft(root.leftIndex("recent", "/home/tester/code/api"))
      root.mark = Date.now()
    }, function () { return root.since(400) && root.last("loadFolderSessions") !== null })
    step(C, function (C) {
      sheet.selectLeft(root.leftIndex("recent", "/home/tester/code/web"))
      root.mark = Date.now()
    }, function () { return root.since(400) })
    step(C, function (C) {
      var asked = root.calls("loadFolderSessions").map(function (c) { return c.args[0] })
      check(C, asked.indexOf("/home/tester/code/api") >= 0, "the api list was asked for: " + asked.join())
      svc.flush("loadFolderSessions")
      check(C, sheet.activePath === "/home/tester/code/web" && sheet.rows.every(function (r) { return r.cwd === "/home/tester/code/web" }),
            "a late answer for another folder never shows here")
      check(C, sheet.marksFor("/home/tester/code/api").length === 3, "and still counts for its own folder")
    }, function () { return root.since(400) })

    C = "no_project_is_made_once_and_late_answers_are_dropped"
    step(C, function (C) {
      fresh({}, { loadWorkspace: true })
      sheet.selectLeft(root.leftIndex("place", "workspace"))
      key(Qt.Key_Home)
      key(Qt.Key_Return)
      key(Qt.Key_Return)
      check(C, root.calls("loadWorkspace").filter(function (c) { return c.args[0] === true }).length === 1, "Enter twice asks once")
      sheet.open({})
      svc.flush("loadWorkspace")
      check(C, root.picks.length === 0, "an answer for an earlier open picks nothing")
      svc.hold = {}
      sheet.selectLeft(root.leftIndex("place", "workspace"))
      key(Qt.Key_Home)
      key(Qt.Key_Return)
      check(C, root.picks.length === 1 && root.lastPick().cwd === "/home/tester/AutoPilot", "the next open makes it and picks it")
    })

    C = "hover_counts_only_when_the_pointer_moves"
    step(C, function (C) {
      fresh({})
      check(C, sheet.pointerMoved(sheet, 40, 40) === false, "the first position after open only sets where the pointer is")
      check(C, sheet.pointerMoved(sheet, 40, 40) === false, "rows moving under a resting pointer are not hover")
      check(C, sheet.pointerMoved(sheet, 44, 40) === true, "a real move is")
      var tree = root.find(sheet, function (i) { return i.count !== undefined && i.count === sheet.leftRows.length && i.itemAtIndex !== undefined })
      var item = tree ? tree.itemAtIndex(root.leftIndex("dir", "/home/tester/code")) : null
      check(C, item !== null && typeof item.hovered === "function", "the tree row reacts through hovered()")
      if (item) {
        sheet.focusPane = "list"
        var before = sheet.leftCursor
        item.hovered(5, 5)
        item.hovered(5, 5)
        check(C, sheet.focusPane === "list" || sheet.leftCursor === root.leftIndex("dir", "/home/tester/code"), "a resting pointer leaves the focus alone, a moved one takes it")
        item.hovered(9, 5)
        check(C, sheet.focusPane === "places" && sheet.leftCursor === root.leftIndex("dir", "/home/tester/code") && sheet.placePath === "", "hover moves the highlight, never the place")
      }
    })

    C = "folders_closed_or_hidden_under_the_cursor"
    step(C, function (C) {
      fresh({})
      sheet.focusPane = "places"
      key(Qt.Key_H, Qt.ControlModifier)
      sheet.setExpanded("/home/tester/.config", true)
      sheet.selectLeft(root.leftIndex("dir", "/home/tester/.config/app"))
      check(C, sheet.placePath === "/home/tester/.config/app", "a folder inside a hidden one is the place")
      key(Qt.Key_H, Qt.ControlModifier)
      check(C, sheet.placePath === "/home/tester" && root.leftRow().path === "/home/tester" && sheet.placeRowIndex === sheet.leftCursor,
            "hiding it moves cursor and place to the nearest listed folder: " + sheet.placePath)
      sheet.selectLeft(root.leftIndex("dir", "/home/tester/code"))
      key(Qt.Key_Right)
      key(Qt.Key_Right)
      key(Qt.Key_Right)
      check(C, root.leftRow().path === "/home/tester/code/api", "Right steps into the first child")
      key(Qt.Key_Right)
      key(Qt.Key_Right)
      check(C, root.leftRow().path === "/home/tester/code/api" && sheet.expanded["/home/tester/code/api"] === true,
            "Right on an open folder without subfolders stays put: " + root.leftRow().path)
      sheet.setExpanded("/home/tester/code", false)
      check(C, root.leftRow().path === "/home/tester/code" && sheet.placePath === "/home/tester/code", "closing a parent by mouse moves to it")
    })

    C = "typed_paths_and_refusals"
    step(C, function (C) {
      fresh({})
      root.typeText("~/code/../notes//")
      check(C, sheet.queryPath === "/home/tester/notes", "dot-dot and doubled slashes resolve: " + sheet.queryPath)
      key(Qt.Key_U, Qt.ControlModifier, "")
      root.typeText("/")
      key(Qt.Key_Return)
      check(C, root.picks.length === 0 && lastNotice().text === "The root folder is not allowed. Pick a folder inside your home folder, or No project.", "/ is refused: " + lastNotice().text)
      key(Qt.Key_U, Qt.ControlModifier, "")
      sheet.setExpanded("/home/tester/code", true)
      sheet.focusPane = "places"
      sheet.leftCursor = root.leftIndex("dir", "/home/tester/code/vendor")
      key(Qt.Key_N, Qt.ControlModifier)
      check(C, root.picks.length === 0 && lastNotice().text === "~/code/vendor belongs to another user, so no agent starts there. Pick a folder of yours.",
            "another user's folder is refused: " + lastNotice().text)
    })

    C = "the_tree_scrolls_to_a_revealed_folder"
    step(C, function (C) {
      fresh({ cwd: "/home/tester/many/d35" })
      root.mark = Date.now()
    })
    step(C, function (C) {
      var tree = root.find(sheet, function (i) { return i.count !== undefined && i.count === sheet.leftRows.length && i.itemAtIndex !== undefined })
      check(C, root.leftRow().path === "/home/tester/many/d35", "the cursor is on the draft's folder")
      var item = tree ? tree.itemAtIndex(sheet.leftCursor) : null
      check(C, tree !== null && item !== null && item.y >= tree.contentY && item.y + item.height <= tree.contentY + tree.height,
            "and it is on screen: " + (item ? item.y + " in " + tree.contentY + "+" + tree.height : "no item"))
    }, function () { return root.since(200) })

    C = "sendto_no_project_caption"
    step(C, function (C) {
      sendTo.target = { mode: "new", sessionId: null, cwd: "/home/tester/AutoPilot" }
      check(C, sendTo.noProject === true && sendTo.title === "No project", "title: " + sendTo.title)
      check(C, sendTo.caption === "new session in ~/AutoPilot, no project needed", "caption: " + sendTo.caption)
      sendTo.target = { mode: "new", sessionId: null, cwd: "/home/tester/code/api" }
      check(C, sendTo.title === "New session" && sendTo.caption === "new session in ~/code/api", "a folder stays as it was")
      sendTo.target = { mode: "new", sessionId: null, cwd: null }
      check(C, sendTo.caption === "No session yet. Pick one, a folder for a new session, or No project.", "empty: " + sendTo.caption)
      check(C, Model.workspacePath("/home/tester/") === "/home/tester/AutoPilot" && Model.workspacePath("") === "", "Model.workspacePath")
      check(C, Model.harnessShortName("cursor") === "Cursor" && Model.harnessShortName("opencode") === "OpenCode", "short names")
    })

    C = "new_session_button_names_its_folder_and_agent"
    step(C, function (C) {
      fresh({})
      check(C, sheet.newState === "none" && sheet.newLine.indexOf("Pick a folder on the left") === 0, "no folder yet: " + sheet.newState + " " + sheet.newLine)
      check(C, !sheet.newReady, "and the button is off")
      root.typeText("~/code/api")
    })
    step(C, function (C) {
      check(C, sheet.newState === "ok" && sheet.newLine === "in ~/code/api", "a folder that is there: " + sheet.newState + " " + sheet.newLine)
      check(C, root.calls("checkFolder").some(function (c) { return c.args[0] === "/home/tester/code/api" }), "the helper was asked about it")
      check(C, sheet.newHarness === "claude", "the newest session here names the agent: " + sheet.newHarness)
      var label = root.find(sheet, function (i) { return i.text === "New session" && i.visible })
      check(C, label !== null && label.font.bold === true, "a large New session button")
      key(Qt.Key_Right)
      key(Qt.Key_Right)
      check(C, sheet.filter === "opencode" && sheet.newHarness === "opencode", "←/→ picks the agent it starts: " + sheet.newHarness)
      check(C, sheet.cursor === 0, "the button keeps the cursor")
      key(Qt.Key_Return)
      var p = root.lastPick()
      check(C, p && p.mode === "new" && p.cwd === "/home/tester/code/api" && p.harness === "opencode", "Enter starts it: " + JSON.stringify(p))
    }, function () { return sheet.newState === "ok" })

    C = "a_typed_folder_that_is_not_there_suggests_real_ones"
    step(C, function (C) {
      fresh({})
      root.typeText("~/Code")
    })
    step(C, function (C) {
      check(C, sheet.walk.exists === false && sheet.suggesting, "~/Code is not there: " + JSON.stringify(sheet.walk))
      check(C, JSON.stringify(sheet.suggestions) === JSON.stringify(["/home/tester/code"]), "another case: " + JSON.stringify(sheet.suggestions))
      check(C, sheet.newState === "missing" && !sheet.newReady, "New session is off: " + sheet.newState)
      check(C, sheet.cursor === 1, "the cursor starts on the suggestion")
      key(Qt.Key_Return)
      check(C, root.picks.length === 0 && sheet.query === "" && sheet.placePath === "/home/tester/code" && sheet.activePath === "/home/tester/code",
            "Enter opens the folder: " + sheet.query + " " + sheet.activePath)
      check(C, !sheet.suggesting && sheet.cursor === 0 && sheet.newLine === "in ~/code", "and New session there takes the cursor: " + sheet.newLine)
      key(Qt.Key_U, Qt.ControlModifier, "")
      root.typeText("~/notse")
    }, function () { return sheet.suggesting })
    step(C, function (C) {
      check(C, sheet.suggestions.indexOf("/home/tester/notes") >= 0, "a typo: " + JSON.stringify(sheet.suggestions))
      key(Qt.Key_U, Qt.ControlModifier, "")
      root.typeText("/code/api")
    }, function () { return sheet.suggesting })
    step(C, function (C) {
      check(C, JSON.stringify(sheet.suggestions) === JSON.stringify(["/home/tester/code/api"]), "a path from / meant from ~: " + JSON.stringify(sheet.suggestions))
      check(C, sheet.newState === "missing", "the helper says /code/api is not there: " + sheet.newState)
      key(Qt.Key_U, Qt.ControlModifier, "")
      root.typeText("~/nothere")
      root.mark = Date.now()
    }, function () { return sheet.suggesting })
    step(C, function (C) {
      check(C, sheet.suggestions.length === 0 && sheet.newState === "missing", "nothing close: " + JSON.stringify(sheet.suggestions))
      check(C, root.calls("loadSessions").every(function (c) { return c.args[1] !== "/home/tester/nothere" }), "a folder that is not there is never read")
      key(Qt.Key_Home)
      key(Qt.Key_Return)
      check(C, root.picks.length === 0 && lastNotice().text === "~/nothere does not exist. Pick a folder of yours, or type its path.",
            "New session refuses a folder that is not there: " + lastNotice().text)
    }, function () { return sheet.walk.exists === false && root.since(700) })

    C = "a_folder_the_helper_refuses_is_said_before_a_pick"
    step(C, function (C) {
      fresh({})
      svc.folderStates = { "/home/tester/code/web": { state: "refused", reason: "shared" } }
      root.typeText("~/code/web")
    })
    step(C, function (C) {
      var sentence = "Others can write to ~/code/web, so no agent runs there unattended. chmod go-w ~/code/web fixes that."
      check(C, sheet.newState === "refused" && sheet.newLine === sentence, "said on the button: " + sheet.newLine)
      var asked = root.calls("checkFolder").length
      key(Qt.Key_Home)
      key(Qt.Key_Return)
      check(C, root.picks.length === 0 && lastNotice().text === sentence, "and on Enter: " + lastNotice().text)
      check(C, root.calls("checkFolder").length === asked + 1, "a no is asked again on Enter, in case it was fixed")
    }, function () { return sheet.newState === "refused" })

    C = "a_new_session_waits_for_its_folder_check"
    step(C, function (C) {
      fresh({}, { checkFolder: true })
      root.typeText("~/code/api")
      key(Qt.Key_Home)
      key(Qt.Key_Return)
      check(C, root.picks.length === 0 && sheet.newState === "checking", "nothing is picked before the answer: " + sheet.newState)
      svc.flush("checkFolder")
      var p = root.lastPick()
      check(C, root.picks.length === 1 && p.cwd === "/home/tester/code/api", "the answer starts it: " + JSON.stringify(p))
      fresh({}, { checkFolder: true })
      root.typeText("~/code/web")
      key(Qt.Key_Home)
      key(Qt.Key_Return)
      // Opened again before the answer lands (the queue is kept, unlike fresh()).
      sheet.open({})
      svc.flush("checkFolder")
      check(C, root.picks.length === 0, "an answer for an earlier open starts nothing")
    })

    C = "sendto_starts_with_no_agent_and_says_what_is_missing"
    step(C, function (C) {
      sendTo.target = { mode: "new", sessionId: null, cwd: null }
      sendTo.agentChosen = false
      var lit = root.findAll(sendTo, function (i) { return i.selected === true && i.harness !== undefined && i.visible })
      check(C, lit.length === 0, "no agent chip is lit: " + lit.length)
      check(C, sendTo.ringAgent === "claude" && sendTo.title === "No session picked", "the cursor rests on the first agent")
      sendTo.missing = true
      check(C, sendTo.caption === "Select where to send it: a session, a folder for a new session, or No project.", "missing: " + sendTo.caption)
      var newChip = root.find(sendTo, function (i) { return i.text === "New session" && i.selected !== undefined })
      check(C, newChip !== null && newChip.selected === false, "New session is not lit without a folder")
      sendTo.agentChosen = true
      sendTo.missing = false
      sendTo.harness = "opencode"
      lit = root.findAll(sendTo, function (i) { return i.selected === true && i.harness === "opencode" && i.visible })
      check(C, lit.length === 1, "a chosen agent is lit")
      sendTo.harness = "claude"
    })
    C = "typing_words_finds_folders_anywhere"
    step(C, function (C) {
      fresh({})
      root.typeText("api")
    })
    step(C, function (C) {
      var asked = root.last("findDirs")
      check(C, asked !== null && asked.args[0] === "api", "the words go to the search: " + JSON.stringify(asked))
      check(C, asked && asked.args[1].indexOf("/home/tester/code/api") >= 0, "folders with sessions go along, to rank higher")
      check(C, sheet.folderRows[0] === "/home/tester/code/api", "the folder is found without a path: " + JSON.stringify(sheet.folderRows))
      check(C, sheet.cursor === 1 && sheet.folderAt(1) === "/home/tester/code/api", "the best folder has the cursor")
      check(C, sheet.sessionAt(sheet.folderRows.length + 1) !== null, "the sessions the words match follow the folders")
      check(C, sheet.searching && sheet.placeTitle === "Search" && sheet.placeFact.indexOf("1 folder and ") === 0,
            "a search, not the place on the left: " + sheet.placeTitle + " / " + sheet.placeFact)
      check(C, sheet.newCwd === "/home/tester/code/api" && sheet.newLine === "in ~/code/api", "New session points at the folder found: " + sheet.newLine)
      check(C, sheet.crumbOf("/home/tester/code/api") === "in Home › code" && sheet.crumbOf("/home/tester/notes") === "in Home",
            "where it lives, in words: " + sheet.crumbOf("/home/tester/code/api"))
      var pieces = sheet.namePieces("a<b&c d", [[0, 1], [6, 7]])
      check(C, JSON.stringify(pieces) === JSON.stringify([{ text: "a", hit: true }, { text: "<b&c\u00a0", hit: false }, { text: "d", hit: true }]),
            "the name is cut at its marks into plain pieces, markup left as it is: " + JSON.stringify(pieces))
      var shown = root.findAll(sheet, function (o) { return o.text === "api" && o.font && o.font.bold === true })
      check(C, shown.length > 0 && shown.every(function (o) { return o.textFormat === Text.PlainText }), "the matched letters are a plain-text piece in bold")
      key(Qt.Key_N, Qt.ControlModifier)
      var p = root.lastPick()
      check(C, p && p.mode === "new" && p.cwd === "/home/tester/code/api", "Ctrl+N starts a new session in the found folder: " + JSON.stringify(p))
      root.picks = []
      key(Qt.Key_Return)
      check(C, root.picks.length === 0 && sheet.query === "" && sheet.placePath === "/home/tester/code/api" && sheet.cursor === 0,
            "Enter opens it, New session under the cursor: " + sheet.placePath + " " + sheet.cursor)
      key(Qt.Key_Return)
      p = root.lastPick()
      check(C, p && p.cwd === "/home/tester/code/api", "and Enter again starts it")
    }, function () { return sheet.folderRows.length > 0 })

    C = "the_newest_search_wins_and_an_earlier_open_is_dropped"
    step(C, function (C) {
      fresh({}, { findDirs: true })
      root.typeText("no")
      root.mark = Date.now()
    })
    step(C, function (C) {
      root.typeText("tes")
      root.mark = Date.now()
    }, function () { return root.since(300) })
    step(C, function (C) {
      var asked = root.calls("findDirs").map(function (c) { return c.args[0] })
      check(C, JSON.stringify(asked) === JSON.stringify(["no", "notes"]), "one search per resting moment: " + JSON.stringify(asked))
      check(C, sheet.finding && sheet.folderRows.length === 0, "nothing shown before the answer")
      svc.flush("findDirs")
      check(C, JSON.stringify(sheet.folderRows) === JSON.stringify(["/home/tester/notes"]), "the answer for the words now typed: " + JSON.stringify(sheet.folderRows))
      root.typeText("x")
      root.mark = Date.now()
    }, function () { return root.since(300) })
    step(C, function (C) {
      check(C, root.calls("findDirs").length === 3, "a third search for notesx")
      // Back a letter before notesx answers: notes is answered already, and notesx's late
      // answer lands under its own words.
      key(Qt.Key_Backspace)
      check(C, sheet.searchWords === "notes" && !sheet.finding && sheet.folderRows[0] === "/home/tester/notes", "back to notes at once")
      svc.flush("findDirs")
      check(C, sheet.searchWords === "notes" && !sheet.finding && sheet.folderRows[0] === "/home/tester/notes", "a late answer for notesx changes nothing")
      root.typeText("x")
      root.mark = Date.now()
    }, function () { return root.since(300) })
    step(C, function (C) {
      check(C, !sheet.finding && root.calls("findDirs").length === 3, "notesx was answered already, so nothing is asked again")
      root.typeText("y")
      root.mark = Date.now()
    }, function () { return root.since(300) })
    step(C, function (C) {
      check(C, root.calls("findDirs").length === 4, "notesxy is asked for")
      sheet.open({})
      svc.flush("findDirs")
      check(C, Object.keys(sheet._found).length === 0 && sheet.folderRows.length === 0, "its answer, for an earlier open, is dropped")
    }, function () { return root.since(300) })
  }

  Timer {
    interval: 60
    repeat: true
    running: true
    onTriggered: {
      if (root.si >= root.steps.length) {
        for (var i = 0; i < root.caseNames.length; i++) {
          var n = root.caseNames[i]
          var r = root.results[n]
          if (!r) console.log("CASE " + n + " FAIL never ran")
          else if (r.fail !== "") console.log("CASE " + n + " FAIL " + r.fail)
          else console.log("CASE " + n + " ok")
        }
        Qt.exit(0)
        return
      }
      var s = root.steps[root.si]
      if (s.until && !s.until() && root.tries < 80) { root.tries++; return }
      root.tries = 0
      try {
        s.run(s.name)
      } catch (e) {
        root.check(s.name, false, "threw: " + e)
      }
      root.si++
    }
  }
}
QML

names_js="[$(printf '"%s",' $CASES | sed 's/,$//')]"
sed -i "s|__CASES__|$names_js|" "$W/Q6Smoke.qml"

out="$(cd "$W" && timeout 120 "$QML" -I "$W" Q6Smoke.qml 2>&1)"; rc=$?
[ "$rc" -eq 0 ] || no "engine run" "rc=$rc $(printf '%s' "$out" | grep -vE 'CASE ' | head -4 | tr '\n' ' ')"
for c in $CASES; do
  line="$(printf '%s\n' "$out" | grep -E "CASE $c (ok|FAIL)" | head -1)"
  if printf '%s' "$line" | grep -qE "CASE $c ok"; then ok "$c"
  elif [ -n "$line" ]; then no "$c" "$(printf '%s' "$line" | sed "s/^.*CASE $c FAIL //" | cut -c1-400)"
  else no "$c" "no result line"; fi
done

re='(SessionSheet|SendToSection|Q6Smoke)\.qml'
warn="$(printf '%s\n' "$out" | grep -E 'Warning|TypeError|ReferenceError|Unable to assign|Cannot read|is not a function|Binding loop|is not a type' | grep -E "$re" || true)"
if [ -z "$warn" ]; then ok "and the engine reported no warnings from these files"
else no "and the engine reported no warnings from these files" "$(printf '%s' "$warn" | head -6 | tr '\n' ' ')"; fi

printf '\n%d passed' "$pass"
[ "$fail" -gt 0 ] && printf ', %d FAILED' "$fail"
printf '\n'
[ "$fail" -eq 0 ]
