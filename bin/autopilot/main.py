"""Dispatcher of bin/ap4a: verb table, argv grammar, deadlines, output caps and exit codes.

Exit codes: 0 success, 1 handled error (JSON error object on stdout), 2 argv grammar error
(bad_args on stdout), 70 uncaught exception (internal on stdout, traceback suppressed).
"""
import importlib
import os
import re
import signal

from . import bounded, consts, proto
from .errors import ApError

# verb -> (module, handler, reads stdin)
VERBS = {
    "edition": ("cli_core", "cmd_edition", False),
    "list": ("cli_core", "cmd_list", False),
    "job-get": ("cli_core", "cmd_job_get", False),
    "job-create": ("cli_core", "cmd_job_create", True),
    "job-update": ("cli_core", "cmd_job_update", True),
    "job-delete": ("cli_core", "cmd_job_delete", False),
    "preview": ("cli_core", "cmd_preview", True),
    "arm": ("cli_core", "cmd_arm", False),
    "run-now": ("cli_core", "cmd_run_now", False),
    "disarm": ("cli_core", "cmd_disarm", False),
    "cancel-all": ("cli_core", "cmd_cancel_all", False),
    "reschedule": ("cli_core", "cmd_reschedule", False),
    "swap": ("cli_core", "cmd_swap", False),
    "shift": ("cli_core", "cmd_shift", False),
    "reconcile": ("cli_core", "cmd_reconcile", False),
    "settings-get": ("cli_core", "cmd_settings_get", False),
    "settings-set": ("cli_core", "cmd_settings_set", True),
    "copy-resume": ("cli_core", "cmd_copy_resume", False),
    "sessions": ("cli_scan", "cmd_sessions", False),
    "usage": ("cli_scan", "cmd_usage", False),
    "timeline": ("cli_scan", "cmd_timeline", False),
    "agents": ("cli_scan", "cmd_agents", False),
    "models": ("cli_models", "cmd_models", False),
    "run": ("runner", "cmd_run", False),
}

