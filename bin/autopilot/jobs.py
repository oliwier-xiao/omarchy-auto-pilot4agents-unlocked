"""Jobs: draft validation, the job store, digests, prompts, run records and retention."""
import copy
import hashlib
import json
import os
import re
import secrets
import stat

from . import consts, edition, fsio, timeutil
from .errors import ApError

JOBS_FILE = "jobs.json"
PROMPTS_DIR = "prompts"
RUNS_DIR = "runs"

_RUN_ID_RE = re.compile(r"^([0-9a-f]{16})-g([0-9]{1,6})$")
_RUN_FILE_RE = re.compile(r"^([0-9a-f]{16})-g([0-9]{1,6})\.(json|log|last\.txt)$")
_PROMPT_FILE_RE = re.compile(r"^([0-9a-f]{16})\.txt$")
_TEMP_FILE_RE = re.compile(r"^\..+\.[0-9a-f]{16}\.tmp$")
_DETAIL_RE = re.compile(r"^[a-z_]{1,40}$")
# History events that do not change what a job's status says (public state.statusAt skips them).
_HOUSEKEEPING_EVENTS = ("prompt_deleted", "cli_changed", "late_result")
_VERSION_RE = re.compile(r"^[0-9A-Za-z.+_-]{1,40}$")
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
_TEMP_MAX_AGE_S = 3600
_BASIS_SOURCES = consts.LIMIT_SOURCES + ("clock",)
_SESSION_PATH_MAX = 4096

# Shared scratch space is never a working folder. A folder inside HOME is the user's own,
# whatever HOME itself sits under.
REFUSED_CWD_PREFIXES = ("/tmp", "/run")

_TOP_DRAFT_KEYS = ("id", "label", "harness", "target", "level", "limits", "model", "trigger", "prompt",
                   "expectCommandDigest", "allowPaid", "provider")
_TARGET_DRAFT_KEYS = ("mode", "sessionId", "cwd", "allowNonGit", "sessionPath")
_LIMIT_KEYS = ("maxTurns", "budgetUsd", "runtimeSec")
_TRIGGER_DRAFT_KEYS = ("kind", "fireAt", "delaySec", "marginSec", "weeklyPolicy")


# ---------------------------------------------------------------------------- small checks

def is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def is_number(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and value == value and value not in (float("inf"), float("-inf")))


def is_epoch(value):
    return is_int(value) and consts.EPOCH_MIN <= value <= consts.EPOCH_MAX


