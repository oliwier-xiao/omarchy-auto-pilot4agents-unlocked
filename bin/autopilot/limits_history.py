"""Past and upcoming limit resets for the History day timeline.

STATE/limits-history.json (0600, at most LIMITS_HISTORY_MAX bytes) keeps the resets Auto Pilot has
seen: fixed record resets from each usage poll (origin "record") and resets the runner parsed from a
limit hit (origin "observed"). Only seen values are stored and nothing is inferred backwards;
sliding windows and computed daily resets are never stored. Entries are unique by
(source, kind, resetsAt), where resets under RESET_SAME_S apart count as one, dropped 14 days after their reset, and the oldest observations go first
when the file would outgrow its cap. Writing is best effort: every failure is swallowed, so the
history can never fail a verb or a run.

build_timeline() joins one day of that file with the live records, computed daily resets, run
records and armed jobs. It carries job ids and labels, never prompt text.
"""
import bisect
import json
import re

from . import consts, jobs, usage, windows
from .errors import ApError
from .sessions import clean_text

HISTORY_FILE = "limits-history.json"
LOCK_NAME = "limits.lock"
LOCK_WAIT_S = 1.0
SCHEMA_VERSION = 1
ORIGINS = ("record", "observed")
ENTRIES_MAX = 4096
RUNS_CAP = 500
_ENTRY_KEYS = ("kind", "observedAt", "origin", "resetsAt", "shortLabel", "source")
# Which origin a timeline reset keeps when several describe the same (source, kind, at).
_ORIGIN_RANK = {"record": 0, "current": 1, "observed": 2, "computed": 3}
_ENTRY_MIN_BYTES = 160
_RUN_ID_RE = re.compile(r"^([0-9a-f]{16})-g([0-9]{1,6})$")
# Two resets of one (source, kind) this close together are one reset written two ways (a collector
# that sometimes adds fractional seconds, a limit hit that rounds its own way).
RESET_SAME_S = 60


def _epoch(value):
    return (isinstance(value, int) and not isinstance(value, bool)
            and consts.EPOCH_MIN <= value <= consts.EPOCH_MAX)


def _source_ok(value):
    return isinstance(value, str) and bool(windows.SOURCE_ID_RE.fullmatch(value))


def _entry_ok(entry):
    if not isinstance(entry, dict) or sorted(entry) != list(_ENTRY_KEYS):
        return False
    short = entry["shortLabel"]
    return (_source_ok(entry["source"]) and isinstance(entry["kind"], str) and entry["kind"] in windows.LIMIT_KINDS
            and isinstance(short, str) and bool(short) and clean_text(short, windows.SHORT_LABEL_MAX) == short
            and _epoch(entry["resetsAt"]) and _epoch(entry["observedAt"])
            and isinstance(entry["origin"], str) and entry["origin"] in ORIGINS)


def _entry(source_id, kind, short_label, resets_at, now, origin):
    return {"source": source_id, "kind": kind, "shortLabel": short_label, "resetsAt": resets_at,
            "observedAt": now, "origin": origin}


def _key(entry):
    return entry["source"], entry["kind"], entry["resetsAt"]


def _same_reset(a, b):
    """True when two (source, kind, resetsAt) keys describe one reset."""
    return a[0] == b[0] and a[1] == b[1] and abs(a[2] - b[2]) <= RESET_SAME_S


class _ResetIndex:
    """Sorted resets per (source, kind), for near-duplicate lookups without a full scan."""

    def __init__(self):
        self._by = {}

    def has(self, key):
        found = self._by.get((key[0], key[1]))
        if not found:
            return False
        at = bisect.bisect_left(found, key[2] - RESET_SAME_S)
        return at < len(found) and found[at] <= key[2] + RESET_SAME_S

    def add(self, key):
        bisect.insort(self._by.setdefault((key[0], key[1]), []), key[2])


def _collapse(entries):
    """Entries with near-duplicate resets folded into the earliest observation of each."""
    kept, index = [], _ResetIndex()
    for entry in sorted(entries, key=lambda e: (e["observedAt"], e["resetsAt"], e["source"], e["kind"])):
        key = _key(entry)
        if not index.has(key):
            index.add(key)
            kept.append(entry)
    return kept


def load(sd):
    """Valid history entries, [] when there is no state folder or the file is missing or unreadable."""
    if sd is None:
        return []
    try:
        raw = sd.read_json(HISTORY_FILE, windows.LIMITS_HISTORY_MAX)
    except ApError:
        return []
    if not isinstance(raw, dict) or type(raw.get("schemaVersion")) is not int \
            or raw["schemaVersion"] != SCHEMA_VERSION or not isinstance(raw.get("entries"), list):
        return []
    return [dict(entry) for entry in raw["entries"][:ENTRIES_MAX] if _entry_ok(entry)]


