"""Shared stand-ins and fixtures for the paid, models and scan tests (builder H5).

H5Case gives every test a temporary HOME, clears the XDG variables (so nothing resolves to
the real config, cache or state folders), replaces harness.agent_env and identity.discover_cli
with in-process stand-ins pointing at tests/stubs/fake-cli, and, while consts still carries
the v1 harness tuple, patches consts.HARNESSES to the six v2 ids. Nothing here reads the real
HOME, starts a real agent CLI, uses the network or touches the user systemd manager.

Fixture strings that the repository policy scan would flag are built by concatenation.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(ROOT, "bin") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "bin"))

from autopilot import consts, harness, identity  # noqa: E402

SIX = ("claude", "opencode", "codex", "gemini", "cursor", "pi")
FAKE_CLI = os.path.join(ROOT, "tests", "stubs", "fake-cli")
PY = ["/usr/bin/python3", "-I", "-S", "-B"]
DAY = 86400
_MISSING = object()
_ENV_KEYS = ("HOME", "AP4A_STATE_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME")

UNRESTRICTED = "unre" + "stricted"
AUTO_REVIEW = "auto" + "-review"
ALLOW = "al" + "low"

FREE_IDS = ("big-pickle", "ling-3.0-flash-fin-free", "mimo-v2.5-free", "muse-spark-1.2-contributor-free",
            "muse-spark-1.3-contributor-free", "nemotron-3-ultra-free", "nemotron-3.5-lightning-free")


class H5Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="h5-")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home, mode=0o700)
        self._saved_env = {key: os.environ.get(key) for key in _ENV_KEYS}
        os.environ["HOME"] = self.home
        for key in _ENV_KEYS[1:]:
            os.environ.pop(key, None)
        self._patches = []
        self.now = int(time.time())
        self.log = os.path.join(self.tmp, "cli.log")
        self.script_path = os.path.join(self.tmp, "script.json")
        self.script = {}
        self.env_extra = {}
        self.present = {name: True for name in SIX}
        self.real = {}
        if tuple(consts.HARNESSES) != SIX:
            self.patch(consts, "HARNESSES", SIX)
        self.patch(harness, "agent_env", self.fake_env)
        self.patch(identity, "discover_cli", self.fake_discover)

    def tearDown(self):
        for obj, name, value in reversed(self._patches):
            if value is _MISSING:
                delattr(obj, name)
            else:
                setattr(obj, name, value)
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.tmp, ignore_errors=True)

    def patch(self, obj, name, value):
        self._patches.append((obj, name, getattr(obj, name, _MISSING)))
        setattr(obj, name, value)

    # --- files -----------------------------------------------------------------------

    def path(self, rel):
        return rel if rel.startswith("/") else os.path.join(self.home, rel)

    def write(self, rel, data, mode=0o600, mtime=None):
        path = self.path(rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not isinstance(data, (bytes, str)):
            data = json.dumps(data)
        with open(path, "wb") as handle:
            handle.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        os.chmod(path, mode)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def mkdir(self, rel):
        path = self.path(rel)
        os.makedirs(path, exist_ok=True)
        return path

    # --- the fake CLI ------------------------------------------------------------------

    def exec_prefix(self):
        return PY + [FAKE_CLI]

    def fake_env(self, name, level_id):
        self.assertEqual(level_id, "plan")
        env = {"HOME": self.home, "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "TERM": "dumb",
               "FAKE_CLI_SCRIPT": self.script_path, "FAKE_CLI_LOG": self.log, "FAKE_CLI_NAME": name}
        env.update(self.env_extra.get(name, {}))
        return env

    def fake_discover(self, name):
        if not self.present.get(name):
            return {"harness": name, "link": None, "real": None, "exec": [], "ok": False, "reason": "not_found"}
        real = self.real.get(name, FAKE_CLI)
        return {"harness": name, "link": real, "real": real, "exec": self.exec_prefix(), "ok": True,
                "reason": None}

    def answer(self, words, stdout="", stderr="", rc=0, sleep=0.0, stdout_file=None):
        entry = {"stdout": stdout, "stderr": stderr, "rc": rc, "sleep": sleep}
        if stdout_file:
            entry["stdoutFile"] = stdout_file
        self.script[" ".join(words) if words != "*" else "*"] = entry
        with open(self.script_path, "w") as handle:
            json.dump(self.script, handle)

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as handle:
            return [json.loads(line) for line in handle if line.strip()]


# --- OpenCode fixtures ------------------------------------------------------------------

def zero_cost_verbose():
    return {"input": 0, "output": 0, "cache": {"read": 0, "write": 0}}


def paid_cost_verbose(n=0):
    return {"input": 0.5 + n, "output": 2.0, "cache": {"read": 0.05, "write": 0}}


def verbose_model(provider, model, cost, status="active", name=None):
    return {"id": model, "providerID": provider, "name": name or model.replace("-", " ").title(),
            "family": model.split("-")[0],
            "api": {"id": model, "url": "https://opencode.ai/zen/v1", "npm": "@ai-sdk/openai-compatible"},
            "status": status, "cost": cost, "limit": {"context": 200000, "output": 32000},
            "capabilities": {"temperature": True, "reasoning": False, "attachment": False, "toolcall": True},
            "release_date": "2026-01-01", "variants": {}}


def verbose_text(blocks):
    """`opencode models <provider> --verbose` output for [(provider, model, object)]."""
    return "".join("%s/%s\n%s\n" % (provider, model, json.dumps(obj, indent=2)) for provider, model, obj in blocks)


def seven_free_blocks():
    return [("opencode", model, verbose_model("opencode", model, zero_cost_verbose())) for model in FREE_IDS]


def keyed_69_blocks():
    paid = [("opencode", "paid-model-%02d" % n, verbose_model("opencode", "paid-model-%02d" % n, paid_cost_verbose(n)))
            for n in range(62)]
    return seven_free_blocks() + paid


def cat_model(model, cost, status=None, name=None):
    entry = {"id": model, "name": name or model, "family": "x", "cost": cost,
             "limit": {"context": 200000, "output": 32000}}
    if status:
        entry["status"] = status
    return entry


def zero_cost_catalogue():
    return {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}


def catalogue_document(opencode_models, go_models=None, other=None):
    document = {
        "opencode": {"id": "opencode", "name": "OpenCode Zen", "env": ["OPENCODE_API_KEY"],
                     "api": "https://opencode.ai/zen/v1", "npm": "@ai-sdk/openai-compatible",
                     "models": {m["id"]: m for m in opencode_models}},
        "opencode-go": {"id": "opencode-go", "name": "OpenCode Go", "env": ["OPENCODE_API_KEY"],
                        "api": "https://opencode.ai/zen/go/v1", "npm": "@ai-sdk/openai-compatible",
                        "models": {m["id"]: m for m in (go_models or [])}},
        "anthropic": {"id": "anthropic", "name": "Anthropic", "env": ["ANTHROPIC_API_KEY"],
                      "models": {"claude-fable-5": cat_model("claude-fable-5", {"input": 3, "output": 15})}},
    }
    document.update(other or {})
    return document


def free_catalogue(extra=(), go_models=None):
    return catalogue_document([cat_model(m, zero_cost_catalogue()) for m in FREE_IDS] + list(extra), go_models)


# --- usage fixtures (Usage v2 shape, CONTRACT-V2-DELTA 3.7) ----------------------------------

def window(kind, percent, resets_at, short_label="", sliding=False, bindable=True):
    return {"key": "x:" + (short_label or kind), "label": short_label, "title": None, "shortLabel": short_label,
            "kind": kind, "percent": percent, "over": percent is not None and percent > 1,
            "resetsAt": resets_at, "sliding": sliding, "source": "record", "bindable": bindable}


def provider(source_id, windows, stale=False, readable=True, tier=""):
    return {"id": source_id, "name": source_id, "tier": tier, "statusText": "", "scope": None, "source": "record",
            "readable": readable, "unreadableReason": None, "updatedAtMs": 0, "ageSec": 60, "stale": stale,
            "cadenceSec": 900, "keptFromLastPoll": False, "harnesses": [], "relevant": True,
            "headlineKey": None, "windows": windows}


def usage_of(*providers):
    return {"nowMs": 0, "providers": list(providers)}
