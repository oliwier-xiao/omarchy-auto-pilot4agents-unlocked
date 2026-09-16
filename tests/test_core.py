"""Helper-core tests (owner H1): framing, exit codes, state safety, draft validation, digests,
locks, the systemd argv, the scheduling verbs and the reconciler.

Nothing here touches the real HOME, the real state folder or the real user manager. Each test
gets a temporary HOME and runtime folder, consts.TOOLS points at tests/stubs (which record every
argv into $HOME/.fakesys), and agent discovery, session lookup, usage and notifications are
replaced in-process. bounded.run_bounded refuses any program outside tests/stubs.
"""

import contextlib
import copy
import hashlib
import importlib
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.join(ROOT, "bin"))

from autopilot import (agents, bounded, cli_core, consts, edition, errors, fsio, harness, identity, jobs,  # noqa: E402
                       models, notify, paid, proto, reconcile, sessions, settings, systemd, timeutil, trigger, usage,
                       windows)
from autopilot import main as helper_main  # noqa: E402
from autopilot.errors import ApError  # noqa: E402

STUBS = os.path.join(TESTS, "stubs")
UID = os.getuid()
TOOL_ENV_KEYS = {"HOME", "LANG", "PATH", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"}
ORIGINAL_CANDIDATES = copy.deepcopy(consts.CLI_CANDIDATES)
REAL_CHECK_JOB = paid.check_job
GATE_OK = {"ok": True, "code": None, "detail": None, "notes": ["subscription_only"], "billing": None, "provider": None,
           "resetAtMs": None, "pending": False, "defer": None}

CONTRACT_MESSAGES = {
    "bad_args": "The request was not understood.",
    "bad_input": "The request data is not valid.",
    "bad_env": "The helper environment is not usable.",
    "stdin_too_large": "The request is too large.",
    "stdin_timeout": "The request did not arrive in time.",
    "output_too_large": "The answer would be too large.",
    "state_refused": "The state folder failed a safety check.",
    "state_too_large": "The job store is too large to read.",
    "state_corrupt": "The job store could not be read.",
    "runtime_dir": "The runtime folder failed a safety check.",
    "lock_busy": "Another change is still running. Try again.",
    "not_found": "That job no longer exists.",
    "bad_status": "That job cannot do this in its current state.",
    "store_full": "The job store is full. Delete drafts in the Queue, then try again.",
    "invalid_harness": "Unknown agent.",
    "invalid_level": "Unknown permission level.",
    "invalid_target": "The session choice is not valid.",
    "invalid_session": "The session id is not valid.",
    "session_not_found": "That session could not be found.",
    "invalid_cwd": "That working folder is not allowed.",
    "invalid_model": "The model name is not valid.",
    "invalid_label": "The label is not valid.",
    "invalid_limits": "The limits are out of range.",
    "invalid_trigger": "The schedule is not valid.",
    "trigger_unsupported": "That reset does not apply to this agent.",
    "fork_unsupported": "This agent cannot fork a session.",
    "prompt_empty": "Write a prompt first.",
    "prompt_too_large": "The prompt is longer than 64 KiB.",
    "prompt_missing": "The prompt was deleted after the run. Write it again.",
    "time_past": "That time has already passed.",
    "time_too_far": "That time is more than 8 days away.",
    "no_reset_data": "No usage data for that reset. Pick a time instead.",
    "reset_not_open": "No limit window is open, so the quota is already fresh.",
    "weekly_exhausted": "The weekly limit is used up.",
    "cli_missing": "The agent command was not found. Install it or pick another agent.",
    "cli_untrusted": "The agent command failed a safety check.",
    "not_logged_in": "The agent is not signed in. Sign in with its own command first.",
    "digest_mismatch": "The job changed since you reviewed it. Check it again.",
    "preview_stale": "The command changed since the preview. Check it again.",
    "kill_switch": "Auto Pilot is switched off by its kill switch file. Delete ~/.config/omarchy/auto-pilot4agents/DISABLED to switch it on.",
    "plugin_disabled": "Auto Pilot is not enabled in the bar.",
    "plugin_identity": "The plugin folder does not match its manifest.",
    "systemd_failed": "The system scheduler refused the job.",
    "systemd_timeout": "The system scheduler did not answer in time.",
    "stop_unverified": "The job could not be confirmed as stopped.",
    "too_many_ids": "Too many jobs selected.",
    "not_systemd": "This command only runs from the scheduler.",
    "internal": "The helper hit an unexpected error. Try again.",
}

# Contract delta 2.3 (v2 error codes) and 2.4 (v2 run reasons), exact copy.
V2_MESSAGES = {
    "paid_blocked": "This agent is signed in with an API key. Allow paid usage to run it.",
    "paid_zen": "This model bills your OpenCode Zen balance. Allow paid usage to run it.",
    "paid_opencode_claude": "Claude models in OpenCode bill API or extra usage, not your Claude plan.",
    "paid_pi_claude": "Pi bills Claude through extra usage, which is off for this job.",
    "paid_pi_key": "Pi uses an API key for this provider. Allow paid usage to run it.",
    "pi_slash_prompt": "Pi reads a prompt that starts with / as a command. Start it with a word.",
    "pi_model_required": "Pi needs a provider and a model. Pick one from the list.",
    "pi_auth_invalid": "Pi could not check the sign-in for that provider.",
    "level_unavailable": "That permission level is not offered for this agent.",
    "harness_gated": "Cursor support is waiting for a one-time check.",
    "cursor_autorun_config": "Cursor is set to Run Everything, which would approve every tool.",
    "cursor_network_config": "Cursor's sandbox allows all network access.",
    "cursor_project_rules": "This folder has its own Cursor or Claude allow rules, which Cursor would apply.",
    "cursor_untrusted": ("Cursor does not trust this folder yet. Open cursor-agent in this folder once and choose "
                         "Trust this workspace."),
}
CONTRACT_MESSAGES.update(V2_MESSAGES)
V2_REASONS = {
    "paid_blocked": "It would have used paid usage, which is off for this job.",
    "overage_blocked": "Usage credits would have been used, so it waits for the reset.",
    "paid_defer": "Included usage is used up, so it waits for the reset.",
    "paid_exhausted": "Included usage is used up until after the 8-day limit, so it was skipped.",
    "limit_full": "The usage limit is used up, so it waits for the reset.",
    "zen_billing": "The OpenCode Zen balance or spending limit stopped it.",
    "limit_suspected": "The agent was waiting on a usage limit.",
    "stalled": "The agent produced no output.",
    "cursor_autorun_config": "Cursor is set to approve every tool.",
    "cursor_network_config": "Cursor's sandbox allows all network access.",
    "cursor_project_rules": "This folder has its own Cursor or Claude allow rules.",
    "harness_gated": "Cursor support is waiting for a one-time check.",
    "monthly_limit": "The monthly limit is used up, so it does not retry.",
    "quota_final": "The provider reports no quota or balance left, so it does not retry.",
    "cursor_sandbox": "Cursor's sandbox could not start, so nothing ran. Turn it off in cursor-agent.",
}


def golden_argv(job_id, gen, fire_at, runtime):
    """Independent restatement of contract 3.4.1."""
    unit = "%s-%s-g%d" % (edition.UNIT_PREFIX, job_id, gen)
    argv = [os.path.join(STUBS, "systemd-run"), "--user", "--quiet", "--no-ask-password", "--collect",
            "--unit=" + unit, "--description=" + edition.UNIT_DESCRIPTION]
    if fire_at is not None:
        argv += ["--on-calendar=@%d" % fire_at, "--timer-property=AccuracySec=1s"]
    argv += ["-p", "Type=exec", "-p", "RuntimeMaxSec=%d" % runtime, "-p", "TimeoutStopSec=30s",
             "-p", "KillMode=control-group", "-p", "SendSIGKILL=yes",
             "-p", "MemoryHigh=3G", "-p", "MemoryMax=4G", "-p", "TasksMax=512", "-p", "CPUWeight=50",
             "-p", "OOMPolicy=kill",
             "-p", "Nice=10", "-p", "IOSchedulingClass=best-effort", "-p", "IOSchedulingPriority=7",
             "-p", "NoNewPrivileges=yes", "-p", "UMask=0077", "-p", "LimitCORE=0",
             "-p", "StandardInput=null", "-p", "StandardOutput=null", "-p", "StandardError=journal",
             "-p", "SyslogIdentifier=" + edition.SYSLOG_IDENTIFIER,
             "-p", "LogRateLimitIntervalSec=30s", "-p", "LogRateLimitBurst=200",
             "-E", "PATH=/usr/bin", "-E", "LANG=C.UTF-8",
             "-p", "UnsetEnvironment=DISPLAY WAYLAND_DISPLAY HYPRLAND_INSTANCE_SIGNATURE OMARCHY_PATH",
             "--", "/usr/bin/python3", "-I", "-S", "-B", os.path.realpath(ROOT) + "/bin/ap4a", "run",
             "--job", job_id, "--gen", str(gen)]
    return argv


def read_file(path, mode="r"):
    with open(path, mode) as handle:
        return handle.read()


def write_all(fd, data):
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


class Patch:
    """Attribute and mapping patches, undone in reverse order."""

    def __init__(self):
        self.saved = []

    def set(self, obj, name, value):
        self.saved.append(("attr", obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def item(self, mapping, key, value):
        missing = object()
        self.saved.append(("item", mapping, key, mapping.get(key, missing), missing))
        mapping[key] = value

    def undo(self):
        while self.saved:
            entry = self.saved.pop()
            if entry[0] == "attr":
                setattr(entry[1], entry[2], entry[3])
            elif entry[3] is entry[4]:
                entry[1].pop(entry[2], None)
            else:
                entry[1][entry[2]] = entry[3]


@contextlib.contextmanager
def alarm_guard(seconds):
    def fail(_signum, _frame):
        raise AssertionError("blocked for %s s" % seconds)

    previous = signal.signal(signal.SIGALRM, fail)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


# ================================================================================ framing

class FramingTests(unittest.TestCase):
    def setUp(self):
        self.p = Patch()

    def tearDown(self):
        self.p.undo()

    def read(self, chunks, *, close=True, pause=0.0):
        read_fd, write_fd = os.pipe()

        def writer():
            try:
                for chunk in chunks:
                    write_all(write_fd, chunk)
                    if pause:
                        time.sleep(pause)
            except OSError:
                pass
            finally:
                if close:
                    os.close(write_fd)

        thread = threading.Thread(target=writer, daemon=True)
        thread.start()
        try:
            return proto.read_stdin_json(read_fd)
        finally:
            os.close(read_fd)
            thread.join(10)
            if not close:
                os.close(write_fd)

    def code(self, *args, **kwargs):
        with self.assertRaises(ApError) as ctx:
            self.read(*args, **kwargs)
        return ctx.exception.code

    def test_stdin_line_frame(self):
        self.assertEqual(self.read([b'{"a":', b' 1}\n{"b":2}\n']), {"a": 1})
        self.assertEqual(self.read([b'{"text":"line one\\nline two"}\n']), {"text": "line one\nline two"})
        self.assertEqual(self.code([b"[1,2]\n"]), "bad_input")
        self.assertEqual(self.code([b'{"a":"\xff"}\n']), "bad_input")
        self.assertEqual(self.code([b'{"a":NaN}\n']), "bad_input")
        self.assertEqual(self.code([b'{"a":1,"a":2}\n']), "bad_input")
        self.assertEqual(self.code([b"[" * 100000 + b"\n"]), "bad_input")

    def test_stdin_eof_without_newline(self):
        self.assertEqual(self.read([b'{"a": "x"}']), {"a": "x"})
        self.assertEqual(self.code([b""]), "bad_input")
        self.assertEqual(self.code([b'{"a":']), "bad_input")

    def test_stdin_cap(self):
        cap = consts.STDIN_MAX_BYTES
        exact = b'{"p":"' + b"x" * (cap - 8) + b'"}'
        self.assertEqual(len(exact), cap)
        self.assertEqual(len(self.read([exact + b"\n"])["p"]), cap - 8)
        self.assertEqual(len(self.read([exact])["p"]), cap - 8)
        self.assertEqual(self.code([b"x" * (cap + 1)], close=False), "stdin_too_large")
        self.assertEqual(self.code([b"x" * (cap + 1) + b"\n"]), "stdin_too_large")

    def test_stdin_idle_timeout(self):
        self.p.set(consts, "STDIN_IDLE_S", 0.3)
        self.p.set(consts, "STDIN_TOTAL_S", 5.0)
        start = time.monotonic()
        self.assertEqual(self.code([b'{"a":'], close=False), "stdin_timeout")
        self.assertLess(time.monotonic() - start, 2.0)
        # A slow trickle keeps the idle timer fed but still hits the total budget.
        self.p.set(consts, "STDIN_IDLE_S", 1.0)
        self.p.set(consts, "STDIN_TOTAL_S", 0.6)
        start = time.monotonic()
        self.assertEqual(self.code([b" "] * 20, close=False, pause=0.1), "stdin_timeout")
        self.assertLess(time.monotonic() - start, 2.5)

    def test_error_object_fixed_messages(self):
        self.assertEqual(errors.MESSAGES, CONTRACT_MESSAGES)
        for code, message in errors.MESSAGES.items():
            self.assertEqual(errors.error_object(ApError(code)), {"ok": False, "code": code, "message": message})
            self.assertNotRegex(message, r"[{}%]")
            self.assertNotIn("\u2014", message)
            self.assertTrue(message.endswith("."), code)
        self.assertEqual(ApError("no_such_code").code, "internal")
        obj = errors.error_object(ApError("weekly_exhausted", "trigger.kind", {"until": 1789000000, "path": "/x"}))
        self.assertEqual(obj, {"ok": False, "code": "weekly_exhausted", "message": CONTRACT_MESSAGES["weekly_exhausted"],
                               "field": "trigger.kind", "detail": {"until": 1789000000}})


# ================================================================================ bounded children

class BoundedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="ap4a-h1b-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def script(self, body):
        path = os.path.join(self.tmp, "child-%s.py" % secrets.token_hex(4))
        with open(path, "w") as handle:
            handle.write(body)
        return ["/usr/bin/python3", "-I", "-S", "-B", path]

    def test_bounded_caps_deadline_and_group_kill(self):
        flood = self.script("import os\nwhile True:\n    os.write(1, b'y' * 65536)\n")
        res = bounded.run_bounded(flood, env={"LANG": "C.UTF-8"}, stdout_cap=1000, deadline_s=10)
        self.assertTrue(res["overflow"])
        self.assertEqual(len(res["stdout"]), 1000)
        self.assertGreater(res["stdoutDropped"], 0)

        sleeper = self.script("import time\ntime.sleep(30)\n")
        start = time.monotonic()
        res = bounded.run_bounded(sleeper, env={}, deadline_s=0.5, term_grace_s=0.2)
        self.assertTrue(res["timedOut"])
        self.assertLess(time.monotonic() - start, 4)

        pid_file = os.path.join(self.tmp, "grandchild.pid")
        leaver = self.script(
            "import os, subprocess\n"
            "p = subprocess.Popen(['/usr/bin/sleep', '30'])\n"
            "open(%r, 'w').write(str(p.pid))\n"
            "os.write(1, b'done')\n" % pid_file)
        start = time.monotonic()
        res = bounded.run_bounded(leaver, env={}, deadline_s=10)
        self.assertLess(time.monotonic() - start, 3)
        self.assertEqual((res["rc"], res["stdout"]), (0, b"done"))
        grandchild = int(read_file(pid_file))
        for _ in range(50):
            try:
                os.kill(grandchild, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            os.kill(grandchild, signal.SIGKILL)
            self.fail("descendant survived its process group")

        env_dump = self.script("import os, json\nprint(json.dumps(dict(os.environ)))\n")
        res = bounded.run_bounded(env_dump, env={"ONLY": "this"}, deadline_s=10)
        self.assertEqual(set(json.loads(res["stdout"])) - {"LC_CTYPE"}, {"ONLY"})
        self.assertEqual(bounded.run_bounded(["python3", "-c", "1"], env={})["error"], "spawn_failed")

    def _tool_call(self, marker, timeout=10):
        """pid of the running `/usr/bin/sleep <marker>`."""
        want = b"/usr/bin/sleep\0" + marker.encode() + b"\0"
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for name in os.listdir("/proc"):
                if name.isdigit():
                    try:
                        with open("/proc/%s/cmdline" % name, "rb") as handle:
                            if handle.read() == want:
                                return int(name)
                    except OSError:
                        continue
            time.sleep(0.05)
        self.fail("the tool call never started")

    def _gone(self, pid, marker):
        for _ in range(100):
            try:
                with open("/proc/%d/cmdline" % pid, "rb") as handle:
                    if marker.encode() not in handle.read():
                        return True
            except OSError:
                return True
            time.sleep(0.05)
        os.kill(pid, signal.SIGKILL)
        return False

    def _helper_process(self, body, marker):
        head = "import sys\nsys.path.insert(0, %r)\nfrom autopilot import bounded, main\nMARK = %r\n" % (
            os.path.join(ROOT, "bin"), marker)
        return subprocess.Popen(self.script(head + body), env={"PATH": "/usr/bin", "LANG": "C.UTF-8"},
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def test_killed_helper_takes_its_tool_call_with_it(self):
        # The panel kills a helper by pid on teardown; its tool call runs in its own session.
        marker = "%d.%06d" % (4000 + secrets.randbelow(1000), secrets.randbelow(10 ** 6))
        proc = self._helper_process("bounded.run_bounded(['/usr/bin/sleep', MARK], env={}, deadline_s=60)\n", marker)
        try:
            child = self._tool_call(marker)
            proc.kill()
            proc.wait(timeout=10)
            self.assertTrue(self._gone(child, marker), "a tool call outlived its SIGKILLed helper")
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_terminated_helper_kills_its_tool_call_before_exiting(self):
        marker = "%d.%06d" % (5000 + secrets.randbelow(1000), secrets.randbelow(10 ** 6))
        body = ("import os\nmain.install_stop_handlers()\n"
                "try:\n    bounded.run_bounded(['/usr/bin/sleep', MARK], env={}, deadline_s=60)\n"
                "except main._Terminated:\n"
                "    want = b'/usr/bin/sleep\\0' + MARK.encode() + b'\\0'\n"
                "    alive = False\n"
                "    for n in os.listdir('/proc'):\n"
                "        try:\n            alive = alive or open('/proc/%s/cmdline' % n, 'rb').read() == want\n"
                "        except OSError:\n            pass\n"
                "    sys.exit(4 if alive else 3)\n")
        proc = self._helper_process(body, marker)
        try:
            child = self._tool_call(marker)
            proc.send_signal(signal.SIGTERM)
            self.assertEqual(proc.wait(timeout=10), 3, "TERM did not unwind run_bounded and kill the group")
            self.assertTrue(self._gone(child, marker))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_tool_env_allowlist(self):
        saved = dict(os.environ)
        try:
            home = os.path.join(self.tmp, "home")
            os.makedirs(home)
            os.environ.update(HOME=home, ANTHROPIC_API_KEY="k", WAYLAND_DISPLAY="wayland-1", XDG_RUNTIME_DIR="/tmp")
            env = bounded.tool_env()
            self.assertTrue(set(env) <= TOOL_ENV_KEYS, env)
            self.assertNotIn("XDG_RUNTIME_DIR", env)
            self.assertEqual(env["PATH"], "/usr/bin")
        finally:
            os.environ.clear()
            os.environ.update(saved)


# ================================================================================ sandbox

class Sandbox(unittest.TestCase):
    """Temporary HOME, runtime folder, stub tools and in-process agent/session/usage fakes."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="ap4a-h1-"))
        self.home = os.path.join(self.tmp, "home")
        self.runtime = os.path.join(self.tmp, "run", "user", str(UID))
        os.makedirs(self.home, mode=0o700)
        os.makedirs(self.runtime, mode=0o700)
        os.chmod(self.runtime, 0o700)
        self.saved_env = dict(os.environ)
        for key in list(os.environ):
            if key not in ("PATH", "LANG", "PYTHONDONTWRITEBYTECODE"):
                del os.environ[key]
        os.environ.update(HOME=self.home, USER="tester", XDG_RUNTIME_DIR=self.runtime)
        self.p = Patch()
        self.p.set(consts, "XDG_RUNTIME_RE", re.compile("^" + re.escape(self.runtime) + "$"))
        for key, name in (("systemd_run", "systemd-run"), ("systemctl", "systemctl"), ("busctl", "busctl"),
                          ("qs", "qs"), ("timedatectl", "timedatectl")):
            self.p.item(consts.TOOLS, key, os.path.join(STUBS, name))
        self.p.set(consts, "TOOL_OWNER_UIDS", (0, UID))
        self.p.item(consts.TOOLS, "node", os.path.join(self.tmp, "no-node"))
        self.p.set(consts, "CLI_CANDIDATES", {h: [os.path.join(self.tmp, "none", h)] for h in consts.HARNESSES})
        self.p.set(consts, "STOP_POLL_S", 1.0)
        self.p.set(consts, "STOP_POLL_RUNNING_S", 1.0)
        self.fakesys = os.path.join(self.home, ".fakesys")
        real_run_bounded = bounded.run_bounded

        def guarded(argv, **kw):
            if not argv or not str(argv[0]).startswith(STUBS + "/"):
                raise AssertionError("refusing to start a program outside tests/stubs: %r" % (argv[:1],))
            return real_run_bounded(argv, **kw)

        self.p.set(bounded, "run_bounded", guarded)
        self.links = {}
        for name in consts.HARNESSES:
            path = os.path.join(self.home, ".local", "bin", name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as handle:
                handle.write("#!/bin/false\n")
            os.chmod(path, 0o755)
            self.links[name] = path
        self.cli_ok = True
        self.codex_login = True
        self.usage_obj = None
        self.session_records = {}
        self.notices = []
        self.lookups = []
        self.p.set(identity, "discover_cli", self.fake_discover)
        self.p.set(sessions, "lookup_session", self.fake_lookup)
        self.p.set(agents, "codex_logged_in", lambda exec_prefix: self.codex_login)
        self.p.set(usage, "read_usage", lambda now_ms, **kw: self.usage_obj)
        # paid.check_job stand-in: gate_over is merged into an ok gate, raised when it is an exception,
        # or called when it is a function. Tests that exercise the real gate restore REAL_CHECK_JOB.
        self.gate_over = {}
        self.gate_calls = []
        self.p.set(paid, "check_job", self.fake_gate)
        self.p.set(notify, "send", lambda event, job, **kw: self.notices.append((event, job["id"])) or True)
        self.p.set(notify, "ipc_ping", lambda: None)
        self.project = self.make_dir("proj")
        os.makedirs(os.path.join(self.home, ".config", "omarchy"), mode=0o700)
        self.write_shell(True)

    def tearDown(self):
        self.p.undo()
        os.environ.clear()
        os.environ.update(self.saved_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- fakes -------------------------------------------------------------------------------

    def fake_lookup(self, harness_id, sid, session_path=None):
        self.lookups.append((harness_id, sid, session_path))
        return copy.deepcopy(self.session_records.get((harness_id, sid)))

    def fake_gate(self, job, **kw):
        self.gate_calls.append(dict(kw, harness=job["harness"], allowPaid=job.get("allowPaid")))
        if isinstance(self.gate_over, BaseException):
            raise self.gate_over
        if callable(self.gate_over):
            return self.gate_over(job, **kw)
        return dict(GATE_OK, **self.gate_over)

    def fake_discover(self, harness_id):
        link = self.links[harness_id]
        real = os.path.realpath(link)
        if not self.cli_ok:
            return {"harness": harness_id, "link": None, "real": None, "exec": [], "ok": False, "reason": "not_found"}
        exec_prefix = [consts.TOOLS["node"], real] if harness_id == "gemini" else [real]
        return {"harness": harness_id, "link": link, "real": real, "exec": exec_prefix, "ok": True, "reason": None}

    def make_dir(self, name):
        path = os.path.join(self.home, name)
        os.makedirs(path, exist_ok=True)
        return os.path.realpath(path)

    def write_shell(self, enabled, data=None):
        if data is None:
            right = [edition.PLUGIN_ID] if enabled else ["clock"]
            data = {"bar": {"layout": {"left": [], "center": [], "right": right}}}
        path = os.path.join(self.home, ".config", "omarchy", "shell.json")
        with open(path, "w") as handle:
            json.dump(data, handle)
        os.chmod(path, 0o600)

    def kill_switch(self, present=True):
        path = fsio.kill_switch_path()
        if present:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "w").close()
        elif os.path.exists(path):
            os.unlink(path)

    def fake_config(self, **values):
        os.makedirs(self.fakesys, exist_ok=True)
        with open(os.path.join(self.fakesys, "config.json"), "w") as handle:
            json.dump(values, handle)

    def units(self):
        try:
            with open(os.path.join(self.fakesys, "state.json")) as handle:
                return json.load(handle)["units"]
        except OSError:
            return {}

    def set_units(self, units):
        os.makedirs(self.fakesys, exist_ok=True)
        with open(os.path.join(self.fakesys, "state.json"), "w") as handle:
            json.dump({"units": units}, handle)

    def calls(self, tool=None):
        try:
            with open(os.path.join(self.fakesys, "calls.jsonl")) as handle:
                entries = [json.loads(line) for line in handle]
        except OSError:
            return []
        return [e for e in entries if tool is None or e["tool"] == tool]

    def ctl_verbs(self):
        return [c["argv"][2] for c in self.calls("systemctl")]

    # -- verbs -------------------------------------------------------------------------------

    def verb(self, *args, stdin=None):
        """Run main.main in-process with fds 0/1/2 redirected. Returns (rc, obj, stderr text)."""
        sys.stdout.flush()
        sys.stderr.flush()
        out = tempfile.TemporaryFile(dir=self.tmp)
        err = tempfile.TemporaryFile(dir=self.tmp)
        saved = [os.dup(0), os.dup(1), os.dup(2)]
        thread = None
        if stdin is None:
            in_fd = os.open(os.devnull, os.O_RDONLY)
        else:
            in_fd, write_fd = os.pipe()
            data = stdin.encode("utf-8") if isinstance(stdin, str) else stdin

            def writer():
                try:
                    write_all(write_fd, data)
                except OSError:
                    pass
                finally:
                    os.close(write_fd)

            thread = threading.Thread(target=writer, daemon=True)
            thread.start()
        os.dup2(in_fd, 0)
        os.close(in_fd)
        os.dup2(out.fileno(), 1)
        os.dup2(err.fileno(), 2)
        try:
            rc = helper_main.main(list(args))
        finally:
            for fd, copy_fd in zip((0, 1, 2), saved):
                os.dup2(copy_fd, fd)
                os.close(copy_fd)
            if thread is not None:
                thread.join(10)
        with out, err:
            out.seek(0)
            err.seek(0)
            text = out.read().decode("utf-8")
            err_text = err.read().decode("utf-8")
        lines = text.split("\n")
        self.assertEqual((len(lines), lines[-1]), (2, ""), text)
        return rc, json.loads(lines[0]), err_text

    def draft(self, **over):
        base = {"harness": "claude",
                "target": {"mode": "new", "sessionId": None, "cwd": self.project, "allowNonGit": False},
                "level": "plan", "limits": {}, "model": None,
                "trigger": {"kind": "at", "fireAt": timeutil.now() + 3600, "delaySec": None, "marginSec": None,
                            "weeklyPolicy": "defer"},
                "prompt": "Check the build and summarize failures."}
        base.update(over)
        return base

    def create(self, **over):
        rc, obj, _err = self.verb("job-create", stdin=json.dumps(self.draft(**over)) + "\n")
        self.assertEqual(rc, 0, obj)
        return obj

    def create_armed(self, fire_in=3600, **over):
        trig = over.pop("trigger", None) or {"kind": "at", "fireAt": timeutil.now() + fire_in, "delaySec": None,
                                             "marginSec": None, "weeklyPolicy": "defer"}
        job_id = self.create(trigger=trig, **over)["id"]
        rc, obj, _err = self.verb("arm", job_id, "--digest", self.stored(job_id)["digest"])
        self.assertEqual(rc, 0, obj)
        return job_id

    def sd(self):
        return fsio.open_state(create=True)

    def store(self):
        return jobs.load_store(self.sd())

    def stored(self, job_id):
        return jobs.find_job(self.store(), job_id)

    def mutate(self, job_id, fn):
        sd = self.sd()
        with sd.lock(5):
            store = jobs.load_store(sd)
            fn(jobs.find_job(store, job_id))
            jobs.save_store(sd, store)

    def state_path(self, *parts):
        return os.path.join(self.home, ".local", "state", "omarchy", edition.STATE_DIR_NAME, *parts)


# ================================================================================ dispatcher

class DispatcherTests(Sandbox):
    def test_exit_codes(self):
        self.assertEqual(self.verb()[:2], (2, {"ok": False, "code": "bad_args", "message": CONTRACT_MESSAGES["bad_args"]}))
        for args in (("nope",), ("list", "extra"), ("job-get", "XYZ"), ("job-get",),
                     ("arm", "0123456789abcdef", "--digest=" + "a" * 64), ("arm", "0123456789abcdef", "--digest"),
                     ("reschedule", "0123456789abcdef", "123"), ("swap", "0123456789abcdef", "0123456789abcdef"),
                     ("sessions", "--harness", "vim"), ("agents", "--login", "x"), ("run", "--job", "x", "--gen", "1")):
            rc, obj, _err = self.verb(*args)
            self.assertEqual((rc, obj["code"]), (2, "bad_args"), args)
        rc, obj, err = self.verb("job-get", "0123456789abcdef")
        self.assertEqual((rc, obj["code"], err), (1, "not_found", "ap4a: not_found\n"))
        rc, obj, err = self.verb("edition")
        self.assertEqual((rc, obj["ok"], err), (0, True, ""))

        def boom(argv, payload):
            raise RuntimeError("secret /home/x/prompt text")

        self.p.set(cli_core, "cmd_list", boom)
        rc, obj, err = self.verb("list")
        self.assertEqual((rc, obj, err), (70, {"ok": False, "code": "internal", "message": "The helper hit an unexpected error. Try again."},
                                          "ap4a: internal\n"))

        self.p.item(consts.OUTPUT_CAP, "edition", 100)
        rc, obj, _err = self.verb("edition")
        self.assertEqual((rc, obj["code"]), (1, "output_too_large"))

        self.p.item(consts.VERB_DEADLINE_S, "settings-get", 0.3)
        self.p.set(cli_core, "cmd_settings_get", lambda argv, payload: time.sleep(3) or {"ok": True})
        start = time.monotonic()
        rc, obj, _err = self.verb("settings-get")
        self.assertEqual((rc, obj["code"]), (1, "internal"))
        self.assertLess(time.monotonic() - start, 2)

    def test_launcher_process(self):
        env = {"HOME": self.home, "PATH": "/usr/bin", "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
        argv = ["/usr/bin/python3", "-I", "-S", "-B", os.path.join(ROOT, "bin", "ap4a")]
        res = subprocess.run(argv, env=env, capture_output=True, timeout=20)
        self.assertEqual(res.returncode, 2)
        self.assertEqual(json.loads(res.stdout)["code"], "bad_args")
        res = subprocess.run(argv + ["edition"], env=env, capture_output=True, timeout=20)
        self.assertEqual((res.returncode, res.stderr), (0, b""))
        self.assertEqual([lv["id"] for lv in json.loads(res.stdout)["levels"]], ["plan", "unattended"])
        res = subprocess.run(argv + ["run", "--job", "0123456789abcdef", "--gen", "1"], env=env, capture_output=True,
                             timeout=20)
        self.assertEqual((res.returncode, res.stdout), (0, b""))
        self.assertTrue(os.stat(os.path.join(ROOT, "bin", "ap4a")).st_mode & 0o111)
        self.assertFalse(os.path.exists(os.path.join(ROOT, "bin", "autopilot", "__pycache__")))

    def test_edition_verb_matches_module(self):
        rc, obj, _err = self.verb("edition")
        self.assertEqual(rc, 0)
        self.assertEqual(obj["levels"], json.loads(json.dumps(edition.LEVELS)))
        self.assertEqual([lv["id"] for lv in obj["levels"]], ["plan", "unattended"])
        self.assertEqual(edition.LEVEL_IDS, ("plan", "unattended"))
        for lv in edition.LEVELS:
            value = lv["harness"]["opencode"]["env"]["OPENCODE_PERMISSION"]
            self.assertTrue(set(json.loads(value).values()) <= {"deny", "ask"})
        with open(os.path.join(ROOT, "manifest.json")) as handle:
            manifest = json.load(handle)
        info = obj["edition"]
        self.assertEqual(manifest["id"], edition.PLUGIN_ID)
        self.assertEqual(info, {
            "pluginId": edition.PLUGIN_ID, "displayName": edition.DISPLAY_NAME, "unitPrefix": edition.UNIT_PREFIX,
            "stateDirName": edition.STATE_DIR_NAME, "runtimeDirName": edition.RUNTIME_DIR_NAME,
            "killSwitchPath": os.path.join(self.home, ".config", "omarchy", edition.CONFIG_DIR_NAME,
                                           edition.KILL_SWITCH_NAME),
            "widgetIpcTarget": edition.WIDGET_IPC_TARGET, "serviceIpcTarget": edition.SERVICE_IPC_TARGET,
            "notifyAppName": edition.NOTIFY_APP_NAME, "schemaVersion": 1, "pluginDir": os.path.realpath(ROOT),
            "version": manifest["version"]})
        self.assertEqual(obj["harnesses"], [
            {"id": "claude", "name": "Claude Code", "cliName": "claude", "canFork": True, "gated": False,
             "resetTrigger": "claude_5h_reset", "resetTriggers": ["claude_5h_reset"]},
            {"id": "opencode", "name": "OpenCode", "cliName": "opencode", "canFork": True, "gated": False,
             "resetTrigger": "zen_free_reset", "resetTriggers": ["zen_free_reset", "go_window_reset"]},
            {"id": "codex", "name": "Codex", "cliName": "codex", "canFork": True, "gated": False,
             "resetTrigger": "codex_window_reset", "resetTriggers": ["codex_window_reset"]},
            {"id": "gemini", "name": "Gemini CLI", "cliName": "gemini", "canFork": False, "gated": False,
             "resetTrigger": "gemini_daily_reset", "resetTriggers": ["gemini_daily_reset"]},
            {"id": "cursor", "name": "Cursor Agent", "cliName": "cursor-agent", "canFork": False, "gated": False,
             "resetTrigger": None, "resetTriggers": []},
            {"id": "pi", "name": "Pi", "cliName": "pi", "canFork": True, "gated": False,
             "resetTrigger": "codex_window_reset", "resetTriggers": ["codex_window_reset"]}])
        self.assertEqual(obj["caps"], {"promptBytes": 65536, "labelChars": 40, "titleChars": 120, "maxTurns": [1, 200],
                                       "budgetUsd": [0.1, 100.0], "runtimeSec": [300, 14400], "marginSec": [60, 540],
                                       "horizonSec": 691200, "uiMinLeadSec": 60, "shiftMaxIds": 50,
                                       "shiftRangeSec": 604800})
        self.assertEqual(set(consts.VERB_DEADLINE_S) | {"run"}, set(helper_main.VERBS))

    def test_unit_name_grammar(self):
        job_id = "0123456789abcdef"
        self.assertEqual(systemd.unit_name(job_id, 7), edition.UNIT_PREFIX + "-" + job_id + "-g7")
        for bad in (("0123456789ABCDEF", 1), ("0123", 1), (job_id, -1), (job_id, 1000000), (job_id, True),
                    (job_id, "1"), ("../../etc/passwd", 1), (job_id + "\n", 1), (None, 1)):
            with self.assertRaises(ApError, msg=repr(bad)):
                systemd.unit_name(*bad)
        prefix = edition.UNIT_PREFIX
        self.assertTrue(consts.UNIT_FILE_RE.fullmatch(prefix + "-" + job_id + "-g1.timer"))
        self.assertTrue(consts.UNIT_FILE_RE.fullmatch(prefix + "-" + job_id + "-g999999.service"))
        for name in (prefix + "-" + job_id + "-g1.scope", prefix + "x-" + job_id + "-g1.timer",
                     "x" + prefix + "-" + job_id + "-g1.timer", prefix + "-" + job_id + "-g1234567.timer",
                     prefix + "-notes.service"):
            self.assertIsNone(consts.UNIT_FILE_RE.fullmatch(name), name)
        now = timeutil.now()
        refusals = (((job_id, 0, now + 600, 5400), "internal"), ((job_id, 1, now - 61, 5400), "time_past"),
                    ((job_id, 1, now + consts.HORIZON_S + 10, 5400), "time_too_far"),
                    ((job_id, 1, str(now + 600), 5400), "invalid_trigger"), ((job_id, 1, now + 600, 299), "invalid_limits"),
                    ((job_id, 1, now + 600, 14401), "invalid_limits"))
        for args, code in refusals:
            with self.assertRaises(ApError) as ctx:
                systemd.arm_unit(*args)
            self.assertEqual(ctx.exception.code, code, args)
        self.assertEqual(self.calls("systemd-run"), [])
        for epoch in ("0999999999", "4102444801", "999999999", "99999999999"):
            self.assertEqual(self.verb("reschedule", job_id, epoch)[:1], (2,), epoch)
        self.assertEqual(self.verb("reschedule", job_id, "4102444800")[1]["code"], "not_found")
        for gen in ("0", "1234567", "-1"):
            self.assertEqual(self.verb("run", "--job", job_id, "--gen", gen)[1]["code"], "bad_args")


# ================================================================================ state files

class StateTests(Sandbox):
    def test_state_dir_symlink_refused(self):
        other = os.path.join(self.tmp, "elsewhere")
        os.makedirs(other, mode=0o700)
        parent = os.path.join(self.home, ".local", "state", "omarchy")
        os.makedirs(parent)
        os.symlink(other, self.state_path())
        with self.assertRaises(ApError) as ctx:
            fsio.open_state()
        self.assertEqual(ctx.exception.code, "state_refused")
        self.assertEqual(self.verb("list")[1]["code"], "state_refused")
        os.unlink(self.state_path())

        shutil.rmtree(os.path.join(self.home, ".local"))
        os.symlink(other, os.path.join(self.home, ".local"))
        with self.assertRaises(ApError):
            fsio.open_state()
        os.unlink(os.path.join(self.home, ".local"))

        os.makedirs(self.state_path(), mode=0o755)
        os.chmod(self.state_path(), 0o755)
        with self.assertRaises(ApError) as ctx:
            fsio.open_state()
        self.assertEqual(ctx.exception.code, "state_refused")
        os.chmod(self.state_path(), 0o700)

        target = os.path.join(other, "jobs.json")
        with open(target, "w") as handle:
            json.dump(jobs.empty_store(), handle)
        os.chmod(target, 0o600)
        os.symlink(target, self.state_path("jobs.json"))
        with self.assertRaises(ApError) as ctx:
            jobs.load_store(self.sd())
        self.assertEqual(ctx.exception.code, "state_refused")
        os.unlink(self.state_path("jobs.json"))

        os.symlink(other, self.state_path("prompts"))
        with self.assertRaises(ApError) as ctx:
            jobs.read_prompt(self.sd(), "0123456789abcdef")
        self.assertEqual(ctx.exception.code, "state_refused")

    def test_jobs_fifo_refused_nonblocking(self):
        sd = self.sd()
        os.mkfifo(self.state_path("jobs.json"), 0o600)
        start = time.monotonic()
        with alarm_guard(3):
            with self.assertRaises(ApError) as ctx:
                jobs.load_store(sd)
        self.assertEqual(ctx.exception.code, "state_refused")
        self.assertLess(time.monotonic() - start, 1)
        os.mkfifo(self.state_path("settings.json"), 0o600)
        with alarm_guard(3):
            self.assertEqual(settings.load(sd), settings.DEFAULTS)

    def test_jobs_oversize(self):
        self.sd()
        path = self.state_path("jobs.json")
        with open(path, "wb") as handle:
            handle.write(b" " * (consts.JOBS_FILE_MAX + 1))
        os.chmod(path, 0o600)
        with self.assertRaises(ApError) as ctx:
            jobs.load_store(self.sd())
        self.assertEqual(ctx.exception.code, "state_too_large")
        self.assertEqual(self.verb("list")[1]["code"], "state_too_large")
        os.unlink(path)
        self.create()
        store = self.store()
        self.p.set(consts, "JOBS_FILE_MAX", 1000)
        with self.assertRaises(ApError) as ctx:
            jobs.save_store(self.sd(), store)
        self.assertEqual(ctx.exception.code, "store_full")

    def test_store_near_its_cap(self):
        # job-create keeps a reserve; a change to a stored job evicts the oldest finished jobs instead of failing.
        armed = self.create_armed()
        closed = [self.create()["id"] for _ in range(3)]
        now = timeutil.now()
        for age, job_id in enumerate(closed):
            self.mutate(job_id, lambda j, age=age: (j["state"].update(status="done"),
                                                    j.update(updatedAt=now - 1000 + age, promptAvailable=False)))
        jobs.write_run_record(self.sd(), {"runId": closed[0] + "-g1", "jobId": closed[0], "gen": 1})
        self.p.set(consts, "JOBS_FILE_MAX", os.path.getsize(self.state_path("jobs.json")) + 64)
        rc, obj, _err = self.verb("job-create", stdin=json.dumps(self.draft()) + "\n")
        self.assertEqual((rc, obj["code"]), (1, "store_full"))
        sd = self.sd()
        with sd.lock(5):
            store = jobs.load_store(sd)
            job = jobs.find_job(store, armed)
            for _ in range(6):
                jobs.add_history(job, "rearmed", now)
            jobs.save_store(sd, store)
        self.assertEqual(sorted(j["id"] for j in self.store()["jobs"]), sorted([armed, closed[1], closed[2]]))
        self.assertEqual(os.listdir(self.state_path("runs")), [])

    def test_plugin_code_must_be_safe_to_run(self):
        self.assertTrue(fsio.plugin_code_trusted())
        real_dir, real_trust = fsio.plugin_dir, fsio.check_trusted_file
        fake = os.path.join(self.tmp, "plugin")
        package = os.path.join(fake, "bin", "autopilot")
        os.makedirs(package, mode=0o755)
        launcher = os.path.join(fake, "bin", "ap4a")
        for path, mode in ((launcher, 0o755), (os.path.join(package, "main.py"), 0o644)):
            with open(path, "w") as handle:
                handle.write("#\n")
            os.chmod(path, mode)

        def trust(path, *, executable, check_ancestors=True):
            return real_trust(path, executable=executable, check_ancestors=check_ancestors and not path.startswith(fake))

        self.p.set(fsio, "check_trusted_file", trust)
        self.p.set(fsio, "plugin_dir", lambda: fake)
        self.assertTrue(fsio.plugin_code_trusted())
        os.chmod(launcher, 0o775)
        self.assertFalse(fsio.plugin_code_trusted())
        os.chmod(launcher, 0o755)
        os.chmod(package, 0o777)
        self.assertFalse(fsio.plugin_code_trusted())
        os.chmod(package, 0o755)
        os.symlink(os.path.join(package, "main.py"), os.path.join(package, "linked.py"))
        self.assertFalse(fsio.plugin_code_trusted())
        self.p.set(fsio, "plugin_dir", real_dir)
        self.p.set(fsio, "plugin_code_trusted", lambda: False)
        job_id = self.create()["id"]
        self.mutate(job_id, lambda j: j["state"].update(pluginDir=real_dir()))
        self.assertEqual(identity.check_plugin_identity(self.stored(job_id)), "plugin_identity")
        with self.assertRaises(ApError) as ctx:
            systemd.arm_unit(job_id, 2, timeutil.now() + 600, 5400)
        self.assertEqual(ctx.exception.code, "plugin_identity")

    def test_system_tools_must_be_root_owned(self):
        self.p.set(consts, "TOOL_OWNER_UIDS", (0,))
        res = bounded.run_bounded([consts.TOOLS["busctl"], "--user"], env={}, deadline_s=5)
        self.assertEqual(res["error"], "untrusted_tool")
        self.assertEqual(self.calls("busctl"), [])
        fsio.check_tool("/usr/bin/systemctl")
        fsio.check_tool("/usr/bin/qs")

    def test_jobs_nlink_refused(self):
        self.create()
        path = self.state_path("jobs.json")
        os.link(path, self.state_path("jobs-copy"))
        with self.assertRaises(ApError) as ctx:
            jobs.load_store(self.sd())
        self.assertEqual(ctx.exception.code, "state_refused")
        os.unlink(self.state_path("jobs-copy"))
        jobs.load_store(self.sd())
        os.chmod(path, 0o644)
        with self.assertRaises(ApError) as ctx:
            jobs.load_store(self.sd())
        self.assertEqual(ctx.exception.code, "state_refused")

    def test_atomic_write_mode_0600(self):
        sd = self.sd()
        old_umask = os.umask(0)
        try:
            sd.write_atomic("x.json", b"1")
            first = os.stat(self.state_path("x.json"))
            sd.write_atomic("x.json", b"22")
        finally:
            os.umask(old_umask)
        second = os.stat(self.state_path("x.json"))
        self.assertEqual(stat.S_IMODE(second.st_mode), 0o600)
        self.assertNotEqual(first.st_ino, second.st_ino)
        self.assertEqual(read_file(self.state_path("x.json"), "rb"), b"22")
        outside = os.path.join(self.tmp, "outside.txt")
        with open(outside, "w") as handle:
            handle.write("keep")
        os.symlink(outside, self.state_path("link.json"))
        sd.write_atomic("link.json", b"new")
        self.assertFalse(os.path.islink(self.state_path("link.json")))
        self.assertEqual(read_file(outside), "keep")
        self.assertEqual([n for n in os.listdir(self.state_path()) if n.endswith(".tmp")], [])
        created = self.create()
        self.assertEqual(stat.S_IMODE(os.stat(self.state_path()).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(self.state_path("jobs.json")).st_mode), 0o600)
        prompt = self.state_path("prompts", created["id"] + ".txt")
        self.assertEqual(stat.S_IMODE(os.stat(prompt).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.state_path("prompts")).st_mode), 0o700)

    def test_lock_busy(self):
        sd = self.sd()
        with sd.lock(1):
            start = time.monotonic()
            with self.assertRaises(ApError) as ctx:
                with sd.lock(0.2):
                    pass
            self.assertEqual(ctx.exception.code, "lock_busy")
            self.assertGreaterEqual(time.monotonic() - start, 0.2)
            self.p.set(consts, "LOCK_WAIT_S", 0.2)
            rc, obj, _err = self.verb("job-delete", "0123456789abcdef")
            self.assertEqual((rc, obj["code"]), (1, "lock_busy"))
        with sd.lock(0.2):
            pass

    def test_level_closed_enum_on_load(self):
        job_id = self.create()["id"]
        path = self.state_path("jobs.json")
        with open(path) as handle:
            data = json.load(handle)
        for bad in ("everything", "Plan", "", None, 1):
            data["jobs"][0]["level"] = bad
            self.sd().write_atomic("jobs.json", json.dumps(data).encode())
            with self.assertRaises(ApError, msg=repr(bad)) as ctx:
                jobs.load_store(self.sd())
            self.assertEqual(ctx.exception.code, "state_corrupt")
        self.assertEqual(self.verb("list")[1]["code"], "state_corrupt")
        data["jobs"][0]["level"] = "unattended"
        self.sd().write_atomic("jobs.json", json.dumps(data).encode())
        self.assertEqual(self.stored(job_id)["level"], "unattended")
        data["jobs"][0]["surprise"] = True
        self.sd().write_atomic("jobs.json", json.dumps(data).encode())
        with self.assertRaises(ApError):
            jobs.load_store(self.sd())

    def test_settings_roundtrip(self):
        rc, obj, _err = self.verb("settings-get")
        self.assertEqual((rc, obj["settings"]), (0, settings.DEFAULTS))
        rc, obj, _err = self.verb("settings-set", stdin=json.dumps({"notify": "failures", "resetMarginSec": 300}))
        self.assertEqual(rc, 0, obj)
        self.assertEqual(obj["settings"], dict(settings.DEFAULTS, notify="failures", resetMarginSec=300))
        self.assertEqual(stat.S_IMODE(os.stat(self.state_path("settings.json")).st_mode), 0o600)
        for body, field in (({"colour": "red"}, None), ({"notify": "loud"}, "notify"), ({"resetMarginSec": 59}, "resetMarginSec"),
                            ({"defaultLevel": "everything"}, "defaultLevel"), ({"eveningTime": "24:00"}, "eveningTime")):
            rc, obj, _err = self.verb("settings-set", stdin=json.dumps(body))
            self.assertEqual((rc, obj["code"], obj.get("field")), (1, "bad_input", field), body)
        self.sd().write_atomic("settings.json", b"{broken")
        self.assertEqual(self.verb("settings-get")[1]["settings"], settings.DEFAULTS)
        self.assertEqual(self.create(trigger={"kind": "claude_5h_reset"})["job"]["trigger"]["marginSec"], 120)


# ================================================================================ drafts and digests

class DraftTests(Sandbox):
    def expect(self, code, field, draft, require_prompt=True):
        with self.assertRaises(ApError, msg="%s %r" % (code, draft)) as ctx:
            jobs.validate_draft(draft, require_prompt=require_prompt)
        self.assertEqual((ctx.exception.code, ctx.exception.field), (code, field), draft)

    def test_validate_draft_grammar_table(self):
        uuid_sid = "3f2a0c19-0000-4000-8000-00000000abcd"

        def with_target(**target):
            return self.draft(target=dict({"mode": "new", "sessionId": None, "cwd": self.project, "allowNonGit": False},
                                          **target))

        def with_trigger(**trig):
            return self.draft(trigger=dict({"kind": "at", "fireAt": timeutil.now() + 600}, **trig))

        def with_limits(**limits):
            return self.draft(limits=limits)

        table = [
            ("invalid_harness", "harness", self.draft(harness="vim")),
            ("invalid_harness", "harness", self.draft(harness=None)),
            ("invalid_level", "level", self.draft(level="everything")),
            ("invalid_level", "level", self.draft(level="Plan")),
            ("invalid_level", "level", self.draft(level=None)),
            ("invalid_target", "target", self.draft(target="new")),
            ("invalid_target", "target.mode", with_target(mode="attach")),
            ("invalid_target", "target.sessionId", with_target(sessionId=uuid_sid)),
            ("invalid_target", "target.allowNonGit", with_target(allowNonGit="yes")),
            ("invalid_session", "target.sessionId", with_target(mode="resume", sessionId="not-a-uuid")),
            ("invalid_session", "target.sessionId", with_target(mode="resume", sessionId=uuid_sid.upper())),
            ("invalid_session", "target.sessionId", dict(with_target(mode="resume", sessionId=uuid_sid), harness="opencode")),
            ("invalid_session", "target.sessionId", dict(with_target(mode="resume", sessionId="ses_abc"), harness="opencode")),
            ("invalid_cwd", "target.cwd", with_target(cwd="relative/path")),
            ("invalid_cwd", "target.cwd", with_target(cwd="/tmp/x\n")),
            ("invalid_cwd", "target.cwd", with_target(cwd="/" + "a" * 1024)),
            ("invalid_cwd", "target.cwd", with_target(cwd=None)),
            ("invalid_model", "model", self.draft(model="-flag")),
            ("invalid_model", "model", self.draft(model="a b")),
            ("invalid_model", "model", self.draft(model="m" * 129)),
            ("invalid_model", "model", self.draft(model=5)),
            ("invalid_label", "label", self.draft(label="x" * 41)),
            ("invalid_label", "label", self.draft(label="bell\x07")),
            ("invalid_label", "label", self.draft(label=5)),
            ("invalid_limits", "limits", self.draft(limits=[])),
            ("invalid_limits", "limits", with_limits(maxturns=3)),
            ("invalid_limits", "limits.maxTurns", with_limits(maxTurns=0)),
            ("invalid_limits", "limits.maxTurns", with_limits(maxTurns=201)),
            ("invalid_limits", "limits.maxTurns", with_limits(maxTurns=True)),
            ("invalid_limits", "limits.maxTurns", with_limits(maxTurns=1.5)),
            ("invalid_limits", "limits.budgetUsd", with_limits(budgetUsd=0.09)),
            ("invalid_limits", "limits.budgetUsd", with_limits(budgetUsd=100.01)),
            ("invalid_limits", "limits.budgetUsd", with_limits(budgetUsd="5")),
            ("invalid_limits", "limits.runtimeSec", with_limits(runtimeSec=299)),
            ("invalid_limits", "limits.runtimeSec", with_limits(runtimeSec=14401)),
            ("invalid_trigger", "trigger", self.draft(trigger=None)),
            ("invalid_trigger", "trigger", with_trigger(when="soon")),
            ("invalid_trigger", "trigger.kind", with_trigger(kind="cron")),
            ("invalid_trigger", "trigger.fireAt", with_trigger(fireAt=999999999)),
            ("invalid_trigger", "trigger.fireAt", with_trigger(fireAt=4102444801)),
            ("invalid_trigger", "trigger.fireAt", with_trigger(fireAt=True)),
            ("invalid_trigger", "trigger.fireAt", with_trigger(fireAt=None)),
            ("invalid_trigger", "trigger.fireAt", with_trigger(fireAt=1789000000.5)),
            ("invalid_trigger", "trigger.delaySec", with_trigger(kind="in", delaySec=59)),
            ("invalid_trigger", "trigger.delaySec", with_trigger(kind="in", delaySec=691201)),
            ("invalid_trigger", "trigger.marginSec", with_trigger(marginSec=59)),
            ("invalid_trigger", "trigger.marginSec", with_trigger(marginSec=541)),
            ("invalid_trigger", "trigger.weeklyPolicy", with_trigger(weeklyPolicy="ignore")),
            ("trigger_unsupported", "trigger.kind", with_trigger(kind="codex_window_reset")),
            ("trigger_unsupported", "trigger.kind", dict(with_trigger(kind="claude_5h_reset"), harness="gemini")),
            ("fork_unsupported", "target.mode", dict(with_target(mode="fork", sessionId=uuid_sid), harness="gemini")),
            ("prompt_empty", "prompt", self.draft(prompt="   \n\t")),
            ("prompt_empty", "prompt", self.draft(prompt=None)),
            ("prompt_too_large", "prompt", self.draft(prompt="é" * 32769)),
            ("bad_input", "prompt", self.draft(prompt=["x"])),
            ("bad_input", None, self.draft(extra=True)),
            ("bad_input", "expectCommandDigest", self.draft(expectCommandDigest="abc")),
            ("bad_input", None, ["not", "an", "object"]),
        ]
        codes = set()
        for code, field, draft in table:
            self.expect(code, field, draft)
            codes.add(code)
        self.assertTrue({c for c in CONTRACT_MESSAGES if c.startswith("invalid_")} <= codes)

        ok = jobs.validate_draft(self.draft(limits={"maxTurns": 200, "budgetUsd": 0.1, "runtimeSec": 14400},
                                            label="x" * 40, prompt="a" * 65536, model="claude-opus-5[1m]"),
                                 require_prompt=True)
        self.assertEqual((ok["limits"], ok["label"], ok["trigger"]["weeklyPolicy"]),
                         ({"maxTurns": 200, "budgetUsd": 0.1, "runtimeSec": 14400}, "x" * 40, "defer"))
        self.assertEqual(jobs.validate_draft(self.draft(level="unattended"), require_prompt=True)["limits"]["maxTurns"], 30)
        self.assertEqual(jobs.validate_draft(with_trigger(kind="in", delaySec=691200, fireAt=None),
                                             require_prompt=True)["trigger"]["delaySec"], 691200)
        resume = jobs.validate_draft(with_target(mode="resume", sessionId=uuid_sid, cwd="ignored"), require_prompt=False)
        self.assertIsNone(resume["target"]["cwd"])
        self.assertFalse(jobs.validate_draft(with_target(allowNonGit=True), require_prompt=True)["target"]["allowNonGit"])

    def test_check_cwd_refusals(self):
        plugins = self.make_dir(".config/omarchy/plugins/some.plugin")
        a_file = os.path.join(self.home, "file.txt")
        open(a_file, "w").close()
        os.symlink("/tmp", os.path.join(self.home, "to-tmp"))
        for path in ("/", self.home, "/tmp", "/run/user", os.path.realpath(ROOT), os.path.join(ROOT, "bin"), plugins,
                     a_file, os.path.join(self.home, "missing"), os.path.join(self.home, "to-tmp"), "/usr"):
            with self.assertRaises(ApError, msg=path) as ctx:
                jobs.check_cwd(path)
            self.assertEqual(ctx.exception.code, "invalid_cwd")
        self.assertEqual(jobs.check_cwd(self.project + "/"), self.project)

        sid = "3f2a0c19-0000-4000-8000-00000000abcd"
        target = {"mode": "resume", "sessionId": sid, "cwd": None, "allowNonGit": False}
        rc, obj, _err = self.verb("job-create", stdin=json.dumps(self.draft(target=target)))
        self.assertEqual((rc, obj["code"], obj["field"]), (1, "session_not_found", "target.sessionId"))
        self.session_records[("claude", sid)] = {"harness": "claude", "id": sid, "cwd": "/", "title": "t", "updatedAtMs": 0}
        self.assertEqual(self.verb("job-create", stdin=json.dumps(self.draft(target=target)))[1]["code"], "invalid_cwd")
        self.session_records[("claude", sid)]["cwd"] = self.project
        created = self.create(target=target)
        self.assertEqual((created["job"]["target"]["cwd"], created["job"]["target"]["title"]), (self.project, "t"))
        self.codex_login = False
        self.assertEqual(self.verb("job-create", stdin=json.dumps(self.draft(harness="codex")))[1]["code"], "not_logged_in")
        self.cli_ok = False
        self.assertEqual(self.verb("job-create", stdin=json.dumps(self.draft()))[1]["code"], "cli_missing")

    def test_digest_stable_and_binding(self):
        job = self.stored(self.create()["id"])
        base_cmd, base_full = jobs.command_digest(job), jobs.full_digest(job)
        self.assertEqual((job["commandDigest"], job["digest"]), (base_cmd, base_full))
        reordered = json.loads(json.dumps(dict(reversed(list(job.items())))))
        self.assertEqual((jobs.command_digest(reordered), jobs.full_digest(reordered)), (base_cmd, base_full))

        def changed(fn):
            other = copy.deepcopy(job)
            fn(other)
            return jobs.command_digest(other) != base_cmd, jobs.full_digest(other) != base_full

        bound = {
            "harness": lambda j: j.update(harness="opencode"),
            "cli.link": lambda j: j["cli"].update(link="/usr/bin/other"),
            "target.mode": lambda j: j["target"].update(mode="resume", sessionId="3f2a0c19-0000-4000-8000-00000000abcd"),
            "target.cwd": lambda j: j["target"].update(cwd="/home/else"),
            "target.allowNonGit": lambda j: j["target"].update(allowNonGit=True),
            "level": lambda j: j.update(level="unattended"),
            "limits.maxTurns": lambda j: j["limits"].update(maxTurns=16),
            "limits.budgetUsd": lambda j: j["limits"].update(budgetUsd=5.01),
            "limits.runtimeSec": lambda j: j["limits"].update(runtimeSec=5401),
            "model": lambda j: j.update(model="sonnet"),
            "trigger.kind": lambda j: j["trigger"].update(kind="in"),
        }
        for name, fn in bound.items():
            self.assertEqual(changed(fn), (True, True), name)
        unbound = {
            "label": lambda j: j.update(label="Other"),
            "cli.real": lambda j: j["cli"].update(real="/elsewhere"),
            "cli.version": lambda j: j["cli"].update(version="9.9.9"),
            "target.title": lambda j: j["target"].update(title="x"),
            "trigger.fireAt": lambda j: j["trigger"].update(fireAt=1789000000),
            "state": lambda j: j["state"].update(gen=9, status="armed"),
        }
        for name, fn in unbound.items():
            self.assertEqual(changed(fn), (False, False), name)
        self.assertEqual(changed(lambda j: j.update(promptSha256="f" * 64)), (False, True))
        self.assertEqual(changed(lambda j: j["target"].update(newSessionId="3f2a0c19-0000-4000-8000-00000000ffff")),
                         (False, True))

    def test_preview_digest_matches_create(self):
        draft = self.draft()
        rc, obj, _err = self.verb("preview", stdin=json.dumps(draft))
        self.assertEqual(rc, 0, obj)
        preview = obj["preview"]
        self.assertFalse(os.path.exists(self.state_path("jobs.json")))
        self.assertEqual(preview["fireAt"], draft["trigger"]["fireAt"])
        created = self.create(**draft)
        self.assertEqual(preview["commandDigest"], created["commandDigest"])
        rc, obj, _err = self.verb("job-create", stdin=json.dumps(dict(draft, expectCommandDigest=preview["commandDigest"])))
        self.assertEqual(rc, 0, obj)
        past = dict(draft, trigger={"kind": "at", "fireAt": timeutil.now() - 3600})
        rc, obj, _err = self.verb("preview", stdin=json.dumps(past))
        self.assertEqual((rc, obj["preview"]["fireAtError"]), (0, "time_past"))
        no_trigger = {k: v for k, v in draft.items() if k != "trigger"}
        rc, obj, _err = self.verb("preview", stdin=json.dumps(no_trigger))
        self.assertEqual((rc, obj["preview"]["fireAt"]), (0, None))

    def test_copy_resume_quoting(self):
        sid = "3f2a0c19-0000-4000-8000-00000000abcd"
        odd = self.make_dir("it's a dir")
        self.session_records[("claude", sid)] = {"harness": "claude", "id": sid, "cwd": odd, "title": "", "updatedAtMs": 0}
        job_id = self.create(target={"mode": "resume", "sessionId": sid, "cwd": None, "allowNonGit": False})["id"]
        self.assertEqual(self.stored(job_id)["target"]["title"], "it's a dir")
        rc, obj, _err = self.verb("copy-resume", job_id)
        self.assertEqual(rc, 0, obj)
        self.assertEqual(shlex.split(obj["command"]), ["cd", odd, "&&", "claude", "--resume", sid])
        fresh = self.create()["id"]
        self.assertEqual(self.verb("copy-resume", fresh)[1]["code"], "bad_status")
        self.assertEqual(self.verb("copy-resume", "0123456789abcdef")[1]["code"], "not_found")


# ================================================================================ scheduling verbs

class SchedulingTests(Sandbox):
    def test_arm_argv_golden(self):
        now = timeutil.now()
        created = self.create(trigger={"kind": "at", "fireAt": now + 3600})
        job_id = created["id"]
        self.assertEqual(created["job"]["state"]["status"], "draft")
        rc, obj, err = self.verb("arm", job_id, "--digest", created["digest"])
        self.assertEqual((rc, err), (0, ""), obj)
        unit = "%s-%s-g1" % (edition.UNIT_PREFIX, job_id)
        self.assertEqual((obj["status"], obj["fireAt"], obj["unit"], obj["immediate"], obj["hint"]),
                         ("armed", now + 3600, unit, False, None))
        runs = self.calls("systemd-run")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["argv"], golden_argv(job_id, 1, now + 3600, 5400))
        self.assertTrue(set(runs[0]["env"]) - {"LC_CTYPE"} <= TOOL_ENV_KEYS, runs[0]["env"])
        self.assertIn(unit + ".timer", self.units())
        rc, listed, _err = self.verb("list")
        entry = listed["jobs"][0]
        self.assertEqual((entry["state"]["status"], entry["fireAtMs"], entry["state"]["gen"], entry["canDelete"]),
                         ("armed", (now + 3600) * 1000, 1, False))
        self.assertEqual((listed["killSwitch"], listed["enabledInShell"]), (False, True))
        stored = self.stored(job_id)
        self.assertEqual((stored["state"]["pluginDir"], stored["state"]["armedAt"] >= now), (os.path.realpath(ROOT), True))

        # A time a little in the past still arms, two seconds out; further back is refused.
        late = self.create(trigger={"kind": "at", "fireAt": now - 30})
        rc, obj, _err = self.verb("arm", late["id"], "--digest", late["digest"])
        self.assertEqual(rc, 0, obj)
        self.assertAlmostEqual(obj["fireAt"], timeutil.now() + 2, delta=2)
        gone = self.create(trigger={"kind": "at", "fireAt": now - 600})
        rc, obj, _err = self.verb("arm", gone["id"], "--digest", gone["digest"])
        self.assertEqual((rc, obj["code"]), (1, "time_past"))
        self.assertEqual(self.stored(gone["id"])["state"]["status"], "draft")
        self.assertEqual(len(self.calls("systemd-run")), 2)
        self.assertEqual(self.verb("arm", job_id, "--digest", self.stored(job_id)["digest"])[1]["code"], "bad_status")

    def test_run_now_argv_no_timer(self):
        created = self.create()
        rc, obj, _err = self.verb("run-now", created["id"], "--digest", created["digest"])
        self.assertEqual(rc, 0, obj)
        self.assertEqual((obj["immediate"], obj["status"]), (True, "armed"))
        argv = self.calls("systemd-run")[0]["argv"]
        self.assertEqual(argv, golden_argv(created["id"], 1, None, 5400))
        self.assertFalse([a for a in argv if a.startswith(("--on-calendar", "--timer-property"))])
        base = "%s-%s-g1" % (edition.UNIT_PREFIX, created["id"])
        self.assertEqual(sorted(self.units()), [base + ".service"])

        armed = self.create_armed()
        rc, obj, _err = self.verb("run-now", armed, "--digest", self.stored(armed)["digest"])
        self.assertEqual((rc, obj["unit"]), (0, "%s-%s-g2" % (edition.UNIT_PREFIX, armed)))
        old_timer = "%s-%s-g1.timer" % (edition.UNIT_PREFIX, armed)
        self.assertNotIn(old_timer, self.units())
        self.assertTrue(any(c["argv"][2] == "stop" and old_timer in c["argv"] for c in self.calls("systemctl")))

    def test_arm_refuses_kill_switch(self):
        created = self.create()
        self.kill_switch()
        rc, obj, _err = self.verb("arm", created["id"], "--digest", created["digest"])
        self.assertEqual((rc, obj["code"]), (1, "kill_switch"))
        self.assertEqual(self.verb("run-now", created["id"], "--digest", created["digest"])[1]["code"], "kill_switch")
        self.assertEqual(self.calls("systemd-run"), [])
        self.assertEqual(self.stored(created["id"])["state"]["status"], "draft")

    def test_arm_refuses_not_in_shell(self):
        created = self.create()
        self.write_shell(False)
        self.assertEqual(self.verb("arm", created["id"], "--digest", created["digest"])[1]["code"], "plugin_disabled")
        os.unlink(os.path.join(self.home, ".config", "omarchy", "shell.json"))
        self.assertEqual(self.verb("arm", created["id"], "--digest", created["digest"])[1]["code"], "plugin_disabled")
        self.write_shell(True, {"bar": {"layout": {"left": "not a list"}}})
        self.assertEqual(self.verb("arm", created["id"], "--digest", created["digest"])[1]["code"], "plugin_disabled")
        self.assertEqual(self.calls("systemd-run"), [])
        self.write_shell(True, {"plugins": [{"id": edition.PLUGIN_ID}]})
        self.assertEqual(self.verb("arm", created["id"], "--digest", created["digest"])[0], 0)

    def test_arm_digest_mismatch(self):
        created = self.create()
        job_id = created["id"]
        self.assertEqual(self.verb("arm", job_id, "--digest", "f" * 64)[1]["code"], "digest_mismatch")
        # A store edited behind the helper's back gets fresh digests and the old digest is refused.
        self.mutate(job_id, lambda j: j.update(level="unattended"))
        rc, obj, _err = self.verb("arm", job_id, "--digest", created["digest"])
        self.assertEqual((rc, obj["code"]), (1, "digest_mismatch"))
        edited = self.stored(job_id)
        self.assertEqual(edited["digest"], jobs.full_digest(edited))
        self.assertNotEqual(edited["digest"], created["digest"])
        # A CLI found at a different link changes the command: refused once, with the job updated.
        other = os.path.join(self.home, ".opencode", "bin", "claude")
        os.makedirs(os.path.dirname(other))
        shutil.copy(self.links["claude"], other)
        self.links["claude"] = other
        rc, obj, _err = self.verb("arm", job_id, "--digest", edited["digest"])
        self.assertEqual((rc, obj["code"]), (1, "digest_mismatch"))
        moved = self.stored(job_id)
        self.assertEqual((moved["cli"]["link"], moved["state"]["history"][-1]["event"]), (other, "cli_changed"))
        self.assertEqual(self.calls("systemd-run"), [])
        self.assertEqual(self.verb("arm", job_id, "--digest", moved["digest"])[0], 0)
        # A preview digest that no longer matches stores nothing.
        before = len(self.store()["jobs"])
        rc, obj, _err = self.verb("job-create", stdin=json.dumps(self.draft(expectCommandDigest="e" * 64)))
        self.assertEqual((rc, obj["code"]), (1, "preview_stale"))
        self.assertEqual(len(self.store()["jobs"]), before)
        self.assertEqual(len(os.listdir(self.state_path("prompts"))), before)

    def test_disarm_bumps_gen_before_stop(self):
        job_id = self.create_armed()
        seen = []
        real_stop = systemd.stop_units

        def spy(jid, gen):
            state = self.stored(jid)["state"]
            seen.append((gen, state["gen"], state["status"], state["unit"]))
            return real_stop(jid, gen)

        self.p.set(systemd, "stop_units", spy)
        rc, obj, _err = self.verb("disarm", job_id)
        self.assertEqual((rc, obj["verified"], obj["job"]["state"]["status"]), (0, True, "disarmed"), obj)
        self.assertEqual(seen, [(1, 2, "disarmed", None)])
        timer = "%s-%s-g1.timer" % (edition.UNIT_PREFIX, job_id)
        stops = [c["argv"] for c in self.calls("systemctl") if c["argv"][2] == "stop"]
        self.assertEqual(stops, [[consts.TOOLS["systemctl"], "--user", "stop", "--no-block", timer]])
        self.assertEqual(self.ctl_verbs(), ["show", "stop", "show"])
        self.assertEqual(self.units(), {})
        self.assertEqual(self.verb("list")[1]["jobs"][0]["state"]["status"], "disarmed")
        self.assertEqual(self.verb("disarm", job_id)[1]["code"], "bad_status")

    def test_stop_unverified(self):
        job_id = self.create_armed()
        self.fake_config(stuck=[job_id])
        rc, obj, _err = self.verb("disarm", job_id)
        self.assertEqual((rc, obj["code"]), (1, "stop_unverified"))
        state = self.stored(job_id)["state"]
        self.assertEqual((state["status"], state["gen"], state["unit"]), ("disarmed", 2, None))
        second = self.create_armed()
        self.fake_config(stuck=[second])
        rc, obj, _err = self.verb("cancel-all")
        self.assertEqual((rc, obj["verified"], obj["disarmed"]), (0, False, [second]))

    def test_cancel_all_stops_orphans_only_prefix(self):
        first = self.create_armed()
        second = self.create_armed(fire_in=7200)
        draft = self.create()["id"]
        prefix = edition.UNIT_PREFIX
        orphan = "%s-%s-g3.timer" % (prefix, secrets.token_hex(8))
        failed = "%s-%s-g2.service" % (prefix, secrets.token_hex(8))
        foreign = {"other-0123456789abcdef-g1.timer", prefix + "-notes.service", prefix + "-0123456789abcdef-g1.scope",
                   prefix + "b-0123456789abcdef-g1.service", "user-agent.service"}
        units = self.units()
        units[orphan] = {"load": "loaded", "active": "active", "sub": "waiting", "next": 1}
        units[failed] = {"load": "loaded", "active": "failed", "sub": "failed"}
        for name in foreign:
            units[name] = {"load": "loaded", "active": "active", "sub": "running"}
        self.set_units(units)
        self.kill_switch()
        rc, obj, _err = self.verb("cancel-all")
        self.assertEqual(rc, 0, obj)
        self.assertEqual(sorted(obj["disarmed"]), sorted([first, second]))
        expected = {"%s-%s-g1.timer" % (prefix, first), "%s-%s-g1.timer" % (prefix, second), orphan, failed}
        self.assertEqual(set(obj["stoppedUnits"]), expected)
        self.assertTrue(obj["verified"])
        self.assertEqual(set(self.units()), foreign)
        for call in self.calls("systemctl"):
            for name in foreign:
                self.assertNotIn(name, call["argv"])
        self.assertIn([consts.TOOLS["systemctl"], "--user", "reset-failed", failed],
                      [c["argv"] for c in self.calls("systemctl")])
        for job_id in (first, second):
            self.assertEqual((self.stored(job_id)["state"]["status"], self.stored(job_id)["state"]["gen"]), ("disarmed", 2))
        self.assertEqual(self.stored(draft)["state"]["status"], "draft")

    def test_reschedule_unbinds_reset(self):
        now = timeutil.now()
        plan = {"fireAt": now + 1800, "immediate": False, "hint": None, "wait": "reset",
                "basis": {"source": "record", "resetEpoch": now + 1680, "fetchedAtMs": None, "percent": 0.5}}
        self.p.set(trigger, "compute_fire_at", lambda job, at, usage_obj, **kw: dict(plan))
        job_id = self.create_armed(trigger={"kind": "claude_5h_reset"})
        armed = self.stored(job_id)
        self.assertEqual((armed["state"]["wait"], armed["state"]["fireAt"], armed["trigger"]["graceSec"]),
                         ("reset", now + 1800, consts.GRACE_RESET_S))
        target = now + 7200
        rc, obj, _err = self.verb("reschedule", job_id, str(target))
        self.assertEqual((rc, obj["unbound"], obj["fireAt"]), (0, True, target), obj)
        job = self.stored(job_id)
        self.assertEqual((job["trigger"]["kind"], job["trigger"]["fireAt"], job["trigger"]["graceSec"]),
                         ("at", target, consts.GRACE_TIME_S))
        self.assertEqual((job["state"]["gen"], job["state"]["wait"], job["state"]["basis"], job["state"]["fireAt"]),
                         (2, None, None, target))
        self.assertEqual(job["state"]["history"][-1]["event"], "rescheduled")
        self.assertEqual(job["digest"], jobs.full_digest(job))
        self.assertNotEqual(job["commandDigest"], armed["commandDigest"])
        self.assertEqual(self.calls("systemd-run")[-1]["argv"], golden_argv(job_id, 2, target, 5400))
        self.assertEqual(sorted(self.units()), ["%s-%s-g2.timer" % (edition.UNIT_PREFIX, job_id)])
        rc, obj, _err = self.verb("reschedule", job_id, str(target + 60))
        self.assertEqual((rc, obj["unbound"]), (0, False))
        for epoch, code in ((now + 30, "time_past"), (now + consts.HORIZON_S + 100, "time_too_far")):
            rc, obj, _err = self.verb("reschedule", job_id, str(epoch))
            self.assertEqual((rc, obj["code"]), (1, code))
        self.assertEqual(self.stored(job_id)["state"]["gen"], 3)
        draft = self.create()["id"]
        runs = len(self.calls("systemd-run"))
        self.assertEqual(self.verb("reschedule", draft, str(now + 900))[0], 0)
        self.assertEqual((self.stored(draft)["trigger"]["fireAt"], len(self.calls("systemd-run"))), (now + 900, runs))
        self.verb("disarm", job_id)
        self.assertEqual(self.verb("reschedule", job_id, str(now + 900))[1]["code"], "bad_status")

    def test_swap_exchanges(self):
        now = timeutil.now()
        first = self.create_armed(fire_in=3600)
        second = self.create_armed(fire_in=7200)
        t1, t2 = self.stored(first)["state"]["fireAt"], self.stored(second)["state"]["fireAt"]
        rc, obj, _err = self.verb("swap", first, second)
        self.assertEqual(rc, 0, obj)
        self.assertEqual(obj["jobs"], [{"id": first, "fireAt": t2, "unbound": False},
                                       {"id": second, "fireAt": t1, "unbound": False}])
        a, b = self.stored(first), self.stored(second)
        self.assertEqual((a["state"]["fireAt"], b["state"]["fireAt"], a["state"]["gen"], b["state"]["gen"]), (t2, t1, 2, 2))
        self.assertEqual((a["state"]["history"][-1]["event"], b["state"]["history"][-1]["event"]), ("swapped", "swapped"))
        prefix = edition.UNIT_PREFIX
        self.assertEqual(set(self.units()), {"%s-%s-g2.timer" % (prefix, first), "%s-%s-g2.timer" % (prefix, second)})
        self.assertEqual([c["argv"][-1] for c in self.calls("systemd-run")][-2:], ["2", "2"])

        self.fake_config(failRun=["%s-g3" % second])
        rc, obj, _err = self.verb("swap", first, second)
        self.assertEqual((rc, obj["code"], obj["detail"]), (1, "systemd_failed", {"partial": [second]}))
        a, b = self.stored(first), self.stored(second)
        self.assertEqual((a["state"]["fireAt"], a["state"]["gen"], b["state"]["fireAt"]), (t1, 3, t2))
        # The in-process reconcile re-armed the job whose timer failed, under a newer generation.
        self.assertEqual((b["state"]["status"], b["state"]["gen"], b["state"]["unit"]),
                         ("armed", 4, "%s-%s-g4" % (prefix, second)))
        self.assertIn("%s-%s-g4.timer" % (prefix, second), self.units())
        draft = self.create()["id"]
        self.assertEqual(self.verb("swap", first, draft)[1]["code"], "bad_status")

    def test_shift_all_or_nothing(self):
        first = self.create_armed(fire_in=3600)
        second = self.create_armed(fire_in=7200)
        soon = self.create_armed(fire_in=600)
        far = self.create_armed(fire_in=90000)
        draft = self.create()["id"]
        before = {j: copy.deepcopy(self.stored(j)["state"]) for j in (first, second, soon, far)}
        runs = len(self.calls("systemd-run"))
        rc, obj, _err = self.verb("shift", "--by", "-600", first, second, soon)
        self.assertEqual((rc, obj["code"]), (1, "time_past"))
        rc, obj, _err = self.verb("shift", "--by", str(7 * 86400), first, far)
        self.assertEqual((rc, obj["code"]), (1, "time_too_far"))
        rc, obj, _err = self.verb("shift", "--by", "300", first, draft)
        self.assertEqual((rc, obj["code"]), (1, "bad_status"))
        self.assertEqual({j: self.stored(j)["state"] for j in before}, before)
        self.assertEqual(len(self.calls("systemd-run")), runs)
        rc, obj, _err = self.verb("shift", "--by", "300", first, second)
        self.assertEqual(rc, 0, obj)
        self.assertEqual([(e["id"], e["fireAt"]) for e in obj["jobs"]],
                         [(first, before[first]["fireAt"] + 300), (second, before[second]["fireAt"] + 300)])
        self.assertEqual([self.stored(j)["state"]["gen"] for j in (first, second, soon)], [2, 2, 1])
        self.assertEqual(self.stored(first)["state"]["history"][-1]["event"], "shifted")
        ids = [secrets.token_hex(8) for _ in range(51)]
        self.assertEqual(self.verb("shift", "--by", "300", *ids)[1]["code"], "too_many_ids")
        self.assertEqual(self.verb("shift", "--by", "300", first, first)[0], 2)
        self.assertEqual(self.verb("shift", "--by", "0", first)[0], 2)
        self.assertEqual(self.verb("shift", "--by", "604801", first)[0], 2)
        self.assertEqual(self.verb("shift", "--by", "300", "0123456789abcdef")[1]["code"], "not_found")

    def test_job_update_armed_stops_and_resets(self):
        job_id = self.create_armed()
        self.mutate(job_id, lambda j: j["state"].update(transientRetries=2, runSessionId="3f2a0c19-0000-4000-8000-00000000abcd"))
        old = self.stored(job_id)
        rc, obj, _err = self.verb("job-update", job_id,
                                  stdin=json.dumps(self.draft(level="unattended", prompt="A new prompt.", label="Renamed")))
        self.assertEqual((rc, obj.get("wasArmed")), (0, True), obj)
        job = self.stored(job_id)
        state = job["state"]
        self.assertEqual((state["status"], state["gen"], state["transientRetries"], state["runSessionId"], state["unit"]),
                         ("draft", 2, 0, None, None))
        self.assertEqual((job["level"], job["label"], job["createdAt"]), ("unattended", "Renamed", old["createdAt"]))
        self.assertEqual([h["event"] for h in state["history"]][-2:], ["disarmed", "updated"])
        self.assertEqual(read_file(self.state_path("prompts", job_id + ".txt")), "A new prompt.")
        self.assertEqual(job["promptSha256"], hashlib.sha256(b"A new prompt.").hexdigest())
        self.assertEqual(self.units(), {})
        keep = self.verb("job-update", job_id, stdin=json.dumps(dict(self.draft(), prompt=None)))
        self.assertEqual((keep[0], keep[1]["wasArmed"]), (0, False))
        self.assertEqual(self.stored(job_id)["promptSha256"], job["promptSha256"])
        self.mutate(job_id, lambda j: j["state"].update(status="running"))
        self.assertEqual(self.verb("job-update", job_id, stdin=json.dumps(self.draft()))[1]["code"], "bad_status")
        self.mutate(job_id, lambda j: (j["state"].update(status="failed"), j.update(promptAvailable=False)))
        body = {k: v for k, v in self.draft().items() if k != "prompt"}
        self.assertEqual(self.verb("job-update", job_id, stdin=json.dumps(body))[1]["code"], "prompt_missing")

    def test_job_delete_removes_files(self):
        job_id = self.create()["id"]
        sd = self.sd()
        jobs.write_run_record(sd, {"runId": job_id + "-g1", "jobId": job_id, "gen": 1})
        runs = sd.subdir("runs")
        runs.write_atomic(job_id + "-g1.log", b"log")
        runs.write_atomic(job_id + "-g1.last.txt", b"last")
        rc, obj, _err = self.verb("job-delete", job_id)
        self.assertEqual((rc, obj), (0, {"ok": True, "id": job_id}))
        self.assertEqual((os.listdir(self.state_path("runs")), os.listdir(self.state_path("prompts"))), ([], []))
        self.assertEqual(self.store()["jobs"], [])
        armed = self.create_armed()
        self.assertEqual(self.verb("job-delete", armed)[1]["code"], "bad_status")

    def test_generated_label_follows_the_job_and_a_typed_one_stays(self):
        job_id = self.create()["id"]
        other = self.make_dir("other")
        target = {"mode": "new", "sessionId": None, "cwd": other, "allowNonGit": False}
        self.assertEqual(self.verb("job-update", job_id, stdin=json.dumps(self.draft(target=target)))[0], 0)
        self.assertEqual(self.stored(job_id)["label"], "Claude Code in other")
        self.assertEqual(self.verb("job-update", job_id, stdin=json.dumps(self.draft(label="Nightly check")))[0], 0)
        self.assertEqual(self.verb("job-update", job_id, stdin=json.dumps(self.draft(harness="codex")))[0], 0)
        self.assertEqual(self.stored(job_id)["label"], "Nightly check")
        self.assertEqual(jobs.default_label("gemini", "/"), "Gemini CLI job")

    def test_arm_unit_never_passes_a_calendar_time_in_the_past(self):
        now = timeutil.now()
        systemd.arm_unit(secrets.token_hex(8), 1, now - 30, 5400)
        at = [a for a in self.calls("systemd-run")[-1]["argv"] if a.startswith("--on-calendar=@")]
        self.assertEqual(len(at), 1)
        self.assertGreaterEqual(int(at[0].split("@", 1)[1]), now + consts.ARM_MIN_LEAD_S)

    def test_list_has_no_prompt_text(self):
        # No label is given and the canary opens the prompt: a label taken from the prompt would carry it
        # into list output and the notification body (busctl argv). The generated label is metadata only.
        canary = "CANARY-" + secrets.token_hex(12)
        prompt = canary + " check the nightly build.\nThen summarize it."
        created = self.create(prompt=prompt)
        self.assertEqual(created["job"]["label"], "Claude Code in proj")
        for event in notify.TEMPLATES:
            rendered = notify.render(event, created["job"], now=timeutil.now(), fire_at=timeutil.now(), duration_s=60)
            self.assertNotIn(canary, json.dumps(rendered))
        outputs = [json.dumps(created)]
        job_id = created["id"]
        for args in (("arm", job_id, "--digest", created["digest"]), ("list",), ("reconcile",), ("disarm", job_id),
                     ("cancel-all",), ("copy-resume", job_id)):
            rc, obj, err = self.verb(*args)
            outputs += [json.dumps(obj), err]
        for text in outputs:
            self.assertNotIn(canary, text)
        with open(os.path.join(self.fakesys, "calls.jsonl")) as handle:
            self.assertNotIn(canary, handle.read())
        with open(self.state_path("jobs.json")) as handle:
            self.assertNotIn(canary, handle.read())
        self.assertIn(canary, read_file(self.state_path("prompts", job_id + ".txt")))
        rc, obj, _err = self.verb("job-get", job_id)
        self.assertEqual((rc, obj["prompt"]), (0, prompt))


# ================================================================================ reconcile and retention

class ReconcileTests(Sandbox):
    def reconcile(self):
        rc, obj, _err = self.verb("reconcile")
        self.assertEqual(rc, 0, obj)
        return obj

    def unit(self, job_id, gen, kind):
        return "%s-%s-g%d.%s" % (edition.UNIT_PREFIX, job_id, gen, kind)

    def test_reconcile_matrix(self):
        now = timeutil.now()
        self.p.set(trigger, "compute_fire_at", lambda job, at, usage_obj, **kw: {
            "fireAt": at + 3600, "immediate": False, "hint": None, "wait": "reset", "basis": None})
        lost = self.create_armed(fire_in=3600)                  # step 1
        due = self.create_armed(fire_in=3600)                   # step 2, within grace
        late = self.create_armed(fire_in=3600)                  # step 2, past grace
        reset_due = self.create_armed(trigger={"kind": "claude_5h_reset"})    # step 2, reset grace
        dead = self.create_armed()                              # step 3, no service, no record
        alive = self.create_armed()                             # step 3, service still active
        recorded = self.create_armed()                          # step 3, record says re-armed
        finished = self.create_armed()                          # step 3, record says done
        paused = self.create_armed()                            # step 4, within grace
        paused_late = self.create_armed()                       # step 4, past grace
        kept = self.create_armed(fire_in=5000)                  # step 5, current unit kept
        closed = self.create()["id"]                            # step 6, pruned
        self.mutate(due, lambda j: j["state"].update(fireAt=now - 300))
        self.mutate(late, lambda j: j["state"].update(fireAt=now - 2000))
        self.mutate(reset_due, lambda j: j["state"].update(fireAt=now - 7200))
        for job_id in (dead, alive, recorded, finished):
            self.mutate(job_id, lambda j: j["state"].update(status="running"))
        self.mutate(paused, lambda j: j["state"].update(status="paused", reason="kill_switch", fireAt=now - 60))
        self.mutate(paused_late, lambda j: j["state"].update(status="paused", reason="plugin_disabled", fireAt=now - 5000))
        self.mutate(closed, lambda j: (j["state"].update(status="done"), j.update(updatedAt=now - 31 * 86400,
                                                                                    promptAvailable=False)))
        sd = self.sd()
        jobs.write_run_record(sd, {"runId": recorded + "-g1", "jobId": recorded, "gen": 1,
                                   "action": {"status": "armed", "fireAt": now + 900, "reason": None}})
        jobs.write_run_record(sd, {"runId": finished + "-g1", "jobId": finished, "gen": 1,
                                   "action": {"status": "done", "fireAt": None, "reason": None}})
        stale_timer = self.unit(kept, 9, "timer")
        ghost = self.unit(secrets.token_hex(8), 1, "service")
        foreign = {"other-0123456789abcdef-g1.timer", edition.UNIT_PREFIX + "-notes.service"}
        units = {self.unit(kept, 1, "timer"): {"load": "loaded", "active": "active", "sub": "waiting", "next": 1},
                 self.unit(alive, 1, "service"): {"load": "loaded", "active": "active", "sub": "running"},
                 stale_timer: {"load": "loaded", "active": "active", "sub": "waiting", "next": 1},
                 ghost: {"load": "loaded", "active": "failed", "sub": "failed"}}
        for name in foreign:
            units[name] = {"load": "loaded", "active": "active", "sub": "running"}
        self.set_units(units)
        runs_before = len(self.calls("systemd-run"))

        result = self.reconcile()
        self.assertEqual(sorted(result["rearmed"]), sorted([lost, recorded]))
        self.assertEqual(sorted(result["fired"]), sorted([due, reset_due]))
        self.assertEqual(sorted(result["missed"]), sorted([late, paused_late]))
        self.assertEqual(result["interrupted"], [dead])
        self.assertEqual(result["resumed"], [paused])
        self.assertEqual(sorted(result["stopped"]), sorted([stale_timer, ghost]))
        self.assertGreaterEqual(result["pruned"], 1)
        self.assertIn("linger", result)
        self.assertNotIn("systemdError", result)

        def state(job_id):
            return self.stored(job_id)["state"]

        self.assertEqual((state(lost)["status"], state(lost)["gen"], state(lost)["history"][-1]["event"]), ("armed", 2, "rearmed"))
        self.assertIn(self.unit(lost, 2, "timer"), self.units())
        self.assertIn(self.unit(due, 2, "service"), self.units())
        self.assertEqual((state(late)["status"], state(late)["reason"], state(late)["unit"]), ("missed", "late", None))
        self.assertEqual((state(dead)["status"], state(dead)["reason"]), ("interrupted", "interrupted"))
        self.assertEqual((state(alive)["status"], state(alive)["gen"]), ("running", 1))
        self.assertEqual((state(recorded)["status"], state(recorded)["gen"], state(recorded)["fireAt"]), ("armed", 2, now + 900))
        self.assertEqual((state(finished)["status"], self.stored(finished)["promptAvailable"]), ("done", False))
        self.assertEqual((state(paused)["status"], state(paused)["gen"], state(paused)["history"][-1]["event"]),
                         ("armed", 2, "resumed"))
        self.assertEqual(state(paused_late)["status"], "missed")
        self.assertEqual(state(kept)["gen"], 1)
        self.assertIn(self.unit(kept, 1, "timer"), self.units())
        self.assertTrue(foreign <= set(self.units()))
        self.assertNotIn(closed, [j["id"] for j in self.store()["jobs"]])
        self.assertAlmostEqual(self.store()["reconciledAt"], now, delta=30)
        self.assertEqual(sorted(self.notices), sorted([("missed", late), ("missed", paused_late), ("interrupted", dead)]))
        immediate = [c["argv"] for c in self.calls("systemd-run")[runs_before:]
                     if not any(a.startswith("--on-calendar") for a in c["argv"])]
        self.assertEqual(sorted(a[a.index("--job") + 1] for a in immediate), sorted([due, reset_due]))
        for call in self.calls("systemctl"):
            for name in foreign:
                self.assertNotIn(name, call["argv"])

        # Idempotent: nothing left to do.
        runs_after = len(self.calls("systemd-run"))
        again = self.reconcile()
        self.assertEqual([again[k] for k in reconcile.RESULT_LISTS], [[]] * len(reconcile.RESULT_LISTS))
        self.assertEqual(len(self.calls("systemd-run")), runs_after)

    def test_reconcile_leaves_finishing_runner(self):
        # The runner reconciles after its final save while its own service is still active.
        finishing = self.create_armed()
        orphan = self.create_armed()
        for job_id in (finishing, orphan):
            self.mutate(job_id, lambda j: j["state"].update(status="done", unit=None))
        # The orphan changed long ago: no runner of it is on its way out.
        self.mutate(orphan, lambda j: j.update(updatedAt=timeutil.now() - 120))
        jobs.write_run_record(self.sd(), {"runId": finishing + "-g1", "jobId": finishing, "gen": 1,
                                          "action": {"status": "done", "fireAt": None, "reason": None}})
        own = self.unit(finishing, 1, "service")
        stale = self.unit(orphan, 1, "service")
        self.set_units({own: {"load": "loaded", "active": "active", "sub": "running"},
                        stale: {"load": "loaded", "active": "active", "sub": "running"}})
        result = self.reconcile()
        self.assertEqual(result["stopped"], [stale])
        self.assertIn(own, self.units())
        self.assertEqual(self.stored(finishing)["state"]["status"], "done")

    def test_reconcile_leaves_runner_that_stopped_before_the_agent(self):
        # Deferred, paused, missed or refused before the agent: no record, then a notification.
        job_id = self.create_armed()
        self.mutate(job_id, lambda j: (j["state"].update(status="missed", reason="late", unit=None),
                                       j.update(updatedAt=timeutil.now())))
        own = self.unit(job_id, 1, "service")
        self.set_units({own: {"load": "loaded", "active": "active", "sub": "running"}})
        self.assertEqual(self.reconcile()["stopped"], [])
        self.assertIn(own, self.units())
        self.mutate(job_id, lambda j: j.update(updatedAt=timeutil.now() - consts.RUNNER_EXIT_WINDOW_S - 5))
        self.assertEqual(self.reconcile()["stopped"], [own])

    def test_record_of_a_dead_runner_keeps_its_wait_counter_and_lateness(self):
        # The runner wrote its record and died before saving the job: reconcile applies that save.
        now = timeutil.now()
        late = self.create_armed()
        soon = self.create_armed()
        for job_id, fire_at in ((late, now - 2 * 86400), (soon, now - 600)):
            self.mutate(job_id, lambda j: j["state"].update(status="running", unit=None))
            jobs.write_run_record(self.sd(), {"runId": job_id + "-g1", "jobId": job_id, "gen": 1,
                                              "action": {"status": "armed", "fireAt": fire_at, "reason": None,
                                                         "wait": "limit", "counter": "limitRetries"}})
        self.set_units({})
        result = self.reconcile()
        self.assertEqual((result["missed"], result["fired"], result["rearmed"]), ([late], [soon], []))
        state = self.stored(soon)["state"]
        self.assertEqual((state["status"], state["wait"], state["limitRetries"], state["gen"]), ("armed", "limit", 1, 2))
        self.assertEqual((self.stored(late)["state"]["status"], self.stored(late)["state"]["limitRetries"]),
                         ("missed", 1))

    def test_reconcile_pauses_and_resumes(self):
        job_id = self.create_armed()
        self.set_units({})
        self.kill_switch()
        result = self.reconcile()
        self.assertEqual(result["rearmed"], [])
        state = self.stored(job_id)["state"]
        self.assertEqual((state["status"], state["reason"]), ("paused", "kill_switch"))
        self.assertEqual(self.reconcile()["resumed"], [])
        self.kill_switch(False)
        self.write_shell(False)
        self.assertEqual(self.reconcile()["resumed"], [])
        self.write_shell(True)
        self.assertEqual(self.reconcile()["resumed"], [job_id])
        self.assertEqual(self.stored(job_id)["state"]["status"], "armed")
        self.set_units({})
        self.write_shell(False)
        self.reconcile()
        self.assertEqual(self.stored(job_id)["state"]["reason"], "plugin_disabled")

    def test_reconcile_systemd_failure_changes_nothing(self):
        job_id = self.create_armed()
        self.set_units({})
        self.fake_config(failCtl=["list-timers"])
        before = self.stored(job_id)
        result = self.reconcile()
        self.assertEqual(result["systemdError"], "systemd_failed")
        self.assertEqual(self.stored(job_id)["state"], before["state"])
        self.assertIsNone(self.store()["reconciledAt"])

    def test_prune_retention(self):
        now = timeutil.now()
        keep = self.create()["id"]
        missed = self.create()["id"]
        failed = self.create()["id"]
        sd = self.sd()
        runs = sd.subdir("runs")
        for gen in range(1, 26):
            jobs.write_run_record(sd, {"runId": "%s-g%d" % (keep, gen), "jobId": keep, "gen": gen})
            runs.write_atomic("%s-g%d.log" % (keep, gen), b"x")
        orphan = secrets.token_hex(8)
        jobs.write_run_record(sd, {"runId": orphan + "-g1", "jobId": orphan, "gen": 1})
        sd.subdir("prompts").write_atomic(orphan + ".txt", b"orphan prompt")
        old_tmp = ".jobs.json.%s.tmp" % secrets.token_hex(8)
        new_tmp = ".jobs.json.%s.tmp" % secrets.token_hex(8)
        sd.write_atomic(old_tmp, b"")
        sd.write_atomic(new_tmp, b"")
        os.utime(self.state_path(old_tmp), (now - 7200, now - 7200))
        store = jobs.load_store(sd)
        jobs.find_job(store, missed)["state"]["status"] = "missed"
        jobs.find_job(store, missed)["updatedAt"] = now - 25 * 3600
        jobs.find_job(store, failed)["state"]["status"] = "failed"
        removed = jobs.prune(sd, store, now)
        self.assertGreater(removed, 0)
        names = os.listdir(self.state_path("runs"))
        gens = sorted(int(re.match(r"^[0-9a-f]{16}-g([0-9]+)\.json$", n).group(1)) for n in names if n.endswith(".json"))
        self.assertEqual(gens, list(range(6, 26)))
        self.assertEqual(len([n for n in names if n.endswith(".log")]), 20)
        self.assertFalse([n for n in names if n.startswith(orphan)])
        self.assertEqual(sorted(os.listdir(self.state_path("prompts"))), [keep + ".txt"])
        self.assertFalse(jobs.find_job(store, missed)["promptAvailable"])
        self.assertEqual(jobs.find_job(store, missed)["state"]["history"][-1]["event"], "prompt_deleted")
        self.assertFalse(jobs.find_job(store, failed)["promptAvailable"])
        self.assertFalse(os.path.exists(self.state_path(old_tmp)))
        self.assertTrue(os.path.exists(self.state_path(new_tmp)))

        self.p.set(consts, "RUNS_TOTAL", 10)
        jobs.prune(sd, store, now)
        self.assertLessEqual(len(os.listdir(self.state_path("runs"))), 10)
        jobs.find_job(store, failed)["updatedAt"] = now - 31 * 86400
        jobs.prune(sd, store, now)
        self.assertEqual(sorted(j["id"] for j in store["jobs"]), sorted([keep, missed]))


# ================================================================================ v2: Cursor, Pi, paid usage (H6)

def v1_command_digest(job):
    """Independent restatement of the v1 command digest (contract 3.3)."""
    target = job["target"]
    obj = {"harness": job["harness"], "cli": {"link": job["cli"]["link"]},
           "target": {"mode": target["mode"], "sessionId": target.get("sessionId"), "cwd": target["cwd"],
                      "allowNonGit": bool(target.get("allowNonGit"))},
           "level": job["level"], "limits": {k: job["limits"][k] for k in ("maxTurns", "budgetUsd", "runtimeSec")},
           "model": job.get("model"), "trigger": {"kind": job["trigger"]["kind"]}}
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode("utf-8")).hexdigest()


class V2CoreTests(Sandbox):

    def pi_draft(self, **over):
        base = self.draft(harness="pi", provider="openai-codex", model="gpt-5.5")
        base.update(over)
        return base

    def store_path(self, sid):
        return self.home + "/.pi/agent/sessions/--proj--/2026-09-15T10-00-00-000Z_" + sid + ".jsonl"

    def test_edition_v2_harnesses_levels_unavailable(self):
        self.assertEqual(edition.HARNESS_IDS, ("claude", "opencode", "codex", "gemini", "cursor", "pi"))
        for level in edition.LEVELS:
            present, missing = set(level["harness"]), set(level["unavailable"])
            self.assertEqual(present | missing, set(edition.HARNESS_IDS), level["id"])
            self.assertFalse(present & missing, level["id"])
        plan, unattended = edition.level("plan"), edition.level("unattended")
        self.assertEqual(plan["unavailable"], {})
        self.assertEqual(plan["harness"]["cursor"], {
            "caption": "Ask mode. Cursor reads and answers, and anything that would need your approval is denied, "
                       "so no file is edited.",
            "argv": ["--mode", "ask"], "env": {}, "initPermissionMode": None})
        self.assertEqual(plan["harness"]["pi"], {
            "caption": "Pi runs read-only here: read, grep, find and ls. It cannot edit files or run commands.",
            "argv": ["--offline", "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes",
                     "--no-approve", "--tools", "read,grep,find,ls"],
            "env": {"PI_OFFLINE": "1", "PI_TELEMETRY": "0", "PI_SKIP_VERSION_CHECK": "1"}, "initPermissionMode": None})
        self.assertEqual(unattended["unavailable"], {
            "cursor": "Cursor applies file edits headless only with " + "--fo" + "rce, which Auto Pilot never passes.",
            "pi": "Pi has no approval prompts, so only Plan is offered."})
        rc, obj, _err = self.verb("edition")
        self.assertEqual(rc, 0)
        self.assertEqual([lv["unavailable"] for lv in obj["levels"]], [{}, unattended["unavailable"]])
        text = read_file(os.path.join(ROOT, "lib", "Edition.js"))
        match = re.search(r"var HARNESS_IDS = (\[[^\]]*\])", text)
        self.assertEqual(json.loads(match.group(1)), list(edition.HARNESS_IDS))
        self.assertEqual(consts.RESET_TRIGGER, {"claude": "claude_5h_reset", "opencode": "zen_free_reset",
                                                "codex": "codex_window_reset", "gemini": "gemini_daily_reset",
                                                "cursor": None, "pi": "codex_window_reset"})
        self.assertEqual(consts.LEGACY_RESET_KINDS, {"opencode": ("claude_5h_reset",)})
        # Cursor's one-time live check passed on 2026-09-16, so nothing ships gated; the mechanism is
        # still exercised in test_arm_refuses_harness_gated and test_public_job_gated_and_limit_source.
        self.assertEqual(consts.GATED_HARNESSES, ())
        self.assertEqual(consts.CLI_NAMES["cursor"], "cursor-agent")
        self.assertEqual((ORIGINAL_CANDIDATES["cursor"], ORIGINAL_CANDIDATES["pi"]),
                         (["/usr/bin/cursor-agent", "~/.local/bin/agent"],
                          ["~/.local/share/mise/installs/pi/latest/pi/pi"]))
        self.assertEqual(consts.OUTCOMES[-1], "interrupted")
        self.assertEqual(jobs._BASIS_SOURCES, consts.LIMIT_SOURCES + ("clock",))
        self.assertEqual(tuple(consts.REASONS[-len(V2_REASONS):]), tuple(V2_REASONS))

    def test_validate_draft_v2_table(self):
        sid = "3f2a0c19-0000-4000-8000-00000000abcd"
        path = self.store_path(sid)

        def target(draft, **values):
            return dict(draft, target=dict(draft["target"], **values))

        def trig(draft, **values):
            return dict(draft, trigger=dict({"kind": "at", "fireAt": timeutil.now() + 600}, **values))

        def expect(code, field, draft):
            with self.assertRaises(ApError, msg="%s %r" % (code, draft)) as ctx:
                jobs.validate_draft(draft, require_prompt=True)
            self.assertEqual((ctx.exception.code, ctx.exception.field), (code, field), draft)

        table = [
            ("bad_input", "allowPaid", self.draft(allowPaid="yes")),
            ("bad_input", "allowPaid", self.draft(allowPaid=None)),
            ("level_unavailable", "level", self.pi_draft(level="unattended")),
            ("level_unavailable", "level", self.draft(harness="cursor", level="unattended")),
            ("level_unavailable", "level", self.pi_draft(level="unattended", model=None)),
            ("pi_model_required", "provider", self.pi_draft(provider=None)),
            ("pi_model_required", "provider", self.pi_draft(provider="Open AI")),
            ("pi_model_required", "provider", self.pi_draft(provider="x" * 41)),
            ("pi_model_required", "provider", self.pi_draft(provider=None, model=None)),
            ("pi_model_required", "model", self.pi_draft(model=None)),
            ("pi_model_required", "model", self.pi_draft(model="")),
            ("bad_input", "provider", self.draft(provider="openai-codex")),
            ("invalid_model", "model", self.pi_draft(model="-x")),
            ("invalid_model", "model", self.draft(model="m" * 129)),
            ("invalid_model", "model", self.draft(harness="cursor", model="auto")),
            ("invalid_model", "model", target(self.draft(harness="cursor", model="auto"), mode="fork", sessionId=sid,
                                              cwd=None)),
            ("invalid_session", "target.sessionId",
             target(self.pi_draft(), mode="resume", sessionId="not-a-uuid", sessionPath=path, cwd=None)),
            ("invalid_session", "target.sessionPath",
             target(self.pi_draft(), mode="resume", sessionId=sid, sessionPath=None, cwd=None)),
            ("invalid_session", "target.sessionPath",
             target(self.pi_draft(), mode="fork", sessionId=sid, sessionPath="relative.jsonl", cwd=None)),
            ("invalid_session", "target.sessionPath",
             target(self.pi_draft(), mode="resume", sessionId=sid, sessionPath="/" + "a" * 4096, cwd=None)),
            ("invalid_target", "target.sessionPath", target(self.draft(), sessionPath=path)),
            ("invalid_target", "target.sessionPath", target(self.pi_draft(), sessionPath=path)),
            ("fork_unsupported", "target.mode", target(self.draft(harness="cursor"), mode="fork", sessionId=sid,
                                                       cwd=None)),
            ("trigger_unsupported", "trigger.kind", trig(self.draft(harness="cursor"), kind="codex_window_reset")),
            ("trigger_unsupported", "trigger.kind", trig(self.draft(harness="opencode"), kind="claude_5h_reset")),
            ("trigger_unsupported", "trigger.kind",
             trig(self.pi_draft(provider="anthropic", model="claude-opus-5"), kind="codex_window_reset")),
            ("trigger_unsupported", "trigger.kind",
             trig(self.draft(harness="opencode", model="anthropic/claude-opus-5"), kind="zen_free_reset")),
            ("trigger_unsupported", "trigger.kind", trig(self.draft(harness="opencode", model=None),
                                                         kind="zen_free_reset")),
            ("trigger_unsupported", "trigger.kind",
             trig(self.draft(harness="opencode", model="opencode/big-pickle"), kind="go_window_reset")),
            ("pi_slash_prompt", "prompt", self.pi_draft(prompt="/llama hello")),
            ("pi_slash_prompt", "prompt", self.pi_draft(prompt="  \n /help")),
        ]
        for code, field, draft in table:
            expect(code, field, draft)

        ok = jobs.validate_draft(target(self.pi_draft(allowPaid=True, prompt="Read the README."), mode="fork",
                                        sessionId=sid, sessionPath=path, cwd=None), require_prompt=True)
        self.assertEqual((ok["allowPaid"], ok["provider"], ok["model"], ok["target"]["sessionPath"],
                          ok["target"]["cwd"]), (True, "openai-codex", "gpt-5.5", path, None))
        plain = jobs.validate_draft(self.draft(), require_prompt=True)
        self.assertEqual((plain["allowPaid"], plain["provider"], plain["target"]["sessionPath"]), (False, None, None))
        for draft in (self.pi_draft(prompt="Explain /etc/hosts"), self.draft(prompt="/review the diff"),
                      trig(self.draft(harness="opencode", model="opencode/big-pickle"), kind="zen_free_reset"),
                      trig(self.draft(harness="opencode", model="opencode-go/glm-5.2"), kind="go_window_reset"),
                      trig(self.pi_draft(), kind="codex_window_reset"), self.draft(model="auto"),
                      self.draft(harness="cursor", model="claude-opus-4-8[context=1m,effort=high]")):
            jobs.validate_draft(draft, require_prompt=True)
        rc, obj, _err = self.verb("job-create", stdin=json.dumps(self.pi_draft(prompt="/llama x")))
        self.assertEqual((rc, obj["code"], obj["field"], obj["message"]),
                         (1, "pi_slash_prompt", "prompt", V2_MESSAGES["pi_slash_prompt"]))
        self.session_records[("pi", sid)] = {"harness": "pi", "id": sid, "cwd": self.project, "title": "t",
                                             "updatedAtMs": 0, "path": path}
        created = self.create(**target(self.pi_draft(), mode="resume", sessionId=sid, sessionPath=path, cwd=None))
        self.assertEqual(self.lookups[-1], ("pi", sid, path))
        stored = self.stored(created["id"])
        self.assertEqual((stored["provider"], stored["target"]["sessionPath"], stored["target"]["cwd"],
                          stored["state"]["runSessionPath"]), ("openai-codex", path, self.project, None))

    def test_model_grammar_brackets(self):
        good = ("claude-opus-5[1m]", "claude-opus-4-8[context=1m,effort=high,fast=false]", "gpt-5.5",
                "opencode/big-pickle", "openrouter/anthropic/claude-opus-5", "us.anthropic.claude@2026:v1",
                "m" * 128, "a" * 125 + "[x]")
        bad = ("-flag", "a b", "a[b", "a]", "a[]", "a[b][c]", "a[b]c", "[1m]", "m" * 129, "a" * 126 + "[x]", "a\nb",
               "a[b c]", "a;b", 5, None, "")
        for value in good:
            self.assertTrue(jobs.model_ok(value), value)
        for value in bad:
            self.assertFalse(jobs.model_ok(value), value)
        legacy = "x[1m]y"
        self.assertFalse(jobs.model_ok(legacy))
        self.assertTrue(consts.MODEL_RE_V1.fullmatch(legacy))
        job_id = self.create()["id"]

        def set_legacy(job):
            job["model"] = legacy
            jobs.refresh_digests(job)

        self.mutate(job_id, set_legacy)
        stored = self.stored(job_id)
        self.assertEqual(stored["model"], legacy)
        argv = harness.build_command(stored, exec_prefix=[self.links["claude"]], run_dir="/s", gen=1)["argv"]
        self.assertEqual(argv[argv.index("--model") + 1], legacy)
        with self.assertRaises(ApError):
            jobs.validate_draft(self.draft(model=legacy), require_prompt=True)

    def test_legacy_job_upgrade_keeps_digests(self):
        armed = self.create_armed()
        self.create(model="claude-opus-5[1m]")
        self.create(harness="opencode", trigger={"kind": "in", "delaySec": 600})
        path = self.state_path("jobs.json")
        raw = json.loads(read_file(path))
        before = {j["id"]: (j["commandDigest"], j["digest"]) for j in raw["jobs"]}
        for job in raw["jobs"]:
            for key in ("allowPaid", "provider"):
                del job[key]
            del job["target"]["sessionPath"]
            del job["state"]["runSessionPath"]
            self.assertEqual(v1_command_digest(job), before[job["id"]][0])
        with open(path, "w") as handle:
            json.dump(raw, handle)
        store = self.store()
        for job in store["jobs"]:
            self.assertEqual((job["allowPaid"], job["provider"], job["target"]["sessionPath"],
                              job["state"]["runSessionPath"]), (False, None, None, None))
            self.assertEqual((jobs.command_digest(job), jobs.full_digest(job)), before[job["id"]])
        rc, listed, _err = self.verb("list")
        self.assertEqual((rc, len(listed["jobs"])), (0, 3))
        rc, obj, _err = self.verb("run-now", armed, "--digest", before[armed][1])
        self.assertEqual(rc, 0, obj)
        saved = json.loads(read_file(path))
        self.assertTrue(all("allowPaid" in j and "sessionPath" in j["target"] for j in saved["jobs"]))

    def test_digest_binds_allow_paid_provider_session_path(self):
        job = self.stored(self.create()["id"])
        base = (jobs.command_digest(job), jobs.full_digest(job))

        def digests(fn):
            other = copy.deepcopy(job)
            fn(other)
            return jobs.command_digest(other), jobs.full_digest(other)

        self.assertEqual(digests(lambda j: j.update(allowPaid=False)), base)
        on = digests(lambda j: j.update(allowPaid=True))
        self.assertTrue(on[0] != base[0] and on[1] != base[1])
        self.assertNotEqual(digests(lambda j: j.update(provider="openai-codex"))[0], base[0])
        self.assertNotEqual(digests(lambda j: j["target"].update(sessionPath="/s/x.jsonl"))[0], base[0])
        self.assertEqual(digests(lambda j: j["state"].update(runSessionPath="/s/x.jsonl")), base)
        off_preview = self.verb("preview", stdin=json.dumps(self.draft()))[1]["preview"]["commandDigest"]
        on_preview = self.verb("preview", stdin=json.dumps(self.draft(allowPaid=True)))[1]["preview"]["commandDigest"]
        self.assertNotEqual(off_preview, on_preview)
        created = self.create(allowPaid=True)
        self.assertEqual(created["commandDigest"], on_preview)
        self.assertIs(created["job"]["allowPaid"], True)
        pi_job = self.stored(self.create(**self.pi_draft())["id"])
        self.assertRegex(pi_job["target"]["newSessionId"], consts.UUID_RE)
        moved = copy.deepcopy(pi_job)
        moved["target"]["newSessionId"] = "3f2a0c19-0000-4000-8000-00000000ffff"
        self.assertEqual(jobs.command_digest(moved), pi_job["commandDigest"])
        self.assertNotEqual(jobs.full_digest(moved), pi_job["digest"])
        other_provider = copy.deepcopy(pi_job)
        other_provider["provider"] = "xai"
        self.assertNotEqual(jobs.command_digest(other_provider), pi_job["commandDigest"])
        armed = self.create_armed()
        digest = self.stored(armed)["digest"]
        rc, obj, _err = self.verb("job-update", armed, stdin=json.dumps(self.draft(allowPaid=True)))
        self.assertEqual((rc, obj["wasArmed"]), (0, True))
        self.assertEqual(self.verb("arm", armed, "--digest", digest)[1]["code"], "digest_mismatch")

    def test_error_messages_v2_fixed(self):
        for code, message in V2_MESSAGES.items():
            self.assertEqual(errors.MESSAGES[code], message, code)
        for reason, sentence in V2_REASONS.items():
            self.assertEqual(notify.REASON_SENTENCES[reason], sentence, reason)
            self.assertIn(reason, consts.REASONS)
        self.assertNotIn("\u2014", "".join(errors.MESSAGES.values()) + "".join(notify.REASON_SENTENCES.values()))
        err = ApError("paid_pi_key", "allowPaid", detail={"provider": "openrouter", "path": "/home/x"})
        self.assertEqual(errors.error_object(err), {"ok": False, "code": "paid_pi_key",
                                                    "message": V2_MESSAGES["paid_pi_key"], "field": "allowPaid",
                                                    "detail": {"provider": "openrouter"}})
        for bad in ("Open Router", "openrouter; rm -rf", 5, "x" * 41):
            self.assertNotIn("detail", errors.error_object(ApError("paid_pi_key", "allowPaid",
                                                                   detail={"provider": bad})))
        self.assertIn("provider", errors.DETAIL_KEYS)

    def test_arm_refuses_harness_gated(self):
        # Nothing ships gated today, so the mechanism is exercised with cursor put back in the tuple.
        created = self.create(harness="cursor")
        self.p.set(consts, "GATED_HARNESSES", ("cursor",))
        for verb in ("arm", "run-now"):
            rc, obj, _err = self.verb(verb, created["id"], "--digest", created["digest"])
            self.assertEqual((rc, obj["code"], obj["field"], obj["message"]),
                             (1, "harness_gated", "harness", V2_MESSAGES["harness_gated"]), verb)
        self.assertEqual(self.verb("arm", created["id"], "--digest", "f" * 64)[1]["code"], "digest_mismatch")
        self.assertEqual((self.calls("systemd-run"), self.gate_calls), ([], []))
        self.assertEqual(self.stored(created["id"])["state"]["status"], "draft")
        self.p.set(paid, "check_job", REAL_CHECK_JOB)
        rc, obj, _err = self.verb("preview", stdin=json.dumps(self.draft(harness="cursor")))
        self.assertEqual((rc, obj["preview"]["gate"]["code"], obj["preview"]["gate"]["ok"]), (0, "harness_gated", False))
        self.assertTrue(obj["preview"]["display"].startswith("cursor-agent -p --output-format stream-json --mode ask"))
        rc, listed, _err = self.verb("list")
        entry = [j for j in listed["jobs"] if j["id"] == created["id"]][0]
        self.assertEqual((entry["gated"], entry["canRunNow"]), (True, False))
        self.p.set(paid, "check_job", self.fake_gate)
        self.p.set(consts, "GATED_HARNESSES", ())
        rc, obj, _err = self.verb("arm", created["id"], "--digest", created["digest"])
        self.assertEqual(rc, 0, obj)
        self.assertEqual(self.gate_calls[-1]["phase"], "arm")

    def test_arm_gate_codes_raised_with_fields(self):
        created = self.create(harness="codex")
        fields = {"harness_gated": "harness", "cursor_autorun_config": "target.cwd",
                  "cursor_network_config": "target.cwd", "cursor_project_rules": "target.cwd",
                  "cursor_untrusted": "target.cwd", "not_logged_in": "harness", "pi_auth_invalid": "provider",
                  "paid_blocked": "allowPaid", "paid_zen": "allowPaid", "paid_opencode_claude": "allowPaid",
                  "paid_pi_claude": "allowPaid", "paid_pi_key": "allowPaid"}
        self.assertEqual(set(fields), set(cli_core.GATE_CODES))
        for code, field in fields.items():
            detail = {"provider": "openrouter"} if code == "paid_pi_key" else None
            self.gate_over = {"ok": False, "code": code, "detail": detail}
            rc, obj, _err = self.verb("arm", created["id"], "--digest", created["digest"])
            self.assertEqual((rc, obj["code"], obj.get("field"), obj["message"]), (1, code, field, CONTRACT_MESSAGES[code]))
            self.assertEqual(obj.get("detail"), detail)
        pi_job = self.create(**self.pi_draft(provider="openrouter", model="anthropic/claude-opus-5"))
        self.gate_over = {"ok": False, "code": "not_logged_in"}
        self.assertEqual(self.verb("arm", pi_job["id"], "--digest", pi_job["digest"])[1].get("field"), "provider")
        for over in ({"ok": False, "code": None}, RuntimeError("probe bug"), {"ok": True, "code": "made_up"}):
            self.gate_over = over
            self.assertEqual(self.verb("run-now", created["id"], "--digest", created["digest"])[1]["code"], "internal")
        self.assertEqual(self.calls("systemd-run"), [])
        self.assertEqual(self.stored(created["id"])["state"]["status"], "draft")
        self.usage_obj = {"nowMs": 1, "providers": []}

        def gate_while_lock_free(job, **kw):
            with fsio.open_state(create=True).lock(0.5):
                pass
            return dict(GATE_OK, billing=None)

        self.gate_over = gate_while_lock_free
        rc, obj, _err = self.verb("arm", created["id"], "--digest", created["digest"])
        self.assertEqual(rc, 0, obj)
        call = self.gate_calls[-1]
        self.assertEqual((call["phase"], call["exec_prefix"], call["usage"]),
                         ("arm", [os.path.realpath(self.links["codex"])], self.usage_obj))
        self.assertTrue(0 < call["deadline_s"] <= 30 and call["sd"] is not None)

    def test_arm_paid_blocked_codex_off_allowed_on(self):
        self.p.set(paid, "check_job", REAL_CHECK_JOB)
        login = {"kind": "api_key"}
        self.p.set(paid, "codex_login_probe", lambda exec_prefix, deadline_s: login["kind"])
        off = self.create(harness="codex")
        rc, obj, _err = self.verb("arm", off["id"], "--digest", off["digest"])
        self.assertEqual((rc, obj["code"], obj["field"], obj["message"]),
                         (1, "paid_blocked", "allowPaid", V2_MESSAGES["paid_blocked"]))
        rc, obj, _err = self.verb("preview", stdin=json.dumps(self.draft(harness="codex")))
        self.assertEqual((rc, obj["preview"]["gate"]["code"], obj["preview"]["gate"]["ok"]), (0, "paid_blocked", False))
        on = self.create(harness="codex", allowPaid=True)
        rc, obj, _err = self.verb("arm", on["id"], "--digest", on["digest"])
        self.assertEqual(rc, 0, obj)
        login["kind"] = "chatgpt"
        rc, obj, _err = self.verb("arm", off["id"], "--digest", off["digest"])
        self.assertEqual(rc, 0, obj)
        login["kind"] = "not_logged_in"
        other = self.create(harness="codex")
        rc, obj, _err = self.verb("arm", other["id"], "--digest", other["digest"])
        self.assertEqual((rc, obj["code"], obj["field"]), (1, "not_logged_in", "harness"))
        self.assertEqual(len(self.calls("systemd-run")), 2)

    def test_preview_gate_object(self):
        self.gate_over = {"notes": ["subscription_only", "zen_free", 7, "x" * 50], "billing": "zen_free",
                          "provider": "Bad Provider", "resetAtMs": 1789430400000, "defer": {"action": "rearm"},
                          "secret": "/home/x"}
        draft = self.draft(harness="opencode", model="opencode/big-pickle", trigger={"kind": "zen_free_reset"})
        rc, obj, _err = self.verb("preview", stdin=json.dumps(draft))
        self.assertEqual(rc, 0, obj)
        now = timeutil.now()
        self.assertEqual(obj["preview"]["gate"], {"ok": True, "code": None, "detail": None,
                                                  "notes": ["subscription_only", "zen_free"], "billing": "zen_free",
                                                  "provider": None, "resetAtMs": 1789430400000, "pending": False})
        self.assertEqual(obj["preview"]["fireAt"], (now // 86400 + 1) * 86400 + 120)
        self.assertEqual(self.gate_calls[-1]["phase"], "preview")
        self.codex_login = False
        self.gate_over = {"ok": False, "code": "not_logged_in"}
        rc, obj, _err = self.verb("preview", stdin=json.dumps(self.draft(harness="codex")))
        self.assertEqual((rc, obj["preview"]["gate"]["code"], obj["preview"]["gate"]["ok"]), (0, "not_logged_in", False))
        self.gate_over = {"ok": False, "code": "paid_pi_key", "detail": {"provider": "openrouter"},
                          "provider": "openrouter", "notes": ["pi_subscription"]}
        gate = self.verb("preview", stdin=json.dumps(self.pi_draft(provider="openrouter", model="x/y")))[1]["preview"]["gate"]
        self.assertEqual((gate["code"], gate["detail"], gate["provider"]), ("paid_pi_key", {"provider": "openrouter"},
                                                                            "openrouter"))
        self.gate_over = {"billing": None}
        rc, obj, _err = self.verb("preview", stdin=json.dumps(draft))
        self.assertEqual((rc, obj["preview"]["fireAtError"]), (0, "no_reset_data"))
        self.gate_over = RuntimeError("probe bug")
        rc, obj, _err = self.verb("preview", stdin=json.dumps(self.draft()))
        self.assertEqual((rc, obj["preview"]["gate"]), (0, {"ok": False, "code": None, "detail": None, "notes": [],
                                                            "billing": None, "provider": None, "resetAtMs": None,
                                                            "pending": True}))

    def test_preview_no_budget_flag_when_off(self):
        off = self.verb("preview", stdin=json.dumps(self.draft(limits={"budgetUsd": 7.5})))[1]["preview"]
        self.assertNotIn("--max-budget-usd", off["argv"])
        self.assertNotIn("--max-budget-usd", off["display"])
        on = self.verb("preview", stdin=json.dumps(self.draft(limits={"budgetUsd": 7.5}, allowPaid=True)))[1]["preview"]
        index = on["argv"].index("--max-budget-usd")
        self.assertEqual(on["argv"][index + 1], "7.5")
        self.assertIn("--max-budget-usd 7.5", on["display"])
        self.assertNotEqual(off["commandDigest"], on["commandDigest"])
        codex = self.verb("preview", stdin=json.dumps(self.draft(harness="codex", allowPaid=True)))[1]["preview"]
        self.assertNotIn("--max-budget-usd", codex["argv"])

    def test_public_job_status_at_skips_housekeeping(self):
        job = self.stored(self.create()["id"])
        history = job["state"]["history"]
        start = history[-1]["at"] if history else job["updatedAt"]
        self.assertEqual(jobs.public_job(job, timeutil.now())["state"]["statusAt"], start)
        jobs.set_status(job, "interrupted", start + 10, reason="interrupted")
        jobs.add_history(job, "interrupted", start + 10, "interrupted")
        # prune deletes the prompt a day later; a CLI move and a late result follow. None is news.
        jobs.add_history(job, "prompt_deleted", start + 86460)
        jobs.add_history(job, "cli_changed", start + 86470)
        jobs.add_history(job, "late_result", start + 86480)
        public = jobs.public_job(job, timeutil.now())
        self.assertEqual((public["state"]["statusAt"], public["state"]["lastEvent"]["event"]),
                         (start + 10, "late_result"))
        self.assertNotIn("statusAt", job["state"])
        jobs.add_history(job, "resumed", start + 90000)
        self.assertEqual(jobs.public_job(job, timeutil.now())["state"]["statusAt"], start + 90000)
        job["state"]["history"] = [{"at": start + 86460, "event": "prompt_deleted", "detail": None}]
        self.assertEqual(jobs.public_job(job, timeutil.now())["state"]["statusAt"], job["updatedAt"])

    def test_public_job_gated_and_limit_source(self):
        # Nothing ships gated, so cursor goes back into the tuple to cover the gated fields.
        self.p.set(consts, "GATED_HARNESSES", ("cursor",))
        calls = []
        self.p.set(windows, "limit_source_for", lambda job, billing, usage=None: calls.append(
            (job["harness"], billing, usage)) or {"claude": "claude", "opencode": "zen-free",
                                                  "cursor": "Not An Id"}.get(job["harness"]))
        self.p.set(models, "cached_billing", lambda model, now: "zen_free" if model == "opencode/big-pickle" else None)
        claude = self.create()["id"]
        opencode = self.create(harness="opencode", model="opencode/big-pickle")["id"]
        cursor = self.create(harness="cursor")
        self.assertEqual((cursor["job"]["gated"], cursor["job"]["canRunNow"]), (True, False))
        rc, listed, _err = self.verb("list")
        by_id = {j["id"]: j for j in listed["jobs"]}
        self.assertEqual((by_id[claude]["gated"], by_id[claude]["canRunNow"], by_id[claude]["limitSource"]),
                         (False, True, "claude"))
        self.assertEqual(by_id[opencode]["limitSource"], "zen-free")
        self.assertEqual((by_id[cursor["id"]]["gated"], by_id[cursor["id"]]["limitSource"]), (True, None))
        self.assertIn(("opencode", "zen_free", None), calls)
        self.assertIn(("claude", None, None), calls)

        def boom(*_a, **_k):
            raise RuntimeError("binding bug")

        self.p.set(windows, "limit_source_for", boom)
        rc, listed, _err = self.verb("list")
        self.assertEqual((rc, {j["limitSource"] for j in listed["jobs"]}), (0, {None}))

    def test_verbs_v2_registered_deadlines_caps(self):
        self.assertEqual(helper_main.VERBS["timeline"], ("cli_scan", "cmd_timeline", False))
        self.assertEqual(helper_main.VERBS["models"], ("cli_models", "cmd_models", False))
        self.assertEqual({v: consts.VERB_DEADLINE_S[v] for v in ("preview", "arm", "run-now", "usage", "models",
                                                                 "timeline", "sessions")},
                         {"preview": 12, "arm": 40, "run-now": 40, "usage": 3, "models": 25, "timeline": 4,
                          "sessions": 8})
        self.assertEqual({v: consts.OUTPUT_CAP[v] for v in ("usage", "models", "timeline", "sessions")},
                         {"usage": 131072, "models": 262144, "timeline": 262144, "sessions": 921600})
        self.assertEqual(set(consts.VERB_DEADLINE_S) | {"run"}, set(helper_main.VERBS))
        for verb, (module_name, handler, _stdin) in helper_main.VERBS.items():
            module = importlib.import_module("autopilot." + module_name)
            self.assertTrue(callable(getattr(module, handler, None)), verb)

    def test_argv_grammar_cwd_models_timeline(self):
        accepted = [("sessions", []), ("sessions", ["--harness", "pi"]), ("sessions", ["--cwd", self.project]),
                    ("sessions", ["--harness", "cursor", "--cwd", "/home/u/my proj"]),
                    ("models", ["--harness", "opencode"]), ("models", ["--harness", "pi", "--refresh"]),
                    ("timeline", ["--from", "1789430400", "--to", "1789516800"]), ("usage", [])]
        refused = [("sessions", ["--cwd", "relative"]), ("sessions", ["--cwd", "/a\nb"]),
                   ("sessions", ["--cwd", "/" + "a" * 1024]), ("sessions", ["--cwd", "/x", "--harness", "pi"]),
                   ("sessions", ["--cwd", "/x", "--cwd", "/y"]), ("sessions", ["--harness", "pi", "--harness", "pi"]),
                   ("sessions", ["--cwd"]), ("sessions", ["--harness", "vim"]), ("sessions", ["--cwd", "/x\x07"]),
                   ("models", []), ("models", ["--harness"]), ("models", ["--harness", "vim"]),
                   ("models", ["--refresh", "--harness", "pi"]), ("models", ["--harness", "pi", "--refresh", "--refresh"]),
                   ("models", ["--harness", "pi", "--all"]), ("timeline", []), ("timeline", ["--from", "1789430400"]),
                   ("timeline", ["--to", "1789516800", "--from", "1789430400"]),
                   ("timeline", ["--from", "178943040", "--to", "1789516800"]),
                   ("timeline", ["--from", "0999999999", "--to", "1789516800"]),
                   ("timeline", ["--from", "1789430400", "--to", "4102444801"]),
                   ("timeline", ["--from", "-1", "--to", "1"]), ("usage", ["--x"])]
        for verb, args in accepted:
            helper_main.check_argv(verb, args)
        for verb, args in refused:
            with self.assertRaises(helper_main._ArgError, msg=repr((verb, args))):
                helper_main.check_argv(verb, args)
        self.assertEqual(self.verb("models", "--harness", "vim")[:2][0], 2)
        self.assertEqual(self.verb("timeline", "--from", "1", "--to", "2")[1]["code"], "bad_args")

    def test_list_run_records_range_cap(self):
        sd = self.sd()
        self.assertEqual(jobs.list_run_records(sd, since=0, until=4102444800), [])
        job_id = "0123456789abcdef"
        base = 1789400000

        def put(gen, started, ended, mtime):
            jobs.write_run_record(sd, {"schemaVersion": 1, "runId": "%s-g%d" % (job_id, gen), "jobId": job_id,
                                       "gen": gen, "startedAt": started, "endedAt": ended, "outcome": "done"})
            os.utime(self.state_path("runs", "%s-g%d.json" % (job_id, gen)), (mtime, mtime))

        put(1, base, base + 100, base + 100)
        put(2, base + 500, base + 900, base + 900)
        put(3, base + 1500, base + 1600, base + 1600)
        put(4, base + 2500, base + 3000, base + 3000)
        put(5, base + 1900, None, base + 1900)
        put(6, base + 1000, base + 1100, base + 10)          # written long before the range: never read
        with open(self.state_path("runs", job_id + "-g7.json"), "w") as handle:
            handle.write("{broken")
        os.chmod(self.state_path("runs", job_id + "-g7.json"), 0o600)
        got = jobs.list_run_records(sd, since=base + 800, until=base + 2000)
        self.assertEqual([r["gen"] for r in got], [5, 3, 2])
        self.assertEqual([r["gen"] for r in jobs.list_run_records(sd, since=base + 800, until=base + 2000, cap=2)],
                         [5, 3])
        self.assertEqual(jobs.list_run_records(sd, since=base + 800, until=base + 2000, cap=0), [])

    def test_legacy_opencode_claude_reset_loads_refuses_rearm(self):
        created = self.create(harness="opencode")
        now = timeutil.now()

        def legacy(job):
            job["trigger"].update(kind="claude_5h_reset", fireAt=None, graceSec=consts.GRACE_RESET_S)
            job["state"].update(status="armed", gen=1, fireAt=now + 600, wait="reset",
                                unit="%s-%s-g1" % (edition.UNIT_PREFIX, job["id"]),
                                basis={"source": "record", "resetEpoch": now + 480, "fetchedAtMs": None,
                                       "percent": 0.2})
            jobs.refresh_digests(job)

        self.mutate(created["id"], legacy)
        stored = self.stored(created["id"])
        self.assertEqual(stored["trigger"]["kind"], "claude_5h_reset")
        rc, _listed, _err = self.verb("list")
        self.assertEqual(rc, 0)
        exhausted = {"providers": [{"id": "claude", "source": "record", "readable": True, "stale": False,
                                    "updatedAtMs": now * 1000, "windows": [
                                        {"label": "Weekly (7-day)", "kind": "weekly", "percent": 1.0,
                                         "resetsAt": now + 86400, "title": None, "bindable": True, "sliding": False}]}]}
        self.assertEqual(trigger.prefire(stored, now + 600, exhausted)["action"], "fire")
        rc, obj, _err = self.verb("preview", stdin=json.dumps(self.draft(harness="opencode",
                                                                         trigger={"kind": "claude_5h_reset"})))
        self.assertEqual((rc, obj["code"], obj["field"]), (1, "trigger_unsupported", "trigger.kind"))
        self.assertEqual(self.verb("disarm", created["id"])[0], 0)
        rc, obj, _err = self.verb("arm", created["id"], "--digest", self.stored(created["id"])["digest"])
        self.assertEqual((rc, obj["code"], obj["field"]), (1, "trigger_unsupported", "trigger.kind"))
        self.assertEqual(self.stored(created["id"])["state"]["status"], "disarmed")


if __name__ == "__main__":
    unittest.main()
