"""Model lists for the Compose picker: one detector per agent, a 6 h cache, never an error.

  opencode  `opencode models` lines, joined with `opencode models opencode --verbose` (and
            opencode-go when listed) and ~/.cache/opencode/models.json for a billing class;
            deprecated models are hidden; default = global OpenCode config `model`
  codex     `codex debug models` JSON: visibility "list" only, sorted by priority
  claude    the four family aliases, each with the version it is newest at, then the pinned
            versions and their names from the models.dev copy OpenCode keeps
            (~/.cache/opencode/models.json); default = ~/.claude/settings.json `model`
  gemini    model constants scanned by name from the CLI bundle, alias fallback; default from
            ~/.gemini/settings.json model.name
  cursor    `cursor-agent models`; any sign-in or parse failure gives reason cursor_no_list;
            `auto` is never listed (Agent default omits the flag)
  pi        `<pi> --offline --no-extensions --no-approve --list-models` table; no default row

Each probe runs bounded (20 s, stdout cap per agent) with the agent environment of the plan
level, in HOME, without a prompt. A failure answers models [] with a fixed reason. Results are
cached in the state folder (0600, <= 512 KiB) keyed by the CLI's real path and version.
"""

import json
import os
import re
import stat
import time

from . import agents, bounded, consts, fsio, h5_v2, harness, identity, paid, sessions
from .errors import ApError

CACHE_TTL_S = 21600
CACHE_MAX = 512 * 1024
ENTRIES_MAX = 1000
OUTPUT_BUDGET = 240000               # serialized models array, inside the verb's 262144 cap
LABEL_MAX = 80
GROUP_MAX = 40
NOTE_MAX = 80
REASONS = ("cursor_no_list", "pi_no_provider", "cli_missing", "timeout", "too_large", "failed")
SOURCES = ("cli", "static", "settings", "mixed")
LEVEL = "plan"

_PROBE_DEADLINE_S = 20.0
_VERSION_DEADLINE_S = 5.0
_PROBE_MIN_S = 0.5
_STDERR_CAP = 16384
_CAPS = {"opencode": 1024 * 1024, "codex": 512 * 1024, "cursor": 256 * 1024, "pi": 256 * 1024}
_SETTINGS_CAP = 256 * 1024
_GEMINI_SETTINGS_CAP = 64 * 1024
_GEMINI_FILES_MAX = 128
_GEMINI_FILE_MAX = 32 * 1024 * 1024
_GEMINI_TOTAL_MAX = 64 * 1024 * 1024
_CACHE_PREFIX = "models-"
# 2: entries carry `note`, and Claude lists catalogue versions instead of modelSettings keys.
_CACHE_SCHEMA = 2

_CLAUDE_ALIASES = (("fable", "Fable"), ("opus", "Opus"), ("sonnet", "Sonnet"), ("haiku", "Haiku"))
_CLAUDE_ID_RE = re.compile(r"^claude-(fable|opus|sonnet|haiku)-(\d{1,2})(?:-(\d{1,2}))?(?:-(\d{8}))?$")
_CLAUDE_LATEST = "claude-latest"
_CLAUDE_PINNED = "claude-pinned"
_WIDE = "[1m]"
_GEMINI_ALIASES = ("auto", "pro", "flash", "flash-lite")
_GEMINI_CONST_RE = re.compile(
    r"\b((?:DEFAULT|PREVIEW)_GEMINI_[A-Z0-9_]*MODEL[A-Z0-9_]*|GEMINI_MODEL_ALIAS_[A-Z0-9_]+)"
    r"\s*=\s*[\"']([A-Za-z0-9._-]{1,64})[\"']")
_OPENCODE_LINE_RE = re.compile(r"^([a-z0-9][a-z0-9._-]{0,39})/(\S{1,127})$")
_CURSOR_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:/@-]{0,127}(?:\[[A-Za-z0-9._:/=,-]{1,96}\])?$")
_CURSOR_FAIL_RE = re.compile(r"Authentication required|Please run '[^']+ login'|Not logged in|\blogin\b|^Error\b",
                             re.I | re.M)
_CURSOR_MARK_RE = re.compile(r"\s*\((current|default)\)\s*", re.I)
_CURSOR_SPLIT_RE = re.compile(r"\s+[-\u2013\u2014]\s+|\t+|\s{2,}")
_PI_HEADER = ["provider", "model", "context", "max-out", "thinking", "images"]
_PI_EMPTY_RE = re.compile(r"^\s*No models available", re.M)
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")


