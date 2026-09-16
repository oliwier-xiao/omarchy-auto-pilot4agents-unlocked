"""Which agent CLIs are usable: path, version and sign-in state.

Discovery itself (candidate paths, realpath, trust checks) belongs to identity.discover_cli.
This module adds short, bounded probes run with the agent environment allowlist from
harness.agent_env, in the user's home folder, never with a prompt:

  <cli> --version                    5 s, 4 KiB; cached per binary identity
  codex login status                 10 s, 4 KiB; on every call, Codex stays disabled unless signed in
  claude auth status --json          10 s, 64 KiB; only when the user asked (agents --login)
  cursor-agent status --format json  10 s, 64 KiB; only with --login; only status and
                                     isAuthenticated are kept, the rest (user details) is dropped
                                     before anything else looks at it

Only the parsed values leave this module. Raw probe output is never stored or returned.
The version cache lives in the state folder and is keyed by the device, inode, size and
mtime of every file the CLI is started from, so an upgrade invalidates it.
"""

import hashlib
import json
import os
import re
import time

from . import bounded, consts, edition, fsio, h5_v2, harness, identity
from .errors import ApError

_BUDGET_S = 25.0                 # verb deadline is 30 s
_VERSION_DEADLINE_S = 5.0
_LOGIN_DEADLINE_S = 10.0
_STATUS_DEADLINE_S = 10.0
_MIN_PROBE_S = 0.5
_PROBE_CAP = 4096
_AUTH_CAP = 65536
_VERSION_RE = re.compile(rb"(\d{1,9}\.\d{1,9}\.\d{1,9})")
_CURSOR_VERSION_RE = re.compile(rb"(?<![0-9])(\d{4}\.\d{2}\.\d{2}-[0-9a-f]{7})(?![0-9a-f])")
_CACHED_VERSION_RE = re.compile(r"^(?:\d{1,9}\.\d{1,9}\.\d{1,9}|\d{4}\.\d{2}\.\d{2}-[0-9a-f]{7})$")
_VERSION_FULL = {"cursor": re.compile(r"^\d{4}\.\d{2}\.\d{2}-[0-9a-f]{7}$")}
_VERSION_FULL_DEFAULT = re.compile(r"^\d{1,9}\.\d{1,9}\.\d{1,9}$")
_STATUS_RE = re.compile(r"^[a-z][a-z-]{0,39}$")
_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
_CACHE_NAME = "agents-cache.json"
_CACHE_MAX = 16384
_VERSION_TTL_S = 86400
_REASONS = ("not_found", "untrusted", "shim")
_SIGNED_OUT_STATUSES = ("unauthenticated", "partially-authenticated")


def _probe(argv, env, deadline_s, cap):
    if deadline_s < _MIN_PROBE_S:
        return None
    result = bounded.run_bounded(list(argv), env=dict(env), cwd=fsio.home(), stdout_cap=cap,
                                 stderr_cap=_PROBE_CAP, deadline_s=deadline_s)
    if result.get("error") is not None or result.get("timedOut"):
        return None
    return result


def _env(name):
    try:
        return harness.agent_env(name, edition.DEFAULT_LEVEL)
    except ApError:
        return None


def _codex_login(exec_prefix, env, deadline_s):
    result = _probe(list(exec_prefix) + ["login", "status"], env, deadline_s, _PROBE_CAP)
    if result is None:
        return None
    text = (bytes(result.get("stdout") or b"") + b"\n" + bytes(result.get("stderr") or b"")) \
        .decode("utf-8", "replace")
    if re.search(r"(?m)^\s*Not logged in\b", text):
        return False
    if result.get("rc") == 0 and re.search(r"(?m)^\s*Logged in\b", text):
        return True
    return None


def codex_logged_in(exec_prefix):
    """True / False from `codex login status`; None when the answer is unclear or late."""
    env = _env("codex")
    return None if env is None else _codex_login(exec_prefix, env, _LOGIN_DEADLINE_S)


def _claude_auth(exec_prefix, env, deadline_s):
    result = _probe(list(exec_prefix) + ["auth", "status", "--json"], env, deadline_s, _AUTH_CAP)
    if result is None or result.get("overflow"):
        return None
    try:
        document = json.loads(bytes(result.get("stdout") or b""))
    except (ValueError, RecursionError):
        return None
    if isinstance(document, dict) and isinstance(document.get("loggedIn"), bool):
        return document["loggedIn"]
    return None


