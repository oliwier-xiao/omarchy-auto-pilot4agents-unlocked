"""Verbs that describe the machine: sessions, usage, timeline, agents.

None of them take stdin or hold the jobs lock. The only state they change is the agents version
cache and, for `usage`, the limits history and the last good Cursor record (both best effort,
under limits.lock or by atomic replace).
"""

import re

from . import agents, consts, fsio, limits_history, sessions, timeutil, usage, windows
from .errors import ApError

_EPOCH_RE = re.compile(r"^[0-9]{1,10}$")


def _cwd_ok(value):
    """An absolute, printable folder path within CWD_MAX_BYTES (no NUL, newline or control character)."""
    if not isinstance(value, str) or not value.startswith("/"):
        return False
    if any(ord(ch) < 0x20 or 0x7f <= ord(ch) <= 0x9f for ch in value):
        return False
    try:
        return len(value.encode("utf-8")) <= consts.CWD_MAX_BYTES
    except UnicodeEncodeError:
        return False


def _state_or_none():
    """The state folder when it already exists; None when absent or refused (reads then skip it)."""
    try:
        return fsio.open_state(create=False)
    except ApError as exc:
        if exc.code == "bad_env":
            raise
        return None


def cmd_sessions(argv, payload):
    """sessions [--harness <harness>] [--cwd <abs>] -> Sessions v2 (delta 3.12)."""
    args = list(argv or [])
    harness = cwd = None
    if args[:1] == ["--harness"]:
        if len(args) < 2 or args[1] not in consts.HARNESSES:
            raise ApError("bad_args")
        harness = args[1]
        args = args[2:]
    if args[:1] == ["--cwd"]:
        if len(args) < 2 or not _cwd_ok(args[1]):
            raise ApError("bad_args")
        cwd = args[1]
        args = args[2:]
    if args:
        raise ApError("bad_args")
    extra = {"cwd": cwd} if cwd is not None else {}
    result = sessions.list_sessions(harness, timeutil.now(), **extra)
    return dict({"ok": True}, **result)


def cmd_usage(argv, payload):
    """usage -> Usage v2 (delta 3.7), with relevance, the Cursor keep and the history append."""
    if argv:
        raise ApError("bad_args")
    sd = _state_or_none()
    try:
        result = usage.read_usage(timeutil.now_ms(), sd=sd, record_history=True, with_relevance=True)
    finally:
        if sd is not None:
            sd.close()
    return dict({"ok": True}, **result)


def cmd_timeline(argv, payload):
    """timeline --from <epoch> --to <epoch> -> Timeline (delta 3.9) for at most one day and an hour."""
    args = list(argv or [])
    if (len(args) != 4 or args[0] != "--from" or args[2] != "--to"
            or not all(isinstance(a, str) and _EPOCH_RE.fullmatch(a) for a in (args[1], args[3]))):
        raise ApError("bad_args")
    start, end = int(args[1]), int(args[3])
    now_ms = timeutil.now_ms()
    now = now_ms // 1000
    if (not 0 < end - start <= windows.TIMELINE_RANGE_MAX_S or start < now - windows.TIMELINE_BACK_S
            or end > now + windows.TIMELINE_AHEAD_S or start < consts.EPOCH_MIN or end > consts.EPOCH_MAX):
        raise ApError("bad_args")
    sd = _state_or_none()
    try:
        live = usage.read_usage(now_ms)
        result = limits_history.build_timeline(sd, start, end, now, live)
    finally:
        if sd is not None:
            sd.close()
    result["nowMs"] = now_ms
    return dict({"ok": True}, **result)


def cmd_agents(argv, payload):
    """agents [--login] -> Agents (4.10). --login adds `claude auth status --json`."""
    if argv and argv != ["--login"]:
        raise ApError("bad_args")
    return dict({"ok": True}, **agents.list_agents(bool(argv)))