# --- entries -----------------------------------------------------------------------

def _entry(model_id, label=None, group="", *, default=False, billing=None, provider=None, model_value=None,
           note=None):
    if not h5_v2.model_ok(model_id):
        return None
    text = sessions.clean_text(label, LABEL_MAX) if isinstance(label, str) else ""
    return {"id": model_id,
            "label": text or sessions.clean_text(model_id, LABEL_MAX),
            "group": sessions.clean_text(group, GROUP_MAX) if isinstance(group, str) else "",
            "default": default is True,
            "billing": billing if billing in paid.CLASSES else None,
            "provider": provider if h5_v2.pi_provider_ok(provider) else None,
            "modelId": model_value if h5_v2.model_ok(model_value) else None,
            "note": (sessions.clean_text(note, NOTE_MAX) or None) if isinstance(note, str) else None}


def _lines(text):
    return [_ANSI_RE.sub("", line) for line in text.splitlines()] if isinstance(text, str) else []


def parse_opencode_lines(text):
    """Entries from `opencode models`: one provider/model per line, grouped by provider."""
    out, seen = [], set()
    for raw in _lines(text):
        line = raw.strip()
        match = _OPENCODE_LINE_RE.match(line)
        if not match or line in seen:
            continue
        entry = _entry(line, match.group(2), match.group(1))
        if entry is not None:
            seen.add(line)
            out.append(entry)
    return out


def parse_codex_debug(raw):
    """Entries from `codex debug models` JSON: visibility "list" only, by priority then order."""
    try:
        document = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError, TypeError):
        return []
    models = document.get("models") if isinstance(document, dict) else None
    ranked, seen = [], set()
    for index, model in enumerate(models[:ENTRIES_MAX * 4] if isinstance(models, list) else []):
        if not isinstance(model, dict) or model.get("visibility") != "list":
            continue
        slug = model.get("slug")
        if slug in seen:
            continue
        entry = _entry(slug, model.get("display_name") or slug)
        if entry is None:
            continue
        seen.add(slug)
        priority = model.get("priority")
        rank = priority if isinstance(priority, (int, float)) and not isinstance(priority, bool) else float("inf")
        ranked.append((rank, index, entry))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [entry for _rank, _index, entry in ranked]


def claude_pinned(catalogue):
    """[(family, major, minor, id, name)] of the Claude versions in a models.dev catalogue, newest first.

    `catalogue` is paid.read_catalogue's {"anthropic/<id>": entry}. Deprecated versions are left
    out, and a dated snapshot is dropped when its dateless id is listed too.
    """
    parsed, dateless = [], set()
    for key, obj in catalogue.items() if isinstance(catalogue, dict) else []:
        match = _CLAUDE_ID_RE.match(key[len("anthropic/"):]) if key.startswith("anthropic/") else None
        if match is None or not isinstance(obj, dict) or obj.get("status") == "deprecated":
            continue
        family, major, minor, date = match.group(1), int(match.group(2)), int(match.group(3) or 0), match.group(4)
        parsed.append((family, major, minor, date, key[len("anthropic/"):], obj.get("name")))
        if not date:
            dateless.add((family, major, minor))
    order = [family for family, _label in _CLAUDE_ALIASES]
    rows = []
    for family, major, minor, date, model_id, name in parsed:
        if date and (family, major, minor) in dateless:
            continue
        text = name if isinstance(name, str) else ""
        if text.endswith(" (latest)"):
            text = text[:-len(" (latest)")]
        if not text:
            text = "Claude %s %d%s" % (dict(_CLAUDE_ALIASES)[family], major, ".%d" % minor if minor else "")
        rows.append((family, major, minor, model_id, text))
    rows.sort(key=lambda row: (-row[1], -row[2], order.index(row[0]), row[3]))
    return rows