def _cursor_status(exec_prefix, env, deadline_s):
    kept = {"isAuthenticated": None, "status": None}
    result = _probe(list(exec_prefix) + ["status", "--format", "json"], env, deadline_s, _AUTH_CAP)
    if result is None or result.get("overflow"):
        return kept
    try:
        document = json.loads(bytes(result.get("stdout") or b""))
    except (ValueError, RecursionError):
        return kept
    if isinstance(document, dict):
        authenticated = document.get("isAuthenticated")
        status = document.get("status")
        kept["isAuthenticated"] = authenticated if isinstance(authenticated, bool) else None
        kept["status"] = status if isinstance(status, str) and _STATUS_RE.match(status) else None
    del document
    return kept


def cursor_status(exec_prefix):
    """{"isAuthenticated": bool|None, "status": str|None} from `cursor-agent status --format json`.

    Every other field of the answer (email, user id, names) is discarded right after parsing.
    """
    env = _env("cursor")
    if env is None:
        return {"isAuthenticated": None, "status": None}
    return _cursor_status(exec_prefix, env, _STATUS_DEADLINE_S)


def _cursor_logged_in(status):
    if status["status"] in _SIGNED_OUT_STATUSES or status["isAuthenticated"] is False:
        return False
    if status["isAuthenticated"] is True and status["status"] in (None, "authenticated"):
        return True
    return None


def _version(name, exec_prefix, env, deadline_s):
    result = _probe(list(exec_prefix) + ["--version"], env, deadline_s, _PROBE_CAP)
    if result is None or result.get("rc") != 0:
        return None
    pattern = _CURSOR_VERSION_RE if name == "cursor" else _VERSION_RE
    match = pattern.search(bytes(result.get("stdout") or b""))
    if not match:
        return None
    version = match.group(1).decode("ascii")
    return version if _VERSION_FULL.get(name, _VERSION_FULL_DEFAULT).match(version) else None


def _identity_key(name, discovery):
    parts = [name, str(discovery.get("link")), str(discovery.get("real"))]
    for path in discovery.get("exec") or []:
        if not isinstance(path, str) or not path.startswith("/"):
            parts.append(str(path))
            continue
        try:
            info = os.stat(path)
        except (OSError, ValueError):
            return None
        parts.append("%s:%d:%d:%d:%d" % (path, info.st_dev, info.st_ino, info.st_size,
                                         info.st_mtime_ns))
    return hashlib.sha256("\0".join(parts).encode("utf-8", "replace")).hexdigest()


class _VersionCache:
    """Best effort: any failure to read or write it just means probing again."""

    def __init__(self, now, create=True):
        self.now = now
        self.entries = {}
        self.dirty = False
        self.state = None
        try:
            self.state = fsio.open_state(create=create)
            if self.state is None:
                return
            document = self.state.read_json(_CACHE_NAME, _CACHE_MAX)
        except (ApError, OSError):
            return
        versions = document.get("versions") if isinstance(document, dict) \
            and document.get("schemaVersion") == 1 else None
        if not isinstance(versions, dict):
            return
        for name in h5_v2.const("CLI_NAMES"):
            entry = versions.get(name)
            if not isinstance(entry, dict):
                continue
            key, version, at = entry.get("key"), entry.get("version"), entry.get("at")
            if isinstance(key, str) and _KEY_RE.match(key) and isinstance(version, str) \
                    and _CACHED_VERSION_RE.match(version) and isinstance(at, int) \
                    and not isinstance(at, bool) and now - _VERSION_TTL_S <= at <= now + 300:
                self.entries[name] = {"key": key, "version": version, "at": at}

    def get(self, name, key):
        entry = self.entries.get(name)
        return entry["version"] if entry and key and entry["key"] == key else None

    def put(self, name, key, version):
        if key and version:
            self.entries[name] = {"key": key, "version": version, "at": self.now}
            self.dirty = True

    def save(self):
        if self.state is None:
            return
        try:
            if self.dirty:
                data = json.dumps({"schemaVersion": 1, "versions": self.entries}, sort_keys=True,
                                  separators=(",", ":")).encode("utf-8")
                self.state.write_atomic(_CACHE_NAME, data)
        except (ApError, OSError):
            pass
        finally:
            self.state.close()
            self.state = None


