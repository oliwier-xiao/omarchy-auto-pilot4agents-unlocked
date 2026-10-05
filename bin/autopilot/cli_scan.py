"""Verbs that describe the machine: sessions, dirs, workspace, usage, timeline, agents.

None of them take stdin or hold the jobs lock. The only state they change is the agents version
cache and, for `usage`, the limits history and the last good Cursor record (both best effort,
under limits.lock or by atomic replace). `workspace --create` makes the No project folder.
"""

import re
import unicodedata

from . import agents, consts, finder, folders, fsio, limits_history, sessions, timeutil, usage, windows
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
    """sessions [--harness <harness>] [--cwd <abs> | --in <abs> | --stdin] -> Sessions v2 (delta 3.12).

    --cwd scopes Pi to one folder and leaves every other agent as it is; --in answers only the
    sessions recorded in that one folder, for every agent. With --stdin either arrives on stdin, as
    {"cwd": <abs>} or {"in": <abs>} (or {} for neither), never in argv.
    """
    args = list(argv or [])
    harness = cwd = only = None
    if args[:1] == ["--harness"]:
        if len(args) < 2 or args[1] not in consts.HARNESSES:
            raise ApError("bad_args")
        harness = args[1]
        args = args[2:]
    if args[:1] in (["--cwd"], ["--in"]):
        if len(args) < 2 or not _cwd_ok(args[1]):
            raise ApError("bad_args")
        if args[0] == "--cwd":
            cwd = args[1]
        else:
            only = args[1]
        args = args[2:]
    elif args[:1] == ["--stdin"]:
        if not isinstance(payload, dict) or set(payload) - {"cwd", "in"} or len(payload) > 1:
            raise ApError("bad_args")
        for key, value in payload.items():
            if not _cwd_ok(value):
                raise ApError("bad_args")
            if key == "cwd":
                cwd = value
            else:
                only = value
        args = args[1:]
    if args:
        raise ApError("bad_args")
    extra = {}
    if cwd is not None:
        extra["cwd"] = cwd
    if only is not None:
        extra["only"] = only
    result = sessions.list_sessions(harness, timeutil.now(), **extra)
    return dict({"ok": True}, **result)


def cmd_dirs(argv, payload):
    """dirs (--path <abs> [--hidden] | [--hidden] --stdin) -> the subfolders of one folder in the home folder.

    With --stdin the folder arrives as {"path": <abs>} on stdin: the panel walks the user's own
    folders, and a command line is readable by every account on the computer.
    """
    args = list(argv or [])
    if args in (["--stdin"], ["--hidden", "--stdin"]):
        if not isinstance(payload, dict) or set(payload) != {"path"} or not _cwd_ok(payload["path"]):
            raise ApError("bad_args")
        return dict({"ok": True}, **folders.list_dirs(payload["path"], hidden=args[0] == "--hidden"))
    if len(args) not in (2, 3) or args[0] != "--path" or not _cwd_ok(args[1]) or args[2:] not in ([], ["--hidden"]):
        raise ApError("bad_args")
    return dict({"ok": True}, **folders.list_dirs(args[1], hidden=len(args) == 3))


def cmd_folder(argv, payload):
    """folder -> whether a job may run in the folder given as {"path": <abs>} on stdin, and why not."""
    if argv:
        raise ApError("bad_args")
    if not isinstance(payload, dict) or set(payload) != {"path"} or not _cwd_ok(payload["path"]):
        raise ApError("bad_args")
    return dict({"ok": True}, **folders.check_folder(payload["path"]))


def cmd_find_dirs(argv, payload):
    """find-dirs -> the folders under the home folder that best match the words given as
    {"q": <words>, "known": [<abs>, ...]} on stdin (known: folders with sessions, ranked higher)."""
    if argv:
        raise ApError("bad_args")
    if not isinstance(payload, dict) or not set(payload) <= {"q", "known"} or "q" not in payload:
        raise ApError("bad_args")
    query, known = payload["q"], payload.get("known", [])
    if not isinstance(query, str) or not 0 < len(query) <= consts.FIND_QUERY_MAX:
        raise ApError("bad_args")
    # Zero-width joiners and the like (inside emoji, pasted text) carry no letters: left out.
    query = "".join(ch for ch in query if unicodedata.category(ch) != "Cf")
    if not query.isprintable():
        raise ApError("bad_args")
    if len("".join(query.split())) < 2:
        raise ApError("bad_args")
    if not isinstance(known, list) or len(known) > consts.FIND_KNOWN_MAX or not all(_cwd_ok(k) for k in known):
        raise ApError("bad_args")
    return dict({"ok": True}, **finder.find_dirs(query, known))


def cmd_workspace(argv, payload):
    """workspace [--create] -> the No project folder, made first with --create."""
    if argv and argv != ["--create"]:
        raise ApError("bad_args")
    return dict({"ok": True}, **folders.workspace(bool(argv)))


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
