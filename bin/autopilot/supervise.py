"""Supervision of one agent child: prompt on stdin, bounded output, deadline, clean kill.

The agent runs in its own session so its whole process group can be signalled. Output is
read by raw chunks: stdout goes through a line framer (one JSON event per line, with a per-line
cap) and both streams feed a fixed-size ring that becomes the run log. Nothing read here is
ever written to the journal, and memory stays bounded however much the agent prints.
"""

import os
import re
import select
import signal
import subprocess
import time

from . import consts

_ANSI_RE = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
_CTRL_RE = re.compile(rb"[\x00-\x08\x0b-\x1f\x7f]")
_CHUNK = 65536
_POLL_S = 0.25
_POST_EXIT_DRAIN_S = 2.0
_STRAGGLER_GRACE_S = 2.0


class _Ring:
    """Keeps the last cap bytes of everything appended."""

    def __init__(self, cap):
        self.cap = cap
        self.buf = bytearray()
        self.total = 0

    def add(self, data):
        self.total += len(data)
        self.buf += data
        if len(self.buf) > 2 * self.cap:
            del self.buf[: len(self.buf) - self.cap]

    def tail(self):
        return bytes(self.buf[-self.cap:]) if self.cap else b""


# Callback answers that stop the agent, and the killedBy value each one records.
_KILL_ANSWERS = {"kill": "boundary", "kill_paid": "paid", "kill_overage": "overage"}


class _LineFramer:
    """Splits stdout into lines of at most max_bytes; longer lines are dropped whole.

    Every complete line goes to the callback, then to the log sink unless the callback answered
    "drop" (a line that echoes the prompt). The first stopping answer becomes the verdict.
    """

    def __init__(self, max_bytes, callback, sink=None):
        self.max_bytes = max_bytes
        self.callback = callback
        self.sink = sink
        self.buf = bytearray()
        self.skipping = False
        self.dropped = 0
        self.lines = 0
        self.verdict = None

    def _emit(self, line):
        answer = None
        if self.callback is not None:
            try:
                answer = self.callback(bytes(line))
            except Exception:  # a parser bug must not leave the agent unsupervised
                answer = None
        if answer in _KILL_ANSWERS and self.verdict is None:
            self.verdict = _KILL_ANSWERS[answer]
        if answer != "drop" and self.sink is not None:
            self.sink(bytes(line) + b"\n")

    def feed(self, data):
        start = 0
        while start < len(data):
            nl = data.find(b"\n", start)
            end = len(data) if nl < 0 else nl
            piece = data[start:end]
            if self.skipping:
                self.dropped += len(piece)
            else:
                room = self.max_bytes - len(self.buf)
                if len(piece) > room:
                    self.dropped += len(self.buf) + len(piece)
                    self.buf.clear()
                    self.skipping = True
                else:
                    self.buf += piece
            if nl < 0:
                break
            self.lines += 1
            if not self.skipping:
                self._emit(self.buf)
            self.buf.clear()
            self.skipping = False
            start = nl + 1

    def finish(self):
        if self.buf and not self.skipping:
            self._emit(self.buf)
        self.buf.clear()


def clean_log(data):
    """Run log text: ANSI sequences and control characters other than newline and tab removed."""
    return _CTRL_RE.sub(b"", _ANSI_RE.sub(b"", data))