def claude_static(settings_obj, catalogue=None):
    """Claude's family aliases, then the pinned versions a models.dev catalogue lists.

    Each alias row says which version it is newest at, or the ANTHROPIC_DEFAULT_*_MODEL pin the
    settings set. The settings `model` is marked default, and gets its own row when it is not
    listed (an alias or a version with the 1M suffix, or a name the catalogue lacks).
    `modelSettings` keys are per-model preferences, not a model list, and are never read.
    """
    settings = settings_obj if isinstance(settings_obj, dict) else {}
    configured = settings.get("model") if h5_v2.model_ok(settings.get("model")) else None
    env = settings.get("env") if isinstance(settings.get("env"), dict) else {}
    labels = dict(_CLAUDE_ALIASES)
    pinned = claude_pinned(catalogue)
    names = {model_id: name for _family, _major, _minor, model_id, name in pinned}
    newest = {}
    for family, _major, _minor, _model_id, name in pinned:
        newest.setdefault(family, name)

    def alias_note(family, wide):
        pin = env.get("ANTHROPIC_DEFAULT_%s_MODEL" % family.upper())
        text = ("set to %s in your Claude settings" % pin if h5_v2.model_ok(pin)
                else ("newest: %s" % newest[family] if family in newest else ""))
        return ", ".join(part for part in (text, "1M context" if wide else "") if part)

    out = [_entry(family, label + " (newest)", _CLAUDE_LATEST, default=(configured == family),
                  note=alias_note(family, False)) for family, label in _CLAUDE_ALIASES]
    seen = set(labels)
    if configured is not None and configured not in seen and configured not in names:
        base = configured[:-len(_WIDE)] if configured.endswith(_WIDE) else configured
        if base in labels and base != configured:
            out.append(_entry(configured, labels[base] + " 1M (newest)", _CLAUDE_LATEST, default=True,
                              note=alias_note(base, True)))
        elif base in names and base != configured:
            out.append(_entry(configured, names[base] + " 1M", _CLAUDE_PINNED, default=True, note="1M context"))
        else:
            out.append(_entry(configured, configured, _CLAUDE_LATEST, default=True, note="from your Claude settings"))
        seen.add(configured)
    for _family, _major, _minor, model_id, name in pinned:
        if model_id not in seen:
            out.append(_entry(model_id, name, _CLAUDE_PINNED, default=(model_id == configured)))
            seen.add(model_id)
    return out


def parse_gemini_bundle(text):
    """Model ids from named constants in Gemini CLI bundle code (aliases first); [] when none."""
    aliases, others, seen = {}, [], set()
    for match in _GEMINI_CONST_RE.finditer(text if isinstance(text, str) else ""):
        name, value = match.group(1), match.group(2)
        if "EMBEDDING" in name or "CUSTOM_TOOLS" in name or value.lower() == "none" or value in seen:
            continue
        seen.add(value)
        if name.startswith("GEMINI_MODEL_ALIAS_"):
            aliases[value] = True
        else:
            others.append(value)
    ordered = [value for value in _GEMINI_ALIASES if value in aliases]
    ordered += [value for value in aliases if value not in ordered] + others
    return [entry for entry in (_entry(value, value) for value in ordered) if entry is not None]


def parse_cursor_models(text):
    """Entries from `cursor-agent models`: an id, optionally a label; `auto` is never listed."""
    out, seen = [], set()
    for raw in _lines(text):
        line = raw.strip().lstrip("-*• \t")
        if not line or line.endswith(":"):
            continue
        is_default = bool(_CURSOR_MARK_RE.search(line))
        line = _CURSOR_MARK_RE.sub(" ", line).strip()
        parts = _CURSOR_SPLIT_RE.split(line, maxsplit=1)
        ident = parts[0].strip()
        label = parts[1].strip() if len(parts) > 1 else ""
        if " " in ident:
            ident, rest = ident.split(" ", 1)
            label = label or rest.strip()
        if ident.lower() == "auto" or ident in seen or not _CURSOR_ID_RE.match(ident):
            continue
        entry = _entry(ident, label or ident, default=is_default)
        if entry is not None:
            seen.add(ident)
            out.append(entry)
    return out


def parse_pi_table(text):
    """Entries from the `--list-models` table after its exact header; value provider/model."""
    lines = _lines(text)
    start = next((i for i, line in enumerate(lines) if line.split() == _PI_HEADER), None)
    if start is None:
        return []
    out, seen = [], set()
    for line in lines[start + 1:]:
        tokens = line.split()
        if len(tokens) < 2:
            continue
        provider, model = tokens[0], tokens[1]
        model_id = provider + "/" + model
        if model_id in seen or not h5_v2.pi_provider_ok(provider) or not h5_v2.model_ok(model):
            continue
        entry = _entry(model_id, model, provider, provider=provider, model_value=model)
        if entry is not None:
            seen.add(model_id)
            out.append(entry)
    return out


# --- probes ------------------------------------------------------------------------

def _remaining(end):
    left = end - time.monotonic()
    budget = bounded.remaining_budget()
    return max(0.0, left if budget is None else min(left, budget))


