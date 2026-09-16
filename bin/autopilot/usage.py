"""Limit windows from the usage records other tools keep, for every provider.

Omarchy's Agents panel, and any plugin that follows its record contract, writes one JSON record
per provider into the usage folder (`$XDG_STATE_HOME/omarchy/agents/usage/`, by default under
~/.local/state). This module lists that folder by name only, reads at most USAGE_RECORDS_MAX
records, each bounded and without following symlinks, and keeps nothing but id, schemaVersion,
name, tierLabel, usageStatusText, updatedAt, scope and limits. Token statistics are never parsed.
It never runs a collector, never reads sign-in files and never talks to the network.

A record that is too large, refused, malformed or names another id becomes a row with
`readable: false`; it is never an error of the whole read. Percent values are kept as written
(the record contract is 0..1): above 1 is flagged `over`, above USAGE_PERCENT_MAX the window is
dropped. Reset times up to USAGE_RESET_HORIZON_S ahead are kept, so a 30-day Codex window and a
Cursor billing cycle survive; only windows within the arming horizon are `bindable`.

Freshness for Claude comes from claude-limits.json fetchedAtMs when valid: a collector run that
served cached limits still stamps a new updatedAt. Two computed daily sources (Gemini CLI at
00:00 America/Los_Angeles, OpenCode Zen free at 00:00 UTC) are always listed.

The `usage` verb (record_history=True, with a state folder) also keeps the last good Cursor record
for one poll, because the Cursor collector removes its record whenever its service stops, and
appends the fixed resets it saw to the limits history.
"""

import json
import math
import os
import re
import stat

from . import consts, fsio, proto, timeutil, windows
from .errors import ApError
from .sessions import clean_text

_RECORD_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}\.json$")
_SCAN_ENTRIES_MAX = 4096
_LIMIT_ENTRIES_SCAN = 64
_FUTURE_SKEW_MS = 300000
_STALE_FLOOR_S = 1800
_CADENCE_S = {"cursor": 300}
_CADENCE_DEFAULT_S = 900
_PATH_MAX_BYTES = 4096
_PARSED_KEYS = ("id", "schemaVersion", "name", "tierLabel", "usageStatusText", "updatedAt", "scope", "limits")

KEEP_FILE = "usage-cursor.json"
KEEP_SOURCE = "cursor"
_KEEP_MAX = consts.USAGE_RECORD_MAX + 1024


# ---------------------------------------------------------------------------- folder and files

def _home_path(*parts):
    return "/".join((fsio.home().rstrip("/"),) + parts)


def usage_dir():
    """The usage folder: $XDG_STATE_HOME when it is a clean absolute path inside HOME, else ~/.local/state."""
    home = fsio.home()
    base = home.rstrip("/") + "/.local/state"
    value = os.environ.get("XDG_STATE_HOME", "")
    if value and os.path.isabs(value) and "\0" not in value and "\n" not in value:
        try:
            size = len(value.encode("utf-8"))
        except UnicodeEncodeError:
            size = _PATH_MAX_BYTES + 1
        normal = os.path.normpath(value)
        if size <= _PATH_MAX_BYTES and (normal == home or normal.startswith(home.rstrip("/") + "/")):
            base = normal
    return base + "/omarchy/agents/usage"


def _record_names(directory):
    """Sorted record file names (grammar only, dotfile temporaries skipped), at most USAGE_RECORDS_MAX."""
    try:
        st = os.lstat(directory)
    except OSError:
        return []
    if not stat.S_ISDIR(st.st_mode) or st.st_uid not in (os.getuid(), 0):
        return []
    names = []
    try:
        with os.scandir(directory) as entries:
            for index, entry in enumerate(entries):
                if index >= _SCAN_ENTRIES_MAX:
                    break
                if _RECORD_NAME_RE.fullmatch(entry.name):
                    names.append(entry.name)
    except OSError:
        return []
    names.sort()
    return names[:windows.USAGE_RECORDS_MAX]


def _check_doc(stem, document):
    """(parsed subset, None) or (None, unreadableReason) for one decoded record."""
    if not isinstance(document, dict):
        return None, "invalid"
    if document.get("id") != stem:
        return None, "id_mismatch"
    if "schemaVersion" in document:
        version = document["schemaVersion"]
        if type(version) is not int or version != 1:
            return None, "schema"
    limits = document.get("limits")
    if limits is None:
        limits = []
    if not isinstance(limits, list):
        return None, "schema"
    subset = {key: document[key] for key in _PARSED_KEYS if key in document and key != "limits"}
    subset["limits"] = limits[:_LIMIT_ENTRIES_SCAN]
    return subset, None


