import QtQuick
import Quickshell.Io
import "lib/Model.js" as Model

// The only place this plugin starts a process, and the only file that may say
// `Process {`. Every call to the helper is one of these, created for the call and
// destroyed after it.
//
// What it guarantees, whatever the child does:
// - the environment is exactly `environment` (clearEnvironment is always on),
// - argv[0] is an absolute path, so nothing is resolved through PATH,
// - stdout and stderr are counted as they arrive and the child is killed the
//   moment either passes its cap, rather than collected first and measured later,
// - a deadline ends it with TERM, then KILL 2 s later,
// - stdin carries `stdinText` once and is then closed, which is how the helper
//   knows the request is complete (the prompt never goes into argv or env); with
//   no text it is closed at once, so the child reads end of file,
// - `finished` fires exactly once per start(), even when the child never started.
Item {
  id: root

  property var command: []
  property var environment: ({})
  property string workingDirectory: ""
  property string stdinText: ""
  // Counted on raw chunks as they arrive (UTF-16 length, so never more than the
  // byte count of the same text). The helper caps its own output first.
  property int maxStdoutBytes: 65536
  property int maxStderrBytes: 16384
  property int deadlineMs: 10000

  readonly property bool running: root._running
  readonly property bool overflowed: root._overflowed
  readonly property bool timedOut: root._timedOut

  signal finished(int exitCode, bool overflowed, bool timedOut, string stdoutText, string stderrText)

  property bool _running: false
  property bool _settled: true
  property bool _overflowed: false
  property bool _timedOut: false
  property bool _signalled: false
  property bool _exitSeen: false
  property bool _crashed: false
  property int _exitCode: -1
  property int _outCount: 0
  property int _errCount: 0
  property var _outChunks: []
  property var _errChunks: []

  function start() {
    if (root._running || !root._settled || proc.running) return false
    var src = root.command
    if (!src || typeof src.length !== "number" || src.length < 1) return false
    var argv = []
    for (var i = 0; i < src.length; i++) argv.push(String(src[i]))
    if (argv[0].charAt(0) !== "/") return false

    var env = {}
    var given = root.environment || {}
    for (var k in given) {
      if (given[k] !== undefined && given[k] !== null) env[String(k)] = String(given[k])
    }

    root._overflowed = false
    root._timedOut = false
    root._signalled = false
    root._exitSeen = false
    root._crashed = false
    root._exitCode = -1
    root._outCount = 0
    root._errCount = 0
    root._outChunks = []
    root._errChunks = []

    proc.clearEnvironment = true
    proc.environment = env
    proc.command = argv
    if (root.workingDirectory !== "") proc.workingDirectory = root.workingDirectory
    // Always a pipe, closed right after start. A child started with stdin
    // "disabled" inherits a pipe that never reaches end of file, so anything that
    // reads stdin would wait for the deadline instead of seeing an empty request.
    proc.stdinEnabled = true

    root._settled = false
    root._running = true
    deadline.restart()
    proc.running = true
    return true
  }

  // TERM first: the helper unwinds, kills the process group of any tool call it
  // has in flight and releases its lock. KILL if it is still there after the grace.
  // A helper killed outright still takes its tool calls with it (they are started
  // with a parent-death signal), which is what onDestruction relies on.
  function kill() {
    if (!proc.running) return
    root._signalled = true
    proc.signal(15)
    killTimer.restart()
  }

  function _hardKill() {
    root._signalled = true
    if (proc.running) proc.signal(9)
    abandonTimer.restart()
  }

  function _takeOut(chunk) {
    if (root._settled || root._overflowed) return
    var s = String(chunk)
    if (root._outCount + s.length > root.maxStdoutBytes) {
      root._overflowed = true
      root._outChunks = []
      root._hardKill()
      return
    }
    root._outCount += s.length
    root._outChunks.push(s)
  }

  function _takeErr(chunk) {
    if (root._settled || root._overflowed) return
    var s = String(chunk)
    if (root._errCount + s.length > root.maxStderrBytes) {
      root._overflowed = true
      root._errChunks = []
      root._hardKill()
      return
    }
    root._errCount += s.length
    root._errChunks.push(s)
  }

  function _settle() {
    if (root._settled) return
    root._settled = true
    root._running = false
    deadline.stop()
    killTimer.stop()
    abandonTimer.stop()
    var code = (root._signalled || !root._exitSeen || root._crashed) ? -1 : root._exitCode
    var out = root._outChunks.join("")
    var err = root._errChunks.join("")
    root._outChunks = []
    root._errChunks = []
    root.finished(code, root._overflowed, root._timedOut, out, err)
  }

  Process {
    id: proc
    running: false
    clearEnvironment: true

    stdout: SplitParser {
      splitMarker: ""
      onRead: data => root._takeOut(data)
    }

    stderr: SplitParser {
      splitMarker: ""
      onRead: data => root._takeErr(data)
    }

    onStarted: {
      if (root.stdinText !== "") proc.write(root.stdinText)
      // Closing the pipe is what tells the helper the request is complete (or
      // that there is none).
      proc.stdinEnabled = false
    }

    // A child that failed to start never emits exited; running still drops.
    // The verdict waits a turn of the event loop so the last output chunk and
    // the exit status are both in before anyone reads them.
    onRunningChanged: if (!proc.running && !root._settled) Qt.callLater(root._settle)
  }

  Timer {
    id: deadline
    interval: Model.clampInterval(root.deadlineMs)
    repeat: false
    onTriggered: {
      if (root._settled) return
      root._timedOut = true
      if (proc.running) root.kill()
      else root._settle()
    }
  }

  Timer {
    id: killTimer
    interval: 2000
    repeat: false
    onTriggered: root._hardKill()
  }

  // A KILL that did not end the process within three seconds is not going to be
  // reported by it; settle anyway so the caller is never left waiting.
  Timer {
    id: abandonTimer
    interval: 3000
    repeat: false
    onTriggered: root._settle()
  }

  function _onExited(exitCode, exitStatus) {
    root._exitCode = Number(exitCode)
    root._exitSeen = true
    // QProcess::CrashExit is 1: the child died on a signal.
    root._crashed = Number(exitStatus) !== 0
  }

  // Connected here rather than as an `onExited:` handler: the handler form needs
  // the C++ QProcess::ExitStatus type at lint time, which never loads into qmllint.
  Component.onCompleted: proc.exited.connect(root._onExited)

  Component.onDestruction: {
    if (proc.running) proc.signal(9)
  }
}
