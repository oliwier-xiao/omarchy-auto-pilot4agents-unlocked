"""Runner-side tests (owner H2): command templates, env allowlist, supervision, classification,
trigger timing, notifications and the `run` verb end to end with a stub agent.

Nothing here touches the real HOME, the real state folder or the real user manager: every test
builds a temporary HOME, points the tool table at paths that do not exist, and replaces the
systemd, reconcile and notification entry points in-process. Agents are tests/stubs/fake-agent.
"""

import copy
import datetime
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import signal
import sys
import tempfile
import threading
import time
import types
import unittest
import uuid
import zoneinfo

TESTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS)
sys.dont_write_bytecode = True
sys.path.insert(0, os.environ.get("AP4A_TEST_BIN") or os.path.join(ROOT, "bin"))

import autopilot  # noqa: E402


def _stand_in(name, fill):
    """Register a stand-in for a helper-core module that is not in the tree yet (parallel build).

    When the real module exists it is imported as usual and the tests patch its functions.
    """
    full = "autopilot." + name
    if importlib.util.find_spec(full) is not None:
        return
    module = types.ModuleType(full)
    fill(module)
    sys.modules[full] = module
    setattr(autopilot, name, module)


def _fill_systemd(module):
    from autopilot import consts as c, edition as e
    from autopilot.errors import ApError as Err

    def unit_name(job_id, gen):
        name = e.UNIT_PREFIX + "-" + job_id + "-g" + str(gen)
        if not c.UNIT_RE.match(name):
            raise Err("internal")
        return name

    def arm_unit(job_id, gen, fire_at, runtime_sec):
        raise Err("systemd_failed")

    module.unit_name = unit_name
    module.arm_unit = arm_unit
    module.stop_units = lambda job_id, gen: {"units": [], "verified": True, "wasRunning": False}
    module.list_units = lambda: {"timers": {}, "services": {}}
    module.stop_all_prefix_units = lambda: {"units": [], "verified": True}
    module.clock_synced = lambda: None
    module.under_systemd = lambda: bool(re.fullmatch(r"[0-9a-f]{32}", os.environ.get("INVOCATION_ID", "")))


def _fill_reconcile(module):
    module.reconcile = lambda sd, now=None: {}
    module.reconcile_locked = lambda sd, store, now: {}


def _fill_settings(module):
    module.DEFAULTS = {"schemaVersion": 1, "defaultHarness": "claude", "defaultLevel": "plan", "resetMarginSec": 120,
                       "eveningTime": "23:00", "morningTime": "07:00", "notify": "all", "motion": "full"}
    module.load = lambda sd: dict(module.DEFAULTS)
    module.validate_partial = lambda obj: dict(obj)
    module.save = lambda sd, merged: None


_stand_in("settings", _fill_settings)
_stand_in("systemd", _fill_systemd)
_stand_in("reconcile", _fill_reconcile)

from autopilot import (agents, bounded, classify, consts, edition, fsio, harness, identity, jobs,  # noqa: E402
                       limits_history, liveness, notify, paid, reconcile, runner, sessions, supervise, systemd,
                       trigger, windows)
from autopilot.errors import ApError  # noqa: E402

ORIGINAL_CANDIDATES = copy.deepcopy(consts.CLI_CANDIDATES)
REAL_APPEND_OBSERVED = limits_history.append_observed
REAL_RUN_AGENT = supervise.run_agent
GATE_OK = {"ok": True, "code": None, "detail": None, "notes": [], "billing": None, "provider": None, "resetAtMs": None,
           "pending": False, "defer": None}

FAKE_AGENT = os.path.join(TESTS, "stubs", "fake-agent")
UID = os.getuid()
CHICAGO = zoneinfo.ZoneInfo("America/Chicago")
DIAG_RE = re.compile(r"^ap4a: (E_NOT_SYSTEMD|STALE|PAUSED|NEEDS_CONFIRM|MISSED|SKIPPED|GAVE_UP|DEFERRED|CLI_REFUSED|"
                     r"CWD_REFUSED|BUSY|SESSION_LOCKED|FIRED|OUTCOME_[A-Z_]+|REARMED|FINAL|LATE_RESULT|E_STATE|"
                     r"E_INTERNAL) job=[0-9a-f]{16} gen=[0-9]+$")
ALLOWED_ENV = {"HOME", "USER", "LOGNAME", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "XDG_CONFIG_HOME",
               "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "LANG", "NO_COLOR", "TERM", "PATH",
               "OPENCODE_PERMISSION", "PI_OFFLINE", "PI_TELEMETRY", "PI_SKIP_VERSION_CHECK"}
V1_HARNESSES = ("claude", "opencode", "codex", "gemini")
CURSOR_UUID = "5b0a3c1e-7d2f-4a8b-9c6d-0e1f2a3b4c5d"
PI_UUID = "01a0a56f-6db2-76b1-a858-8cc1c56c0a2f"


def utc(*args):
    return int(datetime.datetime(*args, tzinfo=datetime.timezone.utc).timestamp())


class Patch:
    """Attribute patches undone in reverse order."""

    def __init__(self):
        self.saved = []

    def set(self, obj, name, value):
        self.saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def item(self, mapping, key, value):
        missing = object()
        self.saved.append((mapping, key, mapping.get(key, missing), missing))
        mapping[key] = value

    def undo(self):
        while self.saved:
            entry = self.saved.pop()
            if len(entry) == 4:
                mapping, key, old, missing = entry
                if old is missing:
                    mapping.pop(key, None)
                else:
                    mapping[key] = old
            else:
                obj, name, old = entry
                setattr(obj, name, old)


class Sandbox(unittest.TestCase):
    """Temporary HOME, runtime dir, tool table and trust rules for one test."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="ap4a-h2-"))
        self.home = os.path.join(self.tmp, "home")
        self.runtime = os.path.join(self.tmp, "run", "user", str(UID))
        os.makedirs(self.home, mode=0o700)
        os.makedirs(self.runtime, mode=0o700)
        os.chmod(self.runtime, 0o700)
        self.saved_env = dict(os.environ)
        for key in list(os.environ):
            if key not in ("PATH", "LANG", "PYTHONDONTWRITEBYTECODE"):
                del os.environ[key]
        os.environ["HOME"] = self.home
        os.environ["USER"] = "tester"
        os.environ["XDG_RUNTIME_DIR"] = self.runtime
        self.p = Patch()
        self.p.set(consts, "XDG_RUNTIME_RE", re.compile("^" + re.escape(self.runtime) + "$"))
        missing = os.path.join(self.tmp, "no-such-tool")
        for key in ("systemd_run", "systemctl", "busctl", "qs", "timedatectl"):
            self.p.item(consts.TOOLS, key, missing + "-" + key)
        self.p.set(consts, "TOOL_OWNER_UIDS", (0, os.getuid()))
        real_trust = fsio.check_trusted_file
        tmp = self.tmp

        def trusted(path, *, executable, check_ancestors=True):
            # Temporary folders live under the sticky, world-writable /tmp, which the real ancestor
            # walk refuses; inside the sandbox only the file itself is checked.
            inside = isinstance(path, str) and path.startswith(tmp + "/")
            return real_trust(path, executable=executable, check_ancestors=check_ancestors and not inside)

        self.p.set(fsio, "check_trusted_file", trusted)
        self.p.set(systemd, "clock_synced", lambda: True)
        self.p.set(consts, "AGENT_TERM_GRACE_S", 1)
        self.agent_log = os.path.join(self.home, "fake-agent.log")
        # Discovery must never reach a real agent CLI (for example /usr/bin/opencode): every candidate
        # lives inside the temporary HOME, node points nowhere until a test places the stub.
        self.p.set(consts, "CLI_CANDIDATES", {
            "claude": ["~/.local/share/mise/installs/claude/latest/claude", "~/.local/bin/claude"],
            "opencode": ["~/.opencode/bin/opencode", "~/.local/bin/opencode"],
            "codex": ["~/.local/share/mise/installs/codex/latest/bin/codex", "~/.local/bin/codex"],
            "gemini": [os.path.join(self.tmp, "sys", "gemini.js")],
            "cursor": ["~/.local/bin/cursor-agent"],
            "pi": ["~/.local/bin/pi"],
        })
        self.p.item(consts.TOOLS, "node", missing + "-node")
        # The paid-usage gate (paid.py) is replaced by a stand-in the test controls: gate_over is merged
        # into an ok gate, or raised when it is an exception, or called when it is a function.
        self.gate_over = {}
        self.gate_calls = []

        def fake_gate(job, **kw):
            self.gate_calls.append(dict(kw, harness=job["harness"]))
            if isinstance(self.gate_over, BaseException):
                raise self.gate_over
            if callable(self.gate_over):
                return self.gate_over(job, **kw)
            return dict(GATE_OK, **self.gate_over)

        self.p.set(paid, "check_job", fake_gate)
        self.observed = []
        self.p.set(limits_history, "append_observed", lambda sd, *args: self.observed.append(args) or True)
        real_run_agent = supervise.run_agent
        real_run_bounded = bounded.run_bounded

        def sandboxed(argv):
            if not argv or not str(argv[0]).startswith(tmp + "/"):
                raise AssertionError("refusing to start a program outside the test sandbox: %r" % (argv[:1],))

        def guarded_run_agent(cmd, prompt, **kw):
            sandboxed(cmd["argv"])
            return real_run_agent(cmd, prompt, **kw)

        def guarded_run_bounded(argv, **kw):
            sandboxed(argv)
            return real_run_bounded(argv, **kw)

        self.p.set(supervise, "run_agent", guarded_run_agent)
        self.p.set(bounded, "run_bounded", guarded_run_bounded)

    def tearDown(self):
        self.p.undo()
        os.environ.clear()
        os.environ.update(self.saved_env)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- helpers ---------------------------------------------------------------------------

    def fake_config(self, **values):
        values.setdefault("log", self.agent_log)
        with open(os.path.join(self.home, ".fake-agent.json"), "w") as handle:
            json.dump(values, handle)

    def place_cli(self, name):
        """Copy the stub to the harness's second discovery candidate (or the gemini bundle)."""
        if name == "gemini":
            node = os.path.join(self.tmp, "sys", "node")
            os.makedirs(os.path.dirname(node), exist_ok=True)
            shutil.copy(FAKE_AGENT, node)
            os.chmod(node, 0o755)
            bundle = os.path.join(self.tmp, "sys", "gemini.js")
            with open(bundle, "w") as handle:
                handle.write("// bundle placeholder\n")
            os.chmod(bundle, 0o644)
            self.p.item(consts.TOOLS, "node", node)
            self.p.set(identity, "NODE_OWNER_UIDS", (0, UID))
            return bundle
        rel = {"claude": ".local/bin/claude", "opencode": ".opencode/bin/opencode", "codex": ".local/bin/codex",
               "cursor": ".local/bin/cursor-agent", "pi": ".local/bin/pi"}[name]
        path = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        shutil.copy(FAKE_AGENT, path)
        os.chmod(path, 0o755)
        return path

    def project(self, name="proj"):
        path = os.path.join(self.home, name)
        os.makedirs(path, exist_ok=True)
        return os.path.realpath(path)

    def agent_entries(self):
        try:
            with open(self.agent_log) as handle:
                return [json.loads(line) for line in handle if line.strip()]
        except FileNotFoundError:
            return []


def session_for(name):
    return "ses_" + uuid.uuid4().hex[:20] if name == "opencode" else str(uuid.uuid4())


def pi_path(sid, home=None, folder="--home-u-proj--"):
    """An in-store Pi session file for sid below the (temporary) HOME."""
    home = home or os.environ["HOME"]
    return home + "/.pi/agent/sessions/" + folder + "/2026-09-15T10-00-00-000Z_" + sid + ".jsonl"


def make_job(name, *, level="plan", mode="resume", cwd="/home/u/proj", link=None, real=None, kind="at",
             fire_at=None, now=None, job_id=None, model=None, allow_non_git=False, run_sid=None,
             session=None, new_session=None, allow_paid=False, provider=None, session_path=None, run_path=None):
    now = now if now is not None else int(time.time())
    fire_at = fire_at if fire_at is not None else now
    job_id = job_id or uuid.uuid4().hex[:16]
    if name == "pi":
        provider = provider or "openai-codex"
        model = model or "gpt-5.5"
    target = {"mode": mode, "sessionId": None, "newSessionId": None, "cwd": cwd, "title": "proj",
              "allowNonGit": allow_non_git, "sessionPath": None}
    if mode in ("resume", "fork"):
        target["sessionId"] = session or session_for(name)
        if name == "pi":
            target["sessionPath"] = session_path or pi_path(target["sessionId"])
    elif name in ("claude", "gemini", "pi"):
        target["newSessionId"] = new_session or str(uuid.uuid4())
    if name == "pi" and run_sid and run_path is None:
        run_path = pi_path(run_sid)
    return {
        "id": job_id, "label": "Nightly check", "createdAt": now - 100, "updatedAt": now - 100,
        "harness": name, "cli": {"link": link or "/usr/bin/" + name, "real": real or link or "/usr/bin/" + name,
                                 "version": None},
        "target": target, "level": level,
        "limits": {"maxTurns": 15, "budgetUsd": 5.0, "runtimeSec": 5400},
        "model": model, "allowPaid": allow_paid, "provider": provider,
        "trigger": {"kind": kind, "fireAt": fire_at if kind == "at" else None, "delaySec": None, "marginSec": 120,
                    "weeklyPolicy": "defer",
                    "graceSec": consts.GRACE_RESET_S if kind in consts.RESET_KINDS else consts.GRACE_TIME_S},
        "promptSha256": "0" * 64, "promptBytes": 1, "promptAvailable": True,
        "commandDigest": "0" * 64, "digest": "0" * 64,
        "state": {"status": "armed", "wait": None, "reason": None, "gen": 1, "fireAt": fire_at,
                  "unit": edition.UNIT_PREFIX + "-" + job_id + "-g1", "armedAt": now - 100, "pluginDir": None,
                  "basis": None, "runSessionId": run_sid, "runSessionPath": run_path if name == "pi" else None,
                  "defers": 0, "limitRetries": 0, "transientRetries": 0,
                  "busyDefers": 0, "unknownRetries": 0, "lastRun": None,
                  "history": [{"at": now - 100, "event": "armed", "detail": None}]},
    }


def expected_argv(job, exec_prefix, run_dir, gen):
    """Independent restatement of contract 3.6 and v2 section 5."""
    name = job["harness"]
    lv = edition.level(job["level"])["harness"][name]["argv"]
    t = job["target"]
    run_sid = job["state"]["runSessionId"]
    mode = "resume" if run_sid else t["mode"]
    s = run_sid or (t["sessionId"] if mode != "new" else t["newSessionId"])
    model = job["model"]
    if name == "cursor":
        argv = exec_prefix + ["-p", "--output-format", "stream-json"] + lv + ["--workspace", t["cwd"]]
        argv += ["--model", model] if model else []
        argv += {"resume": ["--resume", s], "new": []}[mode]
    elif name == "pi":
        argv = exec_prefix + ["--mode", "json"] + lv + ["--provider", job["provider"], "--model", model]
        path = job["state"]["runSessionPath"] or t["sessionPath"]
        argv += {"resume": ["--session", path], "fork": ["--fork", t["sessionPath"]],
                 "new": ["--session-id", t["newSessionId"], "--name", "autopilot-" + job["id"][:8]]}[mode]
    elif name == "claude":
        budget = ("%.2f" % job["limits"]["budgetUsd"]).rstrip("0").rstrip(".")
        argv = exec_prefix + ["-p", "--output-format", "stream-json", "--verbose"] + lv + [
            "--max-turns", str(job["limits"]["maxTurns"])]
        argv += ["--max-budget-usd", budget] if job["allowPaid"] else []
        argv += ["--model", model] if model else []
        argv += {"resume": ["--resume", s], "fork": ["--resume", t["sessionId"], "--fork-session"],
                 "new": ["--session-id", t["newSessionId"], "--name", "autopilot-" + job["id"][:8]]}[mode]
    elif name == "opencode":
        argv = exec_prefix + ["run", "--dir", t["cwd"], "--format", "json"] + lv
        argv += ["-m", model] if model else []
        argv += {"resume": ["-s", s], "fork": ["-s", t["sessionId"], "--fork"],
                 "new": ["--title", "autopilot-" + job["id"][:8]]}[mode]
    elif name == "codex":
        argv = exec_prefix + ["exec", "-C", t["cwd"]] + lv + [
            "--json", "--color", "never", "-o", run_dir + "/" + job["id"] + "-g" + str(gen) + ".last.txt"]
        argv += ["--skip-git-repo-check"] if t["allowNonGit"] else []
        argv += ["-m", model] if model else []
        argv += {"resume": ["resume", s, "-"], "fork": ["fork", t["sessionId"], "-"], "new": ["-"]}[mode]
    else:
        argv = exec_prefix + ["-p", "", "-o", "json"] + lv
        argv += ["-m", model] if model else []
        argv += {"resume": ["--resume", s], "new": ["--session-id", t["newSessionId"]]}[mode]
    return argv


# ------------------------------------------------------------------------------------------------ harness