def clean_text(value, limit):
    """Printable characters only, whitespace collapsed, at most limit characters."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(value))
    text = " ".join(text.split())
    return text[:limit].rstrip()


def model_ok(value):
    """The v2 model grammar: MODEL_RE with an optional bracket suffix, at most MODEL_MAX characters."""
    return isinstance(value, str) and len(value) <= consts.MODEL_MAX and consts.MODEL_RE.fullmatch(value) is not None


def _stored_model_ok(value):
    return model_ok(value) or (isinstance(value, str) and consts.MODEL_RE_V1.fullmatch(value) is not None)


def session_id_ok(harness, session_id):
    if not isinstance(session_id, str):
        return False
    if harness == "opencode":
        return bool(consts.OPENCODE_ID_RE.fullmatch(session_id))
    return bool(consts.UUID_RE.fullmatch(session_id))


def _abs_path_ok(value, max_bytes=4096):
    if not isinstance(value, str) or not value.startswith("/") or "\0" in value or "\n" in value:
        return False
    try:
        return len(value.encode("utf-8")) <= max_bytes
    except UnicodeEncodeError:
        return False


def _prompt_bytes(prompt, field="prompt"):
    if not isinstance(prompt, str):
        raise ApError("bad_input", field)
    try:
        data = prompt.encode("utf-8")
    except UnicodeEncodeError:
        raise ApError("bad_input", field) from None
    if not prompt.strip():
        raise ApError("prompt_empty", field)
    if len(data) > consts.PROMPT_MAX_BYTES:
        raise ApError("prompt_too_large", field)
    return data


# ---------------------------------------------------------------------------- drafts

def validate_draft(draft, *, require_prompt):
    """Normalized draft; raises the invalid_* and prompt_* codes with a field path (v2 3.2 order)."""
    if not isinstance(draft, dict):
        raise ApError("bad_input")
    for key in draft:
        if key not in _TOP_DRAFT_KEYS:
            raise ApError("bad_input")
    if "id" in draft and not (isinstance(draft["id"], str) and consts.JOB_ID_RE.fullmatch(draft["id"])):
        raise ApError("bad_input", "id")

    harness = draft.get("harness")
    if harness not in consts.HARNESSES:
        raise ApError("invalid_harness", "harness")

    allow_paid = draft.get("allowPaid", False)
    if not isinstance(allow_paid, bool):
        raise ApError("bad_input", "allowPaid")

    prompt = draft.get("prompt")
    if prompt is None:
        if require_prompt:
            raise ApError("prompt_empty", "prompt")
    else:
        _prompt_bytes(prompt)

    label = draft.get("label")
    if label is not None:
        if not isinstance(label, str):
            raise ApError("invalid_label", "label")
        label = label.strip()
        if not label:
            label = None
        elif len(label) > consts.LABEL_MAX or not label.isprintable():
            raise ApError("invalid_label", "label")

    target = draft.get("target")
    if not isinstance(target, dict):
        raise ApError("invalid_target", "target")
    for key in target:
        if key not in _TARGET_DRAFT_KEYS:
            raise ApError("invalid_target", "target")
    mode = target.get("mode")
    if mode not in consts.TARGET_MODES:
        raise ApError("invalid_target", "target.mode")

    level_id = draft.get("level")
    level = edition.level(level_id) if isinstance(level_id, str) else None
    if level is None:
        raise ApError("invalid_level", "level")
    if harness not in level["harness"]:
        raise ApError("level_unavailable", "level")

    provider = draft.get("provider")
    model = draft.get("model")
    if harness == "pi":
        if not (isinstance(provider, str) and consts.PI_PROVIDER_RE.fullmatch(provider)):
            raise ApError("pi_model_required", "provider")
        if model in (None, ""):
            raise ApError("pi_model_required", "model")
    elif provider is not None:
        raise ApError("bad_input", "provider")
    if model in (None, ""):
        model = None
    elif not model_ok(model):
        raise ApError("invalid_model", "model")
    if harness == "cursor" and model == "auto":
        raise ApError("invalid_model", "model")

    session_id = target.get("sessionId")
    session_path = target.get("sessionPath")
    cwd = target.get("cwd")
    if harness == "pi" and mode in ("resume", "fork"):
        if not (isinstance(session_id, str) and consts.UUID_RE.fullmatch(session_id)):
            raise ApError("invalid_session", "target.sessionId")
        if not _abs_path_ok(session_path, _SESSION_PATH_MAX):
            raise ApError("invalid_session", "target.sessionPath")
    elif session_path is not None:
        raise ApError("invalid_target", "target.sessionPath")
    if mode == "fork" and not consts.CAN_FORK[harness]:
        raise ApError("fork_unsupported", "target.mode")
    if mode in ("resume", "fork"):
        if not session_id_ok(harness, session_id):
            raise ApError("invalid_session", "target.sessionId")
        cwd = None
    else:
        if session_id not in (None, ""):
            raise ApError("invalid_target", "target.sessionId")
        session_id = None
        if not _abs_path_ok(cwd, consts.CWD_MAX_BYTES):
            raise ApError("invalid_cwd", "target.cwd")
    allow_non_git = target.get("allowNonGit", False)
    if not isinstance(allow_non_git, bool):
        raise ApError("invalid_target", "target.allowNonGit")
    if harness != "codex":
        allow_non_git = False

    limits_in = draft.get("limits")
    if limits_in is None:
        limits_in = {}
    if not isinstance(limits_in, dict):
        raise ApError("invalid_limits", "limits")
    for key in limits_in:
        if key not in _LIMIT_KEYS:
            raise ApError("invalid_limits", "limits")
    limits = {"maxTurns": level["defaultMaxTurns"], "budgetUsd": consts.BUDGET_DEFAULT,
              "runtimeSec": consts.RUNTIME_DEFAULT}
    if limits_in.get("maxTurns") is not None:
        value = limits_in["maxTurns"]
        if not is_int(value) or not consts.MAX_TURNS_MIN <= value <= consts.MAX_TURNS_MAX:
            raise ApError("invalid_limits", "limits.maxTurns")
        limits["maxTurns"] = value
    if limits_in.get("budgetUsd") is not None:
        value = limits_in["budgetUsd"]
        if not is_number(value) or not consts.BUDGET_MIN <= value <= consts.BUDGET_MAX:
            raise ApError("invalid_limits", "limits.budgetUsd")
        limits["budgetUsd"] = round(float(value), 2)
    if limits_in.get("runtimeSec") is not None:
        value = limits_in["runtimeSec"]
        if not is_int(value) or not consts.RUNTIME_MIN <= value <= consts.RUNTIME_MAX:
            raise ApError("invalid_limits", "limits.runtimeSec")
        limits["runtimeSec"] = value

    trigger_in = draft.get("trigger")
    if not isinstance(trigger_in, dict):
        raise ApError("invalid_trigger", "trigger")
    for key in trigger_in:
        if key not in _TRIGGER_DRAFT_KEYS:
            raise ApError("invalid_trigger", "trigger")
    kind = trigger_in.get("kind")
    if kind not in consts.TRIGGER_KINDS:
        raise ApError("invalid_trigger", "trigger.kind")
    if kind in consts.RESET_KINDS:
        # Legacy kinds (an OpenCode job on the Claude reset) load from the store but are never drafted.
        if kind not in consts.RESET_KINDS_FOR[harness]:
            raise ApError("trigger_unsupported", "trigger.kind")
        if kind == "codex_window_reset" and harness == "pi" and provider != "openai-codex":
            raise ApError("trigger_unsupported", "trigger.kind")
        if kind == "zen_free_reset" and not (model or "").startswith("opencode/"):
            raise ApError("trigger_unsupported", "trigger.kind")
        if kind == "go_window_reset" and not (model or "").startswith("opencode-go/"):
            raise ApError("trigger_unsupported", "trigger.kind")
    fire_at = None
    delay = None
    if kind == "at":
        fire_at = trigger_in.get("fireAt")
        if not is_epoch(fire_at):
            raise ApError("invalid_trigger", "trigger.fireAt")
    elif kind == "in":
        delay = trigger_in.get("delaySec")
        if not is_int(delay) or not consts.DELAY_MIN <= delay <= consts.HORIZON_S:
            raise ApError("invalid_trigger", "trigger.delaySec")
    margin = trigger_in.get("marginSec")
    if margin is not None and (not is_int(margin) or not consts.MARGIN_MIN <= margin <= consts.MARGIN_MAX):
        raise ApError("invalid_trigger", "trigger.marginSec")
    policy = trigger_in.get("weeklyPolicy")
    if policy is None:
        policy = "defer"
    elif policy not in consts.WEEKLY_POLICIES:
        raise ApError("invalid_trigger", "trigger.weeklyPolicy")

    expect = draft.get("expectCommandDigest")
    if expect is not None and not (isinstance(expect, str) and consts.DIGEST_RE.fullmatch(expect)):
        raise ApError("bad_input", "expectCommandDigest")

    # Pi trims the prompt and runs one that starts with / as a command, swallowing it.
    if harness == "pi" and isinstance(prompt, str) and prompt.lstrip().startswith("/"):
        raise ApError("pi_slash_prompt", "prompt")

    return {
        "label": label,
        "harness": harness,
        "target": {"mode": mode, "sessionId": session_id, "cwd": cwd, "allowNonGit": allow_non_git,
                   "sessionPath": session_path if harness == "pi" and mode in ("resume", "fork") else None},
        "level": level["id"],
        "limits": limits,
        "model": model,
        "trigger": {"kind": kind, "fireAt": fire_at, "delaySec": delay, "marginSec": margin,
                    "weeklyPolicy": policy},
        "prompt": prompt,
        "expectCommandDigest": expect,
        "allowPaid": allow_paid,
        "provider": provider if harness == "pi" else None,
    }


def _under(path, root):
    return path == root or path.startswith(root.rstrip("/") + "/")


def check_cwd(path):
    """The realpath of an allowed working folder, or invalid_cwd."""
    if not _abs_path_ok(path, consts.CWD_MAX_BYTES):
        raise ApError("invalid_cwd", "target.cwd")
    real = os.path.realpath(path)
    home = os.path.realpath(fsio.home())
    refused = real == "/" or real == home
    if not _under(real, home) and any(real.startswith(prefix) for prefix in REFUSED_CWD_PREFIXES):
        refused = True
    if _under(real, fsio.plugin_dir()) or _under(real, os.path.realpath(home + "/.config/omarchy/plugins")):
        refused = True
    if refused or not _abs_path_ok(real, consts.CWD_MAX_BYTES):
        raise ApError("invalid_cwd", "target.cwd")
    try:
        st = os.stat(real)
    except OSError:
        raise ApError("invalid_cwd", "target.cwd") from None
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise ApError("invalid_cwd", "target.cwd")
    return real


def default_label(harness_id, cwd):
    """The label of a job the user did not name, built from job metadata only.

    Never from the prompt: a label reaches the notification body, and with it busctl's argv and
    the toast, where prompt text must never appear (R0 D9, D17).
    """
    name = consts.HARNESS_NAMES.get(harness_id, "Agent")
    base = os.path.basename(str(cwd or "").rstrip("/"))
    label = clean_text("%s in %s" % (name, base), consts.LABEL_MAX) if base else ""
    return label or name + " job"


# ---------------------------------------------------------------------------- digests

def _canonical_sha(obj):
    data = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def command_digest(job):
    """Binds everything that decides what runs, except the prompt text.

    target.newSessionId is left out: it is generated by the helper when the job is stored, so a
    preview made before that could never match it, and it names a session that does not exist yet.

    The v2 fields (allowPaid, provider, target.sessionPath) are bound only when set, so every digest
    stored by an earlier release stays equal and armed jobs do not need confirming again.
    """
    target = job["target"]
    bound_target = {"mode": target["mode"], "sessionId": target.get("sessionId"), "cwd": target["cwd"],
                    "allowNonGit": bool(target.get("allowNonGit"))}
    if target.get("sessionPath") is not None:
        bound_target["sessionPath"] = target["sessionPath"]
    bound = {
        "harness": job["harness"],
        "cli": {"link": (job.get("cli") or {}).get("link")},
        "target": bound_target,
        "level": job["level"],
        "limits": {k: job["limits"][k] for k in _LIMIT_KEYS},
        "model": job.get("model"),
        "trigger": {"kind": job["trigger"]["kind"]},
    }
    if job.get("allowPaid") is True:
        bound["allowPaid"] = True
    if job.get("provider") is not None:
        bound["provider"] = job["provider"]
    return _canonical_sha(bound)


def full_digest(job):
    """commandDigest plus the prompt hash, and the helper-generated new session id.

    newSessionId stays out of commandDigest so a preview can match it, but it decides which
    session a first run creates, so the digest checked at fire time still binds it.
    """
    return _canonical_sha({"commandDigest": command_digest(job), "promptSha256": job["promptSha256"],
                           "newSessionId": (job.get("target") or {}).get("newSessionId")})


def refresh_digests(job):
    job["commandDigest"] = command_digest(job)
    job["digest"] = full_digest(job)


# ---------------------------------------------------------------------------- job schema

def _fail(_what=None):
    raise ApError("state_corrupt")


def _keys(obj, required, optional=()):
    if not isinstance(obj, dict):
        _fail()
    for key in obj:
        if key not in required and key not in optional:
            _fail()
    for key in required:
        if key not in obj:
            _fail()


def _opt(value, check):
    return value is None or check(value)


def _count(value):
    return is_int(value) and 0 <= value <= 100000


_JOB_KEYS = ("id", "label", "createdAt", "updatedAt", "harness", "cli", "target", "level", "limits", "model",
             "trigger", "promptSha256", "promptBytes", "promptAvailable", "commandDigest", "digest", "state",
             "allowPaid", "provider")
_STATE_KEYS = ("status", "wait", "reason", "gen", "fireAt", "unit", "armedAt", "pluginDir", "basis",
               "runSessionId", "defers", "limitRetries", "transientRetries", "busyDefers", "unknownRetries",
               "lastRun", "history", "runSessionPath")


def upgrade_job(job):
    """Add the v2 keys a job stored by an earlier release lacks (before validate_job)."""
    if not isinstance(job, dict):
        return job
    job.setdefault("allowPaid", False)
    job.setdefault("provider", None)
    if isinstance(job.get("target"), dict):
        job["target"].setdefault("sessionPath", None)
    if isinstance(job.get("state"), dict):
        job["state"].setdefault("runSessionPath", None)
    return job
_LAST_RUN_KEYS = ("runId", "startedAt", "endedAt", "outcome", "exit", "signal", "bytesDropped")


def validate_job(job):
    """Full Job schema check for anything loaded from jobs.json; raises state_corrupt."""
    _keys(job, _JOB_KEYS)
    if not isinstance(job["id"], str) or not consts.JOB_ID_RE.fullmatch(job["id"]):
        _fail()
    label = job["label"]
    if not isinstance(label, str) or not 1 <= len(label) <= consts.LABEL_MAX or not label.isprintable():
        _fail()
    if not is_epoch(job["createdAt"]) or not is_epoch(job["updatedAt"]):
        _fail()
    harness = job["harness"]
    if harness not in consts.HARNESSES:
        _fail()
    cli = job["cli"]
    _keys(cli, ("link", "real", "version"))
    if not _abs_path_ok(cli["link"]) or not _abs_path_ok(cli["real"]):
        _fail()
    if not _opt(cli["version"], lambda v: isinstance(v, str) and _VERSION_RE.fullmatch(v)):
        _fail()
    if not isinstance(job["allowPaid"], bool):
        _fail()
    if harness == "pi":
        if not (isinstance(job["provider"], str) and consts.PI_PROVIDER_RE.fullmatch(job["provider"])):
            _fail()
        if job["model"] is None:
            _fail()
    elif job["provider"] is not None:
        _fail()
    target = job["target"]
    _keys(target, ("mode", "sessionId", "newSessionId", "cwd", "title", "allowNonGit", "sessionPath"))
    if target["mode"] not in consts.TARGET_MODES:
        _fail()
    if harness == "pi" and target["mode"] in ("resume", "fork"):
        if not _abs_path_ok(target["sessionPath"], _SESSION_PATH_MAX):
            _fail()
    elif target["sessionPath"] is not None:
        _fail()
    if target["mode"] == "new":
        if target["sessionId"] is not None:
            _fail()
    elif not session_id_ok(harness, target["sessionId"]):
        _fail()
    if target["mode"] == "fork" and not consts.CAN_FORK[harness]:
        _fail()
    if not _opt(target["newSessionId"], lambda v: isinstance(v, str) and consts.UUID_RE.fullmatch(v)):
        _fail()
    if not _abs_path_ok(target["cwd"], consts.CWD_MAX_BYTES):
        _fail()
    title = target["title"]
    if not isinstance(title, str) or len(title) > consts.TITLE_MAX or not title.isprintable():
        _fail()
    if not isinstance(target["allowNonGit"], bool) or (target["allowNonGit"] and harness != "codex"):
        _fail()
    level = edition.level(job["level"]) if isinstance(job["level"], str) else None
    if level is None or harness not in level["harness"]:
        _fail()
    limits = job["limits"]
    _keys(limits, _LIMIT_KEYS)
    if not is_int(limits["maxTurns"]) or not consts.MAX_TURNS_MIN <= limits["maxTurns"] <= consts.MAX_TURNS_MAX:
        _fail()
    if not is_number(limits["budgetUsd"]) or not consts.BUDGET_MIN <= limits["budgetUsd"] <= consts.BUDGET_MAX:
        _fail()
    if not is_int(limits["runtimeSec"]) or not consts.RUNTIME_MIN <= limits["runtimeSec"] <= consts.RUNTIME_MAX:
        _fail()
    if not _opt(job["model"], _stored_model_ok):
        _fail()
    trigger = job["trigger"]
    _keys(trigger, ("kind", "fireAt", "delaySec", "marginSec", "weeklyPolicy", "graceSec"))
    if trigger["kind"] not in consts.TRIGGER_KINDS:
        _fail()
    allowed_resets = consts.RESET_KINDS_FOR[harness] + consts.LEGACY_RESET_KINDS.get(harness, ())
    if trigger["kind"] in consts.RESET_KINDS and trigger["kind"] not in allowed_resets:
        _fail()
    if not _opt(trigger["fireAt"], is_epoch):
        _fail()
    if trigger["kind"] == "at" and trigger["fireAt"] is None:
        _fail()
    if not _opt(trigger["delaySec"], lambda v: is_int(v) and consts.DELAY_MIN <= v <= consts.HORIZON_S):
        _fail()
    if trigger["kind"] == "in" and trigger["delaySec"] is None:
        _fail()
    if not is_int(trigger["marginSec"]) or not consts.MARGIN_MIN <= trigger["marginSec"] <= consts.MARGIN_MAX:
        _fail()
    if trigger["weeklyPolicy"] not in consts.WEEKLY_POLICIES:
        _fail()
    if trigger["graceSec"] not in (consts.GRACE_TIME_S, consts.GRACE_RESET_S):
        _fail()
    for key in ("promptSha256", "commandDigest", "digest"):
        if not isinstance(job[key], str) or not consts.DIGEST_RE.fullmatch(job[key]):
            _fail()
    if not is_int(job["promptBytes"]) or not 0 <= job["promptBytes"] <= consts.PROMPT_MAX_BYTES:
        _fail()
    if not isinstance(job["promptAvailable"], bool):
        _fail()
    _validate_state(job["state"], job["id"], harness)
    return job


def _validate_state(state, job_id, harness):
    _keys(state, _STATE_KEYS)
    if state["status"] not in consts.STATUSES or state["wait"] not in consts.WAITS:
        _fail()
    if not _opt(state["reason"], lambda v: v in consts.REASONS):
        _fail()
    if not is_int(state["gen"]) or not 0 <= state["gen"] <= 999999:
        _fail()
    if not _opt(state["fireAt"], is_epoch) or not _opt(state["armedAt"], is_epoch):
        _fail()
    unit = state["unit"]
    if unit is not None:
        match = consts.UNIT_RE.fullmatch(unit) if isinstance(unit, str) else None
        if not match or match.group(1) != job_id:
            _fail()
    if not _opt(state["pluginDir"], _abs_path_ok):
        _fail()
    basis = state["basis"]
    if basis is not None:
        _keys(basis, (), ("source", "resetEpoch", "fetchedAtMs", "percent"))
        if not _opt(basis.get("source"), lambda v: v in _BASIS_SOURCES):
            _fail()
        if not _opt(basis.get("resetEpoch"), is_int) or not _opt(basis.get("fetchedAtMs"), is_int):
            _fail()
        if not _opt(basis.get("percent"), is_number):
            _fail()
    if not _opt(state["runSessionId"], lambda v: session_id_ok(harness, v)):
        _fail()
    if harness == "pi":
        # A Pi run's session is reopened by its file, so the id and the path are kept together.
        if (state["runSessionId"] is None) != (state["runSessionPath"] is None):
            _fail()
        if not _opt(state["runSessionPath"], lambda v: _abs_path_ok(v, _SESSION_PATH_MAX)):
            _fail()
    elif state["runSessionPath"] is not None:
        _fail()
    for key in ("defers", "limitRetries", "transientRetries", "busyDefers", "unknownRetries"):
        if not _count(state[key]):
            _fail()
    last = state["lastRun"]
    if last is not None:
        _keys(last, ("runId",), _LAST_RUN_KEYS)
        match = _RUN_ID_RE.fullmatch(last["runId"]) if isinstance(last["runId"], str) else None
        if not match or match.group(1) != job_id:
            _fail()
        for key in ("startedAt", "endedAt"):
            if not _opt(last.get(key), is_epoch):
                _fail()
        if not _opt(last.get("outcome"), lambda v: v in consts.OUTCOMES):
            _fail()
        for key in ("exit", "signal"):
            if not _opt(last.get(key), is_int):
                _fail()
        if not _opt(last.get("bytesDropped"), lambda v: is_int(v) and v >= 0):
            _fail()
    history = state["history"]
    if not isinstance(history, list) or len(history) > consts.HISTORY_MAX:
        _fail()
    for entry in history:
        _keys(entry, ("at", "event", "detail"))
        if not is_epoch(entry["at"]) or entry["event"] not in consts.HISTORY_EVENTS:
            _fail()
        if not _opt(entry["detail"], lambda v: isinstance(v, str) and _DETAIL_RE.fullmatch(v)):
            _fail()


# ---------------------------------------------------------------------------- store

def empty_store():
    return {"schemaVersion": edition.SCHEMA_VERSION, "updatedAt": 0, "reconciledAt": None, "jobs": []}


def load_store(sd):
    raw = sd.read_json(JOBS_FILE, consts.JOBS_FILE_MAX)
    if raw is None:
        return empty_store()
    _keys(raw, ("schemaVersion", "updatedAt", "reconciledAt", "jobs"))
    if raw["schemaVersion"] != edition.SCHEMA_VERSION:
        _fail()
    if not is_int(raw["updatedAt"]) or raw["updatedAt"] < 0 or not _opt(raw["reconciledAt"], is_epoch):
        _fail()
    jobs = raw["jobs"]
    if not isinstance(jobs, list) or len(jobs) > consts.JOBS_MAX:
        _fail()
    seen = set()
    for job in jobs:
        validate_job(upgrade_job(job))
        if job["id"] in seen:
            _fail()
        seen.add(job["id"])
    return raw


def _encode_store(store):
    return json.dumps(store, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def has_room_for(store, job):
    """True when adding job keeps the store under the create threshold (a share of JOBS_FILE_MAX)."""
    limit = consts.JOBS_FILE_MAX * consts.JOBS_CREATE_PERCENT // 100
    return len(_encode_store(dict(store, jobs=store["jobs"] + [job]))) <= limit


def save_store(sd, store):
    """Write the store. When it would pass the byte cap, the oldest finished History jobs make way.

    job-create keeps a reserve, so this only happens in a store filled some other way. A job that is
    already stored must still be able to change state: a runner has to record its claim and a
    reconcile has to mark a job missed, or armed jobs would stop firing with nothing said.
    """
    if len(store["jobs"]) > consts.JOBS_MAX:
        raise ApError("store_full")
    store["updatedAt"] = timeutil.now()
    data = _encode_store(store)
    evicted = []
    if len(data) > consts.JOBS_FILE_MAX:
        now = store["updatedAt"]
        kept = list(store["jobs"])
        victims = sorted((j for j in kept if j["state"]["status"] not in ("armed", "running")
                          and not in_queue(j)), key=_job_ended_at)
        for victim in victims:
            kept = [j for j in kept if j is not victim]
            evicted.append(victim["id"])
            data = _encode_store(dict(store, jobs=kept))
            if len(data) <= consts.JOBS_FILE_MAX:
                break
        if len(data) > consts.JOBS_FILE_MAX:
            raise ApError("store_full")
        store["jobs"] = kept
    sd.write_atomic(JOBS_FILE, data)
    for job_id in evicted:
        try:
            delete_job_files(sd, job_id)
        except ApError:
            pass


def find_job(store, job_id):
    for job in store["jobs"]:
        if job["id"] == job_id:
            return job
    raise ApError("not_found")


def new_job_id(store=None):
    taken = {job["id"] for job in (store or {}).get("jobs", [])}
    while True:
        job_id = secrets.token_hex(8)
        if job_id not in taken:
            return job_id


def in_queue(job):
    """True when the job belongs in the Queue view rather than History."""
    status = job["state"]["status"]
    return (status in ("draft", "armed", "running", "paused", "needs_confirm")
            or (status in ("missed", "busy", "interrupted") and bool(job["promptAvailable"])))


def _limit_source(job, now):
    """windows.limit_source_for with the cached OpenCode billing class; None when unknown."""
    try:
        from . import windows
    except Exception:
        return None
    billing = None
    if job.get("harness") == "opencode":
        try:
            from . import models
            billing = models.cached_billing(job.get("model"), int(now if now is not None else timeutil.now()))
        except Exception:
            billing = None
    try:
        value = windows.limit_source_for(job, billing, None)
    except Exception:
        return None
    return value if isinstance(value, str) and consts.SOURCE_ID_RE.fullmatch(value) else None


def public_job(job, now=None):
    out = copy.deepcopy(job)
    out["cli"].pop("real", None)
    history = out["state"].pop("history", [])
    status = out["state"]["status"]
    out["state"]["lastEvent"] = history[-1] if history else None
    # When the job last changed in a way worth showing again: later housekeeping (a prompt
    # deleted by prune, a CLI path change, a result after a disarm) does not move it.
    status_at = next((event.get("at") for event in reversed(history)
                      if isinstance(event, dict) and event.get("event") not in _HOUSEKEEPING_EVENTS), None)
    out["state"]["statusAt"] = status_at if is_epoch(status_at) else job["updatedAt"]
    fire_at = out["state"]["fireAt"]
    gated = job["harness"] in consts.GATED_HARNESSES
    out["fireAtMs"] = fire_at * 1000 if fire_at is not None else None
    out["gated"] = gated
    out["canRunNow"] = (bool(job["promptAvailable"]) and (status in consts.ARMABLE_STATUSES or status == "armed")
                        and not gated)
    out["canDisarm"] = status in ("armed", "running", "paused", "needs_confirm")
    out["canEdit"] = status != "running"
    out["canDelete"] = status not in ("armed", "running")
    out["queue"] = in_queue(job)
    out["limitSource"] = _limit_source(job, now)
    return out


def add_history(job, event, at, detail=None):
    if event not in consts.HISTORY_EVENTS:
        raise ApError("internal")
    if detail is not None and not (isinstance(detail, str) and _DETAIL_RE.fullmatch(detail)):
        detail = None
    history = job["state"]["history"]
    history.append({"at": int(at), "event": event, "detail": detail})
    del history[:-consts.HISTORY_MAX]


def set_status(job, status, at, *, reason=None, wait=None):
    if status not in consts.STATUSES or (reason is not None and reason not in consts.REASONS):
        raise ApError("internal")
    if wait not in consts.WAITS:
        raise ApError("internal")
    state = job["state"]
    state["status"] = status
    state["reason"] = reason
    state["wait"] = wait if status == "armed" else None
    job["updatedAt"] = int(at)


def reset_counters(job):
    state = job["state"]
    for key in ("defers", "limitRetries", "transientRetries", "busyDefers", "unknownRetries"):
        state[key] = 0


# ---------------------------------------------------------------------------- prompts

def store_prompt(sd, job_id, prompt):
    if not consts.JOB_ID_RE.fullmatch(job_id or ""):
        raise ApError("internal")
    data = _prompt_bytes(prompt)
    prompts = sd.subdir(PROMPTS_DIR, create=True)
    try:
        prompts.write_atomic(job_id + ".txt", data)
    finally:
        prompts.close()
    return hashlib.sha256(data).hexdigest(), len(data)


def read_prompt(sd, job_id):
    if not consts.JOB_ID_RE.fullmatch(job_id or ""):
        raise ApError("internal")
    prompts = sd.subdir(PROMPTS_DIR, create=False)
    if prompts is None:
        return None
    try:
        data = prompts.read_bytes(job_id + ".txt", consts.PROMPT_MAX_BYTES)
    finally:
        prompts.close()
    if data is None:
        return None
    try:
        data.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise ApError("state_corrupt") from None
    return data


def delete_prompt(sd, job, at):
    prompts = sd.subdir(PROMPTS_DIR, create=False)
    if prompts is not None:
        try:
            prompts.unlink(job["id"] + ".txt")
        finally:
            prompts.close()
    if job["promptAvailable"]:
        job["promptAvailable"] = False
        add_history(job, "prompt_deleted", at)


def delete_job_files(sd, job_id):
    """Remove prompts/<id>.txt and runs/<id>-g*.json|.log|.last.txt of one job."""
    if not isinstance(job_id, str) or not consts.JOB_ID_RE.fullmatch(job_id):
        raise ApError("internal")
    prompts = sd.subdir(PROMPTS_DIR, create=False)
    if prompts is not None:
        try:
            prompts.unlink(job_id + ".txt")
        finally:
            prompts.close()
    runs = sd.subdir(RUNS_DIR, create=False)
    if runs is not None:
        try:
            for name in runs.list_names():
                match = _RUN_FILE_RE.fullmatch(name)
                if match and match.group(1) == job_id:
                    runs.unlink(name)
        finally:
            runs.close()


# ---------------------------------------------------------------------------- runs

def write_run_record(sd, record):
    if not isinstance(record, dict):
        raise ApError("internal")
    match = _RUN_ID_RE.fullmatch(record.get("runId") or "")
    if not match or record.get("jobId") != match.group(1) or record.get("gen") != int(match.group(2)):
        raise ApError("internal")
    data = json.dumps(record, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(data) > consts.RUN_RECORD_MAX:
        raise ApError("state_too_large")
    runs = sd.subdir(RUNS_DIR, create=True)
    try:
        runs.write_atomic(record["runId"] + ".json", data)
    finally:
        runs.close()


def run_record_exists(sd, job_id, gen):
    runs = sd.subdir(RUNS_DIR, create=False)
    if runs is None:
        return False
    try:
        return runs.stat_name("%s-g%d.json" % (job_id, gen)) is not None
    finally:
        runs.close()


def read_run_record(sd, job_id, gen):
    runs = sd.subdir(RUNS_DIR, create=False)
    if runs is None:
        return None
    try:
        record = runs.read_json("%s-g%d.json" % (job_id, gen), consts.RUN_RECORD_MAX)
    except ApError:
        return None
    finally:
        runs.close()
    return record if isinstance(record, dict) else None


def read_run_records(sd, job_id, limit=20):
    runs = sd.subdir(RUNS_DIR, create=False)
    if runs is None:
        return []
    records = []
    try:
        gens = []
        for name in runs.list_names():
            match = _RUN_FILE_RE.fullmatch(name)
            if match and match.group(1) == job_id and match.group(3) == "json":
                gens.append(int(match.group(2)))
        for gen in sorted(gens, reverse=True):
            if len(records) >= max(0, int(limit)):
                break
            try:
                record = runs.read_json("%s-g%d.json" % (job_id, gen), consts.RUN_RECORD_MAX)
            except ApError:
                continue
            if isinstance(record, dict):
                records.append(record)
    finally:
        runs.close()
    return records


def list_run_records(sd, *, since, until, cap=500):
    """Run records whose [startedAt, endedAt] overlaps [since, until], newest first, at most cap.

    A record is written once, when its run ends, so a file last modified before since belongs to
    a run that ended before the range and is not read at all.
    """
    runs = sd.subdir(RUNS_DIR, create=False)
    if runs is None:
        return []
    limit = max(0, min(int(cap), consts.RUNS_TOTAL))
    records = []
    try:
        candidates = []
        for name in runs.list_names():
            match = _RUN_FILE_RE.fullmatch(name)
            if not match or match.group(3) != "json":
                continue
            st = runs.stat_name(name)
            if st is None or not stat.S_ISREG(st.st_mode) or st.st_mtime < since - 60:
                continue
            candidates.append((st.st_mtime, name))
        for _mtime, name in sorted(candidates, reverse=True):
            try:
                record = runs.read_json(name, consts.RUN_RECORD_MAX)
            except ApError:
                continue
            if not isinstance(record, dict) or not is_epoch(record.get("startedAt")):
                continue
            started = record["startedAt"]
            ended = record["endedAt"] if is_epoch(record.get("endedAt")) else started
            if started > until or ended < since:
                continue
            records.append(record)
    finally:
        runs.close()
    records.sort(key=lambda r: (r["startedAt"], r.get("gen") if is_int(r.get("gen")) else 0), reverse=True)
    return records[:limit]


def read_log_tail_lines(sd, job_id, gen, max_lines=20):
    runs = sd.subdir(RUNS_DIR, create=False)
    if runs is None:
        return []
    try:
        data = runs.read_bytes("%s-g%d.log" % (job_id, int(gen)), consts.RUN_LOG_MAX)
    except ApError:
        return []
    finally:
        runs.close()
    if not data:
        return []
    text = _ANSI_RE.sub("", data.decode("utf-8", errors="replace"))
    lines = []
    for raw in text.split("\n"):
        line = "".join(ch if ch.isprintable() else " " for ch in raw.replace("\t", " ")).strip()
        if line:
            lines.append(line[:200])
    return lines[-max(0, int(max_lines)):] if max_lines else []


# ---------------------------------------------------------------------------- retention

def _job_ended_at(job):
    last = job["state"]["lastRun"] or {}
    return max(job["updatedAt"], last.get("endedAt") or 0)


def prune(sd, store, now):
    """Retention. Returns the number of jobs and files removed."""
    removed = 0
    keep = []
    for job in store["jobs"]:
        status = job["state"]["status"]
        if not in_queue(job) and status not in ("running", "armed") and \
                now - _job_ended_at(job) > consts.CLOSED_RETENTION_S:
            removed += 1
            continue
        if job["promptAvailable"]:
            closed = status in consts.CLOSED_STATUSES
            stale_open = (status in consts.ATTENTION_STATUSES or status == "disarmed") and \
                now - job["updatedAt"] > consts.PROMPT_KEEP_OPEN_S
            if closed or stale_open:
                delete_prompt(sd, job, now)
                removed += 1
        keep.append(job)
    store["jobs"] = keep
    ids = {job["id"] for job in keep}
    with_prompt = {job["id"] for job in keep if job["promptAvailable"]}

    removed += _prune_temp(sd, now)
    prompts = sd.subdir(PROMPTS_DIR, create=False)
    if prompts is not None:
        try:
            removed += _prune_temp(prompts, now)
            for name in prompts.list_names():
                match = _PROMPT_FILE_RE.fullmatch(name)
                if match and match.group(1) not in with_prompt and prompts.unlink(name):
                    removed += 1
        finally:
            prompts.close()
    runs = sd.subdir(RUNS_DIR, create=False)
    if runs is not None:
        try:
            removed += _prune_temp(runs, now)
            removed += _prune_runs(runs, ids)
        finally:
            runs.close()
    return removed


def _prune_temp(folder, now):
    count = 0
    for name in folder.list_names():
        if not _TEMP_FILE_RE.fullmatch(name):
            continue
        st = folder.stat_name(name)
        if st is not None and stat.S_ISREG(st.st_mode) and now - st.st_mtime > _TEMP_MAX_AGE_S:
            if folder.unlink(name):
                count += 1
    return count


def _prune_runs(runs, ids):
    count = 0
    by_run = {}
    for name in runs.list_names():
        match = _RUN_FILE_RE.fullmatch(name)
        if not match:
            continue
        job_id, gen = match.group(1), int(match.group(2))
        if job_id not in ids:
            if runs.unlink(name):
                count += 1
            continue
        by_run.setdefault((job_id, gen), []).append(name)
    doomed = []
    per_job = {}
    for job_id, gen in by_run:
        per_job.setdefault(job_id, []).append(gen)
    for job_id, gens in per_job.items():
        gens.sort(reverse=True)
        doomed.extend((job_id, gen) for gen in gens[consts.RUNS_PER_JOB:])
    for key in doomed:
        for name in by_run.pop(key):
            if runs.unlink(name):
                count += 1
    total = sum(len(names) for names in by_run.values())
    if total > consts.RUNS_TOTAL:
        def age(key):
            stamps = [runs.stat_name(name) for name in by_run[key]]
            return max((st.st_mtime for st in stamps if st is not None), default=0)
        for key in sorted(by_run, key=age):
            if total <= consts.RUNS_TOTAL:
                break
            for name in by_run[key]:
                if runs.unlink(name):
                    count += 1
                total -= 1
    return count
