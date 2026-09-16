"""Bounded supervision for the short tool calls the helper makes (systemctl, busctl, qs, ...).

Every child runs in its own session, with an explicit environment and an absolute argv[0].
Both pipes are drained by one poll loop into byte-capped buffers, so a flooding child is
killed at the cap instead of being read into memory. A deadline covers the whole call.
Termination is always by process group: TERM, a short grace, then KILL, then reap. The
leader is observed with waitid(WNOWAIT) so its pid, and with it the group id, stays reserved
until the group has been signalled.

A child in its own session is out of reach of anything that signals only this process, so
each child also asks the kernel for SIGKILL when its parent dies (PR_SET_PDEATHSIG). A helper
killed from outside, for example by the panel on teardown, takes its tool calls with it.
"""
import ctypes
import os
import select
import signal
import subprocess
import time

from . import consts, fsio
from .errors import ApError

_budget_until = None
_PR_SET_PDEATHSIG = 1


def _load_prctl():
    try:
        fn = ctypes.CDLL(None, use_errno=True).prctl
    except (OSError, AttributeError):
        return None
    fn.restype = ctypes.c_int
    return fn


_prctl = _load_prctl()


def _die_with_parent(parent_pid):
    """preexec_fn for a child: SIGKILL when the parent dies, and exit at once if it already has."""
    def setup():
        if _prctl is not None:
            _prctl(ctypes.c_int(_PR_SET_PDEATHSIG), ctypes.c_ulong(signal.SIGKILL), ctypes.c_ulong(0),
                   ctypes.c_ulong(0), ctypes.c_ulong(0))
        if os.getppid() != parent_pid:
            os._exit(127)
    return setup


def set_budget(seconds):
    """Cap every later call to the remaining verb budget (None removes the cap)."""
    global _budget_until
    _budget_until = None if seconds is None else time.monotonic() + float(seconds)


def remaining_budget():
    if _budget_until is None:
        return None
    return max(0.0, _budget_until - time.monotonic())


def _signal_group(pgid, signum):
    try:
        os.killpg(pgid, signum)
    except (ProcessLookupError, PermissionError):
        pass


def _leader_exited(pid):
    try:
        info = os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
    except ChildProcessError:
        return True
    return info is not None


def _terminate_group(pid, grace_s):
    _signal_group(pid, signal.SIGTERM)
    end = time.monotonic() + max(0.0, grace_s)
    while time.monotonic() < end and not _leader_exited(pid):
        time.sleep(0.02)
    _signal_group(pid, signal.SIGKILL)


def _valid_argv(argv, env):
    if not isinstance(argv, (list, tuple)) or not argv:
        return False
    if not all(isinstance(a, str) and "\0" not in a for a in argv) or not os.path.isabs(argv[0]):
        return False
    if not isinstance(env, dict):
        return False
    return all(isinstance(k, str) and isinstance(v, str) and "\0" not in k + v and "=" not in k
               for k, v in env.items())