class HarnessTests(Sandbox):

    def test_build_command_golden(self):
        literal = make_job("claude", level="plan", mode="resume", job_id="0123456789abcdef",
                           session="3f2a0c19-0000-4000-8000-000000000001")
        cmd = harness.build_command(literal, exec_prefix=["/opt/claude"], run_dir="/s/runs", gen=3)
        self.assertEqual(cmd["argv"], ["/opt/claude", "-p", "--output-format", "stream-json", "--verbose",
                                       "--permission-mode", "plan", "--permission-prompts", "none",
                                       "--max-turns", "15",
                                       "--resume", "3f2a0c19-0000-4000-8000-000000000001"])
        literal = make_job("codex", level="unattended", mode="new", job_id="0123456789abcdef", allow_non_git=True,
                           model="gpt-5.5")
        cmd = harness.build_command(literal, exec_prefix=["/opt/codex"], run_dir="/s/runs", gen=2)
        self.assertEqual(cmd["argv"], ["/opt/codex", "exec", "-C", "/home/u/proj", "-s", "workspace-write", "--json",
                                       "--color", "never", "-o", "/s/runs/0123456789abcdef-g2.last.txt",
                                       "--skip-git-repo-check", "-m", "gpt-5.5", "-"])
        literal = make_job("gemini", level="unattended", mode="new", job_id="0123456789abcdef",
                           new_session="11111111-2222-4333-8444-555555555555")
        cmd = harness.build_command(literal, exec_prefix=["/usr/bin/node", "/b/gemini.js"], run_dir="/s", gen=1)
        self.assertEqual(cmd["argv"], ["/usr/bin/node", "/b/gemini.js", "-p", "", "-o", "json", "--approval-mode",
                                       "default", "--session-id", "11111111-2222-4333-8444-555555555555"])
        literal = make_job("opencode", level="plan", mode="fork", job_id="0123456789abcdef",
                           session="ses_abcdefgh12345678", model="anthropic/claude-opus-5")
        cmd = harness.build_command(literal, exec_prefix=["/usr/bin/opencode"], run_dir="/s", gen=1)
        self.assertEqual(cmd["argv"], ["/usr/bin/opencode", "run", "--dir", "/home/u/proj", "--format", "json",
                                       "--pure", "--agent", "plan", "-m", "anthropic/claude-opus-5",
                                       "-s", "ses_abcdefgh12345678", "--fork"])

        count = 0
        for name in consts.HARNESSES:
            prefix = ["/usr/bin/node", "/b/gemini.js"] if name == "gemini" else ["/opt/" + name]
            for level in edition.LEVEL_IDS:
                if name not in edition.level(level)["harness"]:
                    with self.assertRaises(ApError) as ctx:
                        harness.build_command(make_job(name, level=level, mode="new"), exec_prefix=prefix,
                                              run_dir="/s/runs", gen=4)
                    self.assertEqual(ctx.exception.code, "level_unavailable")
                    continue
                for mode in ("resume", "fork", "new", "retry"):
                    if not consts.CAN_FORK[name] and mode == "fork":
                        job = make_job(name, level=level, mode="fork")
                        with self.assertRaises(ApError) as ctx:
                            harness.build_command(job, exec_prefix=prefix, run_dir="/s/runs", gen=4)
                        self.assertEqual(ctx.exception.code, "fork_unsupported")
                        continue
                    for model in (None, "m-1"):
                        if mode == "retry":
                            job = make_job(name, level=level, mode="new", model=model, run_sid=session_for(name))
                        else:
                            job = make_job(name, level=level, mode=mode, model=model)
                        cmd = harness.build_command(job, exec_prefix=prefix, run_dir="/s/runs", gen=4)
                        self.assertEqual(cmd["argv"], expected_argv(job, prefix, "/s/runs", 4), (name, level, mode))
                        self.assertEqual(cmd["cwd"], "/home/u/proj")
                        count += 1
        # 8 commands (4 modes x 2 models) per agent and level, 6 for Gemini and Cursor, which cannot fork.
        # Plan offers all six agents, Unattended Claude, OpenCode, Codex and Gemini, Auto every agent but
        # Cursor, Full access all six.
        self.assertEqual(count, (4 * 8 + 2 * 6) + (3 * 8 + 6) + (4 * 8 + 6) + (4 * 8 + 2 * 6))

    def test_build_command_refuses_bad_ids(self):
        job = make_job("claude", mode="resume", session="not-a-uuid")
        with self.assertRaises(ApError) as ctx:
            harness.build_command(job, exec_prefix=["/opt/claude"], run_dir="/s", gen=1)
        self.assertEqual(ctx.exception.code, "invalid_session")
        job = make_job("claude", level="everything")
        with self.assertRaises(ApError) as ctx:
            harness.build_command(job, exec_prefix=["/opt/claude"], run_dir="/s", gen=1)
        self.assertEqual(ctx.exception.code, "invalid_level")
        job = make_job("claude", model="-bad")
        with self.assertRaises(ApError) as ctx:
            harness.build_command(job, exec_prefix=["/opt/claude"], run_dir="/s", gen=1)
        self.assertEqual(ctx.exception.code, "invalid_model")

    def test_level_slot_matches_levels_table(self):
        for level in edition.LEVELS:
            for name in level["harness"]:
                mode = "new" if name in ("gemini", "pi") else "resume"
                job = make_job(name, level=level["id"], mode=mode)
                cmd = harness.build_command(job, exec_prefix=["/opt/x"], run_dir="/s", gen=1)
                start, end = cmd["levelSlot"]
                self.assertEqual(cmd["argv"][start:end], level["harness"][name]["argv"])
                for key, value in level["harness"][name]["env"].items():
                    self.assertEqual(cmd["env"][key], value)
                if name != "opencode":
                    self.assertNotIn("OPENCODE_PERMISSION", cmd["env"])
                else:
                    values = set(json.loads(cmd["env"]["OPENCODE_PERMISSION"]).values())
                    # Only the unlocked levels may allow anything outright.
                    if level["id"] in ("auto", "full"):
                        self.assertTrue(values <= {"deny", "ask", "al" + "low"}, values)
                    else:
                        self.assertTrue(values <= {"deny", "ask"}, values)
        self.assertEqual(edition.LEVEL_IDS, ("plan", "unattended", "auto", "full"))

    def test_agent_env_allowlist(self):
        os.environ.update({"ANTHROPIC_API_KEY": "k1", "OPENAI_API_KEY": "k2", "GEMINI_API_KEY": "k3",
                           "CLAUDE_CODE_OAUTH_TOKEN": "k4", "GOOGLE_API_KEY": "k5", "CODEX_HOME": "/x",
                           "WAYLAND_DISPLAY": "wayland-1", "DISPLAY": ":0", "LOGNAME": "tester",
                           "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus",
                           "XDG_CONFIG_HOME": "relative/config", "XDG_DATA_HOME": self.home + "/.local/share",
                           "LANG": "en_US.UTF-8; rm", "TERM": "xterm-256color", "NO_COLOR": "0",
                           "HYPRLAND_INSTANCE_SIGNATURE": "abc"})
        for name in V1_HARNESSES:
            for level in edition.LEVEL_IDS:
                env = harness.agent_env(name, level)
                self.assertTrue(set(env) <= ALLOWED_ENV, set(env) - ALLOWED_ENV)
                for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "WAYLAND_DISPLAY", "DISPLAY",
                            "CLAUDE_CODE_OAUTH_TOKEN", "GOOGLE_API_KEY", "CODEX_HOME", "XDG_CONFIG_HOME"):
                    self.assertNotIn(key, env)
                self.assertEqual(env["PATH"], "/usr/bin:/bin:" + self.home + "/.local/bin")
                self.assertEqual(env["LANG"], "C.UTF-8")
                self.assertEqual(env["TERM"], "dumb")
                self.assertEqual(env["NO_COLOR"], "1")
                self.assertEqual(env["HOME"], self.home)
                self.assertEqual(env["XDG_RUNTIME_DIR"], self.runtime)
                self.assertEqual(env["XDG_DATA_HOME"], self.home + "/.local/share")
                self.assertEqual("OPENCODE_PERMISSION" in env, name == "opencode")
        os.chmod(self.runtime, 0o755)
        self.assertNotIn("XDG_RUNTIME_DIR", harness.agent_env("claude", "plan"))
        with self.assertRaises(ApError):
            harness.agent_env("claude", "everything")

    def test_display_and_resume_text(self):
        job = make_job("claude", mode="new", job_id="0123456789abcdef", cwd="/home/u/my proj")
        cmd = harness.build_command(job, exec_prefix=["/opt/claude"], run_dir="/s", gen=1)
        text = harness.display_argv("claude", cmd["argv"], 1)
        self.assertTrue(text.startswith("claude -p --output-format stream-json --verbose --permission-mode plan"))
        self.assertTrue(text.endswith(" <stdin>"))
        gem = make_job("gemini", mode="new")
        cmd = harness.build_command(gem, exec_prefix=["/usr/bin/node", "/b/gemini.js"], run_dir="/s", gen=1)
        self.assertTrue(harness.display_argv("gemini", cmd["argv"], 2).startswith('gemini -p "" -o json'))
        oc = make_job("opencode", mode="new", cwd="/home/u/my proj")
        cmd = harness.build_command(oc, exec_prefix=["/usr/bin/opencode"], run_dir="/s", gen=1)
        self.assertIn("--dir '/home/u/my proj'", harness.display_argv("opencode", cmd["argv"], 1))
        job["state"]["runSessionId"] = "3f2a0c19-0000-4000-8000-000000000001"
        self.assertEqual(harness.resume_display(job),
                         "cd '/home/u/my proj' && claude --resume 3f2a0c19-0000-4000-8000-000000000001")
        fresh = make_job("codex", mode="new", cwd="/home/u/it's")
        with self.assertRaises(ApError) as ctx:
            harness.resume_display(fresh)
        self.assertEqual(ctx.exception.code, "bad_status")
        fresh["state"]["runSessionId"] = "3f2a0c19-0000-4000-8000-000000000002"
        self.assertEqual(harness.resume_display(fresh),
                         "cd '/home/u/it'\"'\"'s' && codex resume 3f2a0c19-0000-4000-8000-000000000002")

    def test_preview_command_shape(self):
        link = self.place_cli("claude")
        cwd = self.project()
        job = make_job("claude", mode="new", cwd=cwd, link=link, real=link)
        preview = harness.preview_command(job)
        self.assertEqual(preview["binary"], link)
        self.assertEqual(preview["argv"][0], link)
        self.assertTrue(preview["display"].endswith("<stdin>"))
        self.assertEqual(preview["levelCaption"], edition.level("plan")["harness"]["claude"]["caption"])
        self.assertRegex(preview["commandDigest"], r"^[0-9a-f]{64}$")
        self.assertEqual(preview["env"], {})
        codex_link = self.place_cli("codex")
        cjob = make_job("codex", mode="new", cwd=cwd, link=codex_link)
        self.assertEqual(harness.preview_command(cjob)["warnings"], ["non_git_dir"])
        self.assertIn("<runs>/", " ".join(harness.preview_command(cjob)["argv"]))
        os.makedirs(os.path.join(cwd, ".git"))
        self.assertEqual(harness.preview_command(cjob)["warnings"], [])
        # A draft that is not stored yet (normalized by jobs.validate_draft): no id, no newSessionId.
        limits = {"maxTurns": 30, "budgetUsd": 5.0, "runtimeSec": 5400}
        draft_job = {"label": None, "harness": "claude",
                     "target": {"mode": "new", "sessionId": None, "cwd": cwd, "allowNonGit": False},
                     "level": "unattended", "limits": limits, "model": None,
                     "trigger": {"kind": "now", "fireAt": None, "delaySec": None, "marginSec": None,
                                 "weeklyPolicy": "defer"}, "prompt": None, "expectCommandDigest": None}
        preview = harness.preview_command(draft_job)
        self.assertTrue(preview["display"].endswith(
            "--session-id <new-session-id> --name autopilot-<job> <stdin>"), preview["display"])
        stored = make_job("claude", level="unattended", mode="new", cwd=cwd, link=link, kind="now")
        stored["limits"] = dict(limits)
        self.assertEqual(preview["commandDigest"], jobs.command_digest(stored))
        with self.assertRaises(ApError):
            harness.build_command(draft_job, exec_prefix=[link], run_dir="/s", gen=1)


# ------------------------------------------------------------------------------------------------ identity

class IdentityTests(Sandbox):

    def test_discovery_and_fire_resolution(self):
        self.assertEqual(identity.discover_cli("claude")["reason"], "not_found")
        versions = os.path.join(self.home, ".local/share/mise/installs/claude")
        os.makedirs(os.path.join(versions, "2.0.0"))
        os.makedirs(os.path.join(versions, "2.1.0"))
        for version in ("2.0.0", "2.1.0"):
            shutil.copy(FAKE_AGENT, os.path.join(versions, version, "claude"))
            os.chmod(os.path.join(versions, version, "claude"), 0o755)
        os.symlink("2.0.0", os.path.join(versions, "latest"))
        found = identity.discover_cli("claude")
        self.assertTrue(found["ok"], found)
        self.assertEqual(found["link"], os.path.join(versions, "latest", "claude"))
        self.assertEqual(found["real"], os.path.join(versions, "2.0.0", "claude"))
        job = make_job("claude", link=found["link"], real=found["real"])
        fire = identity.resolve_for_fire(job)
        self.assertFalse(fire["changed"])
        os.remove(os.path.join(versions, "2.0.0", "claude"))
        os.remove(os.path.join(versions, "latest"))
        os.symlink("2.1.0", os.path.join(versions, "latest"))
        fire = identity.resolve_for_fire(job)
        self.assertTrue(fire["changed"])
        self.assertEqual(fire["to"], os.path.join(versions, "2.1.0", "claude"))
        self.assertEqual(fire["exec"], [os.path.join(versions, "2.1.0", "claude")])
        os.chmod(os.path.join(versions, "2.1.0", "claude"), 0o777)
        with self.assertRaises(ApError) as ctx:
            identity.resolve_for_fire(job)
        self.assertEqual(ctx.exception.code, "cli_untrusted")
        os.remove(os.path.join(versions, "latest"))
        with self.assertRaises(ApError) as ctx:
            identity.resolve_for_fire(job)
        self.assertEqual(ctx.exception.code, "cli_missing")

    def test_shim_refused(self):
        shims = os.path.join(self.home, consts.MISE_SHIMS_REL)
        os.makedirs(shims)
        shutil.copy(FAKE_AGENT, os.path.join(shims, "codex"))
        os.makedirs(os.path.join(self.home, ".local/bin"))
        os.symlink(os.path.join(shims, "codex"), os.path.join(self.home, ".local/bin/codex"))
        found = identity.discover_cli("codex")
        self.assertFalse(found["ok"])
        self.assertEqual(found["reason"], "shim")

    def test_gemini_exec_through_node(self):
        bundle = self.place_cli("gemini")
        found = identity.discover_cli("gemini")
        self.assertTrue(found["ok"], found)
        self.assertEqual(found["exec"], [consts.TOOLS["node"], bundle])
        self.p.set(identity, "NODE_OWNER_UIDS", (0,))
        self.assertEqual(identity.discover_cli("gemini")["reason"], "untrusted")


class LivenessTests(Sandbox):

    def test_liveness_rules(self):
        now = int(time.time())
        job = make_job("opencode", mode="resume", now=now)
        found = {"harness": "opencode", "id": job["target"]["sessionId"], "cwd": "/x", "title": "t",
                 "updatedAtMs": (now - 30) * 1000}
        self.p.set(sessions, "lookup_session", lambda _h, _sid: dict(found))
        self.assertIs(liveness.session_busy(job, ["/opt/opencode"], now), True)
        found["updatedAtMs"] = (now - 600) * 1000
        self.assertIs(liveness.session_busy(job, ["/opt/opencode"], now), False)
        # the job's own previous run wrote the session last: not busy
        found["updatedAtMs"] = (now - 30) * 1000
        job["state"]["lastRun"] = {"runId": job["id"] + "-g1", "startedAt": now - 200, "endedAt": now - 32,
                                   "outcome": "transient", "exit": 1, "signal": None, "bytesDropped": 0}
        self.assertIs(liveness.session_busy(job, ["/opt/opencode"], now), False)
        self.p.set(sessions, "lookup_session", lambda _h, _sid: None)
        self.assertIsNone(liveness.session_busy(job, ["/opt/opencode"], now))
        self.assertIs(liveness.session_busy(make_job("codex", mode="new"), ["/opt/codex"], now), False)
        self.assertIs(liveness.session_busy(make_job("claude", mode="fork"), ["/opt/claude"], now), False)
        retry = make_job("codex", mode="new", run_sid=str(uuid.uuid4()))
        self.assertIsNone(liveness.session_busy(retry, ["/opt/codex"], now))


# ------------------------------------------------------------------------------------------------ supervise

class SuperviseBase(Sandbox):

    def run_stub(self, name, mode, *, prompt=b"hello", deadline=30, stop_event=None, level="plan", allow_paid=False,
                 model=None, first_line=None, on_line=None, **config):
        self.fake_config(mode=mode, **config)
        link = self.place_cli(name)
        found = identity.discover_cli(name)
        self.assertTrue(found["ok"], found)
        self.assertTrue(all(part.startswith(self.tmp + "/") for part in found["exec"]), found)
        cwd = self.project()
        job = make_job(name, level=level, mode="new", cwd=cwd, link=link, allow_paid=allow_paid, model=model)
        runs = os.path.join(self.tmp, "runs")
        os.makedirs(runs, exist_ok=True)
        cmd = harness.build_command(job, exec_prefix=found["exec"], run_dir=runs, gen=1)
        expected = edition.level(level)["harness"][name]["initPermissionMode"]
        state = classify.new_stream_state(name, expected, allow_paid=allow_paid, provider=job["provider"],
                                          level_id=level)
        state["model"] = job["model"]
        log_path = os.path.join(runs, "run.log")
        if os.path.exists(log_path):
            os.remove(log_path)
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        started = time.monotonic()
        try:
            run = supervise.run_agent(cmd, prompt, deadline_s=deadline, log_fd=fd,
                                      on_stdout_line=on_line or (lambda line: classify.feed_line(state, line)),
                                      stop_event=stop_event, first_line_deadline_s=first_line)
        finally:
            os.close(fd)
        with open(log_path, "rb") as handle:
            log = handle.read()
        return {"run": run, "state": state, "elapsed": time.monotonic() - started, "log": log, "cmd": cmd,
                "job": job}


class SuperviseTests(SuperviseBase):

    def test_prompt_only_on_stdin(self):
        canary = ("CANARY-" + uuid.uuid4().hex + "\nsecond line ").encode() * 400
        for name in consts.HARNESSES:
            os.path.exists(self.agent_log) and os.remove(self.agent_log)
            out = self.run_stub(name, "done", prompt=canary)
            entry = [e for e in self.agent_entries() if e["stdinBytes"]][-1]
            self.assertEqual(entry["stdinSha256"], hashlib.sha256(canary).hexdigest(), name)
            self.assertEqual(entry["stdinBytes"], len(canary))
            flat = json.dumps({"argv": entry["argv"], "env": entry["env"]})
            self.assertNotIn("CANARY-", flat)
            self.assertNotIn(b"CANARY-", out["log"])
            self.assertTrue(set(entry["env"]) <= ALLOWED_ENV, name)
            self.assertEqual(out["run"]["rc"], 0)
            result = classify.classify(out["state"], out["run"], level_id="plan")
            self.assertEqual(result["outcome"], "done", (name, result))

    def test_stub_outcome_matrix(self):
        """Every recorded event shape the stub emits ends in the expected outcome."""
        resets = int(time.time()) + 5400
        cases = (
            ("claude", "done", "plan", "done"), ("claude", "limit_event", "plan", "limit"),
            ("claude", "limit_banner", "plan", "limit"), ("claude", "auth", "plan", "auth"),
            ("claude", "not_found", "plan", "not_found"), ("claude", "max_turns", "unattended", "max_turns"),
            ("claude", "transient", "plan", "transient"),
            ("claude", "wrong_init_mode", "unattended", "boundary_mismatch"),
            ("codex", "done", "plan", "done"), ("codex", "not_found", "plan", "not_found"),
            ("codex", "auth", "plan", "auth"), ("codex", "limit_banner", "unattended", "limit"),
            ("codex", "transient", "plan", "transient"), ("codex", "codex_untrusted", "plan", "untrusted"),
            ("opencode", "done", "unattended", "done"), ("opencode", "not_found", "plan", "not_found"),
            ("opencode", "auth", "plan", "auth"), ("opencode", "limit_event", "plan", "limit"),
            ("opencode", "opencode_tool_violation", "plan", "boundary_mismatch"),
            ("opencode", "opencode_tool_violation", "unattended", "done"),
            ("gemini", "done", "plan", "done"), ("gemini", "not_found", "plan", "not_found"),
            ("gemini", "auth", "plan", "auth"), ("gemini", "limit_event", "plan", "limit"),
            ("gemini", "transient", "plan", "transient"), ("gemini", "max_turns", "plan", "max_turns"),
            ("gemini", "gemini_rc55", "unattended", "untrusted"),
        )
        for name, mode, level, outcome in cases:
            out = self.run_stub(name, mode, level=level, resetsAt=resets)
            result = classify.classify(out["state"], out["run"], level_id=level)
            self.assertEqual(result["outcome"], outcome, (name, mode, level, result))
            self.assertLess(out["elapsed"], 10, (name, mode))
            if (name, mode) == ("claude", "limit_event"):
                self.assertEqual(result["limit"], {"kind": "session", "resetEpoch": resets, "source": "event",
                                                   "isUsingOverage": False, "rearm": True})
                self.assertEqual(result["sessionId"], out["job"]["target"]["newSessionId"])
            if (name, mode) == ("claude", "limit_banner"):
                self.assertEqual((result["limit"]["kind"], result["limit"]["source"]), ("session", "banner"))
            if (name, mode) == ("codex", "limit_banner"):
                self.assertEqual(result["limit"]["source"], "banner")
                self.assertIsInstance(result["limit"]["resetEpoch"], int)
            if (name, mode) == ("gemini", "limit_event"):
                self.assertEqual(result["limit"]["kind"], "daily")
            if (name, mode) == ("codex", "done"):
                self.assertRegex(result["sessionId"], consts.UUID_RE)
            if (name, mode) == ("opencode", "done"):
                self.assertRegex(result["sessionId"], consts.OPENCODE_ID_RE)

    def test_init_permission_mismatch_kill(self):
        out = self.run_stub("claude", "wrong_init_mode")
        self.assertEqual(out["run"]["killedBy"], "boundary")
        self.assertLess(out["elapsed"], 10)
        result = classify.classify(out["state"], out["run"], level_id="plan")
        self.assertEqual(result["outcome"], "boundary_mismatch")
        self.assertEqual(out["state"]["initMode"], "default")

    def test_output_flood_capped(self):
        out = self.run_stub("claude", "flood")
        run = out["run"]
        self.assertGreater(run["stdoutBytes"], 3 * 1024 * 1024)
        self.assertLessEqual(len(out["log"]), consts.RUN_LOG_MAX)
        self.assertGreater(run["bytesDropped"], 0)
        self.assertNotIn(b"\x1b", out["log"])
        self.assertLessEqual(len(run["stderrTail"]), consts.STDERR_CAP)
        self.assertEqual(classify.classify(out["state"], run, level_id="plan")["outcome"], "done")

    def test_term_ignoring_descendant_killed(self):
        out = self.run_stub("claude", "term_ignoring_child", deadline=2)
        run = out["run"]
        self.assertTrue(run["timedOut"])
        self.assertEqual(run["killedBy"], "deadline")
        self.assertLess(out["elapsed"], 12)
        child = [e for e in self.agent_entries() if "childPid" in e][-1]["childPid"]
        for _ in range(50):
            if not os.path.exists("/proc/%d" % child):
                break
            with open("/proc/%d/stat" % child) as handle:
                if handle.read().split(") ")[-1].startswith("Z"):
                    break
            time.sleep(0.1)
        else:
            self.fail("TERM-ignoring descendant survived")
        self.assertEqual(classify.classify(out["state"], run, level_id="plan")["outcome"], "timeout")

    def test_deadline_timeout(self):
        out = self.run_stub("opencode", "sleep", deadline=1, level="unattended")
        run = out["run"]
        self.assertTrue(run["timedOut"])
        self.assertEqual(run["signal"], signal.SIGTERM)
        result = classify.classify(out["state"], run, level_id="unattended")
        self.assertEqual(result["outcome"], "transient")
        self.assertEqual(result["detail"], "silent_timeout")
        out = self.run_stub("codex", "sleep", deadline=1)
        self.assertEqual(classify.classify(out["state"], out["run"], level_id="plan")["outcome"], "timeout")

    def test_sigterm_forwarding(self):
        event = threading.Event()
        previous = signal.signal(signal.SIGTERM, lambda _s, _f: event.set())
        timer = threading.Timer(0.7, lambda: os.kill(os.getpid(), signal.SIGTERM))
        timer.start()
        try:
            out = self.run_stub("claude", "sleep", stop_event=event)
        finally:
            timer.cancel()
            signal.signal(signal.SIGTERM, previous)
        self.assertEqual(out["run"]["killedBy"], "sigterm")
        self.assertLess(out["elapsed"], 10)
        result = classify.classify(out["state"], out["run"], level_id="plan")
        self.assertEqual((result["outcome"], result["detail"]), ("timeout", "sigterm"))
        job = make_job("claude")
        self.assertEqual(trigger.postrun(job, result, int(time.time()), None)["status"], "interrupted")