def _probe(harness_id, argv, end):
    """(result, None) or (None, reason) for one bounded probe."""
    limit = min(_PROBE_DEADLINE_S, _remaining(end))
    if limit < _PROBE_MIN_S:
        return None, "timeout"
    try:
        env = harness.agent_env(harness_id, LEVEL)
        cwd = fsio.home()
    except ApError:
        return None, "failed"
    result = bounded.run_bounded(list(argv), env=env, cwd=cwd, stdout_cap=_CAPS[harness_id],
                                 stderr_cap=_STDERR_CAP, deadline_s=limit)
    if result.get("error") is not None:
        return None, "failed"
    if result.get("timedOut"):
        return None, "timeout"
    if result.get("overflow"):
        return None, "too_large"
    return result, None


def _stdout(result):
    return bytes(result.get("stdout") or b"").decode("utf-8", "replace")


def _stderr(result):
    return bytes(result.get("stderr") or b"").decode("utf-8", "replace")


def _settings(path, cap, owner_uid_or_root=False):
    try:
        data = fsio.read_file_nofollow(path, cap, owner_uid_or_root=owner_uid_or_root)
    except ApError:
        return None
    if data is None:
        return None
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _detect_opencode(found, now, end):
    result, reason = _probe("opencode", found["exec"] + ["models"], end)
    if reason is not None:
        return {"reason": reason}
    if result.get("rc") != 0:
        return {"reason": "failed"}
    listed = parse_opencode_lines(_stdout(result))
    groups = {entry["group"] for entry in listed}
    verbose, complete = {}, True
    for provider in ("opencode", "opencode-go"):
        if provider in groups:
            blocks = paid.opencode_verbose(found["exec"], provider, _remaining(end))
            complete = complete and blocks is not None
            verbose.update(blocks or {})
    home = fsio.home()
    catalogue, mtime = paid.read_catalogue(home)
    configured = paid.opencode_default_model(home)
    models = []
    for entry in listed:
        live, known = verbose.get(entry["id"]), (catalogue or {}).get(entry["id"])
        if "deprecated" in ((live or {}).get("status"), (known or {}).get("status")):
            continue
        entry["billing"] = paid.classify_opencode(entry["id"], verbose, catalogue, mtime, now, False, None)
        name = (live or {}).get("name") or (known or {}).get("name")
        if name:
            entry["label"] = sessions.clean_text(name, LABEL_MAX)
        entry["default"] = entry["id"] == configured
        models.append(entry)
    return {"models": models, "source": "cli", "cacheable": complete}


def _detect_codex(found, now, end):
    result, reason = _probe("codex", found["exec"] + ["debug", "models"], end)
    if reason is not None:
        return {"reason": reason}
    if result.get("rc") != 0:
        return {"reason": "failed"}
    raw = bytes(result.get("stdout") or b"")
    try:
        json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return {"reason": "failed"}
    return {"models": parse_codex_debug(raw), "source": "cli"}


def _detect_claude(found, now, end):
    home = fsio.home()
    settings = _settings(os.path.join(home, ".claude", "settings.json"), _SETTINGS_CAP)
    catalogue, _mtime = paid.read_catalogue(home, providers=("anthropic",))
    models = claude_static(settings, catalogue)
    return {"models": models, "source": "static" if len(models) == len(_CLAUDE_ALIASES) else "mixed"}


def _gemini_bundle_ids(real, end):
    folder = os.path.dirname(real) if isinstance(real, str) and real.startswith("/") else None
    if folder is None:
        return []
    try:
        with os.scandir(folder) as iterator:
            names = sorted(entry.name for entry in iterator if entry.name.endswith(".js"))
    except OSError:
        return []
    ids, seen, total = [], set(), 0
    for name in names[:_GEMINI_FILES_MAX]:
        if _remaining(end) <= 0:
            break
        path = os.path.join(folder, name)
        try:
            info = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_size > _GEMINI_FILE_MAX \
                or total + info.st_size > _GEMINI_TOTAL_MAX:
            continue
        try:
            data = fsio.read_file_nofollow(path, _GEMINI_FILE_MAX, owner_uid_or_root=True)
        except ApError:
            continue
        if data is None or b"GEMINI" not in data:
            continue
        total += len(data)
        for entry in parse_gemini_bundle(data.decode("latin-1")):
            if entry["id"] not in seen:
                seen.add(entry["id"])
                ids.append(entry)
    order = {value: index for index, value in enumerate(_GEMINI_ALIASES)}
    return sorted(ids, key=lambda entry: order.get(entry["id"], len(order)))