def _encode(entries):
    return json.dumps({"schemaVersion": SCHEMA_VERSION, "entries": entries}, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _fit(entries, now):
    """Drop resets older than the keep window, then the oldest observations until the file fits."""
    kept = [entry for entry in entries if entry["resetsAt"] >= now - windows.LIMITS_HISTORY_KEEP_S]
    kept.sort(key=lambda e: (e["observedAt"], e["resetsAt"], e["source"], e["kind"]))
    kept = kept[-ENTRIES_MAX:]
    data = _encode(kept)
    while kept and len(data) > windows.LIMITS_HISTORY_MAX:
        # The smallest valid entry encodes to about 100 bytes, so dividing the excess by a larger
        # figure never drops more entries than needed; the loop drops the rest.
        del kept[:max(1, (len(data) - windows.LIMITS_HISTORY_MAX) // _ENTRY_MIN_BYTES)]
        data = _encode(kept)
    return kept, data


def _append(sd, new_entries, now):
    """Store the entries not already present; returns how many were added. Never raises."""
    if sd is None or not new_entries:
        return 0
    try:
        with sd.lock(LOCK_WAIT_S, name=LOCK_NAME):
            entries = load(sd)
            index = _ResetIndex()
            for entry in entries:
                index.add(_key(entry))
            fresh = []
            for entry in new_entries:
                key = _key(entry)
                if index.has(key):
                    continue
                index.add(key)
                fresh.append(key)
                entries.append(entry)
            if not fresh:
                return 0
            kept, data = _fit(_collapse(entries), now)
            stored = {_key(entry) for entry in kept}
            added = sum(1 for key in fresh if key in stored)
            if added:
                sd.write_atomic(HISTORY_FILE, data)
            return added
    except (ApError, OSError, ValueError, TypeError):
        return 0


def record_from_usage(sd, usage_result, now):
    """Append the fixed resets of readable records in a read_usage result. Returns the count added."""
    if not isinstance(usage_result, dict) or not _epoch(now):
        return 0
    new = []
    for item in usage_result.get("providers") or []:
        if not isinstance(item, dict) or item.get("source") != "record" or item.get("readable") is not True:
            continue
        if not _source_ok(item.get("id")):
            continue
        for window in item.get("windows") or []:
            if (not isinstance(window, dict) or window.get("source") != "record" or window.get("sliding") is not False
                    or not _epoch(window.get("resetsAt")) or window.get("kind") not in windows.LIMIT_KINDS):
                continue
            short = clean_text(window.get("shortLabel"), windows.SHORT_LABEL_MAX)
            if short:
                new.append(_entry(item["id"], window["kind"], short, window["resetsAt"], now, "record"))
    return _append(sd, new, now)


def append_observed(sd, source_id, kind, short_label, resets_at, now):
    """Append a reset the runner parsed from a limit hit. True when a new entry was stored."""
    short = clean_text(short_label, windows.SHORT_LABEL_MAX)
    if (not _source_ok(source_id) or not isinstance(kind, str) or kind not in windows.LIMIT_KINDS or not short
            or not _epoch(resets_at) or not _epoch(now)):
        return False
    return _append(sd, [_entry(source_id, kind, short, resets_at, now, "observed")], now) > 0


def pending_reset(sd, source_id, now):
    """The soonest stored reset of source_id after now, or None."""
    found = [entry["resetsAt"] for entry in load(sd) if entry["source"] == source_id and entry["resetsAt"] > now]
    return min(found) if found else None


# ---------------------------------------------------------------------------- timeline

def _run_item(record, job):
    if not isinstance(record, dict):
        return None
    run_id, job_id, harness_id = record.get("runId"), record.get("jobId"), record.get("harness")
    match = _RUN_ID_RE.fullmatch(run_id) if isinstance(run_id, str) else None
    if not match or match.group(1) != job_id or harness_id not in consts.HARNESSES:
        return None
    started, ended = record.get("startedAt"), record.get("endedAt")
    if not _epoch(started) or not (ended is None or _epoch(ended)):
        return None
    if job is not None:
        label = job.get("label")
    else:
        try:
            label = jobs.default_label(harness_id, record.get("cwd") if isinstance(record.get("cwd"), str) else "")
        except Exception:
            label = None
    outcome = record.get("outcome")
    source = record.get("limitSource")
    return {"runId": run_id, "jobId": job_id, "harness": harness_id,
            "label": clean_text(label, consts.LABEL_MAX) or consts.HARNESS_NAMES.get(harness_id, harness_id),
            "startedAt": started, "endedAt": ended,
            "outcome": outcome if isinstance(outcome, str) and outcome in consts.OUTCOMES else None,
            "status": None, "limitSource": source if _source_ok(source) else None}


def _reset_harnesses(source_id, kind, live):
    if source_id == "gemini" and kind != "daily":
        return []
    if isinstance(live, dict) and isinstance(live.get("harnesses"), list):
        return list(live["harnesses"])
    return list(windows.bindings(source_id))


def build_timeline(sd, from_epoch, to_epoch, now, usage_result):
    """Timeline without "ok" (contract delta 3.9) for [from_epoch, to_epoch]."""
    start, end, now = int(from_epoch), int(to_epoch), int(now)
    live = {}
    listed = usage_result.get("providers") if isinstance(usage_result, dict) else None
    for item in listed if isinstance(listed, list) else []:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            live.setdefault(item["id"], item)
    resets = {}

    def add(source_id, kind, short_label, at, origin):
        if not isinstance(at, int) or not start <= at <= end:
            return
        if source_id == "cursor" and kind in windows.BILLING_KINDS:
            kind, short_label = "billing_total", windows.CURSOR_CYCLE_LABEL
        key = (source_id, kind, at)
        near = next((k for k in resets if _same_reset(key, k)), None)
        old = resets.get(near) if near is not None else None
        if old is not None and _ORIGIN_RANK[old["origin"]] <= _ORIGIN_RANK[origin]:
            return
        if near is not None:
            del resets[near]
        item = live.get(source_id)
        name = item.get("name") if isinstance(item, dict) and item.get("name") else None
        resets[key] = {"source": source_id, "name": name or windows.SOURCE_NAMES.get(source_id, source_id),
                       "kind": kind, "shortLabel": short_label, "at": at, "origin": origin,
                       "harnesses": _reset_harnesses(source_id, kind, item)}

    for entry in load(sd):
        add(entry["source"], entry["kind"], entry["shortLabel"], entry["resetsAt"], entry["origin"])
    for item in live.values():
        if item.get("source") != "record" or item.get("readable") is not True:
            continue
        for window in item.get("windows") or []:
            if isinstance(window, dict) and window.get("sliding") is False and window.get("source") == "record":
                add(item["id"], window.get("kind"), window.get("shortLabel"), window.get("resetsAt"), "current")

    stored = usage.store_jobs(sd)
    by_id = {job.get("id"): job for job in stored}
    records = []
    lister = getattr(jobs, "list_run_records", None)
    if sd is not None and lister is not None:
        try:
            records = lister(sd, since=start, until=end, cap=RUNS_CAP)
        except ApError:
            records = []
    records = records if isinstance(records, list) else []
    runs = []
    seen_runs = set()
    for record in records[:RUNS_CAP]:
        item = _run_item(record, by_id.get(record.get("jobId")) if isinstance(record, dict) else None)
        if item is None or item["runId"] in seen_runs:
            continue
        if item["startedAt"] > end or (item["endedAt"] or item["startedAt"]) < start:
            continue
        seen_runs.add(item["runId"])
        runs.append(item)
    armed = []
    for job in stored:
        state = job.get("state") if isinstance(job.get("state"), dict) else {}
        status = state.get("status")
        if status == "running":
            last = state.get("lastRun") if isinstance(state.get("lastRun"), dict) else {}
            run_id = last.get("runId")
            if (not isinstance(run_id, str) or run_id in seen_runs or not _epoch(last.get("startedAt"))
                    or last["startedAt"] > end or now < start):
                continue
            seen_runs.add(run_id)
            runs.append({"runId": run_id, "jobId": job.get("id"), "harness": job.get("harness"),
                         "label": job.get("label"), "startedAt": last["startedAt"], "endedAt": None,
                         "outcome": None, "status": "running",
                         "limitSource": windows.limit_source_for(job, usage.job_billing(job, now), usage_result)})
        elif status == "armed" and _epoch(state.get("fireAt")) and start <= state["fireAt"] <= end:
            armed.append({"jobId": job.get("id"), "harness": job.get("harness"), "fireAt": state["fireAt"],
                          "limitSource": windows.limit_source_for(job, usage.job_billing(job, now), usage_result)})
    runs.sort(key=lambda r: (r["startedAt"], r["runId"]), reverse=True)
    truncated = len(records) >= RUNS_CAP or len(runs) > RUNS_CAP
    runs = runs[:RUNS_CAP]
    armed.sort(key=lambda a: (a["fireAt"], a["jobId"]))

    sources = {r["limitSource"] for r in runs} | {a["limitSource"] for a in armed}
    for source_id, next_reset in (("gemini-daily", windows.next_la_midnight),
                                  ("zen-free", windows.next_utc_midnight)):
        if source_id not in sources:
            continue
        at = next_reset(start - 1)
        for _ in range(8):
            if at > end:
                break
            add(source_id, "daily", windows.COMPUTED_SHORT_LABEL, at, "computed")
            at = next_reset(at)

    ordered = sorted(resets.values(), key=lambda r: (r["at"], r["source"], r["kind"]))
    return {"nowMs": now * 1000, "from": start, "to": end, "resets": ordered, "runs": runs, "armed": armed,
            "truncated": truncated}
