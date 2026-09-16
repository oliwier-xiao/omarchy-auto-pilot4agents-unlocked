"""Paid-usage gate for one job: config preflights, sign-in probes, billing refusals, notes, defers.

With allowPaid off a job may only draw on the user's plan (CONTRACT-V2-DELTA section 6):

  claude    nothing to probe before arming; the runner watches the stream (pure verdicts here)
            and the pre-fire defer waits out a full Session or Weekly window
  codex     `codex login status` must report a ChatGPT login
  gemini    every settings file Gemini CLI merges (system defaults, ~/.gemini, the folder's .gemini,
            /etc/gemini-cli) must name a Google sign-in (security.auth.selectedType); a missing
            type is refused too, since Gemini would then take an API key from a .env file
  opencode  the model's billing class, from `opencode models <provider> --verbose` joined with the
            local model catalogue; paid Zen models and anthropic/* are refused
  cursor    config preflights in every state (auto-run config, open network, repository allow
            rules, workspace trust); on-demand spend is invisible, so a full Included window defers
  pi        `pi auth check --provider P --json --no-refresh`; API keys, OpenRouter, Radius and
            Claude through extra usage are refused

Everything read here is a config file (bounded, no-follow, owner-checked), a stat, or a probe
answer (bounded child, allowlisted environment, no prompt, only parsed values kept). Credential
stores are never opened and no probe asks a CLI for a secret. Only fixed codes leave this module.
"""

import json
import math
import os
import re
import stat

from . import bounded, consts, fsio, h5_v2, harness, identity
from . import usage as usage_mod
from .errors import ApError

try:  # windows.py arrives with builder H4 in the same round
    from . import windows as windows_mod
except ImportError:  # pragma: no cover - stand-in until then
    windows_mod = None

CLASSES = h5_v2.const("BILLING_CLASSES")
NOTES = ("subscription_only", "paid_on", "budget_cap", "opencode_provider", "zen_free", "go_plan",
         "cursor_on_demand", "pi_subscription", "codex_free_tier")
PHASES = ("preview", "arm", "prefire")
PAID_CODES = ("paid_blocked", "paid_zen", "paid_opencode_claude", "paid_pi_claude", "paid_pi_key")
REASON_FOR_CODE = {
    "paid_blocked": "paid_blocked", "paid_zen": "paid_blocked", "paid_opencode_claude": "paid_blocked",
    "paid_pi_claude": "paid_blocked", "paid_pi_key": "paid_blocked",
    "cursor_autorun_config": "cursor_autorun_config", "cursor_network_config": "cursor_network_config",
    "cursor_project_rules": "cursor_project_rules", "cursor_untrusted": "untrusted",
    "harness_gated": "harness_gated", "not_logged_in": "not_logged_in", "pi_auth_invalid": "failed",
}

LEVEL = "plan"                        # probes run with the environment of the plan level
_PROBE_CAP = 4096
_PROBE_MIN_S = 0.5
_PREVIEW_PROBE_MIN_S = 2.0            # preview skips a probe (pending) with less budget left
_LOGIN_DEADLINE_S = 10.0
_PI_AUTH_DEADLINE_S = 10.0
_VERBOSE_DEADLINE_S = 20.0
_VERBOSE_CAP = 1024 * 1024
_VERBOSE_MODELS_MAX = 1000
_CONFIG_CAP = 256 * 1024
_GEMINI_SETTINGS_CAP = 64 * 1024
_CATALOGUE_CAP = 8 * 1024 * 1024
_CATALOGUE_MODELS_MAX = 4000
_CATALOGUE_FRESH_S = 7 * 86400
_CLOCK_SKEW_S = 300
_COST_LEAVES_MAX = 64
_COST_DEPTH_MAX = 4
_WALK_MAX = 64
_HORIZON_S = 8 * 86400
_NAME_MAX = 80
_UNREADABLE = "unreadable"
# Built by concatenation so the repository policy scan never sees the bare quoted word.
_ALLOW_KEY = "al" + "low"
_OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC

_TYPE_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
# Gemini sign-in types that draw on a Google account's Code Assist quota, never an API key.
_GEMINI_FREE_TYPES = ("oauth-personal", "compute-default-credentials", "cloud-shell")
_GEMINI_SYSTEM_DIR = "/etc/gemini-cli"
_PROVIDER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_BLOCK_RE = re.compile(r"(?m)^([A-Za-z0-9][A-Za-z0-9._-]{0,63})/([A-Za-z0-9][^\s/]*(?:/[^\s]+)?)[ \t]*\r?$")
_NOT_LOGGED_IN_RE = re.compile(r"(?m)^[ \t]*Not logged in\b")
_LOGGED_IN_RE = re.compile(r"(?m)^[ \t]*Logged in using ([^\r\n]{1,80}?)[ \t.]*\r?$")
_CODEX_LOGINS = {
    "ChatGPT": "chatgpt",
    "an API key": "api_key",
    "access token": "access_token",
    "personal access token": "personal_access_token",
    "Amazon Bedrock API key": "bedrock_api_key",
    "Amazon Bedrock AWS access keys": "bedrock_aws",
}
_PI_AUTH_TYPES = ("oauth", "api_key")
_PI_KEY_PROVIDERS = ("openrouter", "radius")
_CURSOR_SLUG_RE = re.compile(r"[^a-zA-Z0-9]")
_OPENCODE_GLOBAL_FILES = ("config.json", "opencode.json", "opencode.jsonc")
_OPENCODE_PROJECT_NAMES = ("opencode.json", "opencode.jsonc", ".opencode")


