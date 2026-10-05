pragma ComponentBehavior: Bound

import QtQuick
import qs.Commons
import qs.Ui
import "../lib/Edition.js" as Edition
import "../lib/Model.js" as Model

// Where the prompt runs, and how. An in-card overlay over the view area, modal while it
// is up (R6 8.4), in two panes:
//
//   - Places and folders, on the left: No project (~/AutoPilot, made when it is first
//     picked), All recent sessions, the folders that have sessions lately, and the folder
//     tree of the home folder, read one folder at a time through `ap4a dirs`. Each folder
//     carries the marks of the agents that have sessions in it.
//   - Sessions, on the right: what the chosen place holds, under one large New session
//     button that starts a new session there (cursor row 0). Enter resumes the highlighted
//     session and Ctrl+F forks it.
//
// Typing words also searches the home folder for folders by name (`ap4a find-dirs`), so nobody
// needs to know a path: "projects" finds ~/Projects, "last man" finds Last-Man-Hooping. The
// folders found come first, then the sessions the words match; Enter on a folder makes it the
// place, with New session under the cursor.
//
// A new session starts only in a folder the helper has looked at (`ap4a folder`): one that
// exists and that a job may run in. A typed path that is not there turns the list into the
// folders it most likely means (another case, a letter off, a path from / meant from ~), and
// Enter on one fills it in, so New session is never offered for a folder that is not there.
//
// The place on the right follows the left cursor when the keyboard moves it, and a click.
// Hover only moves the highlight, so passing the pointer over the tree never re-reads.
// Typing filters the sessions; typing a folder path (/... or ~/...) points both panes at
// that folder. Tab moves between the panes. The home folder itself is browsable but is
// never a working folder; No project is the place for work that needs none.
//
// Everything listed comes from bounded helper reads; the sheet says what a bound left out
// instead of pretending a list is complete. All recent sessions is the newest 50 per agent;
// a folder's own list (`sessions --in`) reads that folder for every agent and replaces the
// recent rows of that folder once it lands. Pi keeps its sessions per folder, so the recent
// list holds Pi's sessions for one folder at a time. Cursor lists only the chats Auto Pilot
// itself started.
Item {
  id: root

  required property var theme
  required property var service
  property bool active: false
  property string home: ""
  property string initialHarness: ""
  property string initialCwd: ""
  // The session the draft already points at, marked with a check.
  property string initialSessionId: ""

  signal picked(var selection)
  signal closed()
  signal noticeRequested(string text, string kind, var undo)

  property string query: ""
  property string filter: "all"
  property int cursor: 0
  // The folder the last sessions read was scoped to (Pi lists only that folder).
  property string _listedCwd: ""

  // "list" (sessions, the default) or "places" (places and folders).
  property string focusPane: "list"
  property int leftCursor: 1
  // "recent", "workspace" or "folder"; placePath is the folder ("" for recent).
  property string placeKind: "recent"
  property string placePath: ""
  // path -> true for every open folder of the tree.
  property var expanded: ({})
  property bool showHidden: false
  // A folder the left cursor moves to once its row exists (a typed path, the draft's folder).
  property string _reveal: ""
  // The right cursor was moved by hand since the place changed.
  property bool _cursorTouched: false
  // leftKeyOf the row under the left cursor.
  property string _leftKey: ""
  // harness + id of the highlighted session, so a list that refills keeps it highlighted.
  property string _rowKey: ""
  // Bumped by every open; an answer from an earlier open is dropped.
  property int _openGen: 0
  // A No project folder is being made.
  property bool _making: false
  // The last pointer position seen; hover counts only when the pointer itself moved, never
  // when rows move under a pointer resting over the sheet.
  property real _px: -1
  property real _py: -1
  property bool _pointerKnown: false
  // Folder checks answered since the sheet opened, path -> {state: ok|missing|refused|unknown,
  // reason}, and the ones on their way, path -> true.
  property var _checks: ({})
  property var _checking: ({})
  // A new session that waits for its folder's check: {cwd, harness, gen}, or null.
  property var _pendingNew: null
  // Folders a typed path's walk asked the tree to read, so each is asked once per open.
  property var _walkAsked: ({})
  // The folder search's answers this open, by the words they were for: {entries, truncated,
  // failed}. Each lands under its own words, so a late answer never stands for newer typing.
  property var _found: ({})

  function pointerMoved(item, x, y) {
    var p = item.mapToItem(null, x, y)
    var known = root._pointerKnown
    var moved = p.x !== root._px || p.y !== root._py
    root._px = p.x
    root._py = p.y
    root._pointerKnown = true
    return known && moved
  }

  function rowKeyOf(r) { return r ? r.harness + " " + r.id : "" }

  readonly property var glyph: ({
    folder: "\uDB80\uDE4B",       // md-folder                     U+F024B
    folderOpen: "\uDB81\uDF70",   // md-folder_open                U+F0770
    home: "\uDB80\uDEDC",         // md-home                       U+F02DC
    workspace: "\uDB83\uDDC9",    // md-file_document_edit_outline U+F0DC9
    recent: "\uDB80\uDEDA",       // md-history                    U+F02DA
    down: "\uDB80\uDD40",         // md-chevron_down               U+F0140
    right: "\uDB80\uDD42",        // md-chevron_right              U+F0142
    git: "\uDB80\uDEA2",          // md-git                        U+F02A2
    link: "\uDB80\uDF39",         // md-link_variant               U+F0339
    lock: "\uDB80\uDF41",         // md-lock_outline               U+F0341
    check: "\uDB80\uDD2C",        // md-check                      U+F012C
    plus: "\uDB81\uDC15",         // md-plus                       U+F0415
    alert: "\uDB80\uDC28"         // md-alert_circle               U+F0028
  })

  readonly property string hints: root.focusPane === "places"
    ? "↑/↓ choose  ·  ←/→ close or open  ·  Enter sessions  ·  Ctrl+H hidden folders  ·  Ctrl+N new session here  ·  Tab sessions  ·  Esc back"
    : "Type to search  ·  ←/→ agent  ·  ↑/↓ choose  ·  Enter pick  ·  Ctrl+F fork  ·  Ctrl+N new session  ·  Tab folders  ·  Esc back"

  readonly property var filters: ["all"].concat(Edition.HARNESS_IDS)
  readonly property var allResult: root.service && root.service.sessions && root.service.sessions["all"]
    ? root.service.sessions["all"] : null
  readonly property var allRows: root.allResult && Array.isArray(root.allResult.sessions) ? root.allResult.sessions : []
  readonly property string queryPath: root.expandPath(root.query)

  // ---------------------------------------------------------------- the place

  readonly property string workspacePath: root.service && root.service.workspace && typeof root.service.workspace.path === "string"
    ? root.service.workspace.path : Model.workspacePath(root.home)
  readonly property bool workspaceExists: !!root.service && !!root.service.workspace && root.service.workspace.exists === true
  // Something else is at ~/AutoPilot (a file, a link, a folder others can write); it is never changed.
  readonly property bool workspaceRefused: !!root.service && !!root.service.workspace && root.service.workspace.refused === true
  readonly property string workspaceRefusal: Model.shortPath(root.workspacePath, root.home)
    + " cannot be used: it has to be a folder of yours, not a link, that nobody else can write. Fix it, or pick another folder."

  // A typed folder path wins over the chosen place while it is typed.
  // Words typed (not a path): a search over every place, so the place on the left steps aside.
  readonly property bool searching: root.queryPath === "" && root.query.trim() !== ""
  readonly property string activePath: root.queryPath !== "" ? root.queryPath : (root.searching ? "" : root.placePath)
  readonly property string activeKind: root.activePath === "" ? "recent"
    : (root.activePath === root.workspacePath ? "workspace" : (root.activePath === root.home ? "home" : "folder"))

  // Every folder list (`sessions --in`) read since the sheet opened, by folder, so going
  // back to a folder is instant and its marks keep their counts after another one is read.
  property var _folderLists: ({})
  // Folders whose own list could not be read, folder -> the helper's sentence.
  property var _folderErrors: ({})
  readonly property var latestIn: root.service && root.service.sessions ? root.service.sessions["in"] : null
  onLatestInChanged: {
    var r = root.latestIn
    if (!r || typeof r["in"] !== "string" || r["in"] === "") return
    var lists = {}, errors = {}
    for (var k in root._folderLists) if (k !== r["in"] || r.ok === true) lists[k] = root._folderLists[k]
    for (var e in root._folderErrors) if (e !== r["in"]) errors[e] = root._folderErrors[e]
    if (r.ok === true) lists[r["in"]] = r
    else if (!lists.hasOwnProperty(r["in"])) errors[r["in"]] = typeof r.message === "string" && r.message !== "" ? r.message : "The helper did not answer."
    root._folderLists = lists
    root._folderErrors = errors
  }
  readonly property bool folderFailed: root.activePath !== "" && root.inResult === null && root._folderErrors.hasOwnProperty(root.activePath)

  // The folder's own list, once `sessions --in` answered for this very folder.
  readonly property var inResult: root.activePath !== "" && root._folderLists.hasOwnProperty(root.activePath)
    ? root._folderLists[root.activePath] : null
  readonly property var result: root.inResult !== null ? root.inResult : root.allResult
  readonly property bool loading: root.allResult === null && !!root.service && root.service.loadingSessions === true
  readonly property bool folderLoading: root.activePath !== "" && root.inResult === null && !!root.service
    && typeof root.service.loadFolderSessions === "function" && root.service.loadingSessions === true

  // Sessions of the place before the agent filter and the words.
  readonly property var placeRows: {
    if (root.activeKind === "recent") return root.allRows
    if (root.activeKind === "home") return []
    var source = root.inResult !== null && Array.isArray(root.inResult.sessions) ? root.inResult.sessions : root.allRows
    var out = []
    for (var i = 0; i < source.length; i++) {
      var r = source[i]
      if (r && String(r.cwd || "") === root.activePath) out.push(r)
    }
    return out
  }

  readonly property var rows: {
    var out = []
    var q = root.query.trim().toLowerCase()
    var words = root.queryPath !== "" || q === "" ? [] : q.split(/\s+/)
    for (var i = 0; i < root.placeRows.length; i++) {
      var r = root.placeRows[i]
      if (!r || typeof r.id !== "string" || Edition.HARNESS_IDS.indexOf(r.harness) < 0) continue
      if (root.filter !== "all" && r.harness !== root.filter) continue
      if (words.length > 0) {
        var hay = [r.title, r.cwd, Model.shortPath(r.cwd, root.home), Model.harnessName(r.harness)]
          .map(function (v) { return v === undefined || v === null ? "" : String(v) }).join("\n").toLowerCase()
        var hit = true
        for (var w = 0; w < words.length; w++) if (words[w] !== "" && hay.indexOf(words[w]) < 0) hit = false
        if (!hit) continue
      }
      out.push(r)
    }
    out.sort(function (a, b) { return (Number(b.updatedAtMs) || 0) - (Number(a.updatedAtMs) || 0) })
    return out
  }

  // Row 0 is the New session button; the rows after it are the sessions, or the folders a
  // typed path that is not there most likely means.
  readonly property int rowCount: (root.suggesting ? root.suggestions.length : root.folderRows.length + root.rows.length) + 1
  // The agent a new session here starts with: the chosen filter, else the draft's own agent,
  // else the agent of the newest session in this place, else the default agent in Settings.
  // The button names it, so nobody starts an agent they did not mean to.
  readonly property string newHarness: {
    if (root.filter !== "all") return root.filter
    if (Edition.HARNESS_IDS.indexOf(root.initialHarness) >= 0) return root.initialHarness
    var newest = null
    for (var i = 0; i < root.placeRows.length; i++) {
      var r = root.placeRows[i]
      if (r && String(r.cwd || "") === root.newCwd && Edition.HARNESS_IDS.indexOf(r.harness) >= 0 && !root.agentBlocked(r.harness)
          && (newest === null || (Number(r.updatedAtMs) || 0) > (Number(newest.updatedAtMs) || 0))) newest = r
    }
    if (newest !== null) return newest.harness
    var preferred = root.service && root.service.settings ? String(root.service.settings.defaultHarness || "") : ""
    if (Edition.HARNESS_IDS.indexOf(preferred) >= 0 && !root.agentBlocked(preferred)) return preferred
    for (var k = 0; k < Edition.HARNESS_IDS.length; k++) if (!root.agentBlocked(Edition.HARNESS_IDS[k])) return Edition.HARNESS_IDS[k]
    return Edition.HARNESS_IDS[0]
  }
  // Where New session starts: the place, or while searching the folder under the cursor, else
  // the best folder found.
  readonly property string newCwd: {
    if (root.searching) {
      var f = root.folderAt(root.cursor)
      return f !== "" ? f : (root.folderRows.length > 0 ? root.folderRows[0] : "")
    }
    return root.activePath !== "" ? root.activePath : (root.initialCwd !== "" ? root.initialCwd : root.home)
  }

  // cwd -> {total, by: {harness: n}}, over the recent rows and every folder list read.
  readonly property var countsByCwd: {
    var out = {}
    var seen = {}
    var lists = [root.allRows]
    for (var f in root._folderLists)
      if (Array.isArray(root._folderLists[f].sessions)) lists.push(root._folderLists[f].sessions)
    for (var l = 0; l < lists.length; l++) {
      for (var i = 0; i < lists[l].length; i++) {
        var r = lists[l][i]
        if (!r || typeof r.cwd !== "string" || Edition.HARNESS_IDS.indexOf(r.harness) < 0) continue
        var key = r.harness + " " + r.id
        if (seen[key] === true) continue
        seen[key] = true
        var c = out[r.cwd] || { total: 0, by: {} }
        c.total += 1
        c.by[r.harness] = (c.by[r.harness] || 0) + 1
        out[r.cwd] = c
      }
    }
    return out
  }

  // Up to four folders with the newest sessions, other than the home folder and No project.
  readonly property var recentFolders: {
    var latest = {}
    for (var i = 0; i < root.allRows.length; i++) {
      var r = root.allRows[i]
      if (!r || typeof r.cwd !== "string" || r.cwd === "" || r.cwd === root.home || r.cwd === root.workspacePath) continue
      var t = Number(r.updatedAtMs) || 0
      if (!latest.hasOwnProperty(r.cwd) || latest[r.cwd] < t) latest[r.cwd] = t
    }
    var paths = Object.keys(latest)
    paths.sort(function (a, b) { return latest[b] - latest[a] })
    return paths.slice(0, 4)
  }

  // ---------------------------------------------------------------- the left pane

  // Flat rows: places, recent folders, then the tree from the home folder. Only "place",
  // "recent" and "dir" rows with a path take the cursor; "head" and "note" rows explain.
  readonly property var leftRows: {
    var out = [
      { kind: "place", id: "workspace", path: root.workspacePath, depth: 0 },
      { kind: "place", id: "recent", path: "", depth: 0 }
    ]
    var rec = root.recentFolders
    if (rec.length > 0) {
      out.push({ kind: "head", text: "Recent folders", depth: 0 })
      for (var i = 0; i < rec.length; i++) out.push({ kind: "recent", path: rec[i], name: root.basename(rec[i]), depth: 0 })
    }
    out.push({ kind: "head", text: "Folders", depth: 0 })
    if (root.home !== "") root.appendTree(out, root.home, 0, "~", null, {})
    return out
  }

  function appendTree(out, path, depth, name, entry, branch) {
    var open = root.expanded[path] === true
    out.push({ kind: "dir", path: path, name: name, depth: depth, expanded: open,
               git: !!entry && entry.git === true, link: !!entry && entry.link === true,
               own: !entry || entry.own !== false, hidden: !!entry && entry.hidden === true })
    if (!open || out.length > 1500) return
    var inner = {}
    for (var b in branch) inner[b] = true
    inner[path] = true
    var d = root.service && root.service.dirs ? root.service.dirs[path] : undefined
    if (d === undefined) {
      out.push({ kind: "note", text: "Reading " + Model.shortPath(path, root.home) + "…", depth: depth + 1 })
      return
    }
    if (d.ok !== true || d.state === "denied") {
      out.push({ kind: "note", text: "This folder cannot be read.", depth: depth + 1, warn: true })
      return
    }
    if (d.state === "missing") {
      out.push({ kind: "note", text: "This folder is gone.", depth: depth + 1, warn: true })
      return
    }
    var entries = Array.isArray(d.entries) ? d.entries : []
    var shown = 0, hidden = d.hidden === true || typeof d.hiddenCount !== "number" ? 0 : d.hiddenCount
    for (var i = 0; i < entries.length; i++) {
      var e = entries[i]
      if (!e || typeof e.name !== "string") continue
      if (e.hidden === true && !root.showHidden) { hidden++; continue }
      shown++
      if (e.link === true && typeof e.target !== "string") {
        out.push({ kind: "dir", path: "", name: e.name, depth: depth + 1, expanded: false, git: false, link: true,
                   own: true, hidden: e.hidden === true, outside: e.outside !== false, closed: true })
        continue
      }
      var child = e.link === true ? e.target : (path === "/" ? "/" : path + "/") + e.name
      if (inner[child] === true) continue
      root.appendTree(out, child, depth + 1, e.name, e, inner)
    }
    if (shown === 0)
      out.push({ kind: "note", text: hidden > 0 ? "Only hidden folders. Ctrl+H shows them." : "No folders inside.", depth: depth + 1 })
    if (typeof d.skipped === "number" && d.skipped > 0)
      out.push({ kind: "note", text: d.skipped === 1 ? "1 folder with a name that cannot be shown is left out."
                 : d.skipped + " folders with names that cannot be shown are left out.", depth: depth + 1 })
    if (d.truncated === true)
      out.push({ kind: "note", text: "Not every folder here is listed. Type a path to reach one.", depth: depth + 1 })
  }

  function leftTakesCursor(row) {
    if (!row) return false
    if (row.kind === "place" || row.kind === "recent") return true
    return row.kind === "dir" && row.path !== ""
  }

  // The place a left row stands for, as {kind, path}.
  function placeOf(row) {
    if (!row) return null
    if (row.kind === "place") return row.id === "recent" ? { kind: "recent", path: "" } : { kind: "workspace", path: root.workspacePath }
    if (row.path === "") return null
    return { kind: row.path === root.workspacePath ? "workspace" : "folder", path: row.path }
  }

  // The one left row marked as the place on the right: the row under the cursor when it is
  // that place, else the first row that is.
  readonly property int placeRowIndex: {
    if (root.rowIsPlace(root.leftRows[root.leftCursor])) return root.leftCursor
    for (var i = 0; i < root.leftRows.length; i++) if (root.rowIsPlace(root.leftRows[i])) return i
    return -1
  }

  function rowIsPlace(row) {
    var p = root.placeOf(row)
    if (p === null) return false
    if (root.queryPath !== "") return p.kind !== "recent" && p.path === root.queryPath
    return p.kind === root.placeKind && p.path === root.placePath
  }

  // A left row's identity, so the cursor stays on its row when rows above it come and go.
  function leftKeyOf(row) {
    if (!row) return ""
    return row.kind + ":" + (row.kind === "place" ? row.id : String(row.path || ""))
  }

  function setPlace(kind, path) {
    if (root.placeKind === kind && root.placePath === path) return
    root.placeKind = kind
    root.placePath = path
    root._cursorTouched = false
    root.resetCursor()
    if (path !== "") folderRead.restart()
  }

  function selectLeft(index) {
    if (index < 0 || index >= root.leftRows.length || !root.leftTakesCursor(root.leftRows[index])) return
    // A move by hand wins over a folder still waiting to be revealed.
    root._reveal = ""
    root.leftCursor = index
    var p = root.placeOf(root.leftRows[index])
    if (p === null) return
    if (root.query !== "" && root.queryPath !== "") root.query = ""
    root.setPlace(p.kind, p.path)
  }

  function moveLeft(delta) {
    var i = root.leftCursor
    var step = delta < 0 ? -1 : 1
    var left = Math.abs(delta)
    var target = i
    while (left > 0) {
      var j = target + step
      while (j >= 0 && j < root.leftRows.length && !root.leftTakesCursor(root.leftRows[j])) j += step
      if (j < 0 || j >= root.leftRows.length) break
      target = j
      left--
    }
    if (target !== i) root.selectLeft(target)
  }

  function setExpanded(path, open) {
    if (path === "") return
    var next = {}
    for (var k in root.expanded) if (k !== path) next[k] = true
    if (open) next[path] = true
    root.expanded = next
    if (open) root.readDirs(path)
  }

  // Hidden folders are asked for only while they are shown.
  function readDirs(path) {
    if (!root.service || typeof root.service.loadDirs !== "function") return
    var d = root.service.dirs ? root.service.dirs[path] : undefined
    if (d === undefined || d.ok !== true || (root.showHidden && d.hidden !== true)) root.service.loadDirs(path, root.showHidden)
  }

  function setShowHidden(on) {
    root.showHidden = on === true
    if (root.showHidden) for (var path in root.expanded) root.readDirs(path)
  }

  function toggleHidden() { root.setShowHidden(!root.showHidden) }

  // Opens every folder from the home folder down to path's parent, and moves the left
  // cursor to path once its row is listed.
  function reveal(path) {
    if (root.home === "" || path === "" || (path !== root.home && path.indexOf(root.home + "/") !== 0)) return
    var parts = path.slice(root.home.length).split("/").filter(function (s) { return s !== "" })
    // A folder inside a hidden one is reached only with hidden folders shown.
    if (!root.showHidden && parts.some(function (s) { return s.charAt(0) === "." })) root.setShowHidden(true)
    var next = {}
    for (var k in root.expanded) next[k] = true
    var at = root.home
    next[at] = true
    root.readDirs(at)
    for (var i = 0; i < parts.length - 1; i++) {
      at = at + "/" + parts[i]
      next[at] = true
      root.readDirs(at)
    }
    root.expanded = next
    root._reveal = path
    root.applyReveal()
  }

  function applyReveal() {
    if (root._reveal === "") return
    for (var i = 0; i < root.leftRows.length; i++) {
      var r = root.leftRows[i]
      if (r.kind === "dir" && r.path === root._reveal) {
        root.leftCursor = i
        root._reveal = ""
        return
      }
    }
  }

  function leftIndexOfPlace(id) {
    for (var i = 0; i < root.leftRows.length; i++)
      if (root.leftRows[i].kind === "place" && root.leftRows[i].id === id) return i
    return 0
  }

  // Right: open a closed folder, then step into it. Left: close an open folder, then go
  // to its parent.
  function stepTree(dir) {
    var r = root.leftRows[root.leftCursor]
    if (!r) return
    if (r.kind !== "dir") {
      if (dir > 0) root.focusPane = "list"
      return
    }
    if (dir > 0) {
      if (r.closed === true || r.path === "") return
      if (!r.expanded) {
        root.setExpanded(r.path, true)
        return
      }
      var next = root.leftRows[root.leftCursor + 1]
      if (next && next.kind === "dir" && next.depth === r.depth + 1 && root.leftTakesCursor(next)) root.selectLeft(root.leftCursor + 1)
      return
    }
    if (r.expanded) {
      root.setExpanded(r.path, false)
      return
    }
    for (var i = root.leftCursor - 1; i >= 0; i--) {
      var up = root.leftRows[i]
      if (up.kind === "dir" && up.depth === r.depth - 1) {
        root.selectLeft(i)
        return
      }
    }
  }

  // ---------------------------------------------------------------- captions

  readonly property string boundLine: {
    if (root.result === null) return ""
    var days = typeof root.result.limitDays === "number" ? root.result.limitDays : 90
    var line = "Sessions older than " + days + " days are not listed."
    var cap = typeof root.result.perHarnessCap === "number" ? root.result.perHarnessCap : 50
    var truncated = root.result.truncated || {}
    for (var i = 0; i < Edition.HARNESS_IDS.length; i++) {
      var h = Edition.HARNESS_IDS[i]
      if ((root.filter === "all" || root.filter === h) && truncated[h] === true)
        return line + " Only the newest " + cap + " per agent are listed."
    }
    return line
  }

  readonly property string errorLine: {
    var errors = root.result && root.result.errors ? root.result.errors : {}
    var out = []
    for (var i = 0; i < Edition.HARNESS_IDS.length; i++) {
      var h = Edition.HARNESS_IDS[i]
      if (root.filter !== "all" && root.filter !== h) continue
      var e = errors[h]
      if (e === "too_large") out.push(Model.harnessName(h) + " sessions too large to list.")
      else if (e === "timeout") out.push(Model.harnessName(h) + " sessions took too long to list.")
      else if (typeof e === "string" && e !== "") out.push(Model.harnessName(h) + " sessions could not be read.")
    }
    return out.join(" ")
  }

  // Which folder Pi's sessions are listed for, and how to list another. A folder's own
  // list already holds its Pi sessions.
  readonly property string piLine: {
    if (root.result === null || root.inResult !== null || (root.filter !== "all" && root.filter !== "pi")) return ""
    if (Array.isArray(root.result.needsCwd) && root.result.needsCwd.indexOf("pi") >= 0) return "Type a folder path to list Pi sessions."
    if (typeof root.result.cwd === "string" && root.result.cwd !== "")
      return "Pi sessions are listed for " + Model.shortPath(root.result.cwd, root.home) + " only. Type a folder path to list another."
    return ""
  }

  readonly property string placeTitle: {
    if (root.searching) return "Search"
    if (root.activeKind === "recent") return "All recent sessions"
    if (root.activeKind === "workspace") return "No project"
    if (root.activeKind === "home") return "Home"
    return root.basename(root.activePath)
  }

  // Facts about the folder from the tree listing of its parent (or its own).
  readonly property var activeEntry: root.entryFor(root.activePath)

  function entryFor(p) {
    if (p === "" || !root.service || !root.service.dirs) return null
    var own = root.service.dirs[p]
    var slash = p.lastIndexOf("/")
    var parent = root.service.dirs[slash > 0 ? p.slice(0, slash) : "/"]
    var name = p.slice(slash + 1)
    if (parent && Array.isArray(parent.entries))
      for (var i = 0; i < parent.entries.length; i++) if (parent.entries[i].name === name) return parent.entries[i]
    return own && own.ok === true ? { git: own.git === true, own: own.own !== false } : null
  }

  // The sentence for a folder no session can start in, or "".
  function refusalFor(cwd) {
    if (cwd === root.home) return "Your home folder itself is not allowed. Pick a folder inside it, or No project."
    if (cwd === "/") return "The root folder is not allowed. Pick a folder inside your home folder, or No project."
    var e = root.entryFor(cwd)
    if (e && e.own === false) return Model.shortPath(cwd, root.home) + " belongs to another user, so no agent starts there. Pick a folder of yours."
    return ""
  }

  readonly property string placeFact: {
    if (root.searching) {
      var nf = root.folderRows.length, ns = root.rows.length
      var found = (nf === 1 ? "1 folder" : nf + " folders") + " and " + (ns === 1 ? "1 session" : ns + " sessions") + " match"
      if (root.finding) return ns > 0 ? "Searching your folders…  ·  " + (ns === 1 ? "1 session" : ns + " sessions") + " match" : "Searching your folders…"
      if (root.searchWords === "") return "Type two letters or more to search your folders."
      if (root.foundTruncated) return found + ". The search stopped early: more letters narrow it."
      return found + ". Enter opens a folder."
    }
    if (root.activeKind === "recent") return "Every folder, newest first."
    if (root.activeKind === "home") return "Agents do not start in your home folder itself. Pick a folder inside it, or No project."
    if (root.activeKind === "workspace") {
      if (root.workspaceRefused) return root.workspaceRefusal
      return root.workspaceExists ? "For work without a project. Agents start here and keep their files here."
        : "For work without a project. Made when you start a session here; agents keep their files in it."
    }
    if (root.queryPath !== "" && root.newState === "missing" && root.placeRows.length === 0) return "Not there: no folder by this name."
    var parts = []
    if (root.activeEntry && root.activeEntry.git === true) parts.push("git")
    if (root.activeEntry && root.activeEntry.own === false) parts.push("owned by another user, agents cannot start here")
    var n = root.placeRows.length
    if (root.folderFailed) parts.push("its own list could not be read, the recent sessions here are shown")
    else parts.push(root.folderLoading && n === 0 ? "reading sessions…" : (n === 1 ? "1 session" : n + " sessions"))
    return parts.join("  ·  ")
  }

  // ---------------------------------------------------------------- the new session

  readonly property bool canCheck: !!root.service && typeof root.service.checkFolder === "function"
  readonly property var newCheck: root.newCwd !== "" && root._checks.hasOwnProperty(root.newCwd) ? root._checks[root.newCwd] : null

  // What New session here would meet: "ok", "checking", "missing", "refused", "home" (the
  // home folder itself) or "none" (no folder chosen yet).
  readonly property string newState: {
    var cwd = root.newCwd
    if (cwd === "" || cwd === root.home) return root.activeKind === "home" ? "home" : "none"
    if (cwd === root.workspacePath) return root.workspaceRefused ? "refused" : "ok"
    if (root.refusalFor(cwd) !== "") return "refused"
    if (cwd === root.queryPath && root.walk.exists === false) return "missing"
    var c = root.newCheck
    if (c === null) return root.canCheck ? "checking" : "ok"
    return c.state === "unknown" ? "ok" : c.state
  }

  // The one line under New session: where it starts, or why it cannot.
  readonly property string newLine: {
    var where = Model.shortPath(root.newCwd, root.home)
    if (root.newState === "none" && root.searching)
      return root.searchWords === "" ? "Type two letters or more to find a folder."
        : (root.finding ? "Looking for folders that match…" : "No folder matches. Pick one on the left, or type a path.")
    if (root.newState === "none") return "Pick a folder on the left, type a path such as ~/code/api, or choose No project."
    if (root.newState === "home") return "Not in your home folder itself. Pick a folder inside it, or No project."
    if (root.newCwd === root.workspacePath)
      return root.workspaceRefused ? root.workspaceRefusal : "in " + where + ", no project needed"
    if (root.newState === "missing" || root.newState === "refused") {
      var own = root.refusalFor(root.newCwd)
      if (own !== "") return own
      if (root.newCheck !== null) return root.checkSentence(root.newCwd, root.newCheck)
      return where + " does not exist."
    }
    return "in " + where
  }

  readonly property bool newReady: root.newState === "ok" || root.newState === "checking"

  // The sentence for a folder check that said no: the fact, then the way out.
  function checkSentence(cwd, c) {
    var where = Model.shortPath(cwd, root.home)
    var reason = c && typeof c.reason === "string" ? c.reason : ""
    if (reason === "missing") return where + " does not exist. Pick a folder of yours, or type its path."
    if (reason === "not_dir") return where + " is a file, not a folder. Pick a folder."
    if (reason === "home") return "Your home folder itself is not allowed. Pick a folder inside it, or No project."
    if (reason === "root") return "The root folder is not allowed. Pick a folder inside your home folder, or No project."
    if (reason === "system") return where + " is a shared system folder, so no agent runs there. Pick a folder of yours."
    if (reason === "plugin") return "Plugin folders are not allowed. Pick another folder."
    if (reason === "not_own") return where + " belongs to another user, so no agent starts there. Pick a folder of yours."
    if (reason === "shared") return "Others can write to " + where + ", so no agent runs there unattended. chmod go-w " + where + " fixes that."
    if (reason === "config") return "Agent settings in " + where + " can be changed by others, so no agent runs there unattended."
    if (reason === "denied") return where + " cannot be opened. Pick another folder."
    if (reason === "unclean") return "That path has characters " + Edition.DISPLAY_NAME + " cannot use. Pick another folder."
    return where + " cannot be used. Pick another folder."
  }

  // Asks the helper whether a job may run in path, once per open (again with force, keeping the
  // last answer shown until the new one lands); a new session waiting for that answer starts, or
  // says why not, when it lands.
  function checkFolder(path, force) {
    if (!root.canCheck || path === "" || path === root.home || path === root.workspacePath) return
    if ((force !== true && root._checks.hasOwnProperty(path)) || root._checking[path] === true) return
    var gen = root._openGen
    var asking = {}
    for (var k in root._checking) asking[k] = true
    asking[path] = true
    root._checking = asking
    root.service.checkFolder(path, function (res) {
      if (gen !== root._openGen) return
      var left = {}
      for (var a in root._checking) if (a !== path) left[a] = true
      root._checking = left
      var c = res && res.ok === true && typeof res.state === "string"
        ? { state: res.state, reason: typeof res.reason === "string" ? res.reason : "" }
        : { state: "unknown", reason: "" }
      var next = {}
      for (var n in root._checks) next[n] = root._checks[n]
      next[path] = c
      root._checks = next
      var wait = root._pendingNew
      if (wait === null || wait.cwd !== path || wait.gen !== gen) return
      root._pendingNew = null
      if (!root.active) return
      if (c.state === "ok" || c.state === "unknown") root.finishNew(wait.cwd, wait.harness)
      else root.noticeRequested(root.checkSentence(path, c), "warn", null)
    })
  }


  // ---------------------------------------------------------------- a typed path

  // The typed folder, walked through the tree listings read so far: whether it is there, and
  // when it is not, the folders it most likely means. A path typed from / is also looked for
  // from ~, since "/Projects" usually means ~/Projects.
  //   {exists: true | false | null (not known), wait: a folder to read first, hidden, suggestions}
  readonly property var walk: root.walkPath(root.queryPath)
  // What the walk found next to the typed folder, then what the search found anywhere.
  readonly property var suggestions: {
    var out = root.walk.suggestions.slice()
    if (root.queryPath !== "")
      for (var i = 0; i < root.foundEntries.length; i++) {
        var p = root.foundEntries[i].path
        if (p !== root.queryPath && out.indexOf(p) < 0) out.push(p)
      }
    return out.slice(0, 8)
  }
  // A typed folder that is not there, and folders it may mean: they take the list's place.
  readonly property bool suggesting: root.queryPath !== "" && root.suggestions.length > 0 && root.rows.length === 0
    && (root.newState === "missing" || root.walk.exists === false)

  function walkPath(p) {
    var out = { exists: null, wait: "", hidden: false, suggestions: [] }
    var home = root.home
    if (p === "" || home === "" || p === home || p === "/" || !root.service || !root.service.dirs) return out
    var inside = p.indexOf(home + "/") === 0
    var parts = (inside ? p.slice(home.length + 1) : p.slice(1)).split("/").filter(function (x) { return x !== "" })
    if (parts.length === 0) return out
    var dir = home
    var exact = inside
    for (var i = 0; i < parts.length; i++) {
      var part = parts[i]
      var wantHidden = part.charAt(0) === "."
      var d = root.service.dirs[dir]
      if (!d || d.ok !== true || (wantHidden && d.hidden !== true)) {
        out.wait = dir
        out.hidden = wantHidden
        return out
      }
      if (d.state !== "ok" || !Array.isArray(d.entries)) return out
      var entries = d.entries.filter(function (e) {
        return e && typeof e.name === "string" && (e.link !== true || typeof e.target === "string")
          && (e.hidden !== true || wantHidden)
      })
      var hit = null, folded = []
      for (var j = 0; j < entries.length; j++) {
        if (entries[j].name === part) hit = entries[j]
        else if (entries[j].name.toLowerCase() === part.toLowerCase()) folded.push(entries[j])
      }
      if (hit === null && folded.length === 1) {
        hit = folded[0]
        exact = false
      }
      var complete = inside && d.truncated !== true
      if (hit !== null && i < parts.length - 1) {
        dir = dir + "/" + hit.name
        continue
      }
      if (hit !== null && exact) {
        out.exists = true
        return out
      }
      out.suggestions = hit !== null ? [dir + "/" + hit.name]
        : root.nearNames(entries, part).map(function (e) { return dir + "/" + e.name })
      if (complete) out.exists = false
      return out
    }
    return out
  }

  // Up to six names in entries that part most likely means: the same name in another case,
  // names that start with it, names that hold it, then names a letter or two off.
  function nearNames(entries, part) {
    var want = part.toLowerCase()
    var limit = want.length <= 4 ? 1 : (want.length <= 8 ? 2 : 3)
    var scored = []
    for (var i = 0; i < entries.length; i++) {
      var name = entries[i].name.toLowerCase()
      var score = -1
      if (name === want) score = 0
      else if (want !== "" && name.indexOf(want) === 0) score = 1
      else if (want.length >= 3 && name.indexOf(want) > 0) score = 2
      else if (want.length >= 2) {
        var dist = root.editDistance(name, want, limit)
        if (dist <= limit) score = 2 + dist
      }
      if (score >= 0) scored.push({ entry: entries[i], score: score })
    }
    scored.sort(function (a, b) {
      if (a.score !== b.score) return a.score - b.score
      if (a.entry.name.length !== b.entry.name.length) return a.entry.name.length - b.entry.name.length
      return a.entry.name < b.entry.name ? -1 : (a.entry.name > b.entry.name ? 1 : 0)
    })
    return scored.slice(0, 6).map(function (x) { return x.entry })
  }

  // Levenshtein distance, given up past limit (returns limit + 1).
  function editDistance(a, b, limit) {
    if (Math.abs(a.length - b.length) > limit || a.length > 64 || b.length > 64) return limit + 1
    var prev = []
    for (var j = 0; j <= b.length; j++) prev.push(j)
    for (var i = 1; i <= a.length; i++) {
      var cur = [i]
      var best = i
      for (var k = 1; k <= b.length; k++) {
        var v = Math.min(prev[k] + 1, cur[k - 1] + 1, prev[k - 1] + (a.charAt(i - 1) === b.charAt(k - 1) ? 0 : 1))
        cur.push(v)
        if (v < best) best = v
      }
      if (best > limit) return limit + 1
      prev = cur
    }
    return prev[b.length]
  }

  // The walk needs a folder the tree has not read yet: read it, once per open.
  onWalkChanged: {
    var w = root.walk
    if (!root.active || w.wait === "" || !root.service || typeof root.service.loadDirs !== "function") return
    var key = w.wait + (w.hidden ? " hidden" : "")
    if (root._walkAsked[key] === true) return
    var asked = {}
    for (var k in root._walkAsked) asked[k] = true
    asked[key] = true
    root._walkAsked = asked
    var wait = w.wait, hidden = w.hidden || root.showHidden
    Qt.callLater(function () { if (root.active) root.service.loadDirs(wait, hidden) })
  }

  // A suggestion taken: that folder becomes the place.
  function acceptSuggestion(path) {
    root.goToFolder(path)
  }

  // ---------------------------------------------------------------- the folder search

  // The words the search looks for: the words typed, or the names in a typed path that is not
  // there ("/Projects" looks for "Projects"). Two letters at least.
  readonly property string searchWords: {
    var t = ""
    if (root.queryPath === "") t = root.query
    else if (root.walk.exists === false || root.newState === "missing") {
      var p = root.queryPath
      t = p.indexOf(root.home + "/") === 0 ? p.slice(root.home.length + 1) : p.slice(1)
    }
    t = t.replace(/[\/\\]+/g, " ").replace(/\s+/g, " ").trim()
    return t.replace(/\s/g, "").length >= 2 && t.length <= 80 ? t : ""
  }
  readonly property bool canFind: !!root.service && typeof root.service.findDirs === "function"
  readonly property var foundNow: root.searchWords !== "" && root._found.hasOwnProperty(root.searchWords) ? root._found[root.searchWords] : null
  readonly property var foundEntries: root.foundNow !== null && Array.isArray(root.foundNow.entries) ? root.foundNow.entries : []
  readonly property bool foundTruncated: root.foundNow !== null && root.foundNow.truncated === true
  readonly property bool foundFailed: root.foundNow !== null && root.foundNow.failed === true
  readonly property bool finding: root.canFind && root.searchWords !== "" && root.foundNow === null
  // Folders found for typed words, above the sessions.
  readonly property var folderRows: {
    if (root.queryPath !== "") return []
    var out = []
    for (var i = 0; i < root.foundEntries.length && out.length < 6; i++) out.push(root.foundEntries[i].path)
    return out
  }

  // Asks for the folders that match the words now typed; a newer search replaces one still waiting.
  function findFolders() {
    var q = root.searchWords
    // A failed search is asked again once the typing rests on the same words again.
    if (!root.active || q === "" || !root.canFind || (root._found.hasOwnProperty(q) && root._found[q].failed !== true)) return
    var gen = root._openGen
    // The folders with the most sessions go along, to rank higher.
    var known = Object.keys(root.countsByCwd).filter(function (cwd) { return cwd.indexOf(root.home + "/") === 0 })
    known.sort(function (a, b) { return root.countsByCwd[b].total - root.countsByCwd[a].total })
    root.service.findDirs(q, known.slice(0, 64), function (res) {
      if (gen !== root._openGen || !res || res.code === "superseded") return
      var next = {}
      var keys = Object.keys(root._found)
      // The newest twenty answers are kept, so going back a letter is instant.
      for (var i = Math.max(0, keys.length - 19); i < keys.length; i++) if (keys[i] !== q) next[keys[i]] = root._found[keys[i]]
      next[q] = { entries: res.ok === true && Array.isArray(res.entries) ? res.entries : [],
                  truncated: res.ok === true && res.truncated === true, failed: res.ok !== true }
      root._found = next
    })
  }

  function hitFor(path) {
    for (var i = 0; i < root.foundEntries.length; i++) if (root.foundEntries[i].path === path) return root.foundEntries[i]
    return null
  }

  // Where a folder lives, the way people say it: "in Home › Projects".
  function crumbOf(path) {
    var slash = path.lastIndexOf("/")
    var parent = slash > 0 ? path.slice(0, slash) : "/"
    if (parent === root.home) return "in Home"
    if (parent.indexOf(root.home + "/") === 0) return "in Home › " + parent.slice(root.home.length + 1).split("/").join(" › ")
    return "in " + parent
  }

  // A folder name with the letters the search matched in the accent ink, as styled text; every
  // character of the name is escaped first. marks are [start, end) in characters, not UTF-16.
  function markedName(name, marks) {
    var chars = Array.from(String(name))
    var esc = function (list) { return list.join("").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;") }
    var out = "", at = 0
    var spans = Array.isArray(marks) ? marks.slice() : []
    spans.sort(function (a, b) { return a[0] - b[0] })
    for (var i = 0; i < spans.length; i++) {
      var a = Math.max(at, Math.min(chars.length, Number(spans[i][0]) || 0))
      var b = Math.max(a, Math.min(chars.length, Number(spans[i][1]) || 0))
      if (b <= a) continue
      out += esc(chars.slice(at, a)) + "<font color=\"" + String(root.theme.accentInk) + "\"><b>" + esc(chars.slice(a, b)) + "</b></font>"
      at = b
    }
    return out + esc(chars.slice(at))
  }

  // A folder taken from the search: the words are cleared, the folder becomes the place, and New
  // session there has the cursor, so a second Enter starts it.
  function goToFolder(path) {
    if (typeof path !== "string" || path === "") return
    root.query = ""
    root.reveal(path)
    root.setPlace(path === root.workspacePath ? "workspace" : "folder", path)
    root.focusPane = "list"
    root.setCursorByHand(0)
  }

  // ---------------------------------------------------------------- the right cursor

  // Cursor row 0 is New session; then the folders (found, or suggested for a typed path), then
  // the sessions.
  function folderAt(slot) {
    var list = root.suggesting ? root.suggestions : root.folderRows
    return slot >= 1 && slot <= list.length ? list[slot - 1] : ""
  }
  function sessionAt(slot) {
    if (root.suggesting) return null
    var i = slot - 1 - root.folderRows.length
    return i >= 0 && i < root.rows.length ? root.rows[i] : null
  }
  function keyAt(slot) {
    var f = root.folderAt(slot)
    if (f !== "") return "dir " + f
    var r = root.sessionAt(slot)
    return r ? root.rowKeyOf(r) : ""
  }
  // The cursor goes back to the row it was on by hand, after the rows above it changed.
  function restoreCursor() {
    // Counted from the lists themselves: this runs from their change handlers, before rowCount's
    // own binding has caught up.
    var count = (root.suggesting ? root.suggestions.length : root.folderRows.length + root.rows.length) + 1
    if (root._cursorTouched && root._rowKey !== "") {
      for (var slot = 1; slot < count; slot++) {
        if (root.keyAt(slot) === root._rowKey) {
          root.cursor = slot
          return true
        }
      }
    }
    if (root.cursor > count - 1) root.cursor = count - 1
    return false
  }

  // ---------------------------------------------------------------- helpers

  function expandPath(text) {
    var t = String(text || "").trim()
    if (t === "~" || t.indexOf("~/") === 0) {
      if (root.home === "") return ""
      t = root.home + t.slice(1)
    }
    if (t.charAt(0) !== "/") return ""
    // "." and ".." and doubled slashes are resolved, so the path matches what agents record.
    var out = []
    var parts = t.split("/")
    for (var i = 0; i < parts.length; i++) {
      var s = parts[i]
      if (s === "" || s === ".") continue
      if (s === "..") out.pop()
      else out.push(s)
    }
    return "/" + out.join("/")
  }

  function countFor(id) {
    if (id === "all") return root.placeRows.length
    var n = 0
    for (var i = 0; i < root.placeRows.length; i++) if (root.placeRows[i] && root.placeRows[i].harness === id) n++
    return n
  }

  // [{harness, n}] in agent order, for the marks beside a folder.
  function marksFor(path) {
    var c = root.countsByCwd[path]
    var out = []
    if (!c) return out
    for (var i = 0; i < Edition.HARNESS_IDS.length; i++) {
      var h = Edition.HARNESS_IDS[i]
      if (c.by[h] > 0) out.push({ harness: h, n: c.by[h] })
    }
    return out
  }

  // Sessions in the folders below path (not in path itself).
  function countBelow(path) {
    if (path === "") return 0
    var n = 0
    for (var cwd in root.countsByCwd) if (cwd.indexOf(path + "/") === 0) n += root.countsByCwd[cwd].total
    return n
  }

  function basename(path) {
    var p = String(path || "").replace(/\/+$/, "")
    var i = p.lastIndexOf("/")
    return i >= 0 ? p.slice(i + 1) : p
  }

  function ago(ms) {
    var now = root.service ? Number(root.service.nowMs) || 0 : 0
    if (typeof ms !== "number" || !isFinite(ms) || now <= 0) return ""
    var s = Math.max(0, Math.round((now - ms) / 1000))
    if (s < 60) return "just now"
    if (s < 3600) return Math.floor(s / 60) + "m ago"
    if (s < 86400) return Math.floor(s / 3600) + "h ago"
    var d = Math.floor(s / 86400)
    return d === 1 ? "yesterday" : d + "d ago"
  }

  function resetCursor() {
    if (root.suggesting) root.cursor = 1
    else root.cursor = root.queryPath === "" && root.folderRows.length + root.rows.length > 0 ? 1 : 0
  }

  function open(args) {
    var a = args && typeof args === "object" ? args : {}
    root.initialHarness = Edition.HARNESS_IDS.indexOf(a.harness) >= 0 ? a.harness : ""
    root.initialCwd = typeof a.cwd === "string" ? a.cwd : ""
    root.initialSessionId = typeof a.sessionId === "string" ? a.sessionId : ""
    root.query = ""
    root.filter = root.initialHarness !== "" ? root.initialHarness : "all"
    root.focusPane = "list"
    root.showHidden = false
    root._reveal = ""
    root._cursorTouched = false
    root._folderLists = ({})
    root._folderErrors = ({})
    root._openGen += 1
    root._making = false
    root._pointerKnown = false
    root._rowKey = ""
    root._checks = ({})
    root._checking = ({})
    root._pendingNew = null
    root._walkAsked = ({})
    root._found = ({})
    var opened = {}
    if (root.home !== "") opened[root.home] = true
    root.expanded = opened
    if (root.service && typeof root.service.clearDirs === "function") root.service.clearDirs()
    if (root.home !== "") root.readDirs(root.home)
    if (root.service && typeof root.service.loadWorkspace === "function") root.service.loadWorkspace(false, null)
    var start = root.initialCwd !== "" && root.initialCwd !== root.home ? root.initialCwd : ""
    root.placeKind = start === "" ? "recent" : (start === root.workspacePath ? "workspace" : "folder")
    root.placePath = start
    root.leftCursor = root.leftIndexOfPlace(start !== "" && start === root.workspacePath ? "workspace" : "recent")
    // Set here too: a cursor index equal to the last open's would keep the last open's row key.
    root._leftKey = root.leftKeyOf(root.leftRows[root.leftCursor])
    if (start !== "" && start !== root.workspacePath) root.reveal(start)
    root.resetCursor()
    root._listedCwd = root.initialCwd !== "" ? root.initialCwd : root.home
    if (root.service && typeof root.service.loadSessions === "function") root.service.loadSessions("", root._listedCwd)
    if (start !== "") folderRead.restart()
  }

  // A typed folder lists Pi's sessions there too (every other agent's rows are already
  // filtered by it), reads that folder's own list, and opens the tree down to it.
  function reloadForPath() {
    var p = root.queryPath
    if (!root.active || p === "" || p === "/") return
    // A folder that is not there holds no sessions: half-typed paths ("/m" on the way to /music)
    // are never read.
    if (root.walk.exists === false || root.newState === "missing") return
    root.reveal(p)
    root.readFolder(p)
    if (p === root._listedCwd) return
    root._listedCwd = p
    if (root.service && typeof root.service.loadSessions === "function") root.service.loadSessions("", p)
  }

  function readFolder(path) {
    if (path === "" || path === root.home || !root.service || typeof root.service.loadFolderSessions !== "function") return
    root.service.loadFolderSessions(path)
  }

  function close() {
    root.query = ""
    root.closed()
  }

  // A fact and the way out of it.
  function blockedSentence(harness) {
    return harness === "codex"
      ? "Codex is not signed in. Run codex login in a terminal, then open " + Edition.DISPLAY_NAME + " again."
      : Model.harnessName(harness) + " cannot run right now."
  }

  readonly property bool noMatch: !root.loading && root.query !== "" && root.queryPath === ""

  function agentBlocked(harness) {
    var a = root.service && typeof root.service.agentFor === "function" ? root.service.agentFor(harness) : null
    return !!a && a.enabled === false
  }

  function pickSession(r, mode) {
    if (!r) return
    var how = mode === "fork" ? "fork" : "resume"
    if (root.agentBlocked(r.harness)) {
      root.noticeRequested(root.blockedSentence(r.harness), "warn", null)
      return
    }
    if (how === "fork" && r.canFork === false) {
      root.noticeRequested(Model.harnessName(r.harness) + " sessions cannot be forked. Enter resumes it, or Ctrl+N starts a new one.", "warn", null)
      return
    }
    // Pi resumes a session by its file, so a row without one cannot be resumed.
    var path = r.harness === "pi" && typeof r.path === "string" && r.path.charAt(0) === "/" ? r.path : null
    if (r.harness === "pi" && path === null) {
      root.noticeRequested("That Pi session has no file to " + how + ". Pick another, or start a new session.", "warn", null)
      return
    }
    root.picked({
      harness: r.harness, mode: how, sessionId: r.id, cwd: r.cwd,
      title: typeof r.title === "string" ? r.title : "",
      updatedAtMs: r.updatedAtMs, messages: r.messages, sessionPath: path
    })
  }

  function pickFork() {
    if (root.sessionAt(root.cursor) !== null) root.pickSession(root.sessionAt(root.cursor), "fork")
    else root.noticeRequested("Ctrl+F forks the highlighted session. Pick one with ↑/↓ first.", "warn", null)
  }

  // fromRow: Ctrl+N, a new session in the highlighted row's folder (the left row's folder
  // when the left pane has the cursor).
  function pickNew(fromRow) {
    var cwd = root.newCwd
    var harness = root.newHarness
    if (fromRow && root.focusPane === "places") {
      var p = root.placeOf(root.leftRows[root.leftCursor])
      if (p !== null && p.path !== "") cwd = p.path
    } else if (fromRow && root.folderAt(root.cursor) !== "") {
      cwd = root.folderAt(root.cursor)
    } else if (fromRow && root.sessionAt(root.cursor) !== null) {
      var r = root.sessionAt(root.cursor)
      if (r) {
        cwd = String(r.cwd || "")
        if (root.filter === "all") harness = r.harness
      }
    }
    if (cwd === "") {
      root.noticeRequested("Type a folder such as ~/proj/api, or press Ctrl+N on a session.", "warn", null)
      return
    }
    // Say what the helper would refuse before asking it.
    var refusal = root.refusalFor(cwd)
    if (refusal !== "") {
      root.noticeRequested(refusal, "warn", null)
      return
    }
    if (root.agentBlocked(harness)) {
      root.noticeRequested(root.blockedSentence(harness), "warn", null)
      return
    }
    if (cwd === root.workspacePath && root.workspaceRefused) {
      root.noticeRequested(root.workspaceRefusal, "warn", null)
      return
    }
    if (cwd === root.workspacePath && !root.workspaceExists && root.service && typeof root.service.loadWorkspace === "function") {
      // One request at a time, and its answer counts only while this same open is up.
      if (root._making) return
      root._making = true
      var gen = root._openGen
      root.service.loadWorkspace(true, function (res) {
        if (gen !== root._openGen) return
        root._making = false
        if (!root.active) return
        if (res && res.ok === true && typeof res.path === "string")
          root.picked({ harness: harness, mode: "new", sessionId: null, cwd: res.path, title: "No project", sessionPath: null })
        else
          root.noticeRequested(res && res.code !== "invalid_cwd" && typeof res.message === "string" && res.message !== ""
            ? res.message : root.workspaceRefusal, "warn", null)
      })
      return
    }
    if (cwd === root.queryPath && root.walk.exists === false && !root._checks.hasOwnProperty(cwd)) {
      root.noticeRequested(root.checkSentence(cwd, { reason: "missing" }), "warn", null)
      return
    }
    // Only a folder the helper has looked at: it exists, and a job may run there. A no from
    // earlier in this open is said at once and asked again behind it, since the folder may have
    // been fixed meanwhile; the suggestions it brought stay up.
    var c = root._checks.hasOwnProperty(cwd) ? root._checks[cwd] : null
    if (c !== null && c.state !== "ok" && c.state !== "unknown") {
      root.noticeRequested(root.checkSentence(cwd, c), "warn", null)
      root.checkFolder(cwd, true)
      return
    }
    // No project has its own check (`workspace`), made above or answered when the sheet opened.
    if (c === null && root.canCheck && cwd !== root.workspacePath) {
      root._pendingNew = { cwd: cwd, harness: harness, gen: root._openGen }
      root.checkFolder(cwd)
      return
    }
    root.finishNew(cwd, harness)
  }

  function finishNew(cwd, harness) {
    root.picked({ harness: harness, mode: "new", sessionId: null, cwd: cwd,
                  title: cwd === root.workspacePath ? "No project" : root.basename(cwd), sessionPath: null })
  }

  function pickCursor() {
    if (root.cursor <= 0) root.pickNew(false)
    else if (root.folderAt(root.cursor) !== "") root.goToFolder(root.folderAt(root.cursor))
    else root.pickSession(root.sessionAt(root.cursor), "resume")
  }

  function stepFilter(dir) {
    var i = root.filters.indexOf(root.filter)
    root.filter = root.filters[(i + dir + root.filters.length) % root.filters.length]
    root._cursorTouched = false
    root.resetCursor()
  }

  // The right cursor moved by hand: remember the session under it, even when the index stays.
  function setCursorByHand(index) {
    root._cursorTouched = true
    root.cursor = Math.max(0, Math.min(root.rowCount - 1, index))
    root._rowKey = root.cursor > 0 ? root.keyAt(root.cursor) : ""
  }

  function moveCursor(delta) { root.setCursorByHand(root.cursor + delta) }

  function editQuery(text) {
    root.query = text
    root._cursorTouched = false
    root.resetCursor()
  }

  function handleKey(event) {
    var key = event.key
    var mods = event.modifiers
    var ctrl = (mods & Qt.ControlModifier) !== 0
    if ((mods & (Qt.AltModifier | Qt.MetaModifier)) !== 0) return true

    if (key === Qt.Key_Escape) {
      if (root.query === "") return false
      root.editQuery("")
      return true
    }
    if (key === Qt.Key_Tab || key === Qt.Key_Backtab) {
      root.focusPane = root.focusPane === "places" ? "list" : "places"
      return true
    }
    if (ctrl && key === Qt.Key_N) { root.pickNew(true); return true }
    if (ctrl && key === Qt.Key_H) { root.toggleHidden(); return true }
    if (root.focusPane === "places") {
      if (key === Qt.Key_Return || key === Qt.Key_Enter) { root.focusPane = "list"; return true }
      if (key === Qt.Key_Right) { root.stepTree(1); return true }
      if (key === Qt.Key_Left) { root.stepTree(-1); return true }
      if (key === Qt.Key_Up || (ctrl && key === Qt.Key_P)) { root.moveLeft(-1); return true }
      if (key === Qt.Key_Down) { root.moveLeft(1); return true }
      if (key === Qt.Key_PageUp) { root.moveLeft(-8); return true }
      if (key === Qt.Key_PageDown) { root.moveLeft(8); return true }
      if (key === Qt.Key_Home) { root.moveLeft(-root.leftRows.length); return true }
      if (key === Qt.Key_End) { root.moveLeft(root.leftRows.length); return true }
    } else {
      if (ctrl && key === Qt.Key_F) { root.pickFork(); return true }
      if (key === Qt.Key_Return || key === Qt.Key_Enter) { root.pickCursor(); return true }
      if (key === Qt.Key_Left) { root.stepFilter(-1); return true }
      if (key === Qt.Key_Right) { root.stepFilter(1); return true }
      if (key === Qt.Key_Up || (ctrl && key === Qt.Key_P)) { root.moveCursor(-1); return true }
      if (key === Qt.Key_Down) { root.moveCursor(1); return true }
      if (key === Qt.Key_PageUp) { root.moveCursor(-8); return true }
      if (key === Qt.Key_PageDown) { root.moveCursor(8); return true }
      if (key === Qt.Key_Home) { root.setCursorByHand(0); return true }
      if (key === Qt.Key_End) { root.setCursorByHand(root.rowCount - 1); return true }
    }
    if (Util.editsFilter(event, root.query)) {
      root.editQuery(Util.editedFilter(event, root.query))
      return true
    }
    var text = String(event.text || "")
    if (!ctrl && text.length === 1 && text.charCodeAt(0) >= 32 && text.charCodeAt(0) !== 127) {
      if (root.query.length < 200) root.editQuery(root.query + text)
      return true
    }
    return false
  }

  // Scrolled once the views have the new rows: a jump that also adds rows lands past the old end.
  onCursorChanged: {
    root._rowKey = root.cursor > 0 ? root.keyAt(root.cursor) : ""
    Qt.callLater(function () {
      if (root.cursor === 0) list.positionViewAtBeginning()
      else if (list.count > root.cursor - 1) list.positionViewAtIndex(root.cursor - 1, ListView.Contain)
    })
  }
  onLeftCursorChanged: {
    root._leftKey = root.leftKeyOf(root.leftRows[root.leftCursor])
    Qt.callLater(function () { if (tree.count > root.leftCursor) tree.positionViewAtIndex(root.leftCursor, ListView.Contain) })
  }
  onQueryPathChanged: if (root.active && root.queryPath !== "") cwdReload.restart()
  onNewCwdChanged: if (root.active) checkTimer.restart()
  onActiveChanged: if (root.active) checkTimer.restart()
  onSuggestingChanged: if (!root._cursorTouched) root.resetCursor()
  onLeftRowsChanged: {
    var lost = ""
    if (root._leftKey !== "" && root.leftKeyOf(root.leftRows[root.leftCursor]) !== root._leftKey) {
      lost = root._leftKey
      for (var k = 0; k < root.leftRows.length; k++) {
        if (root.leftKeyOf(root.leftRows[k]) === root._leftKey) {
          root.leftCursor = k
          lost = ""
          break
        }
      }
    }
    // The row under the cursor is gone (its folder was closed or hidden): the nearest folder
    // above it that is still listed takes the cursor and the place, so they never disagree.
    if (lost.indexOf("dir:") === 0 && root.queryPath === "") {
      var path = lost.slice(4)
      var best = -1, bestLength = -1
      for (var a = 0; a < root.leftRows.length; a++) {
        var up = root.leftRows[a]
        if (up.kind === "dir" && up.path !== "" && path.indexOf(up.path + "/") === 0 && up.path.length > bestLength) {
          best = a
          bestLength = up.path.length
        }
      }
      if (best >= 0) {
        root.selectLeft(best)
        return
      }
    }
    root.applyReveal()
    if (!root.leftTakesCursor(root.leftRows[root.leftCursor])) {
      var i = Math.min(root.leftCursor, root.leftRows.length - 1)
      while (i > 0 && !root.leftTakesCursor(root.leftRows[i])) i--
      root.leftCursor = Math.max(0, i)
    }
  }

  // The folder search runs once the typing rests.
  Timer {
    id: findTimer
    interval: 160
    repeat: false
    onTriggered: root.findFolders()
  }

  // The place's folder is checked once the cursor or the typing rests on it.
  Timer {
    id: checkTimer
    interval: 200
    repeat: false
    onTriggered: if (root.active && root.newCwd !== root.home && root.newCwd.charAt(0) === "/") root.checkFolder(root.newCwd)
  }

  Timer {
    id: cwdReload
    interval: 500
    repeat: false
    onTriggered: root.reloadForPath()
  }

  // The folder's own list, once the cursor rests on it.
  Timer {
    id: folderRead
    interval: 250
    repeat: false
    onTriggered: if (root.active && root.queryPath === "") root.readFolder(root.placePath)
  }

  onRowsChanged: {
    if (root.restoreCursor()) return
    // The first answer lands after the sheet opened: start on the newest session.
    if (root.active && !root._cursorTouched && root.cursor === 0 && root.query === "" && root.rows.length > 0) root.cursor = 1
  }
  // Found folders land after the words were typed: the best one takes the cursor, unless the
  // cursor was moved by hand meanwhile.
  onFolderRowsChanged: {
    if (root.restoreCursor()) return
    if (!root._cursorTouched) root.resetCursor()
  }
  onSearchWordsChanged: if (root.active && root.searchWords !== "") findTimer.restart()

  // Opaque, so the view underneath never shows through; no entrance animation
  // (a keyboard-opened overlay appears at once, R6 6.1).
  Rectangle {
    anchors.fill: parent
    color: root.theme.surface

    MouseArea {
      anchors.fill: parent
      acceptedButtons: Qt.AllButtons
      onWheel: function (wheel) { wheel.accepted = true }
    }
  }

  Item {
    id: head
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: parent.top
    height: Style.space(28)

    // Back first, as in every desktop window: the way out sits where the eye starts.
    ActionButton {
      id: backButton
      anchors.left: parent.left
      anchors.verticalCenter: parent.verticalCenter
      theme: root.theme
      hasCursor: backButton.hovered
      glyph: "\uDB80\uDC4D"   // md-arrow_left U+F004D
      text: "Back"
      shortcut: "Esc"
      onClicked: root.close()
    }

    Text {
      anchors.left: backButton.right
      anchors.leftMargin: Style.space(12)
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      textFormat: Text.PlainText
      text: "Where to run"
      color: root.theme.strong
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.title
      font.bold: true
      elide: Text.ElideRight
      maximumLineCount: 1
    }
  }

  SearchField {
    id: search
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.top: head.bottom
    anchors.topMargin: Style.spacing.lg
    theme: root.theme
    text: root.query
    placeholder: "Search folders and sessions, such as projects, or type a path such as ~/code/api"
    active: root.active
    trailing: root.loading ? "Reading sessions…"
      : (root.query !== "" || root.filter !== "all"
        ? (root.folderRows.length > 0
          ? root.foundEntries.length + (root.foundTruncated ? "+" : "") + (root.foundEntries.length === 1 ? " folder" : " folders") + "  ·  " : "")
          + root.rows.length + " of " + root.countFor(root.filter)
        : root.countFor("all") + (root.countFor("all") === 1 ? " session" : " sessions"))
  }

  // ---------------------------------------------------------------- places and folders

  Item {
    id: leftPane
    anchors.left: parent.left
    anchors.top: search.bottom
    anchors.topMargin: Style.spacing.lg
    anchors.bottom: footerColumn.top
    anchors.bottomMargin: Style.spacing.lg
    width: Math.round(root.width * 0.35)

    ListView {
      id: tree
      anchors.fill: parent
      clip: true
      boundsBehavior: Flickable.StopAtBounds
      model: root.leftRows.length

      delegate: Item {
        id: lrow
        required property int index
        readonly property var info: root.leftRows[lrow.index] || ({ kind: "note", text: "", depth: 0 })
        readonly property bool takes: root.leftTakesCursor(lrow.info)
        readonly property bool cursorHere: root.focusPane === "places" && root.leftCursor === lrow.index
        readonly property bool isPlace: lrow.index === root.placeRowIndex
        readonly property real indent: Style.space(14) * (lrow.info.kind === "dir" ? lrow.info.depth : 0)

        // Hover moves the highlight, never the place on the right.
        function hovered(x, y) {
          if (!root.pointerMoved(lrow, x, y)) return
          root.focusPane = "places"
          root._reveal = ""
          root.leftCursor = lrow.index
        }
        readonly property var marks: lrow.info.kind === "place" && lrow.info.id === "workspace"
          ? root.marksFor(root.workspacePath)
          : (lrow.info.kind === "dir" || lrow.info.kind === "recent" ? root.marksFor(lrow.info.path) : [])
        width: tree.width
        height: lrow.info.kind === "head" ? Style.space(26) : Style.space(30)

        CursorSurface {
          anchors.fill: parent
          visible: lrow.takes
          hasCursor: lrow.cursorHere
          current: lrow.isPlace
          foreground: root.theme.fg
          accent: root.theme.accent
        }

        // Group label, in the section label voice of Compose.
        Text {
          anchors.left: parent.left
          anchors.leftMargin: Style.space(6)
          anchors.bottom: parent.bottom
          anchors.bottomMargin: Style.space(5)
          visible: lrow.info.kind === "head"
          textFormat: Text.PlainText
          text: lrow.info.kind === "head" ? lrow.info.text : ""
          color: root.focusPane === "places" ? root.theme.accentInk : root.theme.soft
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.label
          font.bold: true
          font.letterSpacing: root.theme.type.tracking
        }

        Text {
          id: chevron
          anchors.left: parent.left
          anchors.leftMargin: Style.space(4) + lrow.indent
          anchors.verticalCenter: parent.verticalCenter
          width: Style.space(14)
          visible: lrow.info.kind === "dir"
          textFormat: Text.PlainText
          text: lrow.info.kind !== "dir" || lrow.info.closed === true ? "" : (lrow.info.expanded ? root.glyph.down : root.glyph.right)
          color: root.theme.soft
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.glyph
        }

        Text {
          id: icon
          anchors.left: parent.left
          anchors.leftMargin: lrow.info.kind === "dir" ? chevron.anchors.leftMargin + Style.space(15) : Style.space(8)
          anchors.verticalCenter: parent.verticalCenter
          visible: lrow.info.kind !== "head" && lrow.info.kind !== "note"
          textFormat: Text.PlainText
          text: {
            var r = lrow.info
            if (r.kind === "place") return r.id === "recent" ? root.glyph.recent : root.glyph.workspace
            if (r.kind === "dir" && r.depth === 0 && r.path === root.home) return root.glyph.home
            if (r.kind === "dir" && r.link) return root.glyph.link
            return r.kind === "dir" && r.expanded ? root.glyph.folderOpen : root.glyph.folder
          }
          color: lrow.isPlace || lrow.cursorHere ? root.theme.accentInk : root.theme.soft
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.glyph
        }

        // A note under an open folder: reading, empty, unreadable.
        Text {
          anchors.left: parent.left
          anchors.leftMargin: Style.space(37) + Style.space(14) * Math.max(0, lrow.info.depth - 1)
          anchors.right: parent.right
          anchors.rightMargin: Style.space(8)
          anchors.verticalCenter: parent.verticalCenter
          visible: lrow.info.kind === "note"
          textFormat: Text.PlainText
          text: lrow.info.kind === "note" ? lrow.info.text : ""
          color: lrow.info.warn === true ? root.theme.warnInk : root.theme.soft
          elide: Text.ElideRight
          maximumLineCount: 1
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.meta
        }

        Item {
          id: label
          anchors.left: icon.right
          anchors.leftMargin: Style.space(8)
          anchors.right: markRow.left
          anchors.rightMargin: Style.space(8)
          anchors.verticalCenter: parent.verticalCenter
          height: nameText.implicitHeight
          visible: lrow.info.kind !== "head" && lrow.info.kind !== "note"
          readonly property string caption: {
            var r = lrow.info
            if (r.kind === "place" && r.id === "workspace") return Model.shortPath(root.workspacePath, root.home)
            if (r.kind === "recent") {
              var slash = r.path.lastIndexOf("/")
              return Model.shortPath(slash > 0 ? r.path.slice(0, slash) : "/", root.home)
            }
            if (r.kind === "dir" && r.outside === true) return "outside your home folder"
            if (r.kind === "dir" && r.closed === true) return "cannot be opened"
            if (r.kind === "dir" && r.own === false) return "another user's"
            return ""
          }

          Text {
            id: nameText
            width: Math.max(0, Math.min(implicitWidth, label.width - (gitMark.visible ? gitMark.width + Style.space(6) : 0)
              - (captionText.visible ? Math.min(captionText.implicitWidth, Style.space(90)) + Style.space(8) : 0)))
            textFormat: Text.PlainText
            text: {
              var r = lrow.info
              if (r.kind === "place") return r.id === "recent" ? "All recent sessions" : "No project"
              return typeof r.name === "string" ? r.name : ""
            }
            color: lrow.info.closed === true || lrow.info.own === false ? root.theme.soft
              : (lrow.cursorHere ? root.theme.fg : (lrow.info.hidden === true ? root.theme.readable : root.theme.strong))
            elide: Text.ElideRight
            maximumLineCount: 1
            font.family: root.theme.fontFamily
            font.pixelSize: root.theme.type.body
            font.bold: lrow.info.kind === "place"
          }

          Text {
            id: gitMark
            x: nameText.width + Style.space(6)
            anchors.verticalCenter: nameText.verticalCenter
            visible: lrow.info.git === true
            textFormat: Text.PlainText
            text: root.glyph.git
            color: root.theme.soft
            font.family: root.theme.fontFamily
            font.pixelSize: root.theme.type.glyph
          }

          Text {
            id: captionText
            x: nameText.width + (gitMark.visible ? gitMark.width + Style.space(12) : Style.space(8))
            anchors.baseline: nameText.baseline
            width: Math.max(0, label.width - x)
            visible: label.caption !== ""
            textFormat: Text.PlainText
            text: label.caption
            color: root.theme.soft
            elide: Text.ElideMiddle
            maximumLineCount: 1
            font.family: root.theme.fontFamily
            font.pixelSize: root.theme.type.meta
          }
        }

        // Where each agent has worked: its mark and how many sessions, in this folder.
        Row {
          id: markRow
          anchors.right: parent.right
          anchors.rightMargin: Style.space(8)
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(7)
          visible: lrow.info.kind !== "head" && lrow.info.kind !== "note"

          Repeater {
            model: lrow.marks.length > 3 ? lrow.marks.slice(0, 3) : lrow.marks

            delegate: Row {
              id: markCell
              required property var modelData
              spacing: Style.space(3)

              AgentMark {
                anchors.verticalCenter: parent.verticalCenter
                theme: root.theme
                agent: markCell.modelData.harness
                size: Style.space(11)
              }

              Text {
                anchors.verticalCenter: parent.verticalCenter
                textFormat: Text.PlainText
                text: String(markCell.modelData.n)
                color: root.theme.readable
                font.family: root.theme.fontFamily
                font.pixelSize: root.theme.type.meta
                font.features: root.theme.type.digits
              }
            }
          }

          Text {
            anchors.verticalCenter: parent.verticalCenter
            visible: lrow.marks.length > 3 || (lrow.info.kind === "place" && lrow.info.id === "recent")
              || (lrow.marks.length === 0 && lrow.info.kind === "dir" && lrow.info.depth > 0 && root.countBelow(lrow.info.path) > 0)
            textFormat: Text.PlainText
            text: {
              if (lrow.info.kind === "place") return String(root.allRows.length)
              if (lrow.marks.length > 3) return "+" + (lrow.marks.length - 3)
              return root.countBelow(lrow.info.path) + " inside"
            }
            color: root.theme.soft
            font.family: root.theme.fontFamily
            font.pixelSize: root.theme.type.meta
            font.features: root.theme.type.digits
          }
        }

        MouseArea {
          anchors.fill: parent
          visible: lrow.takes
          hoverEnabled: true
          cursorShape: Qt.PointingHandCursor
          onEntered: lrow.hovered(mouseX, mouseY)
          onPositionChanged: function (mouse) { lrow.hovered(mouse.x, mouse.y) }
          onClicked: function (mouse) {
            root.focusPane = "places"
            var r = lrow.info
            var onChevron = r.kind === "dir" && mouse.x < chevron.x + chevron.width + Style.space(2)
            if (onChevron) {
              root.leftCursor = lrow.index
              root.setExpanded(r.path, !r.expanded)
              return
            }
            root.selectLeft(lrow.index)
            if (r.kind === "dir" && !r.expanded) root.setExpanded(r.path, true)
          }
        }
      }
    }
  }

  // The hairline between the panes.
  Rectangle {
    id: divider
    anchors.left: leftPane.right
    anchors.leftMargin: Style.space(12)
    anchors.top: leftPane.top
    anchors.bottom: leftPane.bottom
    width: Math.max(1, Style.space(1))
    color: root.theme.faint
  }

  // ---------------------------------------------------------------- sessions of the place

  Item {
    id: rightPane
    anchors.left: divider.right
    anchors.leftMargin: Style.space(12)
    anchors.right: parent.right
    anchors.top: leftPane.top
    anchors.bottom: leftPane.bottom

    Item {
      id: placeHead
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.top: parent.top
      height: Style.space(40)

      Text {
        id: placeTitleText
        anchors.left: parent.left
        anchors.top: parent.top
        width: Math.min(implicitWidth, parent.width - placePathText.implicitWidth - Style.space(10))
        textFormat: Text.PlainText
        text: root.placeTitle
        color: root.theme.strong
        elide: Text.ElideRight
        maximumLineCount: 1
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.body
        font.bold: true
      }

      Text {
        id: placePathText
        x: placeTitleText.width + Style.space(10)
        anchors.baseline: placeTitleText.baseline
        width: Math.max(0, Math.min(implicitWidth, parent.width - x))
        visible: root.activePath !== "" && root.activeKind !== "home"
        textFormat: Text.PlainText
        text: Model.shortPath(root.activePath, root.home)
        color: root.theme.soft
        elide: Text.ElideMiddle
        maximumLineCount: 1
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.meta
      }

      Text {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        textFormat: Text.PlainText
        text: root.placeFact
        color: root.activeKind === "home" ? root.theme.warnInk : root.theme.readable
        elide: Text.ElideRight
        maximumLineCount: 1
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.meta
      }
    }

    // Starting fresh is the one action this pane leads with, so it is a large button of its
    // own above the sessions: nobody has to read the list to find it. It names the folder and
    // the agent, or says why no session can start here.
    Item {
      id: newButton
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.top: placeHead.bottom
      anchors.topMargin: Style.spacing.lg
      height: Style.space(52)
      readonly property bool cursorHere: root.focusPane === "list" && root.cursor === 0
      readonly property bool ready: root.newReady
      readonly property bool showAgent: newButton.ready && root.newState !== "none"

      function hovered(x, y) {
        if (!root.pointerMoved(newButton, x, y)) return
        root.focusPane = "list"
        root.setCursorByHand(0)
      }

      BorderSurface {
        anchors.fill: parent
        radius: Style.cornerRadius
        color: newButton.ready
          ? Util.alpha(root.theme.accent, newButton.cursorHere ? 0.40 : 0.20)
          : Util.alpha(root.theme.fg, newButton.cursorHere ? 0.08 : 0.04)
        // The border follows the cursor only, so the button never reads as a second highlight.
        borderSpec: Border.controlSpec(newButton.cursorHere ? "hover-cursor" : "normal", root.theme.fg, root.theme.accent)
      }

      // The plus in a disc of its own, so the button reads as "add" before a word is read.
      Rectangle {
        id: plusDisc
        anchors.left: parent.left
        anchors.leftMargin: Style.space(12)
        anchors.verticalCenter: parent.verticalCenter
        width: Style.space(28)
        height: width
        radius: width / 2
        color: newButton.ready ? Util.alpha(root.theme.accent, newButton.cursorHere ? 0.75 : 0.55) : Util.alpha(root.theme.fg, 0.08)

        Text {
          anchors.centerIn: parent
          textFormat: Text.PlainText
          text: root.glyph.plus
          color: newButton.ready ? root.theme.fg : root.theme.soft
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.glyphLarge
        }
      }

      Text {
        id: newTitle
        anchors.left: plusDisc.right
        anchors.leftMargin: Style.space(12)
        anchors.right: newAgent.left
        anchors.rightMargin: Style.space(12)
        anchors.top: parent.top
        anchors.topMargin: Style.space(8)
        textFormat: Text.PlainText
        text: "New session"
        color: newButton.ready ? root.theme.fg : root.theme.soft
        elide: Text.ElideRight
        maximumLineCount: 1
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.title
        font.bold: true
      }

      Text {
        anchors.left: newTitle.left
        anchors.right: newKey.left
        anchors.rightMargin: Style.space(12)
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Style.space(8)
        textFormat: Text.PlainText
        text: root.newLine
        color: newButton.ready || root.newState === "none" ? root.theme.readable : root.theme.warnInk
        elide: Text.ElideMiddle
        maximumLineCount: 1
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.meta
      }

      // Which agent it starts: its mark and name, in its own ink. ←/→ (the agent filter) changes it.
      Row {
        id: newAgent
        anchors.right: parent.right
        anchors.rightMargin: Style.space(12)
        anchors.top: parent.top
        anchors.topMargin: Style.space(9)
        spacing: Style.space(6)
        visible: newButton.showAgent

        AgentMark {
          anchors.verticalCenter: parent.verticalCenter
          theme: root.theme
          agent: root.newHarness
          size: Style.space(14)
        }

        Text {
          anchors.verticalCenter: parent.verticalCenter
          textFormat: Text.PlainText
          text: Model.harnessName(root.newHarness)
          color: root.theme.harnessInk(root.newHarness)
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.body
        }
      }

      Text {
        id: newKey
        anchors.right: parent.right
        anchors.rightMargin: Style.space(12)
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Style.space(8)
        visible: newButton.showAgent
        textFormat: Text.PlainText
        text: newButton.cursorHere ? (root.filter === "all" ? "Enter start  ·  ←/→ agent" : "Enter start") : "Ctrl+N"
        color: newButton.cursorHere ? root.theme.accentInk : root.theme.soft
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.meta
      }

      MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onEntered: newButton.hovered(mouseX, mouseY)
        onPositionChanged: function (mouse) { newButton.hovered(mouse.x, mouse.y) }
        onClicked: {
          root.focusPane = "list"
          root.setCursorByHand(0)
          root.pickNew(false)
        }
      }
    }

    // The way into a typed folder that is not there: the folders it most likely means.
    Text {
      id: suggestHead
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.top: newButton.bottom
      anchors.topMargin: Style.spacing.lg
      visible: root.suggesting
      textFormat: Text.PlainText
      text: "Did you mean one of these folders? Enter opens it."
      color: root.theme.strong
      elide: Text.ElideRight
      maximumLineCount: 1
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.body
    }

    Flow {
      id: filterRow
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.top: newButton.bottom
      anchors.topMargin: Style.spacing.lg
      spacing: Style.space(6)
      visible: !root.suggesting

      Repeater {
        model: root.filters

        delegate: Chip {
          id: filterChip
          required property string modelData
          theme: root.theme
          pill: true
          harness: filterChip.modelData === "all" ? "" : filterChip.modelData
          text: filterChip.modelData === "all" ? "All" : Model.harnessShortName(filterChip.modelData)
          note: root.allResult === null ? "" : String(root.countFor(filterChip.modelData))
          selected: root.filter === filterChip.modelData
          hasCursor: root.filter === filterChip.modelData
          onClicked: {
            root.filter = filterChip.modelData
            root._cursorTouched = false
            root.resetCursor()
          }
        }
      }
    }

    ListView {
      id: list
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.top: root.suggesting ? suggestHead.bottom : filterRow.bottom
      anchors.topMargin: Style.spacing.lg
      // Whole rows only, so the last one is never cut through.
      height: Math.floor(Math.max(0, rightPane.height - list.y) / Style.space(34)) * Style.space(34)
      clip: true
      boundsBehavior: Flickable.StopAtBounds
      model: root.rowCount - 1

      // Row i of the list is cursor row i + 1: row 0 is the New session button above.
      delegate: Item {
        id: row
        required property int index
        readonly property int slot: row.index + 1
        readonly property string suggestion: root.folderAt(row.slot)
        readonly property var session: root.sessionAt(row.slot)
        readonly property var hit: row.suggestion !== "" ? root.hitFor(row.suggestion) : null
        readonly property bool cursorHere: root.focusPane === "list" && root.cursor === row.slot
        readonly property bool chosen: row.session !== null && root.initialSessionId !== "" && row.session.id === root.initialSessionId
        readonly property bool showFolder: row.session !== null && root.activeKind === "recent"

        function hovered(x, y) {
          if (!root.pointerMoved(row, x, y)) return
          root.focusPane = "list"
          root.setCursorByHand(row.slot)
        }
        width: list.width
        height: Style.space(34)

        CursorSurface {
          anchors.fill: parent
          hasCursor: row.cursorHere
          foreground: root.theme.fg
          accent: root.theme.accent
        }

        HarnessRail {
          anchors.left: parent.left
          anchors.leftMargin: Style.space(2)
          anchors.top: parent.top
          anchors.topMargin: Style.space(5)
          anchors.bottom: parent.bottom
          anchors.bottomMargin: Style.space(5)
          visible: row.session !== null
          theme: root.theme
          harness: row.session ? row.session.harness : ""
        }

        AgentMark {
          id: rowMark
          anchors.left: parent.left
          anchors.leftMargin: Style.space(14)
          anchors.verticalCenter: parent.verticalCenter
          visible: row.session !== null
          theme: root.theme
          agent: row.session ? row.session.harness : ""
          size: Style.space(14)
        }

        Text {
          anchors.left: parent.left
          anchors.leftMargin: Style.space(14)
          anchors.verticalCenter: parent.verticalCenter
          visible: row.suggestion !== ""
          textFormat: Text.PlainText
          text: root.glyph.folder
          color: row.cursorHere ? root.theme.accentInk : root.theme.soft
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.glyph
        }

        Text {
          id: rowTitle
          anchors.left: parent.left
          anchors.leftMargin: Style.space(38)
          anchors.verticalCenter: parent.verticalCenter
          width: row.session
            ? (row.showFolder ? parent.width * 0.40 : parent.width - Style.space(38) - metaRow.width - Style.space(16))
            : Math.min(implicitWidth, parent.width * 0.45)
          // A folder's name carries the matched letters in the accent ink: StyledText over a name
          // whose every character was escaped (markedName). A session's title is plain.
          textFormat: row.session ? Text.PlainText : Text.StyledText
          text: {
            if (row.session) return row.session.title ? String(row.session.title) : Model.elideMiddle(row.session.id, 13)
            return root.markedName(root.basename(row.suggestion), row.hit ? row.hit.marks : [])
          }
          color: row.cursorHere ? root.theme.fg : root.theme.strong
          elide: Text.ElideRight
          maximumLineCount: 1
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.body
        }

        // Where a folder lives, in the soft ink: "in Home › Projects".
        Text {
          anchors.left: rowTitle.right
          anchors.leftMargin: Style.space(10)
          anchors.right: metaRow.left
          anchors.rightMargin: Style.space(12)
          anchors.verticalCenter: parent.verticalCenter
          visible: row.suggestion !== ""
          textFormat: Text.PlainText
          text: row.suggestion !== "" ? root.crumbOf(row.suggestion) : ""
          color: root.theme.soft
          elide: Text.ElideMiddle
          maximumLineCount: 1
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.data
        }

        Text {
          anchors.left: rowTitle.right
          anchors.leftMargin: Style.space(12)
          anchors.right: metaRow.left
          anchors.rightMargin: Style.space(12)
          anchors.verticalCenter: parent.verticalCenter
          visible: row.showFolder
          textFormat: Text.PlainText
          text: row.session ? Model.shortPath(row.session.cwd, root.home) : ""
          color: root.theme.readable
          elide: Text.ElideMiddle
          maximumLineCount: 1
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.data
        }

        Row {
          id: metaRow
          anchors.right: parent.right
          anchors.rightMargin: Style.space(12)
          anchors.verticalCenter: parent.verticalCenter
          spacing: Style.space(14)

          // A folder that has sessions says how many.
          Text {
            width: Style.space(70)
            horizontalAlignment: Text.AlignRight
            visible: row.suggestion !== "" && !!root.countsByCwd[row.suggestion]
            textFormat: Text.PlainText
            text: row.suggestion !== "" && root.countsByCwd[row.suggestion]
              ? root.countsByCwd[row.suggestion].total + (root.countsByCwd[row.suggestion].total === 1 ? " session" : " sessions") : ""
            color: root.theme.soft
            font.family: root.theme.fontFamily
            font.pixelSize: root.theme.type.meta
            font.features: root.theme.type.digits
          }

          Text {
            width: Style.space(70)
            horizontalAlignment: Text.AlignRight
            visible: row.session !== null
            textFormat: Text.PlainText
            text: row.session ? root.ago(row.session.updatedAtMs) : ""
            color: root.theme.soft
            font.family: root.theme.fontFamily
            font.pixelSize: root.theme.type.meta
            font.features: root.theme.type.digits
          }

          Text {
            width: Style.space(64)
            horizontalAlignment: Text.AlignRight
            visible: row.session !== null
            textFormat: Text.PlainText
            text: row.session && typeof row.session.messages === "number" ? row.session.messages + " msgs" : ""
            color: root.theme.soft
            font.family: root.theme.fontFamily
            font.pixelSize: root.theme.type.meta
            font.features: root.theme.type.digits
          }

          Text {
            width: Style.space(52)
            horizontalAlignment: Text.AlignRight
            textFormat: Text.PlainText
            text: row.chosen ? root.glyph.check + " now" : (row.session ? "resume" : "open")
            color: row.chosen ? root.theme.okInk : (row.cursorHere ? root.theme.accentInk : root.theme.soft)
            font.family: root.theme.fontFamily
            font.pixelSize: root.theme.type.meta
          }
        }

        MouseArea {
          anchors.fill: parent
          hoverEnabled: true
          cursorShape: Qt.PointingHandCursor
          onEntered: row.hovered(mouseX, mouseY)
          onPositionChanged: function (mouse) { row.hovered(mouse.x, mouse.y) }
          onClicked: {
            root.focusPane = "list"
            root.cursor = row.slot
            root.pickCursor()
          }
        }
      }

      Text {
        anchors.left: parent.left
        anchors.leftMargin: Style.space(38)
        y: Style.space(10)
        width: parent.width - Style.space(50)
        visible: root.rows.length === 0 && root.folderRows.length === 0 && !root.noMatch && !root.suggesting
        textFormat: Text.PlainText
        text: {
          if (root.loading) return "Reading sessions…"
          if (root.activeKind === "home") return "Sessions started in ~ itself cannot be resumed here."
          if (root.activeKind !== "recent" && root.folderLoading) return "Reading the sessions of " + Model.shortPath(root.activePath, root.home) + "…"
          if (root.queryPath !== "" && root.newState === "missing") return "No folder by that name here. Check the path, or pick a folder on the left."
          if (root.folderFailed) return "This folder's sessions could not be read. New session above starts one."
          if (root.result === null) return "Sessions could not be read."
          if (root.activeKind === "recent") return "No sessions to resume here. Start a new one above."
          return "No sessions here in the last " + (typeof root.result.limitDays === "number" ? root.result.limitDays : 90)
            + " days. New session above starts one."
        }
        color: root.loading || root.folderLoading ? root.theme.soft : root.theme.readable
        elide: Text.ElideRight
        maximumLineCount: 1
        font.family: root.theme.fontFamily
        font.pixelSize: root.theme.type.body
      }

      // The fact, then the way back, in two inks.
      Row {
        anchors.left: parent.left
        anchors.leftMargin: Style.space(38)
        y: Style.space(10)
        width: parent.width - Style.space(50)
        visible: root.rows.length === 0 && root.folderRows.length === 0 && root.noMatch
        spacing: Style.space(7)

        Text {
          width: Math.max(0, Math.min(implicitWidth, parent.width - recoverText.implicitWidth - parent.spacing))
          textFormat: Text.PlainText
          text: root.finding ? "Searching your folders for \"" + root.query + "\"…"
            : (root.foundFailed ? "Folders could not be searched. No sessions match \"" + root.query + "\"."
              : "No folder or session matches \"" + root.query + "\".")
          color: root.theme.strong
          elide: Text.ElideRight
          maximumLineCount: 1
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.body
        }

        Text {
          id: recoverText
          textFormat: Text.PlainText
          text: "Esc clears the filter."
          color: root.theme.soft
          font.family: root.theme.fontFamily
          font.pixelSize: root.theme.type.body
        }
      }
    }
  }

  Column {
    id: footerColumn
    anchors.left: parent.left
    anchors.right: parent.right
    anchors.bottom: parent.bottom
    spacing: Style.space(2)

    Text {
      width: parent.width
      visible: root.boundLine !== ""
      textFormat: Text.PlainText
      text: root.boundLine
      color: root.theme.soft
      elide: Text.ElideRight
      maximumLineCount: 1
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
    }

    Text {
      width: parent.width
      visible: root.piLine !== ""
      textFormat: Text.PlainText
      text: root.piLine
      color: root.theme.soft
      elide: Text.ElideRight
      maximumLineCount: 1
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
    }

    Text {
      width: parent.width
      visible: root.errorLine !== ""
      textFormat: Text.PlainText
      text: root.errorLine
      color: root.theme.warnInk
      elide: Text.ElideRight
      maximumLineCount: 1
      font.family: root.theme.fontFamily
      font.pixelSize: root.theme.type.meta
    }
  }
}