_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_EPOCH_RE = re.compile(r"^[0-9]{10}$")
_SEC_RE = re.compile(r"^-?[0-9]{1,7}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_GEN_RE = re.compile(r"^[0-9]{1,6}$")

_NO_ARGS = ("edition", "list", "job-create", "preview", "cancel-all", "reconcile", "settings-get",
            "settings-set", "usage")
_ONE_ID = ("job-get", "job-update", "job-delete", "disarm", "copy-resume")


class _ArgError(Exception):
    pass


class _Deadline(BaseException):
    """Raised from SIGALRM; a BaseException so no handler's `except Exception` swallows it."""


class _Terminated(BaseException):
    """Raised from SIGTERM or SIGHUP, so bounded.run_bounded's cleanup kills the child's group."""


def _need(condition):
    if not condition:
        raise _ArgError()


def _match(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _cwd_ok(value):
    """--cwd value: absolute, at most CWD_MAX_BYTES, printable, no NUL or newline."""
    if not isinstance(value, str) or not value.startswith("/") or not value.isprintable():
        return False
    if "\x00" in value or "\n" in value:
        return False
    try:
        return len(value.encode("utf-8")) <= consts.CWD_MAX_BYTES
    except UnicodeEncodeError:
        return False


def _epoch_ok(value):
    return _match(_EPOCH_RE, value) and consts.EPOCH_MIN <= int(value) <= consts.EPOCH_MAX


def _check_sessions(argv):
    """sessions [--harness <h>] [--cwd <abs>]: each flag at most once, --harness first."""
    rest = list(argv)
    if rest[:1] == ["--harness"]:
        _need(len(rest) >= 2 and rest[1] in consts.HARNESSES)
        rest = rest[2:]
    if rest[:1] == ["--cwd"]:
        _need(len(rest) >= 2 and _cwd_ok(rest[1]))
        rest = rest[2:]
    _need(not rest)


def check_argv(verb, argv):
    """Raise _ArgError unless argv fits the verb's grammar (contract 2.3/2.4, v2 section 4)."""
    count = len(argv)
    if not all(isinstance(a, str) for a in argv):
        raise _ArgError()
    if verb in _NO_ARGS:
        _need(count == 0)
    elif verb in _ONE_ID:
        _need(count == 1 and _match(_ID_RE, argv[0]))
    elif verb in ("arm", "run-now"):
        _need(count == 3 and _match(_ID_RE, argv[0]) and argv[1] == "--digest" and _match(_DIGEST_RE, argv[2]))
    elif verb == "reschedule":
        _need(count == 2 and _match(_ID_RE, argv[0]) and _match(_EPOCH_RE, argv[1]))
        _need(consts.EPOCH_MIN <= int(argv[1]) <= consts.EPOCH_MAX)
    elif verb == "swap":
        _need(count == 2 and _match(_ID_RE, argv[0]) and _match(_ID_RE, argv[1]) and argv[0] != argv[1])
    elif verb == "shift":
        _need(count >= 3 and argv[0] == "--by" and _match(_SEC_RE, argv[1]))
        ids = argv[2:]
        _need(all(_match(_ID_RE, job_id) for job_id in ids))
        if len(ids) > consts.SHIFT_MAX_IDS:
            raise ApError("too_many_ids")
        _need(len(set(ids)) == len(ids))
    elif verb == "sessions":
        _check_sessions(argv)
    elif verb == "models":
        _need(count in (2, 3) and argv[0] == "--harness" and argv[1] in consts.HARNESSES)
        _need(count == 2 or argv[2] == "--refresh")
    elif verb == "timeline":
        _need(count == 4 and argv[0] == "--from" and _epoch_ok(argv[1]) and argv[2] == "--to" and _epoch_ok(argv[3]))
    elif verb == "agents":
        _need(count == 0 or argv == ["--login"])
    elif verb == "run":
        _need(count == 4 and argv[0] == "--job" and _match(_ID_RE, argv[1]) and argv[2] == "--gen"
              and _match(_GEN_RE, argv[3]) and int(argv[3]) >= 1)
    else:
        raise _ArgError()


def _on_alarm(_signum, _frame):
    raise _Deadline()


def _on_terminate(_signum, _frame):
    raise _Terminated()


def install_stop_handlers():
    """TERM and HUP unwind the verb instead of ending Python on the spot, so every `finally` runs:
    a tool call in flight has its process group killed before the helper exits. Returns the
    previous handlers for restore_handlers()."""
    return {signum: signal.signal(signum, _on_terminate) for signum in (signal.SIGTERM, signal.SIGHUP)}


def restore_handlers(previous):
    for signum, handler in previous.items():
        signal.signal(signum, handler)


def _bad_args():
    proto.emit_error(ApError("bad_args"))
    return 2


def _run_runner(argv):
    """`run` writes nothing to stdout; its outcome lives in the job store and the journal codes."""
    try:
        module = importlib.import_module(".runner", __package__)
        module.cmd_run(argv, None)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 70
    except BaseException:
        return 70
    return 0


def main(args):
    os.umask(0o077)
    if not isinstance(args, list) or not args or args[0] not in VERBS:
        return _bad_args()
    verb, argv = args[0], list(args[1:])
    try:
        check_argv(verb, argv)
    except _ArgError:
        return _bad_args()
    except ApError as err:
        proto.emit_error(err)
        return 1
    if verb == "run":
        return _run_runner(argv)

    module_name, handler_name, reads_stdin = VERBS[verb]
    cap = consts.OUTPUT_CAP.get(verb, consts.OUTPUT_CAP_DEFAULT)
    deadline = float(consts.VERB_DEADLINE_S[verb])
    previous = signal.signal(signal.SIGALRM, _on_alarm)
    previous_stop = install_stop_handlers()
    try:
        try:
            bounded.set_budget(max(0.5, deadline - 1.0))
            signal.setitimer(signal.ITIMER_REAL, deadline)
            try:
                payload = proto.read_stdin_json() if reads_stdin else None
                module = importlib.import_module("." + module_name, __package__)
                result = getattr(module, handler_name)(argv, payload)
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
                bounded.set_budget(None)
        except ApError as err:
            proto.emit_error(err)
            return 2 if err.code == "bad_args" else 1
        except _Deadline:
            proto.emit_error(ApError("internal"))
            return 1
        except _Terminated:
            proto.emit_error(ApError("internal"))
            return 1
        except Exception:
            proto.emit_error(ApError("internal"))
            return 70
    finally:
        signal.signal(signal.SIGALRM, previous)
        restore_handlers(previous_stop)
    if not isinstance(result, dict) or result.get("ok") is not True:
        proto.emit_error(ApError("internal"))
        return 70
    return 0 if proto.emit(result, cap) else 1