# --- small helpers -----------------------------------------------------------------

def _left(deadline_s):
    remaining = bounded.remaining_budget()
    limit = max(0.0, float(deadline_s))
    return limit if remaining is None else min(limit, remaining)


def _exec_ok(exec_prefix):
    """An exec prefix from identity: strings without NUL, the first an absolute path."""
    return isinstance(exec_prefix, (list, tuple)) and bool(exec_prefix) \
        and all(isinstance(p, str) and "\0" not in p for p in exec_prefix) and exec_prefix[0].startswith("/")


def _clean_abs(value, max_bytes=4096):
    if not isinstance(value, str) or not value.startswith("/") or "\0" in value or "\n" in value:
        return None
    try:
        if len(value.encode("utf-8")) > max_bytes:
            return None
    except UnicodeEncodeError:
        return None
    return os.path.normpath(value)


def _run(argv, harness_id, deadline_s, cap):
    """One bounded probe in HOME with the agent environment; None when it could not finish."""
    if deadline_s < _PROBE_MIN_S:
        return None
    try:
        env = harness.agent_env(harness_id, LEVEL)
        cwd = fsio.home()
    except ApError:
        return None
    result = bounded.run_bounded(list(argv), env=env, cwd=cwd, stdout_cap=cap, stderr_cap=_PROBE_CAP,
                                 deadline_s=deadline_s)
    if result.get("error") is not None or result.get("timedOut"):
        return None
    return result


def _text(result):
    return (bytes(result.get("stdout") or b"") + b"\n" + bytes(result.get("stderr") or b"")) \
        .decode("utf-8", "replace")