def cached_version(harness_id):
    """The cached version of the CLI discovered now for harness, or None (no probe, no state created)."""
    discovery = identity.discover_cli(harness_id)
    if discovery.get("ok") is not True:
        return None
    cache = _VersionCache(int(time.time()), create=False)
    try:
        return cache.get(harness_id, _identity_key(harness_id, discovery))
    finally:
        cache.save()


def version_of(harness_id, discovery, deadline_s):
    """Version of a discovered CLI: from the cache, else one bounded `--version` probe (then cached)."""
    if not isinstance(discovery, dict) or discovery.get("ok") is not True or not discovery.get("exec"):
        return None
    key = _identity_key(harness_id, discovery)
    cache = _VersionCache(int(time.time()))
    try:
        version = cache.get(harness_id, key)
        if version is None:
            env = _env(harness_id)
            if env is not None:
                version = _version(harness_id, discovery["exec"], env, min(_VERSION_DEADLINE_S, deadline_s))
                cache.put(harness_id, key, version)
        return version
    finally:
        cache.save()


def _abs_or_none(value):
    return value if isinstance(value, str) and value.startswith("/") else None


def list_agents(check_login):
    """Agents (3.11) without "ok": one entry per harness, in HARNESSES order."""
    deadline = time.monotonic() + _BUDGET_S

    def left(limit):
        return min(limit, deadline - time.monotonic())

    gated_ids = h5_v2.const("GATED_HARNESSES")
    kinds_for = h5_v2.const("RESET_KINDS_FOR")
    entries, probes = [], []
    for name in consts.HARNESSES:
        discovery = identity.discover_cli(name)
        available = discovery.get("ok") is True
        reason = None
        if not available:
            reason = discovery.get("reason") if discovery.get("reason") in _REASONS else "not_found"
        entries.append({"harness": name, "name": h5_v2.const("HARNESS_NAMES")[name],
                        "cliName": h5_v2.const("CLI_NAMES")[name], "available": available,
                        "link": _abs_or_none(discovery.get("link")),
                        "real": _abs_or_none(discovery.get("real")),
                        "version": None, "loggedIn": None, "reason": reason,
                        "canFork": bool(h5_v2.const("CAN_FORK")[name]),
                        "triggers": ["now", "in", "at"] + list(kinds_for.get(name, ())),
                        "enabled": False, "armable": False, "gated": name in gated_ids})
        probes.append((discovery, list(discovery.get("exec") or []) if available else None))

    envs = {}
    for entry, (_discovery, exec_prefix) in zip(entries, probes):
        if exec_prefix:
            envs[entry["harness"]] = _env(entry["harness"])

    # Sign-in first: it decides whether Codex can be used at all.
    for entry, (_discovery, exec_prefix) in zip(entries, probes):
        name = entry["harness"]
        if not exec_prefix or envs.get(name) is None:
            continue
        if name == "codex":
            entry["loggedIn"] = _codex_login(exec_prefix, envs[name], left(_LOGIN_DEADLINE_S))
        elif name == "claude" and check_login:
            entry["loggedIn"] = _claude_auth(exec_prefix, envs[name], left(_LOGIN_DEADLINE_S))
        elif name == "cursor" and check_login:
            entry["loggedIn"] = _cursor_logged_in(
                _cursor_status(exec_prefix, envs[name], left(_STATUS_DEADLINE_S)))

    cache = _VersionCache(int(time.time()))
    for entry, (discovery, exec_prefix) in zip(entries, probes):
        name = entry["harness"]
        if not exec_prefix or envs.get(name) is None:
            continue
        key = _identity_key(name, discovery)
        version = cache.get(name, key)
        if version is None:
            version = _version(name, exec_prefix, envs[name], left(_VERSION_DEADLINE_S))
            cache.put(name, key, version)
        entry["version"] = version
    cache.save()

    for entry in entries:
        name = entry["harness"]
        entry["enabled"] = entry["available"] and not (name == "codex" and entry["loggedIn"] is not True)
        entry["armable"] = entry["enabled"] and not entry["gated"]
        if entry["available"]:
            if name in ("codex", "cursor") and entry["loggedIn"] is False:
                entry["reason"] = "not_logged_in"
            elif entry["gated"]:
                entry["reason"] = "gated"
    return {"checkedLogin": bool(check_login), "agents": entries}