def _read_record(stem, path):
    """(subset, reason); (None, None) when the file vanished after the listing."""
    try:
        raw = fsio.read_file_nofollow(path, consts.USAGE_RECORD_MAX, owner_uid_or_root=True)
    except ApError as exc:
        return None, "too_large" if exc.code == "state_too_large" else "refused"
    if raw is None:
        return None, None
    try:
        document = proto.parse_json_bytes(raw)
    except (ValueError, RecursionError):
        return None, "invalid"
    return _check_doc(stem, document)


def _fetched_at_ms(now_ms):
    try:
        raw = fsio.read_file_nofollow(_home_path(".cache", "omarchy", "agent-usage", "claude-limits.json"),
                                      consts.USAGE_RECORD_MAX, owner_uid_or_root=True)
        document = proto.parse_json_bytes(raw) if raw is not None else None
    except (ApError, ValueError, RecursionError):
        return None
    if not isinstance(document, dict):
        return None
    value = document.get("fetchedAtMs")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if not consts.EPOCH_MIN * 1000 <= value <= now_ms + _FUTURE_SKEW_MS:
        return None
    return value


# ---------------------------------------------------------------------------- providers

def _percent(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    if not math.isfinite(number) or number < 0 or number > windows.USAGE_PERCENT_MAX:
        return None
    return number


def _updated_at(value, now):
    parsed = timeutil.parse_iso(value)
    if parsed is None or not consts.EPOCH_MIN <= parsed <= now + _FUTURE_SKEW_MS // 1000:
        return None
    return parsed


def _window(stem, entry, now, updated_at):
    """One window of Usage v2 from a limits[] entry, or None when it fails the schema."""
    if not isinstance(entry, dict):
        return None
    label = clean_text(entry.get("label"), consts.USAGE_LABEL_MAX)
    if not label:
        return None
    raw_percent = entry.get("percent")
    number = _percent(raw_percent)
    if number is None:
        return None
    percent = raw_percent if isinstance(raw_percent, int) else number
    resets_at = None
    raw_reset = entry.get("resetsAt")
    if isinstance(raw_reset, str) and raw_reset:
        parsed = timeutil.parse_iso(raw_reset)
        if parsed is not None and now - 86400 <= parsed <= now + windows.USAGE_RESET_HORIZON_S:
            resets_at = timeutil.round_epoch(parsed)
    title = clean_text(entry.get("title"), consts.USAGE_LABEL_MAX) or None
    short_label, kind = windows.humanise(label, title)
    sliding = windows.is_sliding(stem, label, percent, resets_at, updated_at)
    bindable = not sliding and resets_at is not None and now < resets_at <= now + consts.HORIZON_S
    return {"key": None, "label": label, "title": title, "shortLabel": short_label, "kind": kind,
            "percent": percent, "over": number > 1, "resetsAt": resets_at, "sliding": sliding,
            "source": "record", "bindable": bindable}


def _harnesses(stem, window_list):
    if stem == "gemini":
        return ["gemini"] if any(w["kind"] == "daily" for w in window_list) else []
    return list(windows.bindings(stem))


def _cadence(stem):
    return _CADENCE_S.get(stem, _CADENCE_DEFAULT_S)


def _stale_after_s(stem):
    return max(_STALE_FLOOR_S, 3 * _cadence(stem))


def _unreadable(stem, reason):
    return {"id": stem, "name": stem, "tier": "", "statusText": "", "scope": None, "source": "record",
            "readable": False, "unreadableReason": reason, "updatedAtMs": None, "ageSec": None, "stale": True,
            "cadenceSec": _cadence(stem), "keptFromLastPoll": False, "harnesses": _harnesses(stem, []),
            "relevant": False, "headlineKey": None, "windows": []}


def _provider(stem, doc, now, now_ms, fetched_ms):
    """A readable provider row from a checked record subset."""
    updated_at = _updated_at(doc.get("updatedAt"), now)
    updated_ms = None if updated_at is None else int(updated_at * 1000)
    if stem == "claude" and fetched_ms is not None:
        updated_ms = fetched_ms
    window_list = []
    seen = {}
    for entry in doc.get("limits") or []:
        if len(window_list) >= consts.USAGE_LIMITS_MAX:
            break
        window = _window(stem, entry, now, updated_at)
        if window is None:
            continue
        window["key"] = windows.window_key(stem, window["shortLabel"], seen)
        window_list.append(window)
    age = None if updated_ms is None else max(0, (now_ms - updated_ms) // 1000)
    return {"id": stem, "name": clean_text(doc.get("name"), consts.USAGE_LABEL_MAX) or stem,
            "tier": clean_text(doc.get("tierLabel"), consts.USAGE_LABEL_MAX),
            "statusText": clean_text(doc.get("usageStatusText"), consts.USAGE_LABEL_MAX),
            "scope": "account" if doc.get("scope") == "account" else None,
            "source": "record", "readable": True, "unreadableReason": None,
            "updatedAtMs": updated_ms, "ageSec": age,
            "stale": updated_ms is None or now_ms - updated_ms > _stale_after_s(stem) * 1000,
            "cadenceSec": _cadence(stem), "keptFromLastPoll": False,
            "harnesses": _harnesses(stem, window_list), "relevant": False,
            "headlineKey": windows.headline_key(stem, window_list), "windows": window_list}


# ---------------------------------------------------------------------------- Cursor keep

def _keep_valid(saved):
    return (isinstance(saved, dict) and saved.get("schemaVersion") == 1 and isinstance(saved.get("used"), bool)
            and isinstance(saved.get("record"), dict))


def _write_keep(sd, obj):
    try:
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(data) > _KEEP_MAX:
            return False
        sd.write_atomic(KEEP_FILE, data)
        return True
    except (ApError, OSError, ValueError, TypeError):
        return False


def _keep_cursor(sd, providers, docs, now, now_ms):
    """Save the last good Cursor record; reuse it for exactly one poll when the file is missing."""
    try:
        saved = sd.read_json(KEEP_FILE, _KEEP_MAX)
    except ApError:
        saved = None
    if not _keep_valid(saved):
        saved = None
    if any(p["id"] == KEEP_SOURCE for p in providers):
        doc = docs.get(KEEP_SOURCE)
        if doc is not None and (saved is None or saved["used"] or saved["record"] != doc):
            _write_keep(sd, {"schemaVersion": 1, "used": False, "record": doc})
        return providers
    if saved is None or saved["used"]:
        return providers
    doc, reason = _check_doc(KEEP_SOURCE, saved["record"])
    if doc is None or reason is not None:
        return providers
    if not _write_keep(sd, {"schemaVersion": 1, "used": True, "record": saved["record"]}):
        return providers
    kept = _provider(KEEP_SOURCE, doc, now, now_ms, None)
    kept["keptFromLastPoll"] = True
    return sorted(providers + [kept], key=lambda p: p["id"])


# ---------------------------------------------------------------------------- relevance

def _cached_billing(model, now):
    """OpenCode billing class from the models cache (H5), or None when unknown or unavailable."""
    try:
        from . import models

        value = models.cached_billing(model, now)
    except Exception:
        return None
    return value if isinstance(value, str) else None


def job_billing(job, now):
    """Cached billing class of an OpenCode job's model; None for every other job."""
    if not isinstance(job, dict) or job.get("harness") != "opencode":
        return None
    model = job.get("model")
    if not isinstance(model, str) or not model:
        return None
    return _cached_billing(model, now)


def store_jobs(sd):
    """Stored jobs as dicts; [] without a state folder or when jobs.json cannot be loaded."""
    if sd is None:
        return []
    from . import jobs

    try:
        store = jobs.load_store(sd)
    except ApError:
        return []
    listed = store.get("jobs") if isinstance(store, dict) else None
    return [job for job in listed if isinstance(job, dict)] if isinstance(listed, list) else []


def _opencode_default_is_go():
    """True when the global OpenCode config's default model is an OpenCode Go model (bounded read)."""
    try:
        from . import fsio as fsio_mod
        from . import paid

        model = paid.opencode_default_model(fsio_mod.home())
    except Exception:
        return False
    return isinstance(model, str) and model.startswith("opencode-go/")


def _fill_relevance(providers, sd, now):
    from . import identity

    stored = store_jobs(sd)
    job_harnesses = {job.get("harness") for job in stored}
    pi_codex = any(job.get("harness") == "pi" and job.get("provider") == "openai-codex" for job in stored)
    found = {}

    def installed(harness_id):
        if harness_id not in found:
            try:
                found[harness_id] = identity.discover_cli(harness_id).get("ok") is True
            except (ApError, OSError, ValueError):
                found[harness_id] = False
        return found[harness_id]

    for item in providers:
        if item["id"] == "zen-free":
            item["relevant"] = any(job_billing(job, now) == "zen_free" for job in stored)
            continue
        if item["id"] == "opencode-go":
            # Go windows bind only to opencode-go/* models (R7 binding table): a job on one, or the
            # global OpenCode default naming one. OpenCode being installed is not enough.
            item["relevant"] = any(job.get("harness") == "opencode" and isinstance(job.get("model"), str)
                                   and job["model"].startswith("opencode-go/") for job in stored) \
                or _opencode_default_is_go()
            continue
        relevant = any(h in job_harnesses or installed(h) for h in item["harnesses"])
        if item["id"] == "codex" and item["source"] == "record" and pi_codex:
            relevant = True
        item["relevant"] = relevant


# ---------------------------------------------------------------------------- legacy aliases

def _legacy_window(window):
    return {"label": window["label"], "kind": window["kind"], "percent": window["percent"],
            "resetsAt": window["resetsAt"], "title": window["title"]}


def _record_provider(providers, source_id):
    for item in providers:
        if item["id"] == source_id and item["source"] == "record" and item["readable"]:
            return item
    return None


def _legacy_claude(providers, now_ms, fetched):
    age = None if fetched is None else max(0, (now_ms - fetched) // 1000)
    result = {"available": False, "fetchedAtMs": fetched, "ageSec": age,
              "stale": fetched is None or age > consts.USAGE_STALE_S,
              "status": "", "tierLabel": "", "windows": []}
    record = _record_provider(providers, "claude")
    if record is not None:
        result.update(available=True, status=record["statusText"], tierLabel=record["tier"],
                      windows=[_legacy_window(w) for w in record["windows"]])
    return result


def _legacy_codex(providers, now_ms):
    result = {"available": False, "updatedAtMs": None, "stale": True, "status": "", "windows": []}
    record = _record_provider(providers, "codex")
    if record is None:
        return result
    updated = record["updatedAtMs"]
    result.update(available=True, updatedAtMs=updated,
                  stale=updated is None or now_ms - updated > consts.USAGE_STALE_S * 1000,
                  status=record["statusText"],
                  windows=[_legacy_window(w) for w in record["windows"]
                           if w["resetsAt"] is not None and not w["sliding"]])
    return result


# ---------------------------------------------------------------------------- public

def read_usage(now_ms, *, sd=None, record_history=False, with_relevance=False):
    """Usage v2 without "ok" (contract delta 3.7).

    Library callers (trigger, runner, paid) pass only now_ms. The verb passes the state folder with
    record_history (Cursor keep, history append) and with_relevance (installed agents and stored jobs).
    """
    now_ms = int(now_ms)
    now = now_ms // 1000
    directory = usage_dir()
    fetched = _fetched_at_ms(now_ms)
    providers = []
    docs = {}
    for name in _record_names(directory):
        stem = name[:-len(".json")]
        doc, reason = _read_record(stem, directory + "/" + name)
        if doc is None and reason is None:
            continue
        if doc is None:
            providers.append(_unreadable(stem, reason))
            continue
        docs[stem] = doc
        providers.append(_provider(stem, doc, now, now_ms, fetched))
    if record_history and sd is not None:
        providers = _keep_cursor(sd, providers, docs, now, now_ms)
    providers.extend(windows.computed_providers(now))
    if with_relevance:
        _fill_relevance(providers, sd, now)
    result = {"nowMs": now_ms, "providers": providers,
              "claude": _legacy_claude(providers, now_ms, fetched),
              "codex": _legacy_codex(providers, now_ms)}
    if record_history and sd is not None:
        from . import limits_history

        limits_history.record_from_usage(sd, result, now)
    return result


def provider(usage, source_id):
    """The provider row with that id from a read_usage result, or None."""
    listed = usage.get("providers") if isinstance(usage, dict) else None
    for item in listed if isinstance(listed, list) else []:
        if isinstance(item, dict) and item.get("id") == source_id:
            return item
    return None


def windows_of(provider, *, kinds=None, bindable_only=False, fresh_only=False):
    """Windows of one provider row, filtered by kind, bindability and freshness of the row."""
    if not isinstance(provider, dict) or provider.get("readable") is not True:
        return []
    if fresh_only and provider.get("stale") is not False:
        return []
    listed = provider.get("windows")
    out = [w for w in listed if isinstance(w, dict)] if isinstance(listed, list) else []
    if kinds is not None:
        out = [w for w in out if w.get("kind") in kinds]
    if bindable_only:
        out = [w for w in out if w.get("bindable") is True]
    return out


def session_window(usage):
    """The Claude window of kind "session" from a read_usage result, or None."""
    found = windows_of(provider(usage, "claude"), kinds=("session",))
    return found[0] if found else None


def gemini_daily_reset(usage, now):
    """Soonest bindable daily reset of a gemini record, else the next 00:00 America/Los_Angeles."""
    record = provider(usage, "gemini")
    if isinstance(record, dict) and record.get("source") == "record":
        resets = [w["resetsAt"] for w in windows_of(record, kinds=("daily",), bindable_only=True)
                  if isinstance(w.get("resetsAt"), int) and w["resetsAt"] > now]
        if resets:
            return min(resets)
    return windows.next_la_midnight(now)