# ------------------------------------------------------------------------------------------------ classify

def feed(name, lines, expected=None, **kwargs):
    state = classify.new_stream_state(name, expected, **kwargs)
    verdict = None
    for line in lines:
        raw = line if isinstance(line, bytes) else json.dumps(line).encode()
        verdict = classify.feed_line(state, raw) or verdict
    return state, verdict


def run_result(rc=1, stderr=b"", stdout=b"", killed=None, stdout_bytes=None):
    return {"rc": rc, "signal": None, "timedOut": killed == "deadline", "killedBy": killed, "bytesDropped": 0,
            "stdoutBytes": len(stdout) if stdout_bytes is None else stdout_bytes, "stderrBytes": len(stderr),
            "stderrTail": stderr, "stdoutTail": stdout, "error": None}


class ClassifyTests(unittest.TestCase):
    NOW = utc(2026, 9, 14, 13, 27, 51)
    SID = "3f2a0c19-0000-4000-8000-000000000001"

    def check(self, name, lines, run, outcome, level="plan", expected="plan"):
        state, _ = feed(name, lines, expected if name == "claude" else None)
        state["targetSession"] = self.SID
        result = classify.classify(state, run, level_id=level, now=self.NOW)
        self.assertEqual(result["outcome"], outcome, (name, result, lines, run))
        return result

    def init(self, mode="plan"):
        return {"type": "system", "subtype": "init", "session_id": self.SID, "permissionMode": mode,
                "apiKeySource": "none"}

    def test_classify_table(self):
        c_ok = [self.init(), {"type": "result", "subtype": "success", "is_error": False, "result": "OK"}]
        # not_found
        self.check("claude", [], run_result(stderr=b"No conversation found with session ID: " + self.SID.encode()),
                   "not_found")
        self.check("codex", [], run_result(stderr=b"Error: thread/resume: thread/resume failed: no rollout found "
                                                  b"for thread id x (code -32600)"), "not_found")
        self.check("opencode", [], run_result(stderr=b"Error: Session not found"), "not_found")
        self.check("gemini", [], run_result(rc=42, stderr=b"Error resuming session: Invalid session identifier"),
                   "not_found")
        # auth
        self.check("claude", [self.init(), {"type": "assistant", "error": "authentication_failed",
                                            "message": {"content": [{"type": "text", "text": "Not logged in"}]}}],
                   run_result(), "auth")
        self.check("claude", [self.init()], run_result(stderr=b"Login expired \xc2\xb7 Please run /login"), "auth")
        self.check("codex", [], run_result(stderr=b"Not logged in"), "auth")
        self.check("codex", [], run_result(stderr=b"Error: no Codex credentials were found"), "auth")
        self.check("opencode", [{"type": "error", "sessionID": "ses_abcdefgh1234",
                                 "error": {"name": "ProviderAuthError", "data": {"message": "bad key"}}}],
                   run_result(), "auth")
        self.check("gemini", [], run_result(rc=41, stderr=b"Please set an Auth method"), "auth")
        # limit
        resets = self.NOW + 5000
        result = self.check("claude", [self.init(), {"type": "rate_limit_event", "rate_limit_info": {
            "status": "rejected", "resetsAt": resets, "rateLimitType": "five_hour", "isUsingOverage": False}},
            {"type": "result", "subtype": "success", "is_error": True, "api_error_status": 429}], run_result(), "limit")
        self.assertEqual(result["limit"], {"kind": "session", "resetEpoch": resets, "source": "event",
                                           "isUsingOverage": False, "rearm": True})
        result = self.check("claude", [self.init(), {"type": "assistant", "error": "rate_limit", "message": {
            "content": [{"type": "text", "text": "You've hit your weekly limit · resets Oct 9, 10am (UTC)"}]}}],
            run_result(), "limit")
        self.assertEqual(result["limit"]["source"], "banner")
        self.assertEqual(result["limit"]["kind"], "weekly")
        self.check("claude", [self.init(), {"type": "result", "subtype": "success", "is_error": True,
                                            "api_error_status": 429, "result": "API Error"}], run_result(), "limit")
        result = self.check("claude", [self.init(), {"type": "result", "subtype": "success", "is_error": True,
                                                     "result": "You've reached your Fable limit. Run /usage-credits"}],
                            run_result(), "limit")
        self.assertEqual(result["limit"]["kind"], "model_weekly")
        result = self.check("codex", [{"type": "error", "message": "You've hit your usage limit. Visit x or try again "
                                                                   "at Sep 13th, 2026 7:11 PM."}], run_result(), "limit")
        self.assertIsNotNone(result["limit"]["resetEpoch"])
        self.check("opencode", [{"type": "error", "error": {"name": "APIError",
                                                            "data": {"message": "GoUsageLimitError"}}}],
                   run_result(), "limit")
        self.check("opencode", [{"type": "error", "error": {"name": "APIError",
                                                            "data": {"message": "rate limit exceeded (429)"}}}],
                   run_result(), "limit")
        result = self.check("gemini", [], run_result(stdout=json.dumps({"error": {
            "type": "TerminalQuotaError", "message": "You have exhausted your daily quota on this model."}}).encode()),
            "limit")
        self.assertEqual(result["limit"]["kind"], "daily")
        # transient
        self.check("claude", [self.init(), {"type": "result", "subtype": "success", "is_error": True,
                                            "api_error_status": 429, "result": "API Error: Server is temporarily "
                                            "limiting requests (not your usage limit)"}], run_result(), "transient")
        self.check("claude", [self.init(), {"type": "result", "subtype": "success", "is_error": True,
                                            "api_error_status": 529, "result": "Overloaded"}], run_result(),
                   "transient")
        self.check("codex", [{"type": "error", "message": "Selected model is at capacity. Please try a different "
                                                          "model."}], run_result(), "transient")
        self.check("codex", [{"type": "turn.failed", "error": {"message": "exceeded retry limit, last status: 429"}}],
                   run_result(), "transient")
        self.check("opencode", [], run_result(rc=None, killed="deadline", stdout_bytes=0), "transient")
        self.check("gemini", [], run_result(stdout=json.dumps({"error": {"type": "RetryableQuotaError",
                                                                         "message": "Please retry in 2s"}}).encode()),
                   "transient")
        # busy
        self.check("claude", [], run_result(stderr=b"Error: session " + self.SID.encode() + b" is in use"), "busy")
        # untrusted
        self.check("codex", [], run_result(stderr=b"Not inside a trusted directory and --skip-git-repo-check was "
                                                  b"not specified."), "untrusted")
        self.check("gemini", [], run_result(rc=55), "untrusted")
        # caps
        self.check("claude", [self.init(), {"type": "result", "subtype": "error_max_turns", "is_error": True}],
                   run_result(), "max_turns")
        self.check("claude", [self.init(), {"type": "result", "subtype": "error_max_budget_usd", "is_error": True}],
                   run_result(), "budget")
        self.check("gemini", [], run_result(rc=53), "max_turns")
        # boundary
        state, verdict = feed("claude", [self.init("default")], "plan")
        self.assertEqual(verdict, "kill")
        state, verdict = feed("claude", [self.init("dontAsk")], "dontAsk")
        self.assertIsNone(verdict)
        state, verdict = feed("claude", [{"type": "assistant", "message": {"content": [{"type": "tool_use"}]}}], "plan")
        self.assertEqual(verdict, "kill")
        violation = [{"type": "tool_use", "part": {"tool": "bash", "state": {"status": "completed"}}}]
        self.check("opencode", violation, run_result(rc=0), "boundary_mismatch", level="plan")
        self.check("opencode", violation, run_result(rc=0), "done", level="unattended")
        for tool in ("apply_patch", "multiedit", "edit", "write", "patch"):
            self.check("opencode", [{"type": "tool_use", "part": {"tool": tool, "state": {"status": "completed"}}}],
                       run_result(rc=0), "boundary_mismatch", level="plan")
        self.check("claude", [{"type": "result", "subtype": "success", "is_error": False}], run_result(rc=0),
                   "boundary_mismatch")
        # done
        self.check("claude", c_ok, run_result(rc=0), "done")
        self.check("codex", [{"type": "thread.started", "thread_id": self.SID}, {"type": "turn.completed"}],
                   run_result(rc=0), "done")
        self.check("opencode", [{"type": "step_start", "sessionID": "ses_abcdefgh1234"}], run_result(rc=0), "done")
        self.check("gemini", [], run_result(rc=0, stdout=b'{\n "response": "OK"\n}'), "done")
        # exit code alone never decides, in either direction
        result = self.check("claude", [self.init()], run_result(rc=0), "failed")
        self.assertEqual(result["detail"], "unclassified")
        self.check("codex", [{"type": "error", "message": "boom"}], run_result(rc=0), "failed")
        self.check("gemini", [], run_result(rc=0, stdout=b"not a document"), "failed")
        self.check("claude", c_ok, run_result(rc=3), "done")
        # session ids come only from structured events and pass the grammar
        state, _ = feed("opencode", [{"type": "step_start", "sessionID": "ses_abcdefgh1234"}])
        self.assertEqual(state["sessionId"], "ses_abcdefgh1234")
        state, _ = feed("codex", [{"type": "thread.started", "thread_id": "../../etc"}])
        self.assertIsNone(state["sessionId"])
        state, _ = feed("claude", [b"not json", b"{broken", b"[1,2]"], "plan")
        self.assertEqual(state["lines"], 3)

    def test_success_first_and_gemini_keys(self):
        # An answer that talks about error messages is still a success and is never sent again.
        answer = ("Fixed the parser for 'No conversation found with session ID' and "
                  "'You've hit your limit · resets 3pm'. Not logged in is handled too.")
        self.check("claude", [self.init(), {"type": "result", "subtype": "success", "is_error": False,
                                            "result": answer}], run_result(rc=0), "done")
        # a result delivered before SIGTERM stays done
        self.check("claude", [self.init(), {"type": "result", "subtype": "success", "is_error": False}],
                   run_result(rc=None, killed="sigterm"), "done")
        self.check("claude", [self.init()], run_result(rc=None, killed="sigterm"), "timeout")
        # codex: a retried error followed by a completed turn and exit 0
        self.check("codex", [{"type": "error", "message": "Reconnecting... 1/5"}, {"type": "turn.completed"}],
                   run_result(rc=0), "done")
        self.check("codex", [{"type": "turn.completed"}, {"type": "turn.failed", "error": {"message": "x"}}],
                   run_result(rc=0), "failed")
        # opencode: a started step without any content before the deadline is a silent retry wait
        self.check("opencode", [{"type": "step_start", "sessionID": "ses_abcdefgh1234"}],
                   run_result(rc=None, killed="deadline", stdout_bytes=80), "transient")
        self.check("opencode", [{"type": "text", "sessionID": "ses_abcdefgh1234", "part": {"text": "x"}}],
                   run_result(rc=None, killed="deadline", stdout_bytes=80), "timeout")
        self.check("opencode", [], run_result(rc=0), "failed")
        # gemini: a pretty-printed document larger than the kept tail is judged by its top-level keys
        big = [b"{", b'  "session_id": "' + self.SID.encode() + b'",', b'  "response": "long answer",',
               b'  "stats": {', b'    "error": 0', b"  }", b"}"]
        state, _ = feed("gemini", big)
        result = classify.classify(state, run_result(rc=0, stdout=b'"tail of a long answer"\n}'), level_id="plan",
                                   now=self.NOW)
        self.assertEqual(result["outcome"], "done")
        state, _ = feed("gemini", [b"{", b'  "error": {', b'    "type": "FatalToolExecutionError"', b"  }", b"}"])
        result = classify.classify(state, run_result(rc=0, stdout=b"garbage"), level_id="plan", now=self.NOW)
        self.assertEqual(result["outcome"], "failed")
        state, _ = feed("gemini", [json.dumps({"session_id": self.SID, "response": "OK"}).encode()])
        self.assertEqual(classify.classify(state, run_result(rc=0), level_id="plan", now=self.NOW)["outcome"], "done")
        self.assertEqual(state["sessionId"], self.SID)
        # detail codes are fixed words, never text taken from the agent
        result = self.check("claude", [self.init(), {"type": "result", "subtype": "success", "is_error": True,
                                                     "result": "You've reached your Fable limit."}],
                            run_result(), "limit")
        self.assertEqual(result["detail"], "model_limit")
        for detail in (result["detail"], "rate_limit_event", "session_held", "silent_timeout"):
            self.assertRegex(detail, r"^[a-z_]{1,40}$")


# ------------------------------------------------------------------------------------------------ trigger

class TriggerBase(Sandbox):
    NOW = utc(2026, 9, 14, 13, 27, 51)

    @staticmethod
    def _bindable(windows_list):
        for window in windows_list:
            window.setdefault("bindable", True)
            window.setdefault("sliding", False)
        return windows_list

    def row(self, source_id, windows_list, stale=False, readable=True):
        """One Usage v2 provider row (contract delta 3.7)."""
        return {"id": source_id, "name": source_id, "source": "record", "readable": readable, "stale": stale,
                "updatedAtMs": self.NOW * 1000 - 60000, "windows": self._bindable(windows_list)}

    def usage(self, windows_list, stale=False, available=True, codex=None, extra=()):
        providers = [self.row("claude", windows_list, stale=stale, readable=available)]
        if codex is not None:
            row = self.row("codex", codex["windows"], stale=codex.get("stale", False),
                           readable=codex.get("available", True))
            row["updatedAtMs"] = codex.get("updatedAtMs")
            providers.append(row)
        providers.extend(extra)
        return {"nowMs": self.NOW * 1000, "providers": providers}

    def session(self, resets, percent=0.22):
        return {"label": "Session (5-hour)", "kind": "session", "percent": percent, "resetsAt": resets, "title": None}

    def weekly(self, resets, percent=1.0):
        return {"label": "Weekly (7-day)", "kind": "weekly", "percent": percent, "resetsAt": resets, "title": None}

    def reset_job(self, name="claude", kind="claude_5h_reset", model=None, policy="defer"):
        job = make_job(name, kind=kind, model=model, now=self.NOW)
        job["trigger"]["weeklyPolicy"] = policy
        return job

    def code(self, fn, *args):
        with self.assertRaises(ApError) as ctx:
            fn(*args)
        return ctx.exception