def _detect_gemini(found, now, end):
    models = _gemini_bundle_ids(found.get("real"), end)
    if not models:
        models = [_entry(value, value) for value in _GEMINI_ALIASES]
    source = "static"
    settings = _settings(os.path.join(fsio.home(), ".gemini", "settings.json"), _GEMINI_SETTINGS_CAP)
    section = settings.get("model") if settings else None
    configured = section.get("name") if isinstance(section, dict) else None
    if h5_v2.model_ok(configured):
        for entry in models:
            entry["default"] = entry["id"] == configured
        if configured not in {entry["id"] for entry in models}:
            models.append(_entry(configured, configured, default=True))
            source = "mixed"
    return {"models": models, "source": source}


def _detect_cursor(found, now, end):
    result, reason = _probe("cursor", found["exec"] + ["models"], end)
    if reason in ("timeout", "too_large"):
        return {"reason": reason}
    if reason is not None or result.get("rc") != 0:
        return {"reason": "cursor_no_list"}
    stdout = _stdout(result)
    if _CURSOR_FAIL_RE.search(stdout) or _CURSOR_FAIL_RE.search(_stderr(result)):
        return {"reason": "cursor_no_list"}
    models = parse_cursor_models(stdout)
    if not models:
        return {"reason": "cursor_no_list"}
    return {"models": models, "source": "cli"}


def _detect_pi(found, now, end):
    argv = found["exec"] + ["--offline", "--no-extensions", "--no-approve", "--list-models"]
    result, reason = _probe("pi", argv, end)
    if reason is not None:
        return {"reason": reason}
    stdout = _stdout(result)
    if _PI_EMPTY_RE.search(stdout) or _PI_EMPTY_RE.search(_stderr(result)):
        return {"reason": "pi_no_provider"}
    if result.get("rc") != 0 or not any(line.split() == _PI_HEADER for line in _lines(stdout)):
        return {"reason": "failed"}
    models = parse_pi_table(stdout)
    if not models:
        return {"reason": "pi_no_provider"}
    for entry in models:
        entry["default"] = False
    return {"models": models, "source": "cli"}


_DETECTORS = {"opencode": _detect_opencode, "codex": _detect_codex, "claude": _detect_claude,
              "gemini": _detect_gemini, "cursor": _detect_cursor, "pi": _detect_pi}


# --- cache -------------------------------------------------------------------------