def run_bounded(argv, *, env, cwd=None, stdin_data=None, stdout_cap=65536, stderr_cap=16384,
                deadline_s=10.0, term_grace_s=2.0):
    """Run argv under caps and a deadline. Never raises for a child failure."""
    result = {"rc": None, "signal": None, "stdout": b"", "stderr": b"", "stdoutDropped": 0,
              "stderrDropped": 0, "timedOut": False, "overflow": False, "error": None}
    if not _valid_argv(argv, env):
        result["error"] = "spawn_failed"
        return result
    if argv[0] in consts.TOOLS.values():
        # A system tool is run as-is, so it has to be one only root can change (fail closed).
        try:
            fsio.check_tool(argv[0])
        except ApError:
            result["error"] = "untrusted_tool"
            return result
    budget = float(deadline_s)
    remaining = remaining_budget()
    if remaining is not None:
        budget = min(budget, remaining)
    if budget <= 0:
        result["timedOut"] = True
        return result
    try:
        proc = subprocess.Popen(
            list(argv), env=dict(env), cwd=cwd,
            stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, close_fds=True, preexec_fn=_die_with_parent(os.getpid()))
    except (OSError, ValueError, subprocess.SubprocessError):
        result["error"] = "spawn_failed"
        return result

    pid = proc.pid
    deadline = time.monotonic() + budget
    out_fd, err_fd = proc.stdout.fileno(), proc.stderr.fileno()
    buffers = {out_fd: bytearray(), err_fd: bytearray()}
    caps = {out_fd: max(0, int(stdout_cap)), err_fd: max(0, int(stderr_cap))}
    dropped = {out_fd: 0, err_fd: 0}
    pending = memoryview(bytes(stdin_data)) if stdin_data is not None else None
    poller = select.poll()
    open_readers = {out_fd, err_fd}
    for fd in open_readers:
        os.set_blocking(fd, False)
        poller.register(fd, select.POLLIN | select.POLLHUP | select.POLLERR)
    in_fd = None
    if pending is not None:
        if pending:
            in_fd = proc.stdin.fileno()
            os.set_blocking(in_fd, False)
            poller.register(in_fd, select.POLLOUT | select.POLLERR)
        else:
            proc.stdin.close()

    finished = False
    try:
        while open_readers or in_fd is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                result["timedOut"] = True
                break
            try:
                events = poller.poll(max(1, int(min(left, 0.1) * 1000)))
            except InterruptedError:
                continue
            if not events and _leader_exited(pid):
                # The leader is gone and nothing is left to read; a descendant holding the
                # pipes open must not keep the call alive until the deadline.
                break
            for fd, mask in events:
                if fd == in_fd:
                    try:
                        written = os.write(fd, pending[:65536])
                        pending = pending[written:]
                    except BlockingIOError:
                        continue
                    except OSError:
                        pending = pending[:0]
                    if not pending:
                        poller.unregister(fd)
                        proc.stdin.close()
                        in_fd = None
                    continue
                try:
                    chunk = os.read(fd, 65536)
                except BlockingIOError:
                    continue
                except OSError:
                    chunk = b""
                if not chunk:
                    poller.unregister(fd)
                    open_readers.discard(fd)
                    continue
                room = caps[fd] - len(buffers[fd])
                if room > 0:
                    buffers[fd] += chunk[:room]
                if len(chunk) > room:
                    dropped[fd] += len(chunk) - max(room, 0)
                    result["overflow"] = True
            if result["overflow"]:
                break
        if not result["timedOut"] and not result["overflow"]:
            while not _leader_exited(pid):
                if time.monotonic() >= deadline:
                    result["timedOut"] = True
                    break
                time.sleep(0.01)
        if result["timedOut"] or result["overflow"]:
            _terminate_group(pid, term_grace_s)
        else:
            # The leader is gone; anything it left behind in its group goes with it.
            _signal_group(pid, signal.SIGKILL)
        finished = True
    finally:
        if not finished:
            _signal_group(pid, signal.SIGKILL)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass

    code = proc.returncode
    if code is not None:
        if code < 0:
            result["signal"] = -code
        else:
            result["rc"] = code
    result["stdout"] = bytes(buffers[out_fd])
    result["stderr"] = bytes(buffers[err_fd])
    result["stdoutDropped"] = dropped[out_fd]
    result["stderrDropped"] = dropped[err_fd]
    return result


def tool_env():
    """Environment for plugin-side tools: nothing from the caller beyond what systemd and D-Bus need."""
    env = {"HOME": fsio.home(), "LANG": "C.UTF-8", "PATH": "/usr/bin"}
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    if fsio.runtime_dir_valid(runtime):
        env["XDG_RUNTIME_DIR"] = runtime
    bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    if bus and len(bus) <= 4096 and bus.isprintable():
        env["DBUS_SESSION_BUS_ADDRESS"] = bus
    return env