class TriggerTests(TriggerBase):

    def test_banner_regex_fixtures(self):
        self.p.set(trigger, "_local_zone", lambda: CHICAGO)
        banner = trigger.parse_claude_banner
        got = banner("You've hit your session limit · resets 9:30pm (America/Chicago)", utc(2026, 9, 6, 23, 0))
        self.assertEqual(got, {"kind": "session", "resetEpoch": utc(2026, 9, 7, 2, 30)})
        self.assertEqual(got["resetEpoch"], 1788748200)
        self.assertEqual(banner("You've hit your limit · resets Jan 1, 2026, 9am (UTC)", utc(2025, 12, 31, 20, 0)),
                         {"kind": "other", "resetEpoch": utc(2026, 1, 1, 9, 0)})
        self.assertEqual(banner("You've hit your weekly limit · resets Oct 9, 10am", self.NOW),
                         {"kind": "weekly", "resetEpoch": utc(2026, 10, 9, 15, 0)})
        self.assertEqual(banner("You're out of extra usage · resets 3pm (America/Los_Angeles)",
                                utc(2026, 3, 25, 15, 0)), {"kind": "other", "resetEpoch": utc(2026, 3, 25, 22, 0)})
        self.assertEqual(banner("You've hit your session limit · resets 12:10am (America/Chicago)",
                                utc(2026, 9, 14, 3, 0))["resetEpoch"], utc(2026, 9, 14, 5, 10))
        self.assertEqual(banner("You've hit your session limit · resets 1:30am (America/Chicago)",
                                utc(2026, 11, 1, 4, 0))["resetEpoch"], utc(2026, 11, 1, 6, 30))
        self.assertIsNone(banner("You've reached your Fable limit. Run /usage-credits to continue", self.NOW))
        self.assertEqual(banner("You've hit your Opus limit · resets 4pm (Nowhere/Zone)", self.NOW)["kind"],
                         "model_weekly")
        retry = trigger.parse_codex_retry
        self.assertEqual(retry("ERROR: You've hit your usage limit. Visit https://chatgpt.com/codex/settings/usage to "
                               "purchase more credits or try again at Sep 13th, 2026 7:11 PM.", self.NOW),
                         utc(2026, 9, 14, 0, 11))
        self.assertEqual(retry("You've hit your usage limit. Try again at 1:36 PM.", self.NOW), utc(2026, 9, 14, 18, 36))
        self.assertIsNone(retry("You've hit your usage limit. Try again later.", self.NOW))

    def test_compute_fire_at_now_in_at(self):
        job = make_job("claude", kind="now", now=self.NOW)
        self.assertEqual(trigger.compute_fire_at(job, self.NOW, None),
                         {"fireAt": self.NOW, "immediate": True, "basis": None, "hint": None, "wait": None})
        job = make_job("claude", kind="in", now=self.NOW)
        job["trigger"]["delaySec"] = 7200
        got = trigger.compute_fire_at(job, self.NOW, None)
        self.assertEqual((got["fireAt"], got["immediate"], got["wait"]), (self.NOW + 7200, False, None))
        job = make_job("claude", kind="at", fire_at=self.NOW + 90000, now=self.NOW)
        self.assertEqual(trigger.compute_fire_at(job, self.NOW, None)["hint"], "far")
        job["trigger"]["fireAt"] = self.NOW - 61
        self.assertEqual(self.code(trigger.compute_fire_at, job, self.NOW, None).code, "time_past")
        job["trigger"]["fireAt"] = self.NOW + consts.HORIZON_S + 1
        self.assertEqual(self.code(trigger.compute_fire_at, job, self.NOW, None).code, "time_too_far")
        job = make_job("gemini", kind="claude_5h_reset", now=self.NOW)
        self.assertEqual(self.code(trigger.compute_fire_at, job, self.NOW, None).code, "trigger_unsupported")

    def test_compute_fire_at_claude_reset(self):
        resets = self.NOW + 6129
        got = trigger.compute_fire_at(self.reset_job(), self.NOW, self.usage([self.session(resets)]))
        self.assertEqual(got["fireAt"], resets + 120)
        self.assertEqual(got["wait"], "reset")
        self.assertEqual(got["basis"], {"source": "record", "resetEpoch": resets,
                                        "fetchedAtMs": self.NOW * 1000 - 60000, "percent": 0.22})
        self.assertIsNone(got["hint"])
        got = trigger.compute_fire_at(self.reset_job(), self.NOW, self.usage([self.session(resets)], stale=True))
        self.assertEqual(got["hint"], "usage_stale")

    def test_compute_fire_at_weekly_defer(self):
        resets = self.NOW + 6129
        weekly = self.NOW + 2 * 86400
        got = trigger.compute_fire_at(self.reset_job(), self.NOW,
                                      self.usage([self.session(resets), self.weekly(weekly)]))
        self.assertEqual((got["fireAt"], got["hint"]), (weekly + 120, "weekly_deferred"))
        model_window = {"label": "Fable Weekly", "kind": "model_weekly", "percent": 1.0, "resetsAt": weekly + 600,
                        "title": "Fable Weekly"}
        usage = self.usage([self.session(resets), model_window])
        self.assertEqual(trigger.compute_fire_at(self.reset_job(), self.NOW, usage)["fireAt"], resets + 120)
        got = trigger.compute_fire_at(self.reset_job(model="claude-fable-5-1"), self.NOW, usage)
        self.assertEqual(got["fireAt"], weekly + 720)

    def test_compute_fire_at_weekly_skip(self):
        weekly = self.NOW + 2 * 86400
        err = self.code(trigger.compute_fire_at, self.reset_job(policy="skip"), self.NOW,
                        self.usage([self.session(self.NOW + 600), self.weekly(weekly)]))
        self.assertEqual((err.code, err.detail), ("weekly_exhausted", {"until": weekly}))

    def test_compute_fire_at_no_data(self):
        self.assertEqual(self.code(trigger.compute_fire_at, self.reset_job(), self.NOW, None).code, "no_reset_data")
        self.assertEqual(self.code(trigger.compute_fire_at, self.reset_job(), self.NOW,
                                   self.usage([], available=False)).code, "no_reset_data")

    def test_compute_fire_at_not_open(self):
        self.assertEqual(self.code(trigger.compute_fire_at, self.reset_job(), self.NOW, self.usage([])).code,
                         "reset_not_open")
        self.assertEqual(self.code(trigger.compute_fire_at, self.reset_job(), self.NOW,
                                   self.usage([self.session(self.NOW - 5)])).code, "reset_not_open")

    def test_compute_fire_at_codex_sliding_filtered_upstream(self):
        job = self.reset_job("codex", kind="codex_window_reset")
        codex = {"available": True, "updatedAtMs": self.NOW * 1000, "stale": False, "status": "", "windows": [
            {"label": "5h window", "kind": "other", "percent": 0.4, "resetsAt": self.NOW + 9000, "title": None},
            {"label": "Weekly (7-day)", "kind": "weekly", "percent": 0.1, "resetsAt": self.NOW + 400000, "title": None}]}
        got = trigger.compute_fire_at(job, self.NOW, self.usage([], codex=codex))
        self.assertEqual(got["fireAt"], self.NOW + 9120)
        codex["windows"][1]["percent"] = 0.995
        self.assertEqual(trigger.compute_fire_at(job, self.NOW, self.usage([], codex=codex))["fireAt"],
                         self.NOW + 400120)
        codex["windows"] = []
        self.assertEqual(self.code(trigger.compute_fire_at, job, self.NOW, self.usage([], codex=codex)).code,
                         "no_reset_data")

    def test_compute_fire_at_gemini_la_midnight(self):
        job = self.reset_job("gemini", kind="gemini_daily_reset")
        got = trigger.compute_fire_at(job, self.NOW, None)
        self.assertEqual(got["fireAt"], utc(2026, 9, 15, 7, 0) + 120)
        self.assertEqual(got["basis"]["source"], "clock")
        winter = utc(2026, 12, 1, 12, 0)
        self.assertEqual(trigger.compute_fire_at(job, winter, None)["fireAt"], utc(2026, 12, 2, 8, 0) + 120)

    def test_compute_fire_at_horizon(self):
        usage = self.usage([self.session(self.NOW + 600), self.weekly(self.NOW + 9 * 86400)])
        self.assertEqual(self.code(trigger.compute_fire_at, self.reset_job(), self.NOW, usage).code, "time_too_far")

    def test_compute_fire_at_clock_unsynced(self):
        self.p.set(systemd, "clock_synced", lambda: False)
        got = trigger.compute_fire_at(self.reset_job(), self.NOW, self.usage([self.session(self.NOW + 600)]))
        self.assertEqual((got["fireAt"], got["hint"]), (self.NOW + 600 + 120 + consts.SKEW_EXTRA_S, "clock_unsynced"))

    def armed_reset(self, reset_epoch, fire_at):
        job = self.reset_job()
        job["state"]["basis"] = {"source": "record", "resetEpoch": reset_epoch, "fetchedAtMs": None, "percent": 0.2}
        job["state"]["fireAt"] = fire_at
        job["state"]["wait"] = "reset"
        return job

    def test_prefire_window_later(self):
        reset_epoch = self.NOW - 120
        job = self.armed_reset(reset_epoch, self.NOW)
        # the record still shows a window that started before the reset we waited for
        later = reset_epoch + 3600
        got = trigger.prefire(job, self.NOW, self.usage([self.session(later)]))
        self.assertEqual(got, {"action": "rearm", "fireAt": later + 120, "reason": "window_later", "resetEpoch": later})
        fresh = reset_epoch + 5 * 3600
        self.assertEqual(trigger.prefire(job, self.NOW, self.usage([self.session(fresh, 0.01)]))["action"], "fire")
        self.assertEqual(trigger.prefire(job, self.NOW, self.usage([self.session(later)], stale=True))["action"],
                         "fire")
        job["state"]["defers"] = consts.MAX_DEFERS
        self.assertEqual(trigger.prefire(job, self.NOW, self.usage([self.session(later)])),
                         {"action": "gave_up", "fireAt": None, "reason": "defers"})

    def test_prefire_window_exhausted(self):
        reset_epoch = self.NOW - 120
        job = self.armed_reset(reset_epoch, self.NOW)
        fresh = reset_epoch + 5 * 3600
        got = trigger.prefire(job, self.NOW, self.usage([self.session(fresh, 0.995)]))
        self.assertEqual(got, {"action": "rearm", "fireAt": fresh + 120, "reason": "window_exhausted",
                               "resetEpoch": fresh})
        weekly = self.NOW + 86400
        got = trigger.prefire(job, self.NOW, self.usage([self.session(fresh, 0.1), self.weekly(weekly)]))
        self.assertEqual(got, {"action": "rearm", "fireAt": weekly + 120, "reason": "weekly_exhausted",
                               "resetEpoch": weekly})
        job["trigger"]["weeklyPolicy"] = "skip"
        self.assertEqual(trigger.prefire(job, self.NOW, self.usage([self.session(fresh, 0.1), self.weekly(weekly)])),
                         {"action": "skip", "fireAt": None, "reason": "weekly_exhausted"})
        # run now (fire time before the awaited reset) is not second-guessed
        early = self.armed_reset(self.NOW + 3000, self.NOW)
        self.assertEqual(trigger.prefire(early, self.NOW, self.usage([self.session(self.NOW + 3000, 1.0)]))["action"],
                         "fire")

    def test_catchup_grace_by_kind(self):
        at_job = make_job("claude", kind="at", now=self.NOW)
        in_job = make_job("claude", kind="in", now=self.NOW)
        for kind in consts.RESET_KINDS:
            self.assertEqual(trigger.catchup_grace_s(make_job("claude", kind=kind)), 10800)
        self.assertEqual(trigger.catchup_grace_s(at_job), 900)
        self.assertEqual(trigger.catchup_grace_s(in_job), 900)
        # A time job re-armed to a reset (limit retry, deferral) waits like a reset job.
        for wait in ("limit", "deferred"):
            at_job["state"]["wait"] = wait
            self.assertEqual(trigger.catchup_grace_s(at_job), 10800)
        at_job["state"]["wait"] = None
        at_job["state"]["fireAt"] = self.NOW - 901
        self.assertEqual(trigger.prefire(at_job, self.NOW, None), {"action": "missed", "fireAt": None,
                                                                   "reason": "late"})
        at_job["state"]["fireAt"] = self.NOW - 899
        self.assertEqual(trigger.prefire(at_job, self.NOW, None)["action"], "fire")
        gem = make_job("gemini", kind="gemini_daily_reset", now=self.NOW)
        gem["state"]["fireAt"] = self.NOW - 10000
        self.assertEqual(trigger.prefire(gem, self.NOW, None)["action"], "fire")
        gem["state"]["fireAt"] = self.NOW - 10801
        self.assertEqual(trigger.prefire(gem, self.NOW, None)["action"], "missed")

    def test_postrun_limit_rearm_and_cap(self):
        job = make_job("claude", now=self.NOW)
        limit = {"outcome": "limit", "detail": "rate_limit_event", "sessionId": None,
                 "limit": {"kind": "session", "resetEpoch": self.NOW + 3000, "source": "event",
                           "isUsingOverage": False, "rearm": True}}
        got = trigger.postrun(job, limit, self.NOW, self.usage([]))
        self.assertEqual(got, {"status": "armed", "wait": "limit", "fireAt": self.NOW + 3120, "reason": None,
                               "counter": "limitRetries", "notify": "limit_rearmed"})
        weekly = self.NOW + 3 * 86400
        self.assertEqual(trigger.postrun(job, limit, self.NOW, self.usage([self.weekly(weekly)]))["fireAt"],
                         weekly + 120)
        no_epoch = copy.deepcopy(limit)
        no_epoch["limit"].update(resetEpoch=None, source="backoff")
        job["state"]["limitRetries"] = 1
        self.assertEqual(trigger.postrun(job, no_epoch, self.NOW, None)["fireAt"], self.NOW + 3600)
        job["state"]["limitRetries"] = consts.MAX_LIMIT_RETRIES
        self.assertEqual(trigger.postrun(job, limit, self.NOW, None),
                         {"status": "limit", "wait": None, "fireAt": None, "reason": "limit_retries", "counter": None,
                          "notify": "limit_final"})
        job["state"]["limitRetries"] = 0
        overage = copy.deepcopy(limit)
        overage["limit"]["isUsingOverage"] = True
        self.assertEqual(trigger.postrun(job, overage, self.NOW, None)["reason"], "overage")
        stale = {"outcome": "limit", "detail": "rate_limit_reached", "sessionId": None,
                 "limit": {"kind": "other", "resetEpoch": None, "source": "backoff", "isUsingOverage": False,
                           "rearm": True}}
        self.assertEqual(trigger.postrun(job, stale, self.NOW, self.usage([self.session(self.NOW + 99, 0.1)]))["reason"],
                         "stale_auth")
        gem = make_job("gemini", now=self.NOW)
        daily = {"outcome": "limit", "detail": "daily_quota", "sessionId": None,
                 "limit": {"kind": "daily", "resetEpoch": None, "source": "backoff", "isUsingOverage": False}}
        self.assertEqual(trigger.postrun(gem, daily, self.NOW, None)["fireAt"], utc(2026, 9, 15, 7, 0) + 120)
        codex = make_job("codex", now=self.NOW)
        codex["trigger"]["marginSec"] = 60
        at = copy.deepcopy(limit)
        at["limit"]["resetEpoch"] = self.NOW + 1000
        self.assertEqual(trigger.postrun(codex, at, self.NOW, None)["fireAt"], self.NOW + 1090)

    def test_postrun_transient_backoff(self):
        job = make_job("codex", now=self.NOW)
        result = {"outcome": "transient", "detail": "capacity", "sessionId": None, "limit": None}
        for n, delay in enumerate((120, 240, 480)):
            job["state"]["transientRetries"] = n
            got = trigger.postrun(job, result, self.NOW, None)
            self.assertEqual((got["status"], got["wait"], got["fireAt"], got["counter"], got["notify"]),
                             ("armed", "transient", self.NOW + delay, "transientRetries", "transient_rearmed"))
        job["state"]["transientRetries"] = 3
        self.assertEqual(trigger.postrun(job, result, self.NOW, None)["status"], "gave_up")
        self.assertEqual(trigger.postrun(job, result, self.NOW, None)["reason"], "transient_retries")

    def test_postrun_busy_defers(self):
        job = make_job("opencode", now=self.NOW)
        result = {"outcome": "busy", "detail": "session_held", "sessionId": None, "limit": None}
        got = trigger.postrun(job, result, self.NOW, None)
        self.assertEqual((got["status"], got["fireAt"], got["counter"], got["notify"]),
                         ("armed", self.NOW + 600, "busyDefers", "busy_deferred"))
        job["state"]["busyDefers"] = 3
        got = trigger.postrun(job, result, self.NOW, None)
        self.assertEqual((got["status"], got["reason"], got["notify"]), ("busy", "session_busy", "busy_final"))

    def test_postrun_unknown_single_retry(self):
        job = make_job("claude", now=self.NOW)
        result = {"outcome": "failed", "detail": "unclassified", "sessionId": None, "limit": None}
        got = trigger.postrun(job, result, self.NOW, None)
        self.assertEqual((got["status"], got["wait"], got["fireAt"]), ("armed", "transient", self.NOW + 1800))
        job["state"]["unknownRetries"] = 1
        self.assertEqual(trigger.postrun(job, result, self.NOW, None)["status"], "failed")
        for outcome in ("auth", "not_found", "untrusted", "boundary_mismatch", "max_turns", "budget", "timeout"):
            got = trigger.postrun(job, {"outcome": outcome, "detail": "x", "sessionId": None, "limit": None},
                                  self.NOW, None)
            self.assertEqual((got["status"], got["reason"], got["notify"]), ("failed", outcome, "failed"))


# ------------------------------------------------------------------------------------------------ notify

class NotifyTests(Sandbox):

    def test_notify_argv_no_prompt(self):
        calls = []
        self.p.set(bounded, "run_bounded", lambda argv, **kw: calls.append((argv, kw)) or {"rc": 0, "timedOut": False})
        job = make_job("claude")
        job["label"] = 'Fix "the" bug\x07 in a label that is far too long to be shown whole'
        self.assertTrue(notify.send("done", job, now=1789400000, duration_s=840))
        argv, kw = calls[-1]
        self.assertEqual(argv[:9], [consts.TOOLS["busctl"], "--user", "call", "org.freedesktop.Notifications",
                                    "/org/freedesktop/Notifications", "org.freedesktop.Notifications", "Notify",
                                    "susssasa{sv}i", edition.NOTIFY_APP_NAME])
        self.assertEqual(argv[9:11], ["0", ""])
        self.assertEqual(argv[11], "Done")
        self.assertEqual(argv[12], "\"Fix 'the' bug in a label that is far too\" finished in 14m.")
        self.assertEqual(argv[13:], ["0", "0", "6000"])
        self.assertEqual(kw["deadline_s"], 5.0)
        notify.send("failed", job, now=1789400000, reason="boundary_mismatch", cli_changed=True)
        self.assertTrue(calls[-1][0][12].endswith("The agent did not start in the requested permission level. "
                                                  "The Claude Code command changed since the job was armed."))
        for event in notify.TEMPLATES:
            summary, body = notify.render(event, job, now=1789400000, fire_at=1789400600, duration_s=5,
                                          reason="late")
            self.assertNotIn("\u2014", summary + body)
            self.assertNotIn("{", body)
        self.assertEqual(set(notify.REASON_SENTENCES), set(consts.REASONS) | {"not_found", "auth", "untrusted"})
        summary, body = notify.render("missed", job, now=1789400000, fire_at=1789400600)
        self.assertEqual(summary, "Missed")
        self.assertTrue(body.endswith("Open " + edition.DISPLAY_NAME + " to run it."), body)
        self.assertEqual(notify.REASON_SENTENCES["plugin_disabled"],
                         edition.DISPLAY_NAME + " is not enabled in the bar.")
        notify.ipc_ping()
        self.assertEqual(calls[-1][0], [consts.TOOLS["qs"], "ipc", "-p", consts.OMARCHY_SHELL_DIR, "call",
                                        edition.SERVICE_IPC_TARGET, "changed"])
        self.p.set(notify, "_notify_setting", lambda: "failures")
        count = len(calls)
        self.assertFalse(notify.send("done", job, now=1789400000, duration_s=1))
        self.assertTrue(notify.send("failed", job, now=1789400000, reason="auth"))
        self.assertEqual(len(calls), count + 1)
        self.p.set(notify, "_notify_setting", lambda: "never")
        self.assertFalse(notify.send("failed", job, now=1789400000, reason="auth"))


# ------------------------------------------------------------------------------------------------ run verb

class RunVerbBase(Sandbox):

    def setUp(self):
        super().setUp()
        os.environ["INVOCATION_ID"] = uuid.uuid4().hex
        shell = os.path.join(self.home, ".config", "omarchy")
        os.makedirs(shell, mode=0o700)
        self.write_shell(True)
        self.armed = []
        self.notified = []
        self.reconciled = []
        self.p.set(systemd, "arm_unit", lambda job_id, gen, fire_at, runtime_sec: self.armed.append(
            (job_id, gen, fire_at, runtime_sec)))
        self.p.set(reconcile, "reconcile", lambda sd, now=None: self.reconciled.append(True) or {})
        real_bounded = bounded.run_bounded
        tools = (consts.TOOLS["busctl"], consts.TOOLS["qs"])

        def recorder(argv, **kw):
            if argv and argv[0] in tools:
                self.notified.append(list(argv))
                return {"rc": 0, "signal": None, "stdout": b"", "stderr": b"", "stdoutDropped": 0,
                        "stderrDropped": 0, "timedOut": False, "overflow": False, "error": None}
            return real_bounded(argv, **kw)

        self.p.set(bounded, "run_bounded", recorder)
        self.p.set(jobs, "check_cwd", lambda path: os.path.realpath(path))
        self.p.set(sessions, "claude_last_quota", lambda sid: None)
        self.p.set(sessions, "claude_last_user_sha256", lambda sid: None)
        self.stderr_lines = []

    def write_shell(self, enabled):
        data = {"bar": {"layout": {"left": [], "center": [], "right": [edition.PLUGIN_ID] if enabled else ["clock"]}}}
        path = os.path.join(self.home, ".config", "omarchy", "shell.json")
        with open(path, "w") as handle:
            json.dump(data, handle)
        os.chmod(path, 0o600)

    def seed(self, name="claude", mode="new", prompt="Summarize the repository.", **kwargs):
        link = kwargs.pop("link", None) or self.place_cli(name)
        cwd = self.project()
        now = int(time.time())
        job = make_job(name, mode=mode, cwd=cwd, link=link, real=kwargs.pop("real", link), now=now, **kwargs)
        job["state"]["pluginDir"] = fsio.plugin_dir()
        sd = fsio.open_state(create=True)
        sha, size = jobs.store_prompt(sd, job["id"], prompt)
        job["promptSha256"], job["promptBytes"] = sha, size
        jobs.refresh_digests(job)
        store = jobs.load_store(sd)
        store["jobs"].append(job)
        jobs.save_store(sd, store)
        return job

    def stored(self, job_id):
        sd = fsio.open_state(create=True)
        return jobs.find_job(jobs.load_store(sd), job_id)

    def mutate(self, job_id, fn):
        sd = fsio.open_state(create=True)
        with sd.lock(5):
            store = jobs.load_store(sd)
            fn(jobs.find_job(store, job_id))
            jobs.save_store(sd, store)

    def run_verb(self, job_id, gen=1):
        path = os.path.join(self.tmp, "stderr-%d.txt" % len(self.stderr_lines))
        saved = os.dup(2)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.dup2(fd, 2)
        os.close(fd)
        try:
            result = runner.cmd_run(["--job", job_id, "--gen", str(gen)], None)
        finally:
            os.dup2(saved, 2)
            os.close(saved)
        with open(path) as handle:
            lines = handle.read().splitlines()
        self.stderr_lines.append(lines)
        for line in lines:
            self.assertRegex(line, DIAG_RE)
        self.assertEqual(result, {})
        return [line.split()[1] for line in lines]

    def run_record(self, job_id, gen):
        path = os.path.join(self.home, ".local/state/omarchy", edition.STATE_DIR_NAME, "runs",
                            "%s-g%d.json" % (job_id, gen))
        with open(path) as handle:
            return json.load(handle)