def _fit(models):
    """At most ENTRIES_MAX entries whose serialized size stays inside OUTPUT_BUDGET."""
    kept, size = [], 2
    for entry in models:
        if entry is None:
            continue
        if len(kept) >= ENTRIES_MAX:
            return kept, True
        size += len(json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1
        if size > OUTPUT_BUDGET:
            return kept, True
        kept.append(entry)
    return kept, False


def _clean_result(harness_id, result):
    if not isinstance(result, dict) or result.get("harness") != harness_id:
        return None
    fetched = result.get("fetchedAt")
    version = result.get("cliVersion")
    if not isinstance(fetched, int) or isinstance(fetched, bool) or result.get("source") not in SOURCES:
        return None
    if version is not None and not (isinstance(version, str) and len(version) <= 40 and version.isprintable()):
        return None
    models = []
    for raw in result.get("models") if isinstance(result.get("models"), list) else []:
        if not isinstance(raw, dict):
            return None
        entry = _entry(raw.get("id"), raw.get("label"), raw.get("group") or "", default=raw.get("default"),
                       billing=raw.get("billing"), provider=raw.get("provider"), model_value=raw.get("modelId"),
                       note=raw.get("note"))
        if entry is None:
            return None
        models.append(entry)
    kept, cut = _fit(models)
    return {"harness": harness_id, "models": kept, "source": result["source"], "fetchedAt": fetched,
            "cliVersion": version, "cached": True, "truncated": bool(result.get("truncated")) or cut,
            "reason": None}


def _load_cache(harness_id):
    try:
        sd = fsio.open_state(create=False)
    except ApError:
        return None
    if sd is None:
        return None
    try:
        document = sd.read_json(_CACHE_PREFIX + harness_id + ".json", CACHE_MAX)
    except ApError:
        return None
    finally:
        sd.close()
    if not isinstance(document, dict) or document.get("schemaVersion") != _CACHE_SCHEMA:
        return None
    key = document.get("key")
    result = _clean_result(harness_id, document.get("result"))
    if result is None or not isinstance(key, dict):
        return None
    return {"key": {"cliReal": key.get("cliReal"), "cliVersion": key.get("cliVersion")}, "result": result}


def _save_cache(harness_id, key, result):
    stored = {k: result[k] for k in ("harness", "models", "source", "fetchedAt", "cliVersion", "truncated")}
    data = json.dumps({"schemaVersion": _CACHE_SCHEMA, "key": key, "result": stored}, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")
    if len(data) > CACHE_MAX:
        return
    try:
        sd = fsio.open_state(create=True)
    except ApError:
        return
    try:
        sd.write_atomic(_CACHE_PREFIX + harness_id + ".json", data)
    except (ApError, OSError):
        pass
    finally:
        sd.close()


def _current_key(harness_id):
    """The cache key of the CLI installed now, from discovery and the version cache (no probe).

    None when the CLI is missing or its version is not cached: the models cache cannot be matched.
    """
    try:
        found = identity.discover_cli(harness_id)
        version = agents.cached_version(harness_id) if found.get("ok") and found.get("exec") else None
    except Exception:
        return None
    return {"cliReal": found.get("real"), "cliVersion": version} if version is not None else None


# One cmd_list can ask cached() once per OpenCode job. The memo is off unless
# begin_list_cache() is in force, so unit tests that change the CLI between calls
# still re-read the files.
_LIST_CACHE = None


def begin_list_cache():
    global _LIST_CACHE
    _LIST_CACHE = {}


def end_list_cache():
    global _LIST_CACHE
    _LIST_CACHE = None


def cached(harness_id, now):
    """The cached Models result for harness when younger than 6 h and written for the CLI installed
    now (same real path and version, as detect() checks), else None. Never probes."""
    now = int(now)
    memo_key = (harness_id, now)
    if _LIST_CACHE is not None and memo_key in _LIST_CACHE:
        return _LIST_CACHE[memo_key]
    result = None
    if harness_id in _DETECTORS:
        hit = _load_cache(harness_id)
        if hit is not None and 0 <= now - hit["result"]["fetchedAt"] <= CACHE_TTL_S:
            key = _current_key(harness_id)
            if key is not None and hit["key"] == key:
                result = hit["result"]
    if _LIST_CACHE is not None:
        _LIST_CACHE[memo_key] = result
    return result


def cached_billing(model, now):
    """Billing class of an OpenCode model from the fresh models cache, or None when not known there."""
    if not h5_v2.model_ok(model) or not model.startswith("opencode"):
        return None
    result = cached("opencode", now)
    for entry in result["models"] if result else []:
        if entry["id"] == model:
            return entry["billing"]
    return None


# --- public ------------------------------------------------------------------------

def detect(harness_id, *, refresh, now, deadline_s):
    """Models (3.10) without "ok". Never raises for a probe failure; unknown harness -> invalid_harness."""
    if harness_id not in consts.HARNESSES or harness_id not in _DETECTORS:
        raise ApError("invalid_harness", field="harness")
    end = time.monotonic() + max(0.0, float(deadline_s))
    now = int(now)
    base = {"harness": harness_id, "models": [], "source": "static" if harness_id in ("claude", "gemini") else "cli",
            "fetchedAt": now, "cliVersion": None, "cached": False, "truncated": False, "reason": None}
    found = identity.discover_cli(harness_id)
    if not found.get("ok") or not found.get("exec"):
        return dict(base, reason="cli_missing")
    version = agents.version_of(harness_id, found, min(_VERSION_DEADLINE_S, _remaining(end)))
    key = {"cliReal": found.get("real"), "cliVersion": version}
    if not refresh:
        hit = _load_cache(harness_id)
        if hit is not None and hit["key"] == key and 0 <= now - hit["result"]["fetchedAt"] <= CACHE_TTL_S:
            return hit["result"]
    found = dict(found, exec=list(found["exec"]))
    detected = _DETECTORS[harness_id](found, now, end)
    reason = detected.get("reason")
    if reason is not None:
        return dict(base, cliVersion=version, reason=reason if reason in REASONS else "failed")
    models, cut = _fit(detected.get("models") or [])
    result = dict(base, models=models, source=detected.get("source", base["source"]), cliVersion=version,
                  truncated=cut)
    if detected.get("cacheable", True):
        _save_cache(harness_id, key, result)
    return result