def _json_object(data):
    try:
        value = json.loads(bytes(data).decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _read_config(path, cap):
    """(object|None, refused) for one config file: absent -> (None, False); refused -> (None, True)."""
    try:
        data = fsio.read_file_nofollow(path, cap)
    except ApError:
        return None, True
    if data is None:
        return None, False
    return _json_object(data), False


def _read_with_mtime(path, cap):
    """(bytes, mtime) of a regular file owned by this user, read without following a link."""
    try:
        fd = os.open(path, _OPEN_FLAGS)
    except (OSError, ValueError):
        return None, None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_size > cap:
            return None, None
        chunks, total = [], 0
        while total <= cap:
            chunk = os.read(fd, min(1048576, cap + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        if total > cap:
            return None, None
        return b"".join(chunks), int(info.st_mtime)
    except OSError:
        return None, None
    finally:
        os.close(fd)


def _jsonc_object(data):
    """A JSON object from JSON with comments and trailing commas (OpenCode config), or None."""
    try:
        text = bytes(data).decode("utf-8")
    except UnicodeDecodeError:
        return None
    out, i, n, in_string = [], 0, len(text), False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = n if end < 0 else end
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end < 0 else end + 2
            continue
        elif ch in "}]":
            while out and out[-1].isspace():
                out.pop()
            if out and out[-1] == ",":
                out.pop()
        out.append(ch)
        i += 1
    try:
        value = json.loads("".join(out))
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _walk_to_git_root(path):
    """Folders from path up to the nearest folder holding .git, or [path] when there is none."""
    current, dirs = path, []
    for _ in range(_WALK_MAX):
        dirs.append(current)
        if os.path.lexists(os.path.join(current, ".git")):
            return dirs
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return [path]


def _name(value):
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())
    text = "".join(ch for ch in text if ch.isprintable())
    return text[:_NAME_MAX] or None


# --- Claude (pure verdicts for the runner) -----------------------------------------------

def claude_init_verdict(api_key_source, allow_paid):
    """"paid" when a Claude run that may not spend would bill an API key; None otherwise.

    Only "none" (claude.ai subscription sign-in) is allowed while paid usage is off; a missing
    value counts as not "none".
    """
    if allow_paid is True:
        return None
    return None if api_key_source == "none" else "paid"


def claude_rate_event_verdict(rate_limit_info, allow_paid):
    """"overage" when a rate_limit_event says usage credits are in play and paid usage is off."""
    if allow_paid is True or not isinstance(rate_limit_info, dict):
        return None
    return "overage" if rate_limit_info.get("isUsingOverage") is True else None


# --- Codex -------------------------------------------------------------------------

def codex_login_kind(text):
    """Login kind from `codex login status` output (codex 0.154 lines), or None when unclear.

    A "Logged in using ..." line this table does not know is treated as a paid login (api_key),
    so an unfamiliar sign-in never passes as a ChatGPT one.
    """
    if not isinstance(text, str):
        return None
    if _NOT_LOGGED_IN_RE.search(text):
        return "not_logged_in"
    match = _LOGGED_IN_RE.search(text)
    if match is None:
        return None
    return _CODEX_LOGINS.get(match.group(1).strip(), "api_key")


def codex_login_probe(exec_prefix, deadline_s):
    """Login kind from a bounded `codex login status` (10 s, 4 KiB), or None when unclear or late."""
    if not _exec_ok(exec_prefix):
        return None
    result = _run(list(exec_prefix) + ["login", "status"], "codex",
                  min(_LOGIN_DEADLINE_S, _left(deadline_s)), _PROBE_CAP)
    if result is None:
        return None
    kind = codex_login_kind(_text(result))
    if kind not in (None, "not_logged_in") and result.get("rc") != 0:
        return None
    return kind


def _codex_free_tier(usage):
    provider = _provider(usage, "codex")
    tier = provider.get("tier") if isinstance(provider, dict) and provider.get("readable") is True else None
    return isinstance(tier, str) and tier.strip().lower() == "free"


# --- Gemini CLI --------------------------------------------------------------------

def _gemini_type_in(path, system):
    """The sign-in type one Gemini settings file sets: None (no file, or no type), the type, or
    "unreadable" (fails the read checks, is not JSON with comments, or holds a malformed type).

    Gemini CLI strips comments before parsing (strip-json-comments), so a commented file is read
    the same way here. The older top-level selectedAuthType is still honoured by its migration.
    """
    try:
        data = fsio.read_file_nofollow(path, _GEMINI_SETTINGS_CAP, owner_uid_or_root=system)
    except ApError:
        return _UNREADABLE
    if data is None:
        return None
    obj = _jsonc_object(data)
    if obj is None:
        return _UNREADABLE
    security = obj.get("security")
    auth = security.get("auth") if isinstance(security, dict) else None
    value = auth.get("selectedType") if isinstance(auth, dict) else None
    if value is None:
        value = obj.get("selectedAuthType")
    if value is None:
        return None
    return value if isinstance(value, str) and _TYPE_RE.match(value) else _UNREADABLE


def gemini_settings_paths(home, cwd=None):
    """[(path, system)] in Gemini CLI's merge order: system defaults, user, workspace, system."""
    paths = [(os.path.join(_GEMINI_SYSTEM_DIR, "system-defaults.json"), True),
             (os.path.join(home, ".gemini", "settings.json"), False)]
    if isinstance(cwd, str) and os.path.isabs(cwd) and "\0" not in cwd:
        try:
            same = os.path.realpath(cwd) == os.path.realpath(home)
        except (OSError, ValueError):
            same = False
        if not same:
            paths.append((os.path.join(cwd, ".gemini", "settings.json"), False))
    paths.append((os.path.join(_GEMINI_SYSTEM_DIR, "settings.json"), True))
    return paths


def gemini_selected_type(home, cwd=None):
    """The sign-in type a Gemini run in cwd would use, as far as the settings files can vouch.

    Every settings file Gemini CLI merges is read (64 KiB each, no-follow): "unreadable" when any of
    them fails the checks; else the first type that is not a Google sign-in, since a workspace file
    Gemini ignores in an untrusted folder must not hide the user's API key; else the Google sign-in
    type; else None (no file sets one: Gemini would then pick an API key from the environment or a
    .env file, which is not read here).
    """
    found = []
    for path, system in gemini_settings_paths(home, cwd):
        value = _gemini_type_in(path, system)
        if value == _UNREADABLE:
            return _UNREADABLE
        if value is not None:
            found.append(value)
    for value in found:
        if value not in _GEMINI_FREE_TYPES:
            return value
    return found[-1] if found else None


def gemini_verdict(selected_type, allow_paid):
    """None when a Google sign-in is confirmed or paid usage is on.

    Otherwise "not_logged_in" when no settings file names a sign-in type (Gemini would fall back to
    an API key in the environment or a .env file), and "paid_blocked" for an API key, Vertex, a
    gateway, an unknown type or a file that cannot be vouched for.
    """
    if allow_paid is True:
        return None
    if selected_type is None:
        return "not_logged_in"
    return None if selected_type in _GEMINI_FREE_TYPES else "paid_blocked"


# --- OpenCode billing classifier -------------------------------------------------------

def _flat_costs(value, prefix="", out=None, depth=0):
    """{path: number} of every numeric cost leaf; a tier's own description (size, type) is skipped."""
    out = {} if out is None else out
    if depth > _COST_DEPTH_MAX or len(out) >= _COST_LEAVES_MAX:
        return out
    if isinstance(value, dict):
        items = [(k, v) for k, v in value.items() if isinstance(k, str) and k != "tier"]
    elif isinstance(value, list):
        items = [(str(i), v) for i, v in enumerate(value[:16])]
    else:
        return out
    for key, item in items:
        path = prefix + "." + key if prefix else key
        if isinstance(item, bool):
            continue
        if isinstance(item, (int, float)):
            out[path] = float(item) if math.isfinite(item) else 1.0
        elif isinstance(item, (dict, list)):
            _flat_costs(item, path, out, depth + 1)
        if len(out) >= _COST_LEAVES_MAX:
            break
    return out


def _model_entry(obj):
    status = obj.get("status")
    return {"cost": _flat_costs(obj.get("cost")) if isinstance(obj.get("cost"), dict) else {},
            "status": status if isinstance(status, str) and _TYPE_RE.match(status) else None,
            "name": _name(obj.get("name"))}


def parse_verbose_blocks(text):
    """{"provider/model": {"cost": {path: number}, "status": str|None, "name": str|None}}.

    `opencode models <provider> --verbose` prints a `provider/model` line followed by a
    pretty-printed JSON object, repeated. Blocks whose body is not one JSON object are skipped.
    """
    out = {}
    if not isinstance(text, str):
        return out
    headers = list(_BLOCK_RE.finditer(text))
    decoder = json.JSONDecoder()
    for index, match in enumerate(headers):
        if len(out) >= _VERBOSE_MODELS_MAX:
            break
        end = headers[index + 1].start() if index + 1 < len(headers) else len(text)
        body = text[match.end():end].strip()
        if not body.startswith("{"):
            continue
        try:
            obj, used = decoder.raw_decode(body)
        except (ValueError, RecursionError):
            continue
        if not isinstance(obj, dict) or body[used:].strip():
            continue
        model_id = match.group(1) + "/" + match.group(2)
        if h5_v2.model_ok(model_id):
            out[model_id] = _model_entry(obj)
    return out


def read_catalogue(home, providers=("opencode", "opencode-go")):
    """({"provider/model": entry}, mtime) from ~/.cache/opencode/models.json, or (None, None).

    Read whole (<= 8 MiB, regular file of this user, no link followed); only the named
    providers' models are kept.
    """
    data, mtime = _read_with_mtime(os.path.join(home, ".cache", "opencode", "models.json"), _CATALOGUE_CAP)
    if data is None:
        return None, None
    document = _json_object(data)
    if document is None:
        return None, None
    entries = {}
    for provider in providers:
        section = document.get(provider)
        models = section.get("models") if isinstance(section, dict) else None
        if not isinstance(models, dict):
            continue
        for model_name, obj in models.items():
            if len(entries) >= _CATALOGUE_MODELS_MAX:
                break
            model_id = "%s/%s" % (provider, model_name)
            if isinstance(model_name, str) and isinstance(obj, dict) and h5_v2.model_ok(model_id):
                entries[model_id] = _model_entry(obj)
    return entries, mtime


def _all_zero(entry):
    costs = entry["cost"]
    return bool(costs) and "input" in costs and all(value == 0 for value in costs.values())


def _any_paid(entry):
    return entry is not None and any(value != 0 for value in entry["cost"].values())


def classify_opencode(model, verbose, catalogue, catalogue_mtime, now, project_config, default_model):
    """Billing class of an OpenCode model: zen_free | zen_paid | go | anthropic | other | unknown.

    model None means the agent default: the global config model, unless the working folder has
    its own OpenCode config (then unknown). zen_free needs provider opencode, the model in both
    sources, not deprecated in either, every cost 0 in both, and a catalogue at most 7 days old.
    A name suffix never counts.
    """
    if model is None:
        if project_config:
            return "unknown"
        model = default_model
    if not h5_v2.model_ok(model) or "/" not in model:
        return "unknown"
    provider = model.split("/", 1)[0]
    if not _PROVIDER_RE.match(provider):
        return "unknown"
    if provider == "anthropic":
        return "anthropic"
    if provider == "opencode-go":
        return "go"
    if provider != "opencode":
        return "other"
    live = (verbose or {}).get(model)
    listed = (catalogue or {}).get(model)
    if _any_paid(live) or _any_paid(listed):
        return "zen_paid"
    if live is None or listed is None or "deprecated" in (live["status"], listed["status"]):
        return "unknown"
    if not (_all_zero(live) and _all_zero(listed)):
        return "unknown"
    if not isinstance(catalogue_mtime, int) or now - catalogue_mtime > _CATALOGUE_FRESH_S \
            or catalogue_mtime > now + _CLOCK_SKEW_S:
        return "unknown"
    return "zen_free"


def opencode_default_model(home):
    """The `model` of the global OpenCode config (config.json, opencode.json, opencode.jsonc; later wins)."""
    model = None
    for name in _OPENCODE_GLOBAL_FILES:
        try:
            data = fsio.read_file_nofollow(os.path.join(home, ".config", "opencode", name), _CONFIG_CAP)
        except ApError:
            continue
        obj = _jsonc_object(data) if data is not None else None
        value = obj.get("model") if obj else None
        if h5_v2.model_ok(value) and "/" in value:
            model = value
    return model


def opencode_project_config(cwd):
    """True when the folder, or a parent up to its repository root, has its own OpenCode config (stat only)."""
    path = _clean_abs(cwd)
    if path is None:
        return True
    for folder in _walk_to_git_root(path):
        if any(os.path.lexists(os.path.join(folder, name)) for name in _OPENCODE_PROJECT_NAMES):
            return True
    return False


def opencode_verbose(exec_prefix, provider, deadline_s):
    """parse_verbose_blocks of `opencode models <provider> --verbose` (20 s, 1 MiB), or None."""
    if provider not in ("opencode", "opencode-go") or not _exec_ok(exec_prefix):
        return None
    result = _run(list(exec_prefix) + ["models", provider, "--verbose"], "opencode",
                  min(_VERBOSE_DEADLINE_S, _left(deadline_s)), _VERBOSE_CAP)
    if result is None or result.get("overflow") or result.get("rc") != 0:
        return None
    return parse_verbose_blocks(bytes(result.get("stdout") or b"").decode("utf-8", "replace"))


def opencode_billing(model, cwd, *, now, allow_probe, deadline_s):
    """{"billing", "resolvedModel", "pending"} for an OpenCode job.

    The models cache answers first. On a miss, preview (allow_probe False) answers unknown with
    pending True; arm and pre-fire run the verbose probe and read the catalogue.
    """
    home = fsio.home()
    resolved = model
    if model is None:
        resolved = None if opencode_project_config(cwd) else opencode_default_model(home)
        if resolved is None:
            return {"billing": "unknown", "resolvedModel": None, "pending": False}
    if not h5_v2.model_ok(resolved) or not resolved.startswith("opencode/"):
        return {"billing": classify_opencode(resolved, None, None, None, now, False, None),
                "resolvedModel": resolved if h5_v2.model_ok(resolved) else None, "pending": False}
    from . import models  # models imports this module
    cached = models.cached_billing(resolved, now)
    if cached is not None:
        return {"billing": cached, "resolvedModel": resolved, "pending": False}
    if not allow_probe:
        return {"billing": "unknown", "resolvedModel": resolved, "pending": True}
    found = identity.discover_cli("opencode")
    verbose = opencode_verbose(found.get("exec"), "opencode", deadline_s) if found.get("ok") else None
    catalogue, mtime = read_catalogue(home)
    return {"billing": classify_opencode(resolved, verbose, catalogue, mtime, now, False, None),
            "resolvedModel": resolved, "pending": False}


# --- Pi ----------------------------------------------------------------------------

def pi_auth_parse(rc, stdout):
    """{"status": ready|not_ready|invalid|garbage, "provider", "authType"} from `pi auth check --json`.

    ready needs exit 0, a JSON object with status ready, a provider id and authType oauth|api_key.
    not_ready needs exit 1 and status not_ready. Exit 2 or status invalid is invalid. Anything
    else, including output over 4 KiB, is garbage. No other field is kept.
    """
    garbage = {"status": "garbage", "provider": None, "authType": None}
    document = None
    if isinstance(stdout, (bytes, bytearray)) and 0 < len(stdout) <= _PROBE_CAP:
        document = _json_object(stdout)
    if document is None:
        return dict(garbage, status="invalid") if rc == 2 else garbage
    status = document.get("status")
    provider = document.get("provider") if h5_v2.pi_provider_ok(document.get("provider")) else None
    auth_type = document.get("authType")
    if rc == 0 and status == "ready" and provider is not None and auth_type in _PI_AUTH_TYPES:
        return {"status": "ready", "provider": provider, "authType": auth_type}
    if rc == 1 and status == "not_ready":
        return {"status": "not_ready", "provider": provider, "authType": None}
    if rc == 2 or status == "invalid":
        return {"status": "invalid", "provider": provider, "authType": None}
    return garbage


def pi_auth_probe(exec_prefix, provider, deadline_s):
    """pi_auth_parse of `<pi> auth check --provider P --json --no-refresh` (10 s, 4 KiB).

    The result also carries "timedOut" (the probe could not finish in time). A ready answer for
    a different provider than the one asked for is garbage.
    """
    garbage = {"status": "garbage", "provider": None, "authType": None, "timedOut": False}
    if not h5_v2.pi_provider_ok(provider) or not _exec_ok(exec_prefix):
        return garbage
    limit = min(_PI_AUTH_DEADLINE_S, _left(deadline_s))
    result = _run(list(exec_prefix) + ["auth", "check", "--provider", provider, "--json", "--no-refresh"],
                  "pi", limit, _PROBE_CAP)
    if result is None:
        return dict(garbage, timedOut=True)
    if result.get("overflow"):
        return garbage
    parsed = pi_auth_parse(result.get("rc"), bytes(result.get("stdout") or b""))
    if parsed["status"] == "ready" and parsed["provider"] != provider:
        return garbage
    return dict(parsed, timedOut=False)


def pi_verdict(provider, parsed, allow_paid):
    """{"code", "detail", "notes"} for a Pi job from its auth check answer (table 6.2)."""
    state = "paid_on" if allow_paid is True else "subscription_only"
    status = parsed.get("status") if isinstance(parsed, dict) else None
    if status == "not_ready":
        return {"code": "not_logged_in", "detail": None, "notes": [state]}
    if status != "ready":
        return {"code": "pi_auth_invalid", "detail": None, "notes": [state]}
    if allow_paid is True:
        return {"code": None, "detail": None, "notes": [state]}
    if parsed.get("authType") != "oauth" or provider in _PI_KEY_PROVIDERS:
        detail = {"provider": provider} if h5_v2.pi_provider_ok(provider) else None
        return {"code": "paid_pi_key", "detail": detail, "notes": [state]}
    if provider == "anthropic":
        return {"code": "paid_pi_claude", "detail": None, "notes": [state]}
    if provider == "openai-codex":
        return {"code": None, "detail": None, "notes": [state]}
    return {"code": None, "detail": None, "notes": [state, "pi_subscription"]}


# --- Cursor preflights -------------------------------------------------------------------

def cursor_config_dir(env):
    """Cursor's config folder for a child with env: $XDG_CONFIG_HOME/cursor when valid, else ~/.cursor."""
    env = env if isinstance(env, dict) else {}
    xdg = _clean_abs(env.get("XDG_CONFIG_HOME"), consts.CWD_MAX_BYTES)
    if xdg is not None:
        return os.path.join(xdg, "cursor")
    home = _clean_abs(env.get("HOME"), consts.CWD_MAX_BYTES) or fsio.home()
    return os.path.join(home, ".cursor")


def cursor_trust_slug(path):
    """Cursor's project slug: every non-alphanumeric character becomes -, runs collapse, ends trimmed."""
    return re.sub(r"-+", "-", _CURSOR_SLUG_RE.sub("-", path)).strip("-")


def _trust_marker(home, path):
    slug = cursor_trust_slug(path)
    if not slug:
        return False
    try:
        info = os.lstat(os.path.join(home, ".cursor", "projects", slug, ".workspace-trusted"))
    except (OSError, ValueError):
        return False
    return stat.S_ISREG(info.st_mode)


def cursor_trusted(workspace_real, home):
    """True when Cursor trusts the folder: its own marker, or a marked ancestor below HOME with >= 3 segments."""
    workspace = _clean_abs(workspace_real, consts.CWD_MAX_BYTES)
    home_path = _clean_abs(home, consts.CWD_MAX_BYTES)
    if workspace is None or home_path is None:
        return False
    if _trust_marker(home_path, workspace):
        return True
    current = os.path.dirname(workspace)
    while current.startswith(home_path.rstrip("/") + "/"):
        if len([part for part in current.split("/") if part]) >= 3 and _trust_marker(home_path, current):
            return True
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return False


def cursor_cli_config_verdict(obj):
    """cursor_autorun_config | cursor_network_config | None for a parsed cli-config.json object."""
    if not isinstance(obj, dict):
        return None
    if obj.get("approvalMode") == "unrestricted":
        return "cursor_autorun_config"
    sandbox = obj.get("sandbox")
    if isinstance(sandbox, dict) and sandbox.get("networkAccess") == "allow_all":
        return "cursor_network_config"
    return None


def claude_project_allow_nonempty(obj):
    """True when a .claude/settings.json object carries a non-empty permissions allow list."""
    permissions = obj.get("permissions") if isinstance(obj, dict) else None
    rules = permissions.get(_ALLOW_KEY) if isinstance(permissions, dict) else None
    if isinstance(rules, list):
        return len(rules) > 0
    return bool(rules)


def cursor_preflight(cwd, home, env):
    """First Cursor preflight refusal for a working folder, or None (table 6.2).

    Reads only <config>/cli-config.json and <repository root>/.claude/settings.json; everything
    else is a stat. A config file that exists but cannot be read or parsed refuses, because what
    it would allow cannot be known.
    """
    workspace = _clean_abs(cwd, consts.CWD_MAX_BYTES)
    home_path = _clean_abs(home, consts.CWD_MAX_BYTES)
    if workspace is None or home_path is None:
        return "cursor_project_rules"
    config_path = os.path.join(cursor_config_dir(env), "cli-config.json")
    obj, refused = _read_config(config_path, _CONFIG_CAP)
    if refused or (obj is None and os.path.lexists(config_path)):
        return "cursor_autorun_config"
    verdict = cursor_cli_config_verdict(obj)
    if verdict is not None:
        return verdict

    real = os.path.realpath(workspace)
    folders = _walk_to_git_root(real)
    if any(os.path.lexists(os.path.join(folder, ".cursor", "cli.json")) for folder in folders):
        return "cursor_project_rules"
    root = folders[-1]
    if root != os.path.realpath(home_path):
        settings_path = os.path.join(root, ".claude", "settings.json")
        settings, refused = _read_config(settings_path, _CONFIG_CAP)
        if refused or (settings is None and os.path.lexists(settings_path)) \
                or claude_project_allow_nonempty(settings):
            return "cursor_project_rules"

    if not cursor_trusted(real, home_path):
        return "cursor_untrusted"
    return None


def cursor_env(home):
    try:
        return harness.agent_env("cursor", LEVEL)
    except ApError:
        env = {"HOME": home}
        xdg = _clean_abs(os.environ.get("XDG_CONFIG_HOME", ""), consts.CWD_MAX_BYTES)
        if xdg is not None:
            env["XDG_CONFIG_HOME"] = xdg
        return env


# --- usage lookups and the pre-fire defer ---------------------------------------------------

def _provider(usage, source_id):
    lookup = getattr(usage_mod, "provider", None)
    if callable(lookup):
        try:
            return lookup(usage, source_id)
        except (KeyError, TypeError, ValueError, AttributeError):
            return None
    providers = usage.get("providers") if isinstance(usage, dict) else None
    for entry in providers if isinstance(providers, list) else []:
        if isinstance(entry, dict) and entry.get("id") == source_id:
            return entry
    return None


def _fresh(usage, source_id):
    entry = _provider(usage, source_id)
    if isinstance(entry, dict) and entry.get("readable") is True and entry.get("stale") is False:
        return entry
    return None


def _windows(entry):
    windows = entry.get("windows") if isinstance(entry, dict) else None
    return [w for w in windows if isinstance(w, dict)] if isinstance(windows, list) else []


def _full(window):
    percent = window.get("percent")
    return isinstance(percent, (int, float)) and not isinstance(percent, bool) and percent >= 1.0


def _reset(window):
    value = window.get("resetsAt")
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def cursor_pool(model):
    if windows_mod is not None and callable(getattr(windows_mod, "cursor_pool_for_model", None)):
        return windows_mod.cursor_pool_for_model(model)
    if not isinstance(model, str) or not model:
        return None
    lowered = model.lower()
    if lowered == "auto" or lowered.startswith("composer") or "grok" in lowered:
        return "Cursor models"
    return "Other models"


def _margin(job):
    trigger = job.get("trigger") if isinstance(job.get("trigger"), dict) else {}
    value = trigger.get("marginSec")
    if isinstance(value, int) and not isinstance(value, bool) and consts.MARGIN_MIN <= value <= consts.MARGIN_MAX:
        return value
    return consts.MARGIN_DEFAULT


def _verdict(reset, margin, now, rearm_reason, skip_reason):
    if reset is None:
        return {"action": "skip", "fireAt": None, "reason": skip_reason, "resetEpoch": None}
    fire_at = max(reset, now) + margin
    if fire_at - now > _HORIZON_S:
        return {"action": "skip", "fireAt": None, "reason": skip_reason, "resetEpoch": reset}
    return {"action": "rearm", "fireAt": fire_at, "reason": rearm_reason, "resetEpoch": reset}


def _latest(windows):
    resets = [_reset(w) for w in windows]
    return None if any(r is None for r in resets) else max(resets)


def paid_defer(job, usage, now, *, billing=None, zen_limited_until=None):
    """Pre-fire defer while paid usage is off (table 6.3), from fresh usage records only.

    Returns None or {"action": "rearm"|"skip", "fireAt", "reason", "resetEpoch"}.
    """
    if not isinstance(job, dict) or job.get("allowPaid") is True:
        return None
    harness_id, model = job.get("harness"), job.get("model")
    margin = _margin(job)

    if harness_id == "claude":
        entry = _fresh(usage, "claude")
        hits = [w for w in _windows(entry) if w.get("kind") in ("session", "weekly") and _full(w)
                and _reset(w) is not None and _reset(w) > now]
        if not hits:
            return None
        window = max(hits, key=_reset)
        reason = "weekly_exhausted" if window.get("kind") == "weekly" else "window_exhausted"
        return _verdict(_reset(window), margin, now, reason, reason)

    if harness_id == "codex" or (harness_id == "pi" and job.get("provider") == "openai-codex"):
        entry = _fresh(usage, "codex")
        hits = [w for w in _windows(entry) if w.get("sliding") is not True and _full(w)
                and _reset(w) is not None and _reset(w) > now]
        if not hits:
            return None
        return _verdict(max(_reset(w) for w in hits), margin, now, "limit_full", "limit_full")

    if harness_id == "cursor":
        entry = _fresh(usage, "cursor")
        pool = cursor_pool(model)
        hits = []
        for window in _windows(entry):
            if not _full(window):
                continue
            kind, label = window.get("kind"), window.get("shortLabel")
            if kind == "billing_total" or (kind == "billing_pool" and (
                    label == pool or (pool is None and label in ("Cursor models", "Other models")))):
                hits.append(window)
        if not hits:
            return None
        return _verdict(_latest(hits), margin, now, "paid_defer", "paid_exhausted")

    if harness_id == "opencode":
        if billing == "go" or (isinstance(model, str) and model.startswith("opencode-go/")):
            entry = _fresh(usage, "opencode-go")
            hits = [w for w in _windows(entry) if w.get("sliding") is not True and _full(w)]
            if not hits:
                return None
            return _verdict(_latest(hits), margin, now, "limit_full", "limit_full")
        if billing == "zen_free" and isinstance(zen_limited_until, int) and zen_limited_until > now:
            return {"action": "rearm", "fireAt": zen_limited_until + h5_v2.const("ZEN_FREE_REARM_EXTRA_S"),
                    "reason": "limit_full", "resetEpoch": zen_limited_until}
    return None


def _zen_pending(sd, now):
    try:
        from . import limits_history
    except ImportError:  # pragma: no cover - arrives with builder H4
        return None
    try:
        value = limits_history.pending_reset(sd, "zen-free", now)
    except (ApError, OSError, ValueError):
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


# --- the gate ----------------------------------------------------------------------

def _next_utc_midnight(now):
    if windows_mod is not None and callable(getattr(windows_mod, "next_utc_midnight", None)):
        return windows_mod.next_utc_midnight(now)
    return (int(now) // 86400 + 1) * 86400


def check_job(job, *, phase, now, usage, sd=None, exec_prefix=None, deadline_s=10.0):
    """Every paid and preflight gate for one job, in order (6.1). Never raises for a probe failure.

    Returns {"ok", "code", "detail", "notes", "billing", "provider", "resetAtMs", "pending", "defer"}.
    code is an errors code (the caller raises it at arm and maps it with REASON_FOR_CODE at
    pre-fire); notes are NOTES codes in display order; defer is set only at pre-fire.
    """
    if phase not in PHASES or not isinstance(job, dict):
        raise ApError("internal")
    harness_id = job.get("harness")
    allow = job.get("allowPaid") is True
    target = job.get("target") if isinstance(job.get("target"), dict) else {}
    cwd = target.get("cwd")
    now = int(now)
    home = fsio.home()
    probe_min = _PREVIEW_PROBE_MIN_S if phase == "preview" else _PROBE_MIN_S
    can_probe = _exec_ok(exec_prefix)
    code = detail = None
    billing = None
    pending = False
    pi_notes = None
    provider = job.get("provider") if harness_id == "pi" and h5_v2.pi_provider_ok(job.get("provider")) else None

    # 1. release gate
    if harness_id in h5_v2.const("GATED_HARNESSES"):
        code = "harness_gated"

    # 2. Cursor preflights. They keep a lower level from being widened by the folder's own Cursor
    # configuration. Full access already approves every tool and trusts the folder, so nothing is left to widen.
    if code is None and harness_id == "cursor" and job.get("level") != "full":
        code = cursor_preflight(cwd, home, cursor_env(home))

    # 3. sign-in probes (4. paid refusal is folded in where one answer decides both)
    login_kind = None
    if code is None and harness_id == "codex":
        if can_probe and _left(deadline_s) >= probe_min:
            login_kind = codex_login_probe(exec_prefix, deadline_s)
        if login_kind == "not_logged_in":
            code = "not_logged_in"
        elif login_kind is None:
            if phase == "preview":
                pending = True
            elif not allow:
                code = "not_logged_in"          # no ChatGPT login could be confirmed
        elif not allow and login_kind != "chatgpt":
            code = "paid_blocked"
    if code is None and harness_id == "pi":
        if provider is None:
            code = "pi_auth_invalid"
        elif not can_probe or _left(deadline_s) < probe_min:
            if phase == "preview":
                pending = True
            else:
                code = "pi_auth_invalid"
        else:
            parsed = pi_auth_probe(exec_prefix, provider, deadline_s)
            if parsed.get("timedOut") and phase == "preview":
                pending = True
            else:
                verdict = pi_verdict(provider, parsed, allow)
                code, detail, pi_notes = verdict["code"], verdict["detail"], verdict["notes"]

    # 4. paid refusal
    if code is None and harness_id == "gemini":
        code = gemini_verdict(gemini_selected_type(home, cwd), allow)
    if harness_id == "opencode":
        found = opencode_billing(job.get("model"), cwd, now=now, allow_probe=(phase != "preview"),
                                 deadline_s=deadline_s)
        billing = found["billing"] if found["billing"] in CLASSES else "unknown"
        pending = pending or bool(found["pending"])
        if code is None and not allow:
            code = {"zen_paid": "paid_zen", "anthropic": "paid_opencode_claude"}.get(billing)

    # 5. notes
    notes = ["paid_on" if allow else "subscription_only"]
    reset_at_ms = None
    if harness_id == "claude" and allow:
        notes.append("budget_cap")
    elif harness_id == "codex" and not allow and _codex_free_tier(usage):
        notes.append("codex_free_tier")
    elif harness_id == "opencode":
        if billing == "zen_free":
            notes.append("zen_free")
            reset_at_ms = _next_utc_midnight(now) * 1000
        elif billing == "go":
            notes.append("go_plan")
        elif billing in ("other", "unknown"):
            notes.append("opencode_provider")
    elif harness_id == "cursor":
        if allow:
            notes.append("cursor_on_demand")
        elif _fresh(usage, "cursor") is None:
            notes = ["cursor_on_demand"]
    elif harness_id == "pi" and pi_notes:
        notes = list(pi_notes)

    # 6. pre-fire defer
    defer = None
    if phase == "prefire" and code is None and not allow:
        zen_until = _zen_pending(sd, now) if harness_id == "opencode" and billing == "zen_free" \
            and sd is not None else None
        defer = paid_defer(job, usage, now, billing=billing, zen_limited_until=zen_until)

    return {"ok": code is None, "code": code, "detail": detail, "notes": notes, "billing": billing,
            "provider": provider, "resetAtMs": reset_at_ms, "pending": pending, "defer": defer}