class RunVerbTests(RunVerbBase):

    def test_runner_requires_invocation_id(self):
        job = self.seed()
        del os.environ["INVOCATION_ID"]
        self.assertEqual(self.run_verb(job["id"]), ["E_NOT_SYSTEMD"])
        self.assertEqual(self.stored(job["id"])["state"]["status"], "armed")
        self.assertEqual(self.agent_entries(), [])
        os.environ["INVOCATION_ID"] = "not-a-systemd-id"
        self.assertEqual(self.run_verb(job["id"]), ["E_NOT_SYSTEMD"])

    def test_prompt_only_on_stdin(self):
        canary = "CANARY-" + uuid.uuid4().hex
        self.fake_config(mode="done")
        job = self.seed(prompt="Please check " + canary + " carefully.")
        codes = self.run_verb(job["id"])
        self.assertEqual(codes, ["FIRED", "OUTCOME_DONE", "FINAL"])
        after = self.stored(job["id"])
        self.assertEqual(after["state"]["status"], "done")
        self.assertFalse(after["promptAvailable"])
        sd = fsio.open_state(create=True)
        self.assertIsNone(jobs.read_prompt(sd, job["id"]))
        entry = [e for e in self.agent_entries() if e["stdinBytes"]][-1]
        self.assertEqual(entry["stdinSha256"], job["promptSha256"])
        state_dir = os.path.join(self.home, ".local/state/omarchy", edition.STATE_DIR_NAME)
        for root, _dirs, files in os.walk(state_dir):
            for name in files:
                with open(os.path.join(root, name), "rb") as handle:
                    self.assertNotIn(canary.encode(), handle.read(), name)
        self.assertNotIn(canary, json.dumps([entry["argv"], entry["env"], self.notified, self.stderr_lines]))
        self.assertEqual(self.notified[0][11:13], ["Done", self.notified[0][12]])
        self.assertIn("changed", self.notified[-1])
        record = self.run_record(job["id"], 1)
        self.assertEqual((record["outcome"], record["action"]["status"], record["mode"]), ("done", "done", "new"))
        self.assertEqual(after["state"]["runSessionId"], job["target"]["newSessionId"])
        self.assertEqual(after["state"]["lastRun"]["outcome"], "done")
        self.assertEqual(self.reconciled, [True])

    def test_limit_rearms_into_same_session(self):
        resets = int(time.time()) + 7200
        self.fake_config(mode="limit_event", resetsAt=resets)
        job = self.seed(mode="new")
        self.assertEqual(self.run_verb(job["id"]), ["FIRED", "OUTCOME_LIMIT", "REARMED"])
        after = self.stored(job["id"])
        self.assertEqual((after["state"]["status"], after["state"]["wait"], after["state"]["gen"]),
                         ("armed", "limit", 2))
        self.assertEqual(after["state"]["limitRetries"], 1)
        self.assertEqual(self.armed, [(job["id"], 2, resets + 120, 5400)])
        self.assertTrue(after["promptAvailable"])
        self.assertEqual(after["state"]["runSessionId"], job["target"]["newSessionId"])
        self.assertEqual(self.notified[0][11], "Limit hit")
        cmd = harness.build_command(after, exec_prefix=["/x"], run_dir="/s", gen=2)
        self.assertIn("--resume", cmd["argv"])

    def test_digest_mismatch_no_fire(self):
        self.fake_config(mode="done")
        job = self.seed()
        self.mutate(job["id"], lambda j: j.update(level="unattended"))
        self.assertEqual(self.run_verb(job["id"]), ["NEEDS_CONFIRM"])
        self.assertEqual(self.stored(job["id"])["state"]["status"], "needs_confirm")
        self.assertEqual(self.agent_entries(), [])
        self.assertEqual(self.notified[0][11], "Check the job")

    def test_prompt_file_swap_no_fire(self):
        self.fake_config(mode="done")
        job = self.seed()
        sd = fsio.open_state(create=True)
        jobs.store_prompt(sd, job["id"], "A different prompt than the one confirmed.")
        self.assertEqual(self.run_verb(job["id"]), ["NEEDS_CONFIRM"])
        self.assertEqual(self.agent_entries(), [])

    def test_plugin_disabled_no_fire(self):
        self.fake_config(mode="done")
        kill = fsio.kill_switch_path()
        job = self.seed()
        os.makedirs(os.path.dirname(kill))
        open(kill, "w").close()
        self.assertEqual(self.run_verb(job["id"]), ["PAUSED"])
        self.assertEqual(self.stored(job["id"])["state"]["reason"], "kill_switch")
        os.remove(kill)

        job = self.seed()
        self.write_shell(False)
        self.assertEqual(self.run_verb(job["id"]), ["PAUSED"])
        self.assertEqual(self.stored(job["id"])["state"]["reason"], "plugin_disabled")
        self.write_shell(True)

        job = self.seed()
        self.p.set(fsio, "manifest_id", lambda: "someone.else")
        self.assertEqual(self.run_verb(job["id"]), ["PAUSED"])
        self.assertEqual(self.stored(job["id"])["state"]["reason"], "plugin_identity")

        job = self.seed()
        self.p.set(fsio, "manifest_id", lambda: edition.PLUGIN_ID)
        self.mutate(job["id"], lambda j: j["state"].update(pluginDir="/elsewhere/plugin"))
        self.assertEqual(self.run_verb(job["id"]), ["PAUSED"])
        self.assertEqual(self.stored(job["id"])["state"]["reason"], "plugin_identity")
        self.assertEqual(self.agent_entries(), [])
        self.assertEqual([n[11] for n in self.notified if n[0] == consts.TOOLS["busctl"]], ["Paused"] * 4)

    def test_stale_gen_exits(self):
        job = self.seed()
        self.assertEqual(self.run_verb(job["id"], gen=7), ["STALE"])
        self.assertEqual(self.run_verb("f" * 16, gen=1), ["STALE"])
        self.assertEqual(self.stored(job["id"])["state"]["status"], "armed")
        self.assertEqual(self.notified, [])

    def test_disarm_during_run_late_result(self):
        self.fake_config(mode="sleep")
        job = self.seed()

        def disarm():
            def change(j):
                j["state"]["status"] = "disarmed"
                j["state"]["gen"] = 2
                j["state"]["unit"] = None
            for _ in range(100):
                if self.agent_entries():
                    break
                time.sleep(0.05)
            self.mutate(job["id"], change)
            os.kill(os.getpid(), signal.SIGTERM)

        previous = signal.getsignal(signal.SIGTERM)
        thread = threading.Thread(target=disarm)
        thread.start()
        try:
            codes = self.run_verb(job["id"])
        finally:
            thread.join()
            signal.signal(signal.SIGTERM, previous)
        self.assertEqual(codes, ["FIRED", "OUTCOME_TIMEOUT", "LATE_RESULT"])
        after = self.stored(job["id"])
        self.assertEqual((after["state"]["status"], after["state"]["gen"]), ("disarmed", 2))
        self.assertEqual(after["state"]["history"][-1]["event"], "late_result")
        record = self.run_record(job["id"], 1)
        self.assertEqual(record["action"], {"status": None, "fireAt": None, "reason": None})
        self.assertEqual(record["killedBy"], "sigterm")
        self.assertEqual(self.armed, [])

    def test_session_lock_defer(self):
        self.fake_config(mode="done")
        session = str(uuid.uuid4())
        job = self.seed(mode="resume", session=session)
        lock_dir = os.path.join(self.runtime, edition.RUNTIME_DIR_NAME)
        os.makedirs(lock_dir, mode=0o700, exist_ok=True)
        name = "session-" + hashlib.sha256(("claude:" + session).encode()).hexdigest() + ".lock"
        import fcntl
        holder = os.open(os.path.join(lock_dir, name), os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(holder, fcntl.LOCK_EX)
        try:
            before = int(time.time())
            self.assertEqual(self.run_verb(job["id"]), ["SESSION_LOCKED"])
        finally:
            os.close(holder)
        after = self.stored(job["id"])
        self.assertEqual((after["state"]["status"], after["state"]["wait"], after["state"]["busyDefers"]),
                         ("armed", "busy", 1))
        self.assertEqual(len(self.armed), 1)
        self.assertTrue(before + consts.SESSION_LOCK_DEFER_S <= self.armed[0][2] <= before + 310)
        self.assertEqual(self.agent_entries(), [])
        self.assertEqual(self.notified[0][11], "Session busy")

    def test_retry_sends_continuation(self):
        self.fake_config(mode="done")
        session = str(uuid.uuid4())
        job = self.seed(mode="resume", session=session)
        self.mutate(job["id"], lambda j: j["state"].update(limitRetries=1))
        self.p.set(sessions, "claude_last_user_sha256", lambda sid: job["promptSha256"] if sid == session else None)
        self.assertEqual(self.run_verb(job["id"]), ["FIRED", "OUTCOME_DONE", "FINAL"])
        entry = [e for e in self.agent_entries() if e["stdinBytes"]][-1]
        self.assertEqual(entry["stdinSha256"], hashlib.sha256(runner.CONTINUE_TEXT).hexdigest())

    def test_reset_prefire_deferral(self):
        self.fake_config(mode="done")
        job = self.seed(kind="claude_5h_reset")
        now = int(time.time())
        self.mutate(job["id"], lambda j: j["state"].update(
            basis={"source": "record", "resetEpoch": now - 120, "fetchedAtMs": None, "percent": 0.2},
            wait="reset", fireAt=now))
        later = now - 120 + 3600
        usage = {"nowMs": now * 1000,
                 "providers": [{"id": "claude", "name": "Claude", "source": "record", "readable": True,
                                "stale": False, "updatedAtMs": now * 1000,
                                "windows": [{"label": "Session (5-hour)", "kind": "session", "percent": 0.3,
                                             "resetsAt": later, "title": None, "bindable": True,
                                             "sliding": False}]}]}
        self.p.set(runner, "_read_usage", lambda: usage)
        self.assertEqual(self.run_verb(job["id"]), ["DEFERRED"])
        after = self.stored(job["id"])
        state = after["state"]
        self.assertEqual((state["status"], state["wait"], state["gen"], state["defers"], state["reason"]),
                         ("armed", "deferred", 2, 1, "window_later"))
        self.assertEqual(state["basis"]["resetEpoch"], later)
        self.assertEqual(self.armed, [(job["id"], 2, later + 120, 5400)])
        self.assertEqual(self.notified[0][11], "Deferred")
        self.assertEqual(self.agent_entries(), [])
        self.assertTrue(after["promptAvailable"])

    def test_liveness_busy_defer(self):
        session = str(uuid.uuid4())
        self.fake_config(mode="done", agents=[{"id": "a1", "kind": "interactive", "sessionId": session}])
        job = self.seed(mode="resume", session=session)
        self.assertEqual(self.run_verb(job["id"]), ["BUSY"])
        after = self.stored(job["id"])
        self.assertEqual((after["state"]["status"], after["state"]["busyDefers"]), ("armed", 1))
        self.assertEqual([e for e in self.agent_entries() if e["stdinBytes"]], [])

    def test_cli_changed_note(self):
        self.fake_config(mode="done")
        versions = os.path.join(self.home, ".local/share/mise/installs/claude")
        os.makedirs(os.path.join(versions, "9.9.9"))
        new_real = os.path.join(versions, "9.9.9", "claude")
        shutil.copy(FAKE_AGENT, new_real)
        os.chmod(new_real, 0o755)
        os.symlink("9.9.9", os.path.join(versions, "latest"))
        link = os.path.join(versions, "latest", "claude")
        job = self.seed(link=link, real=os.path.join(versions, "1.0.0", "claude"))
        self.assertEqual(self.run_verb(job["id"]), ["FIRED", "OUTCOME_DONE", "FINAL"])
        record = self.run_record(job["id"], 1)
        self.assertEqual(record["cli"], {"exec": new_real, "changed": True,
                                         "from": os.path.join(versions, "1.0.0", "claude"), "to": new_real})
        self.assertTrue(self.notified[0][12].endswith("The Claude Code command changed since the job was armed."))
        self.assertEqual(self.stored(job["id"])["cli"]["real"], new_real)

    def test_cli_missing_final(self):
        self.fake_config(mode="done")
        link = self.place_cli("claude")
        job = self.seed(link=link)
        os.remove(link)
        self.assertEqual(self.run_verb(job["id"]), ["CLI_REFUSED"])
        after = self.stored(job["id"])
        self.assertEqual((after["state"]["status"], after["state"]["reason"], after["promptAvailable"]),
                         ("failed", "cli_missing", False))

    def test_missed_after_grace(self):
        job = self.seed()
        self.mutate(job["id"], lambda j: j["state"].update(fireAt=int(time.time()) - 1000))
        self.assertEqual(self.run_verb(job["id"]), ["MISSED"])
        after = self.stored(job["id"])
        self.assertEqual((after["state"]["status"], after["state"]["reason"]), ("missed", "late"))
        self.assertTrue(after["promptAvailable"])
        self.assertEqual(self.notified[0][11], "Missed")

    def test_other_harnesses_end_to_end(self):
        for name, mode, outcome, status in (("codex", "done", "DONE", "done"),
                                            ("opencode", "auth", "AUTH", "failed"),
                                            ("gemini", "gemini_rc55", "UNTRUSTED", "failed")):
            self.fake_config(mode=mode)
            job = self.seed(name=name, mode="new", level="unattended")
            self.assertEqual(self.run_verb(job["id"]), ["FIRED", "OUTCOME_" + outcome, "FINAL"], name)
            self.assertEqual(self.stored(job["id"])["state"]["status"], status)
        entries = [e for e in self.agent_entries() if e["stdinBytes"]]
        self.assertEqual([e["harness"] for e in entries], ["codex", "opencode", "gemini"])
        self.assertEqual(json.loads(entries[1]["env"]["OPENCODE_PERMISSION"]),
                         json.loads(edition.level("unattended")["harness"]["opencode"]["env"]["OPENCODE_PERMISSION"]))

    def test_stderr_codes_only(self):
        self.fake_config(mode="flood")
        job = self.seed()
        self.assertEqual(self.run_verb(job["id"]), ["FIRED", "OUTCOME_DONE", "FINAL"])
        for lines in self.stderr_lines:
            for line in lines:
                self.assertRegex(line, DIAG_RE)
        self.assertEqual(os.stat(os.path.join(self.home, ".local/state/omarchy", edition.STATE_DIR_NAME, "runs",
                                              job["id"] + "-g1.log")).st_mode & 0o777, 0o600)


# ================================================================================ v2: Cursor, Pi, paid usage (H6)

def joined(*parts):
    """A denylisted flag spelling, built by concatenation so this file never holds it whole."""
    return "".join(parts)


def sfeed(name, lines, **kwargs):
    """Stream state and the answer feed_line gave for every line."""
    state = classify.new_stream_state(name, None, **kwargs)
    answers = []
    for line in lines:
        raw = line if isinstance(line, bytes) else json.dumps(line).encode()
        answers.append(classify.feed_line(state, raw))
    return state, answers


def _walk_words(test, argv, exec_len, with_value, bare):
    """Every word after the executable is a known flag or the value right after one (no prompt word)."""
    words = argv[exec_len:]
    index = 0
    values = {}
    while index < len(words):
        word = words[index]
        if word in bare:
            index += 1
            continue
        test.assertIn(word, with_value, (word, argv))
        test.assertLess(index + 1, len(words), argv)
        values.setdefault(word, []).append(words[index + 1])
        index += 2
    return values


class V2HarnessTests(Sandbox):

    def test_build_command_golden_v2(self):
        cursor = make_job("cursor", mode="new", job_id="0123456789abcdef",
                          model="claude-opus-4-8[context=1m,effort=high]")
        cmd = harness.build_command(cursor, exec_prefix=["/opt/cursor-agent/cursor-agent"], run_dir="/s", gen=1)
        self.assertEqual(cmd["argv"], ["/opt/cursor-agent/cursor-agent", "-p", "--output-format", "stream-json",
                                       "--mode", "ask", "--workspace", "/home/u/proj",
                                       "--model", "claude-opus-4-8[context=1m,effort=high]"])
        self.assertEqual((cmd["levelSlot"], cmd["cwd"]), ([4, 6], "/home/u/proj"))
        resume = make_job("cursor", mode="resume", session=CURSOR_UUID)
        self.assertEqual(harness.build_command(resume, exec_prefix=["/c"], run_dir="/s", gen=1)["argv"][-2:],
                         ["--resume", CURSOR_UUID])

        pi_new = make_job("pi", mode="new", job_id="0123456789abcdef", provider="openai-codex", model="gpt-5.5",
                          new_session=PI_UUID)
        cmd = harness.build_command(pi_new, exec_prefix=["/p/pi"], run_dir="/s", gen=1)
        self.assertEqual(cmd["argv"], ["/p/pi", "--mode", "json", "--offline", "--no-extensions", "--no-skills",
                                       "--no-prompt-templates", "--no-themes", "--no-approve", "--tools",
                                       "read,grep,find,ls", "--provider", "openai-codex", "--model", "gpt-5.5",
                                       "--session-id", PI_UUID, "--name", "autopilot-01234567"])
        self.assertEqual(cmd["levelSlot"], [3, 11])
        self.assertEqual({k: cmd["env"][k] for k in ("PI_OFFLINE", "PI_TELEMETRY", "PI_SKIP_VERSION_CHECK")},
                         {"PI_OFFLINE": "1", "PI_TELEMETRY": "0", "PI_SKIP_VERSION_CHECK": "1"})
        path = pi_path(PI_UUID)
        for mode, flag in (("resume", "--session"), ("fork", "--fork")):
            job = make_job("pi", mode=mode, session=PI_UUID, session_path=path)
            self.assertEqual(harness.build_command(job, exec_prefix=["/p"], run_dir="/s", gen=1)["argv"][-2:],
                             [flag, path])
        run_sid = "0f0e0d0c-0b0a-4908-8706-050403020100"
        retry = make_job("pi", mode="fork", session=PI_UUID, session_path=path, run_sid=run_sid)
        self.assertEqual(harness.build_command(retry, exec_prefix=["/p"], run_dir="/s", gen=2)["argv"][-2:],
                         ["--session", pi_path(run_sid)])
        self.assertEqual(harness.effective_session_path(retry), pi_path(run_sid))
        self.assertIsNone(harness.effective_session_path(make_job("claude")))

        claude = make_job("claude", mode="new", job_id="0123456789abcdef", allow_paid=True,
                          new_session="11111111-2222-4333-8444-555555555555")
        argv = harness.build_command(claude, exec_prefix=["/c"], run_dir="/s", gen=1)["argv"]
        index = argv.index("--max-turns")
        self.assertEqual(argv[index:index + 4], ["--max-turns", "15", "--max-budget-usd", "5"])
        claude["allowPaid"] = False
        self.assertNotIn("--max-budget-usd", harness.build_command(claude, exec_prefix=["/c"], run_dir="/s", gen=1)["argv"])
        opencode = make_job("opencode", mode="new", job_id="0123456789abcdef")
        self.assertEqual(harness.build_command(opencode, exec_prefix=["/o"], run_dir="/s", gen=1)["argv"][-2:],
                         ["--title", "autopilot-01234567"])

        def refused(job, code, field=None):
            with self.assertRaises(ApError) as ctx:
                harness.build_command(job, exec_prefix=["/x"], run_dir="/s", gen=1)
            self.assertEqual((ctx.exception.code, ctx.exception.field), (code, field))

        refused(make_job("cursor", mode="new", model="auto"), "invalid_model", "model")
        refused(make_job("cursor", mode="fork"), "fork_unsupported", "target.mode")
        refused(make_job("cursor", level="unattended", mode="new"), "level_unavailable", "level")
        refused(make_job("pi", level="unattended", mode="new"), "level_unavailable", "level")
        no_provider = make_job("pi", mode="new")
        no_provider["provider"] = None
        refused(no_provider, "pi_model_required", "provider")
        no_model = make_job("pi", mode="new")
        no_model["model"] = None
        refused(no_model, "pi_model_required", "model")
        for bad in (self.home + "/elsewhere/x.jsonl", pi_path(PI_UUID) + ".bak", "relative.jsonl",
                    self.home + "/.pi/agent/sessions/--a--/../../../x.jsonl", self.home + "/.pi/agent/sessions/x.jsonl"):
            refused(make_job("pi", mode="resume", session=PI_UUID, session_path=bad), "invalid_session",
                    "target.sessionPath")

    def test_cursor_argv_forbidden_flags_absent(self):
        forbidden = {"-f", joined("--fo", "rce"), joined("--yo", "lo"), joined("--tru", "st"),
                     joined("--auto-", "review"), joined("--approve-", "mcps"), "--api-key", "--auth-token",
                     "--continue", "--list-models", "--print"}
        with_value = {"--output-format", "--mode", "--sandbox", "--workspace", "--model", "--resume"}
        for mode in ("new", "resume"):
            for model in (None, "gpt-5.5", "claude-opus-4-8[context=1m]"):
                job = make_job("cursor", mode=mode, model=model)
                argv = harness.build_command(job, exec_prefix=["/opt/cursor"], run_dir="/s", gen=1)["argv"]
                self.assertFalse(forbidden & set(argv), argv)
                values = _walk_words(self, argv, 1, with_value, {"-p"})
                self.assertEqual(values["--mode"], ["ask"])
                # Cursor's sandbox cannot start inside the job's unit, so the flag is never passed.
                self.assertNotIn("--sandbox", values)
                self.assertEqual(values["--output-format"], ["stream-json"])
                self.assertEqual(values["--workspace"], [job["target"]["cwd"]])
                self.assertNotIn("auto", values.get("--model", []))
                for sid in values.get("--resume", []):
                    self.assertRegex(sid, consts.UUID_RE)
                self.assertEqual("--resume" in values, mode == "resume")
        preview = harness.display_argv("cursor", harness.build_command(
            make_job("cursor", mode="new"), exec_prefix=["/opt/cursor"], run_dir="/s", gen=1)["argv"], 1)
        self.assertEqual(preview, "cursor-agent -p --output-format stream-json --mode ask "
                                  "--workspace /home/u/proj <stdin>")

    def test_pi_argv_forbidden_flags_absent(self):
        forbidden = {"-p", "-a", joined("--appr", "ove"), "-e", "--extension", "--skill", "--prompt-template",
                     "--api-key", "--system-prompt", "--append-system-prompt", "--continue", "--resume",
                     "--thinking", "--print", "--no-session"}
        with_value = {"--mode", "--tools", "--provider", "--model", "--session-id", "--name", "--session", "--fork"}
        bare = {"--offline", "--no-extensions", "--no-skills", "--no-prompt-templates", "--no-themes", "--no-approve"}
        for mode in ("new", "resume", "fork", "retry"):
            if mode == "retry":
                job = make_job("pi", mode="new", run_sid=str(uuid.uuid4()))
            else:
                job = make_job("pi", mode=mode, model="claude-opus-5[1m]", provider="anthropic")
            argv = harness.build_command(job, exec_prefix=["/opt/pi"], run_dir="/s", gen=1)["argv"]
            self.assertFalse(forbidden & set(argv), argv)
            self.assertFalse([w for w in argv if w.startswith("@")], argv)
            values = _walk_words(self, argv, 1, with_value, bare)
            self.assertEqual(values["--mode"], ["json"])
            self.assertEqual(len(values["--tools"]), 1)
            self.assertTrue(set(values["--tools"][0].split(",")) <= set(consts.PI_TOOLS), values["--tools"])
            self.assertEqual(values["--provider"], [job["provider"]])
            self.assertEqual(len([k for k in ("--session-id", "--session", "--fork") if k in values]), 1)
            for key in ("--session", "--fork"):
                for value in values.get(key, []):
                    self.assertTrue(harness.pi_session_path_ok(value), value)

    def test_agent_env_cursor_pi_allowlist(self):
        os.environ.update({"CURSOR_API_KEY": "k", "CURSOR_AUTH_TOKEN": "t", "CURSOR_API_ENDPOINT": "https://x",
                           "CURSOR_CONFIG_DIR": "/x", "CURSOR_DATA_DIR": "/y", joined("AGENT_CLI_", "E2E"): "1",
                           "NODE_OPTIONS": "--require /x", "OPENAI_API_KEY": "k2", "ANTHROPIC_API_KEY": "k3",
                           "PI_CODING_AGENT_DIR": "/z", "PI_PACKAGE_DIR": "/w", "PI_CODING_AGENT_SESSION_DIR": "/v",
                           "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1/bus",
                           "XDG_CONFIG_HOME": self.home + "/.config", "XDG_DATA_HOME": "relative/data",
                           "XDG_STATE_HOME": self.home + "/.local/state", "XDG_CACHE_HOME": self.home + "/.cache",
                           "LANG": "pl_PL.UTF-8", "LOGNAME": "tester", "WAYLAND_DISPLAY": "wayland-1"})
        self.assertEqual(harness.agent_env("cursor", "plan"), {
            "HOME": self.home, "USER": "tester", "LOGNAME": "tester", "XDG_RUNTIME_DIR": self.runtime,
            "XDG_CONFIG_HOME": self.home + "/.config", "LANG": "pl_PL.UTF-8", "TERM": "dumb", "NO_COLOR": "1",
            "PATH": "/usr/bin:/bin"})
        self.assertEqual(harness.agent_env("pi", "plan"), {
            "HOME": self.home, "PATH": "/usr/bin:/bin", "LANG": "pl_PL.UTF-8", "TERM": "dumb", "PI_OFFLINE": "1",
            "PI_TELEMETRY": "0", "PI_SKIP_VERSION_CHECK": "1"})
        os.environ["XDG_CONFIG_HOME"] = "relative/config"
        os.environ["XDG_DATA_HOME"] = self.home + "/.local/share"
        env = harness.agent_env("cursor", "plan")
        self.assertNotIn("XDG_CONFIG_HOME", env)
        self.assertEqual(env["XDG_DATA_HOME"], self.home + "/.local/share")
        os.environ["LANG"] = "en_US.UTF-8; rm"
        self.assertEqual(harness.agent_env("pi", "plan")["LANG"], "C.UTF-8")
        for name in ("cursor", "pi"):
            with self.assertRaises(ApError) as ctx:
                harness.agent_env(name, "unattended")
            self.assertEqual(ctx.exception.code, "level_unavailable")

    def test_discovery_cursor_pi_realpath_not_shim(self):
        self.assertEqual(ORIGINAL_CANDIDATES["cursor"], ["/usr/bin/cursor-agent", "~/.local/bin/agent"])
        self.assertEqual(ORIGINAL_CANDIDATES["pi"], ["~/.local/share/mise/installs/pi/latest/pi/pi"])
        versions = os.path.join(self.home, ".local/share/cursor-agent/versions/2026.09.10-fd3934a")
        os.makedirs(versions)
        cursor_real = os.path.join(versions, "cursor-agent")
        shutil.copy(FAKE_AGENT, cursor_real)
        os.chmod(cursor_real, 0o755)
        os.makedirs(os.path.join(self.home, ".local/bin"), exist_ok=True)
        os.symlink(cursor_real, os.path.join(self.home, ".local/bin/agent"))
        pi_dir = os.path.join(self.home, ".local/share/mise/installs/pi/0.85.1/pi")
        os.makedirs(pi_dir)
        pi_real = os.path.join(pi_dir, "pi")
        shutil.copy(FAKE_AGENT, pi_real)
        os.chmod(pi_real, 0o755)
        os.symlink("0.85.1", os.path.join(self.home, ".local/share/mise/installs/pi/latest"))
        self.p.set(consts, "CLI_CANDIDATES", dict(consts.CLI_CANDIDATES, cursor=[
            os.path.join(self.tmp, "no-such", "cursor-agent"), "~/.local/bin/agent"], pi=ORIGINAL_CANDIDATES["pi"]))
        found = identity.discover_cli("cursor")
        self.assertEqual((found["ok"], found["link"], found["real"], found["exec"]),
                         (True, os.path.join(self.home, ".local/bin/agent"), cursor_real, [cursor_real]))
        found = identity.discover_cli("pi")
        link = os.path.join(self.home, ".local/share/mise/installs/pi/latest/pi/pi")
        self.assertEqual((found["ok"], found["link"], found["real"], found["exec"]), (True, link, pi_real, [pi_real]))
        fire = identity.resolve_for_fire(make_job("pi", mode="new", link=link, real=pi_real))
        self.assertEqual((fire["exec"], fire["changed"]), ([pi_real], False))
        shims = os.path.join(self.home, consts.MISE_SHIMS_REL)
        os.makedirs(shims)
        shutil.copy(FAKE_AGENT, os.path.join(shims, "pi"))
        os.symlink(os.path.join(shims, "pi"), os.path.join(self.home, ".local/bin/pi-shim"))
        self.p.set(consts, "CLI_CANDIDATES", dict(consts.CLI_CANDIDATES, pi=["~/.local/bin/pi-shim"]))
        found = identity.discover_cli("pi")
        self.assertEqual((found["ok"], found["reason"], found["exec"]), (False, "shim", []))

    def test_resume_display_cursor_pi(self):
        cursor = make_job("cursor", mode="resume", session=CURSOR_UUID, cwd="/home/u/my proj")
        self.assertEqual(harness.resume_display(cursor), "cd '/home/u/my proj' && cursor-agent --resume " + CURSOR_UUID)
        path = pi_path(PI_UUID)
        pi_job = make_job("pi", mode="resume", session=PI_UUID, session_path=path)
        self.assertEqual(shlex.split(harness.resume_display(pi_job)), ["cd", "/home/u/proj", "&&", "pi", "--session", path])
        fresh = make_job("pi", mode="new")
        with self.assertRaises(ApError) as ctx:
            harness.resume_display(fresh)
        self.assertEqual(ctx.exception.code, "bad_status")
        ran = make_job("pi", mode="new", run_sid=PI_UUID)
        self.assertTrue(harness.resume_display(ran).endswith("pi --session " + pi_path(PI_UUID)))
        outside = make_job("pi", mode="resume", session=PI_UUID, session_path="/tmp/x_" + PI_UUID + ".jsonl")
        with self.assertRaises(ApError) as ctx:
            harness.resume_display(outside)
        self.assertEqual(ctx.exception.code, "bad_status")
        fresh_cursor = make_job("cursor", mode="new")
        with self.assertRaises(ApError):
            harness.resume_display(fresh_cursor)


class V2StreamTests(SuperviseBase):
    NOW = utc(2026, 9, 14, 13, 27, 51)

    def test_claude_api_key_source_kill_off_ignored_on(self):
        out = self.run_stub("claude", "claude_api_key_source")
        self.assertEqual(out["run"]["killedBy"], "paid")
        self.assertLess(out["elapsed"], 10)
        result = classify.classify(out["state"], out["run"], level_id="plan")
        self.assertEqual((result["outcome"], result["detail"], result["reason"]), ("failed", "api_key_source",
                                                                                   "paid_blocked"))
        self.assertEqual(trigger.postrun(out["job"], result, int(time.time()), None)["reason"], "paid_blocked")
        init = {"type": "system", "subtype": "init", "session_id": PI_UUID, "permissionMode": "plan"}
        for source, allow, answer in (("ANTHROPIC_API_KEY", False, "kill_paid"), ("ANTHROPIC_API_KEY", True, None),
                                      (None, False, "kill_paid"), (None, True, None), ("none", False, None),
                                      ("apiKeyHelper", False, "kill_paid")):
            line = dict(init, apiKeySource=source) if source else init
            _state, answers = sfeed("claude", [line], allow_paid=allow)
            self.assertEqual(answers, [answer], (source, allow))

    def test_claude_overage_kill_rearm_off_allowed_on(self):
        resets = int(time.time()) + 5400
        out = self.run_stub("claude", "claude_overage_event", resetsAt=resets)
        self.assertEqual(out["run"]["killedBy"], "overage")
        now = int(time.time())
        result = classify.classify(out["state"], out["run"], level_id="plan", now=now)
        self.assertEqual((result["outcome"], result["reason"]), ("limit", "overage_blocked"))
        self.assertEqual(result["limit"], {"kind": "session", "resetEpoch": resets, "source": "event",
                                           "isUsingOverage": True, "rearm": True})
        action = trigger.postrun(out["job"], result, now, None)
        self.assertEqual((action["status"], action["wait"], action["fireAt"], action["reason"], action["counter"]),
                         ("armed", "limit", resets + 120, "overage_blocked", "limitRetries"))
        out = self.run_stub("claude", "claude_overage_event", allow_paid=True, resetsAt=resets, afterGuardSleep=0.1)
        self.assertIsNone(out["run"]["killedBy"])
        self.assertEqual(classify.classify(out["state"], out["run"], level_id="plan")["outcome"], "done")
        rejected = {"outcome": "limit", "detail": "rate_limit_event", "sessionId": None,
                    "limit": {"kind": "session", "resetEpoch": resets, "source": "event", "isUsingOverage": True,
                              "rearm": True}}
        self.assertEqual(trigger.postrun(out["job"], rejected, now, None)["reason"], "overage")

    def test_cursor_classify_table(self):
        now = self.NOW
        init = {"type": "system", "subtype": "init", "apiKeySource": "login", "session_id": CURSOR_UUID,
                "permissionMode": "default"}
        done = {"type": "result", "subtype": "success", "is_error": False, "result": "OK", "session_id": CURSOR_UUID}

        def check(lines, run, outcome, detail=None, level="plan"):
            state, _answers = sfeed("cursor", lines)
            got = classify.classify(state, run, level_id=level, now=now)
            self.assertEqual(got["outcome"], outcome, (lines, run, got))
            if detail is not None:
                self.assertEqual(got["detail"], detail, got)
            return got

        self.assertEqual(check([init, done], run_result(rc=0), "done", "result")["sessionId"], CURSOR_UUID)
        check([init], run_result(rc=0), "failed", "no_result")
        check([init], run_result(rc=143), "interrupted")
        check([init], run_result(rc=130), "interrupted")
        check([init], run_result(rc=None, killed="deadline"), "timeout")
        check([], run_result(rc=1, stderr=b"Error: Workspace Trust Required"), "untrusted")
        check([], run_result(rc=1, stderr=b"Authentication required. Please run 'cursor-agent login' first"), "auth")
        check([], run_result(rc=1, stderr=b"Not logged in"), "auth")
        got = check([init], run_result(rc=1, stderr=b"You've hit your usage limit. Your monthly cycle ends on "
                                                     b"10/9/2026."), "limit", "monthly_limit")
        self.assertEqual(got["limit"], {"kind": "monthly", "resetEpoch": trigger.local_date_epoch(2026, 10, 9),
                                        "source": "stderr", "isUsingOverage": False, "rearm": False})
        self.assertEqual(datetime.datetime.fromtimestamp(got["limit"]["resetEpoch"]).timetuple()[:5],
                         (2026, 10, 9, 0, 0))
        self.assertIsNone(check([], run_result(rc=1, stderr=b"You are out of usage for now"), "limit")["limit"]["resetEpoch"])
        for text in (b"High Load, try again", b"Rate limited by model provider", b"connect ECONNREFUSED 1.2.3.4:443"):
            check([], run_result(rc=1, stderr=text), "transient")
        check([], run_result(rc=1, stderr=b"Chat 5b0a may still be running"), "busy")
        check([], run_result(rc=1, stderr=b"boom"), "failed", "unclassified")
        for source in ("env", "flag", None):
            line = dict(init, apiKeySource=source) if source else {k: v for k, v in init.items() if k != "apiKeySource"}
            for allow in (False, True):
                state, answers = sfeed("cursor", [line], allow_paid=allow)
                self.assertEqual(answers, ["kill"], (source, allow))
                got = classify.classify(state, run_result(rc=None, killed="boundary"), level_id="plan", now=now)
                self.assertEqual((got["outcome"], got["detail"]), ("boundary_mismatch", "api_key_source"))
        write = {"type": "tool_call", "subtype": "completed",
                 "tool_call": {"writeToolCall": {"args": {"path": "a"}, "result": {"success": {"lines": 1}}}}}
        check([init, write, done], run_result(rc=0), "boundary_mismatch", "tool_violation")
        rejected = {"type": "tool_call", "subtype": "completed",
                    "tool_call": {"editToolCall": {"args": {}, "result": {"rejected": {}}}}}
        started = {"type": "tool_call", "subtype": "started", "tool_call": {"deleteToolCall": {"args": {}}}}
        check([init, rejected, started, done], run_result(rc=0), "done")
        _state, answers = sfeed("cursor", [{"type": "user", "message": {}},
                                           {"type": "interaction_query", "subtype": "request"}, init])
        self.assertEqual(answers, ["drop", "drop", None])
        limit = check([init], run_result(rc=1, stderr=b"You've hit your usage limit"), "limit")
        self.assertEqual(trigger.postrun(make_job("cursor", mode="new", now=now), limit, now, None),
                         {"status": "limit", "wait": None, "fireAt": None, "reason": "monthly_limit", "counter": None,
                          "notify": "limit_final"})

    def test_cursor_log_drops_prompt_echo(self):
        canary = ("CANARY-" + uuid.uuid4().hex).encode()
        for mode in ("cursor_done", "cursor_user_echo"):
            out = self.run_stub("cursor", mode, prompt=b"Please review " + canary)
            entry = [e for e in self.agent_entries() if e["stdinBytes"]][-1]
            self.assertEqual(entry["stdinSha256"], hashlib.sha256(b"Please review " + canary).hexdigest())
            self.assertNotIn(canary.decode(), json.dumps([entry["argv"], entry["env"]]))
            self.assertNotIn(canary, out["log"])
            self.assertNotIn(b'"type": "user"', out["log"])
            self.assertNotIn(b"interaction_query", out["log"])
            self.assertIn(b'"type": "result"', out["log"])
            self.assertEqual(classify.classify(out["state"], out["run"], level_id="plan")["outcome"], "done")
        out = self.run_stub("cursor", "cursor_write_success")
        self.assertEqual(classify.classify(out["state"], out["run"], level_id="plan")["outcome"], "boundary_mismatch")
        out = self.run_stub("cursor", "cursor_api_key_env")
        self.assertEqual(out["run"]["killedBy"], "boundary")
        self.assertLess(out["elapsed"], 10)
        for mode, outcome in (("cursor_untrusted", "untrusted"), ("cursor_limit_monthly", "limit"),
                              ("cursor_sandbox", "failed")):
            out = self.run_stub("cursor", mode)
            self.assertEqual(classify.classify(out["state"], out["run"], level_id="plan")["outcome"], outcome)
        # A Cursor config that turns the sandbox on cannot start inside the job's unit: final, no retry.
        out = self.run_stub("cursor", "cursor_sandbox")
        self.assertEqual(classify.classify(out["state"], out["run"], level_id="plan")["detail"], "cursor_sandbox")

    def test_pi_classify_table(self):
        now = self.NOW
        header = {"type": "session", "version": 3, "id": PI_UUID, "timestamp": "2026-09-15T14:18:58.355Z",
                  "cwd": "/home/u/proj"}

        def end(stop, error=None, provider="openai-codex"):
            message = {"role": "assistant", "provider": provider, "model": "gpt-5.5", "stopReason": stop,
                       "content": []}
            if error is not None:
                message["errorMessage"] = error
            return {"type": "message_end", "message": message}

        def check(lines, run, outcome, detail=None):
            state, _answers = sfeed("pi", lines, provider="openai-codex")
            got = classify.classify(state, run, level_id="plan", now=now)
            self.assertEqual(got["outcome"], outcome, (lines, run, got))
            if detail is not None:
                self.assertEqual(got["detail"], detail, got)
            return got

        got = check([header, end("stop")], run_result(rc=0), "done", "stop_reason")
        expected_path = os.path.join(self.home, ".pi/agent/sessions/--home-u-proj--",
                                     "2026-09-15T14-18-58-355Z_" + PI_UUID + ".jsonl")
        self.assertEqual((got["sessionId"], got["sessionPath"]), (PI_UUID, expected_path))
        if hasattr(sessions, "pi_session_path"):
            self.assertEqual(sessions.pi_session_path("/home/u/proj", header["timestamp"], PI_UUID, self.home),
                             expected_path)
        check([header, end("length")], run_result(rc=0), "done")
        check([header, end("aborted")], run_result(rc=0), "interrupted", "aborted")
        got = check([header, end("error", "You have hit your ChatGPT usage limit (plus plan). Try again in ~42 min.")],
                    run_result(rc=0), "limit", "codex_limit")
        self.assertEqual((got["limit"]["resetEpoch"], got["limit"]["source"], got["limit"]["rearm"]),
                         (now + 42 * 60, "event", True))
        got = check([header, end("error", '429 {"error":{"type":"usage_limit_reached","resets_at":%d}}' % (now + 999))],
                    run_result(rc=0), "limit")
        self.assertEqual(got["limit"]["resetEpoch"], now + 999)
        got = check([header, end("error", 'usage_limit_reached {\\"resets_in_seconds\\": 2520}')], run_result(rc=0),
                    "limit")
        self.assertEqual(got["limit"]["resetEpoch"], now + 2520)
        for text in ("You're out of extra usage. Add more at claude.ai.", "This would draw from your extra usage"):
            got = check([header, end("error", text)], run_result(rc=0), "failed", "extra_usage")
            self.assertEqual(got["reason"], "paid_blocked")
        for text in ("insufficient_quota: check your plan", "Monthly usage limit reached", "available balance too low",
                     "GoUsageLimitError", "FreeUsageLimitError", "quota exceeded"):
            got = check([header, end("error", text)], run_result(rc=0), "limit", "quota_final")
            self.assertIs(got["limit"]["rearm"], False)
        retry_failed = {"type": "auto_retry_end", "success": False, "attempt": 2}
        check([header, end("error", '429: {"message":"Rate limit reached"}'), retry_failed], run_result(rc=0),
              "transient", "rate_limited")
        check([header, end("error", '429: {"message":"Rate limit reached"}')], run_result(rc=0), "failed",
              "unclassified")
        check([header, end("error", "something else broke")], run_result(rc=0), "failed", "unclassified")
        check([header], run_result(rc=1, stderr=b"No API key found for openai-codex"), "auth")
        check([header], run_result(rc=1, stderr=b"No models available."), "auth")
        check([header], run_result(rc=143), "interrupted")
        check([header], run_result(rc=129), "interrupted")
        check([header], run_result(rc=0), "failed", "no_result")
        check([header], run_result(rc=None, killed="deadline"), "timeout")

        state, answers = sfeed("pi", [{"type": "agent_start"}, header], provider="openai-codex")
        self.assertEqual(answers, ["kill", None])
        got = classify.classify(state, run_result(rc=None, killed="boundary"), level_id="plan", now=now)
        self.assertEqual((got["outcome"], got["detail"]), ("boundary_mismatch", "no_header"))
        _state, answers = sfeed("pi", [b"not json", header], provider="openai-codex")
        self.assertEqual(answers[0], "kill")
        state, answers = sfeed("pi", [header, end("stop", provider="anthropic")], provider="openai-codex")
        self.assertEqual(answers, [None, "kill"])
        got = classify.classify(state, run_result(rc=0, killed="boundary"), level_id="plan", now=now)
        self.assertEqual((got["outcome"], got["detail"]), ("boundary_mismatch", "provider_mismatch"))
        tools = [{"type": "tool_execution_start", "toolName": name} for name in ("read", "grep", "find", "ls", "bash")]
        _state, answers = sfeed("pi", [header] + tools, provider="openai-codex")
        self.assertEqual(answers, [None, None, None, None, None, "kill"])
        # Each level's own --tools list decides: Auto adds edit and write, Full access adds bash.
        edits = [{"type": "tool_execution_start", "toolName": name} for name in ("edit", "write", "bash")]
        _state, answers = sfeed("pi", [header] + edits, provider="openai-codex", level_id="auto")
        self.assertEqual(answers, [None, None, None, "kill"])
        _state, answers = sfeed("pi", [header] + edits, provider="openai-codex", level_id="full")
        self.assertEqual(answers, [None, None, None, None])
        _state, answers = sfeed("pi", [header] + edits[:1], provider="openai-codex", level_id="unattended")
        self.assertEqual(answers, [None, "kill"])
        user = {"role": "user", "content": [{"type": "text", "text": "PROMPT"}]}
        _state, answers = sfeed("pi", [header, {"type": "message_start", "message": user},
                                       {"type": "message_end", "message": user},
                                       {"type": "message_update", "assistantMessageEvent": {"type": "text_delta"}},
                                       {"type": "agent_end", "messages": [user]}, {"type": "agent_settled"}],
                                provider="openai-codex")
        self.assertEqual(answers, [None, "drop", "drop", "drop", "drop", None])

    def test_pi_exit_zero_alone_not_success(self):
        out = self.run_stub("pi", "pi_exit0_error", prompt=b"Summarize the repository.")
        self.assertEqual((out["run"]["rc"], out["run"]["killedBy"]), (0, None))
        result = classify.classify(out["state"], out["run"], level_id="plan")
        self.assertEqual((result["outcome"], result["detail"]), ("failed", "no_result"))
        self.assertEqual(trigger.postrun(out["job"], result, int(time.time()), None)["status"], "failed")
        self.assertNotIn(b"Summarize the repository.", out["log"])
        for mode, outcome in (("pi_error_extra_usage", "failed"), ("pi_error_codex_limit", "limit"),
                              ("pi_quota", "limit"), ("pi_done", "done")):
            out = self.run_stub("pi", mode, prompt=b"Summarize the repository.")
            self.assertEqual(out["run"]["rc"], 0, mode)
            self.assertEqual(classify.classify(out["state"], out["run"], level_id="plan")["outcome"], outcome, mode)
            self.assertNotIn(b"Summarize the repository.", out["log"], mode)
        for mode in ("pi_no_header", "pi_provider_mismatch", "pi_tool_bash"):
            out = self.run_stub("pi", mode)
            self.assertEqual(out["run"]["killedBy"], "boundary", mode)
            self.assertLess(out["elapsed"], 10, mode)
            self.assertEqual(classify.classify(out["state"], out["run"], level_id="plan")["outcome"],
                             "boundary_mismatch", mode)

    def test_opencode_zen_error_types(self):
        now = self.NOW
        midnight = utc(2026, 9, 15)

        def api(status, error_type, retry, metadata=None, body=None):
            raw = body if body is not None else json.dumps({
                "type": "error", "error": {"type": error_type, "message": "Rate limit exceeded. Please try again later."},
                "metadata": metadata or {}})
            return {"type": "error", "sessionID": "ses_abcdefgh1234", "error": {"name": "APIError", "data": {
                "message": "Rate limit exceeded. Please try again later.", "statusCode": status, "isRetryable": True,
                "responseHeaders": {"retry-after": str(retry)}, "responseBody": raw}}}

        def check(line, outcome, detail):
            state, _answers = sfeed("opencode", [{"type": "step_start", "sessionID": "ses_abcdefgh1234"}, line])
            got = classify.classify(state, run_result(rc=1), level_id="plan", now=now)
            self.assertEqual((got["outcome"], got["detail"]), (outcome, detail), got)
            return got

        got = check(api(429, "FreeUsageLimitError", 3600), "limit", "zen_free_limit")
        self.assertEqual(got["limit"], {"kind": "daily", "resetEpoch": now + 3600, "source": "retry_after",
                                        "isUsingOverage": False, "rearm": True})
        for retry in (100000, 0, "soon"):
            self.assertEqual(check(api(429, "FreeUsageLimitError", retry), "limit", "zen_free_limit")["limit"]["resetEpoch"],
                             midnight)
        for name, kind in (("5 hour", "session"), ("weekly", "weekly"), ("monthly", "monthly"), ("hourly", "other")):
            got = check(api(429, "GoUsageLimitError", 7200, {"workspace": "wrk_x", "limitName": name}), "limit", "go_limit")
            self.assertEqual((got["limit"]["kind"], got["limit"]["resetEpoch"], got["limit"]["source"]),
                             (kind, now + 7200, "retry_after"))
        got = check(api(429, "GoUsageLimitError", 41 * 86400, {"limitName": "monthly"}), "limit", "go_limit")
        self.assertIsNone(got["limit"]["resetEpoch"])
        check(api(429, "RateLimitError", 60), "transient", "rate_limit")
        for error_type in ("CreditsError", "MonthlyLimitError", "UserLimitError"):
            got = check(api(401, error_type, 0), "failed", "zen_billing")
            self.assertEqual(got["reason"], "zen_billing")
            self.assertEqual(trigger.postrun(make_job("opencode", now=now), got, now, None)["reason"], "zen_billing")
        check(api(429, None, 5, body="not json"), "limit", "usage_limit")
        big = json.dumps({"error": {"type": "FreeUsageLimitError"}, "pad": "x" * 70000})
        self.assertIsNone(check(api(429, None, 5, body=big), "limit", "usage_limit")["limit"]["resetEpoch"])
        check(api(500, "FreeUsageLimitError", 5), "limit", "usage_limit")
        for mode, outcome, detail in (("opencode_free_limit", "limit", "zen_free_limit"),
                                      ("opencode_go_limit", "limit", "go_limit"),
                                      ("opencode_credits", "failed", "zen_billing")):
            out = self.run_stub("opencode", mode, model="opencode/big-pickle")
            got = classify.classify(out["state"], out["run"], level_id="plan")
            self.assertEqual((got["outcome"], got["detail"]), (outcome, detail), mode)

    def test_opencode_first_event_watchdog(self):
        out = self.run_stub("opencode", "opencode_silent", first_line=1.5, model="opencode/big-pickle")
        self.assertEqual(out["run"]["killedBy"], "first_line")
        self.assertLess(out["elapsed"], 12)
        now = int(time.time())
        state = out["state"]
        silent_run = out["run"]
        state["billing"] = "zen_free"
        result = classify.classify(state, out["run"], level_id="plan", now=now)
        midnight = (now // 86400 + 1) * 86400
        self.assertEqual((result["outcome"], result["detail"], result["reason"]),
                         ("limit", "limit_suspected", "limit_suspected"))
        self.assertEqual((result["limit"]["resetEpoch"], result["limit"]["source"]), (midnight, "computed"))
        self.assertEqual(trigger.postrun(out["job"], result, now, None)["fireAt"],
                         midnight + consts.ZEN_FREE_REARM_EXTRA_S)
        state.update(billing="go", model="opencode-go/glm-5.2")
        result = classify.classify(state, out["run"], level_id="plan", now=now)
        self.assertEqual((result["outcome"], result["reason"], result["limit"]["resetEpoch"]),
                         ("limit", "limit_suspected", None))
        state.update(billing="other", model="anthropic/claude-opus-5")
        result = classify.classify(state, out["run"], level_id="plan", now=now)
        self.assertEqual((result["outcome"], result["detail"], result["reason"]), ("transient", "stalled", "stalled"))
        out = self.run_stub("opencode", "done", first_line=2, level="unattended")
        self.assertIsNone(out["run"]["killedBy"])
        self.assertEqual(classify.classify(out["state"], out["run"], level_id="unattended")["outcome"], "done")
        for model, expected in (("opencode/big-pickle", 180), ("opencode-go/glm-5.2", 180),
                                ("anthropic/claude-opus-5", 600), (None, 600)):
            self.assertEqual(runner.first_line_deadline(make_job("opencode", model=model)), expected)
        # An Agent-default job that the gate resolved to a Zen or Go model watches like that model.
        for billing, expected in (("zen_free", 180), ("zen_paid", 180), ("go", 180), ("other", 600),
                                  ("anthropic", 600), ("unknown", 600), (None, 600)):
            self.assertEqual(runner.first_line_deadline(make_job("opencode", model=None), billing), expected, billing)
        self.assertEqual(runner.first_line_deadline(make_job("opencode", model="openai/gpt-5"), "zen_free"), 600)
        state.update(billing="zen_free", model=None)
        result = classify.classify(state, silent_run, level_id="plan", now=now)
        self.assertEqual((result["outcome"], result["reason"], result["limit"]["resetEpoch"]),
                         ("limit", "limit_suspected", midnight))
        state.update(billing="go", model=None)
        result = classify.classify(state, silent_run, level_id="plan", now=now)
        self.assertEqual((result["outcome"], result["reason"], result["limit"]["resetEpoch"]),
                         ("limit", "limit_suspected", None))
        state.update(billing="other", model=None)
        self.assertEqual(classify.classify(state, silent_run, level_id="plan", now=now)["outcome"], "transient")
        self.assertIsNone(runner.first_line_deadline(make_job("claude")))
        self.assertIsNone(runner.first_line_deadline(make_job("pi", mode="new")))

    def test_supervise_first_line_deadline_and_drop(self):
        seen = []

        def dropping(line):
            seen.append(line)
            return "drop" if b'"type": "user"' in line else None

        out = self.run_stub("cursor", "cursor_done", prompt=b"hello there", on_line=dropping)
        self.assertGreaterEqual(len(seen), 5)
        self.assertNotIn(b'"type": "user"', out["log"])
        self.assertIn(b'"type": "result"', out["log"])
        for answer, killed in (("kill", "boundary"), ("kill_paid", "paid"), ("kill_overage", "overage")):
            out = self.run_stub("claude", "wrong_init_mode", on_line=lambda line, a=answer: a)
            self.assertEqual(out["run"]["killedBy"], killed)
            self.assertLess(out["elapsed"], 10)
        out = self.run_stub("claude", "flood")
        self.assertNotIn(b"x" * 4096, out["log"])
        self.assertGreater(out["run"]["bytesDropped"], 0)
        out = self.run_stub("codex", "sleep", first_line=1)
        self.assertEqual(out["run"]["killedBy"], "first_line")
        self.assertLess(out["elapsed"], 10)
        out = self.run_stub("claude", "term_ignoring_child", first_line=1, deadline=3)
        self.assertEqual(out["run"]["killedBy"], "deadline")

    @unittest.skipUnless(os.environ.get("AP4A_REAL_CLI") == "1" and os.path.exists("/usr/bin/opencode"),
                         "real OpenCode check runs only with AP4A_REAL_CLI=1")
    def test_real_opencode_fake_zen_server(self):
        """The recorded Zen free-limit line of a real OpenCode 1.18 run, against a local 429 server (no model)."""
        import http.server

        env = {"HOME": self.home, "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}
        import subprocess
        version = subprocess.run(["/usr/bin/opencode", "--version"], env=env, capture_output=True, timeout=30)
        if not version.stdout.decode("utf-8", "replace").strip().startswith("1.18."):
            self.skipTest("OpenCode is not 1.18.x")
        body = json.dumps({"type": "error", "error": {"type": "FreeUsageLimitError", "message": "Rate limit exceeded."},
                           "metadata": {}}).encode()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                self.rfile.read(int(self.headers.get("content-length") or 0))
                self.send_response(429)
                self.send_header("content-type", "application/json")
                self.send_header("retry-after", "1")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            for key, rel in (("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"),
                             ("XDG_CACHE_HOME", ".cache"), ("XDG_STATE_HOME", ".local/state")):
                os.makedirs(os.path.join(self.home, rel), exist_ok=True)
                os.environ[key] = os.path.join(self.home, rel)
            config_dir = os.path.join(self.home, ".config", "opencode")
            os.makedirs(config_dir, exist_ok=True)
            with open(os.path.join(config_dir, "opencode" + ".json"), "w") as handle:
                json.dump({"provider": {"opencode": {"options": {"baseURL": "http://127.0.0.1:%d/zen/v1"
                                                                            % server.server_port}}},
                           "small_model": "opencode/big-pickle", "share": "disabled", "autoupdate": False}, handle)
            cwd = self.project()
            job = make_job("opencode", mode="new", model="opencode/big-pickle", cwd=cwd, link="/usr/bin/opencode")
            cmd = harness.build_command(job, exec_prefix=["/usr/bin/opencode"], run_dir=self.tmp, gen=1)
            cmd["env"]["OPENCODE_DISABLE_MODELS_FETCH"] = "1"
            state = classify.new_stream_state("opencode", None, billing="zen_free")
            state["model"] = job["model"]
            run = REAL_RUN_AGENT(cmd, b"Say OK.", deadline_s=120, log_fd=None,
                                 on_stdout_line=lambda line: classify.feed_line(state, line), stop_event=None,
                                 first_line_deadline_s=90)
        finally:
            server.shutdown()
        result = classify.classify(state, run, level_id="plan")
        self.assertEqual((result["outcome"], result["detail"]), ("limit", "zen_free_limit"), (result, run["rc"]))


class V2TriggerTests(TriggerBase):

    def window(self, kind, resets, percent=0.2, label="Session (5-hour)", **over):
        return dict({"label": label, "kind": kind, "percent": percent, "resetsAt": resets, "title": None,
                     "bindable": True, "sliding": False}, **over)

    def test_compute_fire_at_new_kinds(self):
        now = self.NOW
        midnight = utc(2026, 9, 15)
        zen = make_job("opencode", kind="zen_free_reset", model="opencode/big-pickle", now=now)
        got = trigger.compute_fire_at(zen, now, None, billing="zen_free")
        self.assertEqual((got["fireAt"], got["wait"], got["basis"]),
                         (midnight + 120, "reset", {"source": "clock", "resetEpoch": midnight, "fetchedAtMs": None,
                                                    "percent": None}))
        self.assertEqual(self.code(lambda: trigger.compute_fire_at(zen, now, None)).code, "no_reset_data")
        for billing in ("zen_paid", "go", "anthropic", "other", "unknown"):
            self.assertEqual(self.code(lambda b=billing: trigger.compute_fire_at(zen, now, None, billing=b)).code,
                             "trigger_unsupported")
        zen["model"] = "opencode-go/glm-5.2"
        self.assertEqual(self.code(lambda: trigger.compute_fire_at(zen, now, None, billing="zen_free")).code,
                         "trigger_unsupported")

        go = make_job("opencode", kind="go_window_reset", model="opencode-go/glm-5.2", now=now)
        go_row = self.row("opencode-go", [self.window("session", now + 4000, 0.3),
                                          self.window("weekly", now + 90000, 1.0, label="Weekly"),
                                          self.window("session", now + 100, 0.0, sliding=True, bindable=False)])
        usage = {"nowMs": now * 1000, "providers": [go_row]}
        got = trigger.compute_fire_at(go, now, usage)
        self.assertEqual((got["fireAt"], got["basis"]["source"], got["basis"]["resetEpoch"]),
                         (now + 90120, "record", now + 90000))
        go_row["windows"][1]["percent"] = 0.5
        self.assertEqual(trigger.compute_fire_at(go, now, usage)["fireAt"], now + 4120)
        go_row["windows"] = [self.window("session", now + 100, 0.0, sliding=True, bindable=False)]
        self.assertEqual(self.code(trigger.compute_fire_at, go, now, usage).code, "no_reset_data")
        go["model"] = "opencode/big-pickle"
        self.assertEqual(self.code(trigger.compute_fire_at, go, now, usage).code, "trigger_unsupported")

        pi_job = make_job("pi", kind="codex_window_reset", now=now)
        codex_row = self.row("codex", [self.window("session", now + 9000, 0.4, label="5h window")])
        self.assertEqual(trigger.compute_fire_at(pi_job, now, {"providers": [codex_row]})["fireAt"], now + 9120)
        pi_job["provider"] = "anthropic"
        self.assertEqual(self.code(trigger.compute_fire_at, pi_job, now, {"providers": [codex_row]}).code,
                         "trigger_unsupported")

        gem = make_job("gemini", kind="gemini_daily_reset", now=now)
        self.assertEqual(trigger.compute_fire_at(gem, now, None)["fireAt"], utc(2026, 9, 15, 7, 0) + 120)
        gemini_row = self.row("gemini", [self.window("daily", now + 5000, 0.9, label="Daily")])
        if hasattr(usage_module(), "gemini_daily_reset"):
            self.assertEqual(trigger.compute_fire_at(gem, now, {"providers": [gemini_row]})["fireAt"], now + 5120)

        legacy = make_job("opencode", kind="claude_5h_reset", now=now)
        self.assertEqual(self.code(trigger.compute_fire_at, legacy, now,
                                   self.usage([self.session(now + 600)])).code, "trigger_unsupported")
        self.assertEqual(trigger.prefire(legacy, now, self.usage([self.session(now + 600, 1.0)]))["action"], "fire")
        for kind in consts.RESET_KINDS:
            self.assertEqual(self.code(trigger.compute_fire_at, make_job("cursor", kind=kind, now=now), now, None).code,
                             "trigger_unsupported")

    def test_postrun_v2_rules(self):
        now = self.NOW
        job = make_job("claude", now=now)

        def result(outcome, detail, limit=None, reason=None):
            out = {"outcome": outcome, "detail": detail, "sessionId": None, "limit": limit}
            if reason is not None:
                out["reason"] = reason
            return out

        def limit(kind="session", reset=None, source="event", overage=False, rearm=True):
            return {"kind": kind, "resetEpoch": reset, "source": source, "isUsingOverage": overage, "rearm": rearm}

        self.assertEqual(trigger.postrun(job, result("interrupted", "aborted"), now, None),
                         {"status": "interrupted", "wait": None, "fireAt": None, "reason": "interrupted",
                          "counter": None, "notify": "interrupted"})
        got = trigger.postrun(job, result("failed", "unclassified", reason="paid_blocked"), now, None)
        self.assertEqual((got["status"], got["reason"], got["notify"]), ("failed", "paid_blocked", "failed"))
        for detail in ("monthly_limit", "quota_final"):
            got = trigger.postrun(job, result("limit", detail, limit(reset=now + 999, rearm=False)), now, None)
            self.assertEqual((got["status"], got["reason"], got["notify"]), ("limit", detail, "limit_final"))
        blocked = result("limit", "overage_blocked", limit(reset=now + 3000, overage=True), reason="overage_blocked")
        self.assertEqual(trigger.postrun(job, blocked, now, self.usage([])),
                         {"status": "armed", "wait": "limit", "fireAt": now + 3120, "reason": "overage_blocked",
                          "counter": "limitRetries", "notify": "limit_rearmed"})
        self.assertEqual(trigger.postrun(job, result("limit", "rate_limit_event", limit(reset=now + 3000, overage=True)),
                                         now, None)["reason"], "overage")
        weekly = self.weekly(now + 3 * 86400)
        opencode = make_job("opencode", now=now)
        plain = result("limit", "usage_limit", limit(reset=now + 3000, source="retry_after"))
        self.assertEqual(trigger.postrun(opencode, plain, now, self.usage([weekly]))["fireAt"], now + 3120)
        self.assertEqual(trigger.postrun(job, plain, now, self.usage([weekly]))["fireAt"], now + 3 * 86400 + 120)

        midnight = utc(2026, 9, 15)
        zen = make_job("opencode", model="opencode/big-pickle", now=now)
        suspected = result("limit", "limit_suspected", limit("daily", midnight, "computed"), reason="limit_suspected")
        got = trigger.postrun(zen, suspected, now, None)
        self.assertEqual((got["fireAt"], got["reason"], got["wait"]), (midnight + 120, "limit_suspected", "limit"))
        go = make_job("opencode", model="opencode-go/glm-5.2", now=now)
        go_suspected = result("limit", "limit_suspected", limit("other", None, "backoff"), reason="limit_suspected")
        go_usage = {"providers": [self.row("opencode-go", [{"label": "Session (5-hour)", "kind": "session",
                                                            "percent": 1.0, "resetsAt": now + 5000, "title": None}])]}
        self.assertEqual(trigger.postrun(go, go_suspected, now, go_usage)["fireAt"], now + 5120)
        self.assertEqual(trigger.postrun(go, go_suspected, now, None)["fireAt"], now + consts.LIMIT_SUSPECTED_BACKOFF_S)
        paid_zen = make_job("opencode", model="opencode/gpt-5.5", now=now)
        self.assertEqual(trigger.postrun(paid_zen, go_suspected, now, None)["fireAt"],
                         now + consts.LIMIT_SUSPECTED_BACKOFF_S)

        pi_job = make_job("pi", now=now)
        pi_job["trigger"]["marginSec"] = 60
        at = result("limit", "codex_limit", limit("session", now + 1000))
        self.assertEqual(trigger.postrun(pi_job, at, now, None)["fireAt"], now + 1090)
        pi_job["provider"] = "xai"
        self.assertEqual(trigger.postrun(pi_job, at, now, None)["fireAt"], now + 1060)
        got = trigger.postrun(job, result("transient", "stalled", reason="stalled"), now, None)
        self.assertEqual((got["status"], got["wait"], got["reason"], got["fireAt"]),
                         ("armed", "transient", "stalled", now + 120))

    def test_zen_free_limited_day_defer(self):
        now = int(time.time())
        midnight = (now // 86400 + 1) * 86400
        sd = fsio.open_state(create=True)
        self.assertTrue(REAL_APPEND_OBSERVED(sd, "zen-free", "daily", "Daily", midnight, now))
        pending = limits_history.pending_reset(sd, "zen-free", now)
        self.assertEqual(pending, midnight)
        job = make_job("opencode", model="opencode/big-pickle", kind="at", now=now)
        defer = paid.paid_defer(job, None, now, billing="zen_free", zen_limited_until=pending)
        self.assertEqual((defer["action"], defer["reason"], defer["fireAt"]),
                         ("rearm", "limit_full", midnight + consts.ZEN_FREE_REARM_EXTRA_S))
        verdict = trigger.defer_verdict(job, defer, now)
        self.assertEqual((verdict["action"], verdict["fireAt"], verdict["reason"]),
                         ("rearm", midnight + consts.ZEN_FREE_REARM_EXTRA_S, "limit_full"))
        self.assertIsNone(paid.paid_defer(job, None, now, billing="zen_free", zen_limited_until=None))
        self.assertEqual(trigger.defer_verdict(job, {"action": "skip", "fireAt": None, "reason": "paid_exhausted",
                                                     "resetEpoch": None}, now)["action"], "skip")
        self.assertEqual(trigger.defer_verdict(job, {"action": "rearm", "fireAt": now + 9 * 86400,
                                                     "reason": "paid_defer", "resetEpoch": None}, now)["action"], "skip")
        self.assertEqual(trigger.defer_verdict(job, {"action": "rearm", "fireAt": "soon", "reason": "made_up"},
                                               now), {"action": "skip", "fireAt": None, "reason": "failed",
                                                      "resetEpoch": None})
        job["state"]["defers"] = consts.MAX_DEFERS
        self.assertEqual(trigger.defer_verdict(job, defer, now)["action"], "gave_up")


def usage_module():
    from autopilot import usage
    return usage


class V2LivenessTests(Sandbox):

    def test_liveness_cursor_pi_stat_only(self):
        now = int(time.time())
        path = pi_path(PI_UUID)
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as handle:
            handle.write("{}\n")
        os.utime(path, (now - 30, now - 30))
        os.chmod(path, 0)     # stat still works; any read of the session would fail
        job = make_job("pi", mode="resume", session=PI_UUID, session_path=path, now=now)
        self.p.set(sessions, "lookup_session", lambda *a, **k: self.fail("Pi liveness must not read sessions"))
        self.assertIs(liveness.session_busy(job, ["/opt/pi"], now), True)
        os.utime(path, (now - 600, now - 600))
        self.assertIs(liveness.session_busy(job, ["/opt/pi"], now), False)
        job["target"]["sessionPath"] = self.home + "/elsewhere/" + os.path.basename(path)
        self.assertIsNone(liveness.session_busy(job, ["/opt/pi"], now))
        job["target"]["sessionPath"] = pi_path(str(uuid.uuid4()))
        self.assertIsNone(liveness.session_busy(job, ["/opt/pi"], now))
        self.assertIs(liveness.session_busy(make_job("pi", mode="new"), ["/opt/pi"], now), False)
        self.assertIs(liveness.session_busy(make_job("pi", mode="fork"), ["/opt/pi"], now), False)
        os.chmod(path, 0o600)
        calls = []
        self.p.set(sessions, "lookup_session", lambda h, sid, **kw: calls.append((h, sid, kw)) or
                   {"updatedAtMs": (now - 10) * 1000})
        cursor = make_job("cursor", mode="resume", session=CURSOR_UUID, now=now)
        self.assertIs(liveness.session_busy(cursor, ["/opt/cursor"], now), True)
        self.assertEqual(calls, [("cursor", CURSOR_UUID, {})])
        self.assertIs(liveness.session_busy(make_job("cursor", mode="new"), ["/opt/cursor"], now), False)


class V2RunVerbTests(RunVerbBase):

    def test_prefire_paid_defer_any_trigger(self):
        self.fake_config(mode="done")
        now = int(time.time())
        job = self.seed(name="codex")
        self.gate_over = {"defer": {"action": "rearm", "fireAt": now + 3600, "reason": "limit_full",
                                    "resetEpoch": now + 3480}}
        self.assertEqual(self.run_verb(job["id"]), ["DEFERRED"])
        state = self.stored(job["id"])["state"]
        self.assertEqual((state["status"], state["wait"], state["reason"], state["defers"], state["gen"]),
                         ("armed", "deferred", "limit_full", 1, 2))
        self.assertEqual(state["basis"]["resetEpoch"], now + 3480)
        self.assertEqual(self.armed[-1][:2], (job["id"], 2))
        self.assertAlmostEqual(self.armed[-1][2], now + 3600, delta=5)
        call = self.gate_calls[-1]
        self.assertEqual((call["phase"], call["deadline_s"], call["exec_prefix"], call["harness"]),
                         ("prefire", 30, [job["cli"]["real"]], "codex"))
        self.assertEqual(self.notified[0][11], "Deferred")
        job = self.seed(name="claude", kind="now")
        self.gate_over = {"defer": {"action": "skip", "fireAt": None, "reason": "paid_exhausted", "resetEpoch": None}}
        self.assertEqual(self.run_verb(job["id"]), ["SKIPPED"])
        after = self.stored(job["id"])
        self.assertEqual((after["state"]["status"], after["state"]["reason"], after["promptAvailable"]),
                         ("skipped", "paid_exhausted", False))
        job = self.seed(name="claude")
        self.mutate(job["id"], lambda j: j["state"].update(defers=consts.MAX_DEFERS))
        self.gate_over = {"defer": {"action": "rearm", "fireAt": now + 600, "reason": "paid_defer", "resetEpoch": None}}
        self.assertEqual(self.run_verb(job["id"]), ["GAVE_UP"])
        self.assertEqual(self.stored(job["id"])["state"]["reason"], "defers")
        self.assertEqual([e for e in self.agent_entries() if e["stdinBytes"]], [])

    def test_prefire_gate_refusal_reasons(self):
        self.fake_config(mode="done")
        table = {"paid_blocked": "paid_blocked", "paid_zen": "paid_blocked", "paid_opencode_claude": "paid_blocked",
                 "paid_pi_claude": "paid_blocked", "paid_pi_key": "paid_blocked",
                 "cursor_autorun_config": "cursor_autorun_config", "cursor_network_config": "cursor_network_config",
                 "cursor_project_rules": "cursor_project_rules", "cursor_untrusted": "untrusted",
                 "harness_gated": "harness_gated", "not_logged_in": "not_logged_in", "pi_auth_invalid": "failed"}
        self.assertEqual(paid.REASON_FOR_CODE, table)
        for code, reason in table.items():
            job = self.seed(name="codex")
            self.gate_over = {"ok": False, "code": code}
            self.assertEqual(self.run_verb(job["id"]), ["FINAL"], code)
            after = self.stored(job["id"])
            self.assertEqual((after["state"]["status"], after["state"]["reason"], after["promptAvailable"]),
                             ("failed", reason, False), code)
            self.assertEqual(after["state"]["history"][-2]["event"], "run_failed")
        self.gate_over = RuntimeError("a probe bug")
        job = self.seed(name="codex")
        self.assertEqual(self.run_verb(job["id"]), ["FINAL"])
        self.assertEqual(self.stored(job["id"])["state"]["reason"], "failed")
        self.gate_over = {}
        calls = len(self.gate_calls)
        # Nothing ships gated since Cursor's live check, so the runner's refusal is tested with it back in.
        self.p.set(consts, "GATED_HARNESSES", ("cursor",))
        job = self.seed(name="cursor", mode="new")
        self.assertEqual(self.run_verb(job["id"]), ["FINAL"])
        self.assertEqual(self.stored(job["id"])["state"]["reason"], "harness_gated")
        self.assertEqual(len(self.gate_calls), calls)
        job = self.seed(name="pi", mode="new", prompt="  /llama tell me a story")
        self.assertEqual(self.run_verb(job["id"]), ["FINAL"])
        self.assertEqual(self.stored(job["id"])["state"]["reason"], "failed")
        self.assertEqual([e for e in self.agent_entries() if e["stdinBytes"]], [])

    def test_prefire_gate_probes_run_outside_the_jobs_lock(self):
        """The gate's probes (up to 30 s) never hold the jobs lock; a change meanwhile ends the claim."""
        free = []

        def gate(job, **kw):
            sd = fsio.open_state(create=True)
            try:
                with sd.lock(0.5):
                    free.append(job["id"])
                    store = jobs.load_store(sd)
                    stored = jobs.find_job(store, job["id"])
                    jobs.set_status(stored, "paused", int(time.time()), reason="plugin_disabled", wait=None)
                    jobs.save_store(sd, store)
            finally:
                sd.close()
            return dict(GATE_OK)

        self.gate_over = gate
        job = self.seed(name="codex")
        self.assertEqual(self.run_verb(job["id"]), ["STALE"])
        self.assertEqual(free, [job["id"]])
        after = self.stored(job["id"])
        self.assertEqual((after["state"]["status"], after["state"]["reason"], after["promptAvailable"]),
                         ("paused", "plugin_disabled", True))
        self.assertEqual([e for e in self.agent_entries() if e["stdinBytes"]], [])

    def test_pi_session_path_from_header_retry_resumes_path(self):
        self.fake_config(mode="pi_done")
        job = self.seed(name="pi", mode="new")
        self.assertEqual(self.run_verb(job["id"]), ["FIRED", "OUTCOME_DONE", "FINAL"])
        after = self.stored(job["id"])
        state = after["state"]
        self.assertEqual(state["runSessionId"], job["target"]["newSessionId"])
        folder = "--" + job["target"]["cwd"].lstrip("/").replace("/", "-") + "--"
        self.assertEqual(os.path.dirname(state["runSessionPath"]),
                         os.path.join(os.path.realpath(self.home), ".pi/agent/sessions", folder))
        self.assertTrue(os.path.isfile(state["runSessionPath"]))
        self.assertEqual(self.run_record(job["id"], 1)["sessionPath"], state["runSessionPath"])
        argv = harness.build_command(after, exec_prefix=["/p"], run_dir="/s", gen=2)["argv"]
        self.assertEqual(argv[-2:], ["--session", state["runSessionPath"]])
        entry = [e for e in self.agent_entries() if e["stdinBytes"]][-1]
        self.assertEqual(entry["argv"][entry["argv"].index("--session-id") + 1], job["target"]["newSessionId"])

        self.fake_config(mode="pi_error_codex_limit")
        job = self.seed(name="pi", mode="new")
        self.assertEqual(self.run_verb(job["id"]), ["FIRED", "OUTCOME_LIMIT", "REARMED"])
        after = self.stored(job["id"])
        self.assertEqual((after["state"]["runSessionId"], after["state"]["runSessionPath"]), (None, None))
        argv = harness.build_command(after, exec_prefix=["/p"], run_dir="/s", gen=2)["argv"]
        self.assertEqual(argv[-4:-2], ["--session-id", job["target"]["newSessionId"]])

        store = os.path.join(self.home, ".pi/agent/sessions/--x--")
        os.makedirs(store, exist_ok=True)
        real_file = os.path.join(store, "2026-09-15T10-00-00-000Z_" + PI_UUID + ".jsonl")
        with open(real_file, "w") as handle:
            handle.write("{}\n")
        self.assertEqual(runner.verified_pi_session_path(real_file), os.path.realpath(real_file))
        outside = os.path.join(self.tmp, "outside.jsonl")
        with open(outside, "w") as handle:
            handle.write("{}\n")
        link = os.path.join(store, "2026-09-15T10-00-00-001Z_" + PI_UUID + ".jsonl")
        os.symlink(outside, link)
        self.assertIsNone(runner.verified_pi_session_path(link))
        self.assertIsNone(runner.verified_pi_session_path(os.path.join(store, "missing.jsonl")))
        self.assertIsNone(runner.verified_pi_session_path(outside))

    def test_run_record_v2_fields(self):
        self.fake_config(mode="done")
        self.p.set(agents, "cached_version", lambda h: "1.18.31" if h == "opencode" else "not a version!")
        self.p.set(windows, "limit_source_for",
                   lambda job, billing, usage=None: "zen-free" if billing == "zen_free" else None)
        self.gate_over = {"billing": "zen_free"}
        job = self.seed(name="opencode", mode="new", level="unattended", model="opencode/big-pickle")
        self.assertEqual(self.run_verb(job["id"]), ["FIRED", "OUTCOME_DONE", "FINAL"])
        record = self.run_record(job["id"], 1)
        self.assertEqual({k: record[k] for k in ("allowPaid", "provider", "model", "billing", "limitSource",
                                                 "cliVersion", "sessionPath")},
                         {"allowPaid": False, "provider": None, "model": "opencode/big-pickle", "billing": "zen_free",
                          "limitSource": "zen-free", "cliVersion": "1.18.31", "sessionPath": None})
        self.gate_over = {"billing": "zen_free"}
        job = self.seed(name="claude", allow_paid=True)
        self.assertEqual(self.run_verb(job["id"]), ["FIRED", "OUTCOME_DONE", "FINAL"])
        record = self.run_record(job["id"], 1)
        self.assertEqual((record["allowPaid"], record["billing"], record["cliVersion"], record["limitSource"]),
                         (True, None, None, None))
        self.fake_config(mode="limit_event", resetsAt=int(time.time()) + 7200)
        job = self.seed(name="claude")
        self.assertEqual(self.run_verb(job["id"]), ["FIRED", "OUTCOME_LIMIT", "REARMED"])
        limit = self.run_record(job["id"], 1)["limit"]
        self.assertEqual((limit["kind"], limit["source"], limit["rearm"]), ("session", "event", True))

    def test_observed_resets_appended(self):
        now = int(time.time())
        self.p.set(windows, "limit_source_for", lambda job, billing, usage=None: {
            "opencode": "opencode-go", "codex": "codex", "pi": "codex"}.get(job["harness"]))
        self.fake_config(mode="opencode_go_limit", retryAfter=7200, limitName="5 hour")
        job = self.seed(name="opencode", mode="new", level="unattended", model="opencode-go/glm-5.2")
        self.assertEqual(self.run_verb(job["id"]), ["FIRED", "OUTCOME_LIMIT", "REARMED"])
        self.assertEqual(len(self.observed), 1)
        source, kind, label, reset, _at = self.observed[0]
        self.assertEqual((source, kind, label), ("opencode-go", "session", "5-hour"))
        self.assertAlmostEqual(reset, now + 7200, delta=10)
        self.fake_config(mode="opencode_free_limit", retryAfter=3600)
        job = self.seed(name="opencode", mode="new", level="unattended", model="opencode/big-pickle")
        self.assertEqual(self.run_verb(job["id"]), ["FIRED", "OUTCOME_LIMIT", "REARMED"])
        self.assertEqual(self.observed[-1][:3], ("zen-free", "daily", "Daily"))
        self.p.set(consts, "GATED_HARNESSES", ())
        self.fake_config(mode="cursor_limit_monthly", cycleEnd="10/9/2026")
        job = self.seed(name="cursor", mode="new")
        self.assertEqual(self.run_verb(job["id"]), ["FIRED", "OUTCOME_LIMIT", "FINAL"])
        self.assertEqual(self.observed[-1][:4], ("cursor", "billing_total", "Cursor cycle",
                                                 trigger.local_date_epoch(2026, 10, 9)))
        after = self.stored(job["id"])
        self.assertEqual((after["state"]["status"], after["state"]["reason"]), ("limit", "monthly_limit"))
        count = len(self.observed)
        self.fake_config(mode="limit_event", resetsAt=now + 7200)
        self.assertEqual(self.run_verb(self.seed(name="claude")["id"]), ["FIRED", "OUTCOME_LIMIT", "REARMED"])
        self.assertEqual(len(self.observed), count)

        def append_unlocked(sd, *args):
            with sd.lock(0.5):
                self.observed.append(args)
            return True

        self.p.set(limits_history, "append_observed", append_unlocked)
        self.fake_config(mode="opencode_go_limit", retryAfter=600, limitName="weekly")
        job = self.seed(name="opencode", mode="new", level="unattended", model="opencode-go/glm-5.2")
        self.assertEqual(self.run_verb(job["id"]), ["FIRED", "OUTCOME_LIMIT", "REARMED"])
        self.assertEqual(self.observed[-1][:2], ("opencode-go", "weekly"))


if __name__ == "__main__":
    unittest.main()