def _killpg(pgid, sig):
    try:
        os.killpg(pgid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def _leader_exited(pid):
    """True once the leader is a zombie. WNOWAIT keeps it unreaped so its group id stays reserved."""
    try:
        info = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError:
        return True
    return info is not None


def _write_all(fd, data):
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def run_agent(cmd, prompt, *, deadline_s, log_fd, on_stdout_line, stop_event, first_line_deadline_s=None):
    """Run cmd with prompt on stdin under caps and a deadline; see contract 3.7 and v2 7.2 for the result.

    killedBy: boundary | paid | overage (a stopping callback answer), first_line (no complete stdout
    line within first_line_deadline_s), sigterm, deadline, or None.
    """
    result = {"rc": None, "signal": None, "timedOut": False, "killedBy": None, "bytesDropped": 0,
              "stdoutBytes": 0, "stderrBytes": 0, "stderrTail": b"", "stdoutTail": b"", "logBytes": 0,
              "error": None}
    ring = _Ring(consts.RUN_LOG_MAX)
    out_tail = _Ring(consts.RUN_LOG_MAX)
    err_tail = _Ring(consts.STDERR_CAP)
    framer = _LineFramer(consts.LINE_MAX_BYTES, on_stdout_line, ring.add)
    try:
        proc = subprocess.Popen(cmd["argv"], env=cmd["env"], cwd=cmd["cwd"], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                                close_fds=True)
    except (OSError, ValueError, subprocess.SubprocessError):
        result["error"] = "spawn_failed"
        _finish_log(log_fd, ring, result, 0)
        return result

    pgid = proc.pid
    pending = memoryview(bytes(prompt))
    stdin_fd = proc.stdin.fileno()
    os.set_blocking(stdin_fd, False)
    if not pending:
        proc.stdin.close()
        stdin_fd = None
    readers = {proc.stdout.fileno(): "out", proc.stderr.fileno(): "err"}
    for fd in readers:
        os.set_blocking(fd, False)

    started = time.monotonic()
    deadline = started + max(1.0, float(deadline_s))
    first_line_at = None if first_line_deadline_s is None else started + max(1.0, float(first_line_deadline_s))
    term_sent_at = None
    kill_sent = False
    exited_at = None

    def begin_stop(reason):
        nonlocal term_sent_at
        if term_sent_at is None:
            result["killedBy"] = reason
            if reason == "deadline":
                result["timedOut"] = True
            _killpg(pgid, signal.SIGTERM)
            term_sent_at = time.monotonic()

    while True:
        now = time.monotonic()
        if framer.verdict is not None:
            begin_stop(framer.verdict)
        if first_line_at is not None and framer.lines == 0 and now >= first_line_at and exited_at is None:
            begin_stop("first_line")
        if stop_event is not None and stop_event.is_set():
            begin_stop("sigterm")
        if now >= deadline:
            begin_stop("deadline")
        if term_sent_at is not None and not kill_sent and now - term_sent_at >= consts.AGENT_TERM_GRACE_S:
            _killpg(pgid, signal.SIGKILL)
            kill_sent = True

        if exited_at is None and _leader_exited(proc.pid):
            exited_at = now
        if exited_at is not None:
            if not readers:
                break
            if kill_sent and now - exited_at >= _POLL_S:
                break
            if term_sent_at is None and now - exited_at >= _POST_EXIT_DRAIN_S:
                # The leader is gone but a descendant still holds a pipe: stop the stragglers.
                _killpg(pgid, signal.SIGTERM)
                term_sent_at = now
            if term_sent_at is not None and not kill_sent and now - term_sent_at >= _STRAGGLER_GRACE_S \
                    and result["killedBy"] is None:
                _killpg(pgid, signal.SIGKILL)
                kill_sent = True

        writers = [stdin_fd] if stdin_fd is not None else []
        try:
            readable, writable, _ = select.select(list(readers), writers, [], _POLL_S)
        except InterruptedError:
            continue
        if stdin_fd is not None and writable:
            try:
                written = os.write(stdin_fd, pending[:_CHUNK])
                pending = pending[written:]
            except BlockingIOError:
                pass
            except (BrokenPipeError, OSError):
                pending = pending[:0]
            if not pending:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
                stdin_fd = None
        for fd in readable:
            try:
                data = os.read(fd, _CHUNK)
            except BlockingIOError:
                continue
            except OSError:
                data = b""
            if not data:
                del readers[fd]
                continue
            if readers[fd] == "out":
                result["stdoutBytes"] += len(data)
                out_tail.add(data)
                framer.feed(data)
            else:
                ring.add(data)
                result["stderrBytes"] += len(data)
                err_tail.add(data)

    if stdin_fd is not None:
        try:
            proc.stdin.close()
        except OSError:
            pass
    framer.finish()
    if framer.verdict is not None and result["killedBy"] is None:
        # The stopping line was the last one, after the agent had already exited.
        result["killedBy"] = framer.verdict
    # Anything still in the group (a descendant that ignored TERM) goes now, while the
    # unreaped leader keeps the group id from being reused.
    _killpg(pgid, signal.SIGKILL)
    rc = proc.wait()
    for stream in (proc.stdout, proc.stderr):
        try:
            stream.close()
        except OSError:
            pass
    if rc < 0:
        result["signal"] = -rc
    else:
        result["rc"] = rc
    result["stdoutTail"] = out_tail.tail()
    result["stderrTail"] = err_tail.tail()
    _finish_log(log_fd, ring, result, framer.dropped)
    return result


def _finish_log(log_fd, ring, result, line_dropped):
    text = clean_log(ring.tail())[-consts.RUN_LOG_MAX:]
    result["logBytes"] = len(text)
    total = result["stdoutBytes"] + result["stderrBytes"]
    kept_raw = len(ring.tail())
    # Bytes that did not make it into the log, plus stdout bytes of lines too long to parse.
    result["bytesDropped"] = min(total, max(0, total - kept_raw) + line_dropped)
    if log_fd is None:
        return
    try:
        os.ftruncate(log_fd, 0)
        os.lseek(log_fd, 0, os.SEEK_SET)
        _write_all(log_fd, text)
    except OSError:
        result["logBytes"] = 0
