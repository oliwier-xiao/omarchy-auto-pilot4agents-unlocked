"""When a job fires, whether it still should when its timer goes off, and when it retries (R5).

All times are epoch seconds. Reset triggers read the usage records other tools keep through the
already bounded `usage` data (never a network call and never a collector run), add a margin so
the new window is really open, and are re-validated by the runner right before firing.
"""

import datetime
import os
import re

from . import consts, systemd, timeutil
from .errors import ApError

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_MONTH_RE = "|".join(_MONTHS)

CLAUDE_BANNER = re.compile(
    "(?P<head>You(?:'|\u2019)ve hit your (?:(?P<kind>[A-Za-z][A-Za-z' ]*?) )?limit|You(?:'|\u2019)re out of extra usage)"
    "\\s*[\u00b7\u2219\u2022\u2013\u2014-]\\s*resets\\s+"
    r"(?:(?P<mon>" + _MONTH_RE + r") (?P<day>\d{1,2}),\s*(?:(?P<year>\d{4}),\s*)?)?"
    r"(?P<h>\d{1,2})(?::(?P<mi>\d{2}))?\s*(?P<ap>am|pm)"
    r"(?:\s*\((?P<tz>[A-Za-z_]+(?:/[A-Za-z0-9_+\-]+)*)\))?")

CODEX_RETRY = re.compile(
    r"[Tt]ry again at (?:(?P<mon>" + _MONTH_RE + r") (?P<day>\d{1,2})(?:st|nd|rd|th), (?P<year>\d{4}) )?"
    r"(?P<h>\d{1,2}):(?P<mi>\d{2}) (?P<ap>AM|PM)")

_BANNER_KIND = {"session": "session", "weekly": "weekly", "opus": "model_weekly", "sonnet": "model_weekly",
                "fable": "model_weekly", "usage credit": "other"}
_FIVE_HOURS = 5 * 3600
_WINDOW_SLACK_S = 30
_STALE_AUTH_PERCENT = 0.5
_MIN_RETRY_LEAD_S = 60
_CODEX_MIN_MARGIN_S = 90
_DAY_S = 86400
_RESET_SOURCE = {"codex_window_reset": "codex", "go_window_reset": "opencode-go"}


# ---------------------------------------------------------------- time parsing

def _local_zone():
    """The system zone from /etc/localtime, the same zone the agent CLIs format times in."""
    try:
        target = os.path.realpath("/etc/localtime")
        marker = "/zoneinfo/"
        index = target.find(marker)
        if index >= 0:
            import zoneinfo
            return zoneinfo.ZoneInfo(target[index + len(marker):])
    except Exception:
        return None
    return None


def _zone(name):
    if name:
        try:
            import zoneinfo
            return zoneinfo.ZoneInfo(name)
        except Exception:
            pass
    return _local_zone()


def _epoch(zone, year, month, day, hour, minute):
    try:
        if zone is None:
            return int(datetime.datetime(year, month, day, hour, minute).timestamp())
        return int(datetime.datetime(year, month, day, hour, minute, tzinfo=zone, fold=0).timestamp())
    except (ValueError, OverflowError, OSError):
        return None


def local_date_epoch(year, month, day):
    """Local midnight at the start of a calendar date, or None for an impossible date."""
    epoch = _epoch(_local_zone(), year, month, day, 0, 0)
    if epoch is None or not consts.EPOCH_MIN <= epoch <= consts.EPOCH_MAX:
        return None
    return epoch


def _today(zone, now):
    if zone is None:
        return datetime.datetime.fromtimestamp(now).date()
    return datetime.datetime.fromtimestamp(now, tz=zone).date()


def _hour24(hour_text, ampm):
    hour = int(hour_text)
    if hour < 1 or hour > 12:
        return None
    hour = hour % 12
    return hour + 12 if ampm.lower() == "pm" else hour


def _resolve_clock(match, zone, now, *, roll_past_s):
    hour = _hour24(match.group("h"), match.group("ap"))
    minute = int(match.group("mi") or 0)
    if hour is None or minute > 59:
        return None
    if match.group("mon"):
        month = _MONTHS.index(match.group("mon")) + 1
        day = int(match.group("day"))
        if match.group("year"):
            return _epoch(zone, int(match.group("year")), month, day, hour, minute)
        year = _today(zone, now).year
        epoch = _epoch(zone, year, month, day, hour, minute)
        if epoch is not None and epoch < now - _DAY_S:
            epoch = _epoch(zone, year + 1, month, day, hour, minute)
        return epoch
    date = _today(zone, now)
    epoch = _epoch(zone, date.year, date.month, date.day, hour, minute)
    if epoch is not None and roll_past_s is not None and epoch < now - roll_past_s:
        date = date + datetime.timedelta(days=1)
        epoch = _epoch(zone, date.year, date.month, date.day, hour, minute)
    return epoch


def parse_claude_banner(text, now):
    """Claude's "You've hit your ... limit · resets <time>" line -> {"kind", "resetEpoch"} or None."""
    if not isinstance(text, str):
        return None
    match = CLAUDE_BANNER.search(text)
    if match is None:
        return None
    if match.group("kind"):
        kind = _BANNER_KIND.get(match.group("kind").strip().lower(), "other")
    else:
        kind = "other"
    epoch = _resolve_clock(match, _zone(match.group("tz")), now, roll_past_s=600)
    if epoch is None:
        return None
    return {"kind": kind, "resetEpoch": epoch}


def parse_codex_retry(text, now):
    """Codex's "try again at <local time>" -> epoch, or None ("Try again later" has no time)."""
    if not isinstance(text, str):
        return None
    match = CODEX_RETRY.search(text)
    if match is None:
        return None
    return _resolve_clock(match, _local_zone(), now, roll_past_s=None)


# ---------------------------------------------------------------- usage helpers

def _margin(job):
    value = (job.get("trigger") or {}).get("marginSec")
    if isinstance(value, int) and not isinstance(value, bool) and consts.MARGIN_MIN <= value <= consts.MARGIN_MAX:
        return value
    return consts.MARGIN_DEFAULT


def _int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _percent(window):
    value = window.get("percent")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _next_utc_midnight(now):
    return (int(now) // _DAY_S + 1) * _DAY_S


def _provider(usage, source_id):
    """The Usage v2 provider entry with this id (same lookup as usage.provider)."""
    providers = usage.get("providers") if isinstance(usage, dict) else None
    for entry in providers if isinstance(providers, list) else []:
        if isinstance(entry, dict) and entry.get("id") == source_id:
            return entry
    return None


def _readable(usage, source_id):
    entry = _provider(usage, source_id)
    return entry if isinstance(entry, dict) and entry.get("readable") is True else None


def _claude_record(usage):
    return _readable(usage, "claude")


def _windows(record):
    windows = record.get("windows") if isinstance(record, dict) else None
    return [w for w in windows if isinstance(w, dict)] if isinstance(windows, list) else []


def _session_window(record):
    for window in _windows(record):
        if window.get("kind") == "session" and window.get("sliding") is not True:
            return window
    return None


def reset_window(usage, source_id, now):
    """The window a reset of source_id binds to: the latest exhausted open one, else the soonest."""
    record = _readable(usage, source_id)
    if record is None:
        return None
    open_windows = [w for w in _windows(record) if w.get("bindable") is True and w.get("sliding") is not True
                    and (_int(w.get("resetsAt")) or 0) > now]
    exhausted = [w for w in open_windows if _percent(w) >= consts.EXHAUSTED]
    if exhausted:
        return max(exhausted, key=lambda w: w["resetsAt"])
    if open_windows:
        return min(open_windows, key=lambda w: w["resetsAt"])
    return None


def _scope_matches(window, model):
    if not isinstance(model, str) or not model:
        return False
    title = window.get("title") or window.get("label") or ""
    if not isinstance(title, str):
        return False
    scope = title[:-len(" Weekly")] if title.endswith(" Weekly") else title
    scope = scope.strip().lower()
    return bool(scope) and scope in model.lower()


def _blockers(record, model):
    out = []
    for window in _windows(record):
        if window.get("kind") == "weekly":
            out.append(window)
        elif window.get("kind") == "model_weekly" and _scope_matches(window, model):
            out.append(window)
    return out


def _apply_blockers(record, t, job, margin):
    """Push t past exhausted weekly windows (policy defer) or raise weekly_exhausted (policy skip)."""
    policy = (job.get("trigger") or {}).get("weeklyPolicy", "defer")
    deferred = False
    blockers = _blockers(record, job.get("model"))
    for _ in range(len(blockers) + 1):
        moved = False
        for window in blockers:
            resets = _int(window.get("resetsAt"))
            if resets is None or _percent(window) < consts.EXHAUSTED or resets <= t:
                continue
            if policy == "skip":
                raise ApError("weekly_exhausted", detail={"until": resets})
            t = resets + margin
            deferred = moved = True
        if not moved:
            break
    return t, deferred


def _gemini_daily_reset(usage, now):
    """usage.gemini_daily_reset (the record's daily window, else the computed LA midnight)."""
    try:
        from . import usage as usage_module
        fn = getattr(usage_module, "gemini_daily_reset", None)
        value = fn(usage, now) if callable(fn) else None
    except Exception:
        value = None
    if _int(value) is not None and now < value <= now + consts.HORIZON_S:
        return value
    return timeutil.next_midnight("America/Los_Angeles", now)


def _clock_unsynced():
    try:
        return systemd.clock_synced() is False
    except Exception:
        return False


# ---------------------------------------------------------------- arm

def catchup_grace_s(job):
    """Lateness allowed before a due job counts as missed.

    A job waiting for a limit or a window to reset gets the reset grace whatever its trigger kind:
    it was re-armed to a reset time, often hours after the time the user picked.
    """
    kind = (job.get("trigger") or {}).get("kind")
    wait = (job.get("state") or {}).get("wait")
    if kind in consts.RESET_KINDS or wait in ("limit", "deferred"):
        return consts.GRACE_RESET_S
    return consts.GRACE_TIME_S


def _pick_hint(*hints):
    for hint in hints:
        if hint:
            return hint
    return None


def _check_reset_kind(job, kind):
    """trigger_unsupported unless the reset kind applies to this job (legacy kinds are refused here)."""
    harness = job.get("harness")
    if kind not in consts.RESET_KINDS_FOR.get(harness, ()):
        raise ApError("trigger_unsupported", field="trigger.kind")
    model = job.get("model") if isinstance(job.get("model"), str) else ""
    if kind == "codex_window_reset" and harness == "pi" and job.get("provider") != "openai-codex":
        raise ApError("trigger_unsupported", field="trigger.kind")
    if kind == "zen_free_reset" and not model.startswith("opencode/"):
        raise ApError("trigger_unsupported", field="trigger.kind")
    if kind == "go_window_reset" and not model.startswith("opencode-go/"):
        raise ApError("trigger_unsupported", field="trigger.kind")


def compute_fire_at(job, now, usage, *, billing=None):
    """Fire time for arming (R5 ARM). Raises the trigger error codes of contract 2.6."""
    trig = job.get("trigger") or {}
    kind = trig.get("kind")
    if kind not in consts.TRIGGER_KINDS:
        raise ApError("invalid_trigger", field="trigger.kind")
    if kind in consts.RESET_KINDS:
        _check_reset_kind(job, kind)
    if kind == "now":
        return {"fireAt": now, "immediate": True, "basis": None, "hint": None, "wait": None}
    if kind == "in":
        delay = _int(trig.get("delaySec"))
        if delay is None or delay < consts.DELAY_MIN or delay > consts.HORIZON_S:
            raise ApError("invalid_trigger", field="trigger.delaySec")
        t = now + delay
        return {"fireAt": t, "immediate": False, "basis": None, "wait": None,
                "hint": "far" if t - now > _DAY_S else None}
    if kind == "at":
        t = _int(trig.get("fireAt"))
        if t is None:
            raise ApError("invalid_trigger", field="trigger.fireAt")
        if t < now - 60:
            raise ApError("time_past", field="trigger.fireAt")
        if t - now > consts.HORIZON_S:
            raise ApError("time_too_far", field="trigger.fireAt")
        return {"fireAt": t, "immediate": False, "basis": None, "wait": None,
                "hint": "far" if t - now > _DAY_S else None}

    margin = _margin(job)
    weekly_hint = stale_hint = None
    if kind == "claude_5h_reset":
        record = _claude_record(usage)
        if record is None:
            raise ApError("no_reset_data", field="trigger.kind")
        window = _session_window(record)
        resets = _int(window.get("resetsAt")) if window else None
        if resets is None or resets <= now:
            raise ApError("reset_not_open", field="trigger.kind")
        t, deferred = _apply_blockers(record, resets + margin, job, margin)
        weekly_hint = "weekly_deferred" if deferred else None
        stale_hint = "usage_stale" if record.get("stale") else None
        basis = {"source": "record", "resetEpoch": resets, "fetchedAtMs": _int(record.get("updatedAtMs")),
                 "percent": _percent(window)}
    elif kind in _RESET_SOURCE:
        record = _readable(usage, _RESET_SOURCE[kind])
        chosen = reset_window(usage, _RESET_SOURCE[kind], now)
        if record is None or chosen is None:
            raise ApError("no_reset_data", field="trigger.kind")
        t = chosen["resetsAt"] + margin
        stale_hint = "usage_stale" if record.get("stale") else None
        basis = {"source": "record", "resetEpoch": chosen["resetsAt"],
                 "fetchedAtMs": _int(record.get("updatedAtMs")), "percent": _percent(chosen)}
    elif kind == "zen_free_reset":
        if billing is None:
            raise ApError("no_reset_data", field="trigger.kind")
        if billing != "zen_free":
            raise ApError("trigger_unsupported", field="trigger.kind")
        midnight = _next_utc_midnight(now)
        t = midnight + margin
        basis = {"source": "clock", "resetEpoch": midnight, "fetchedAtMs": None, "percent": None}
    else:
        midnight = _gemini_daily_reset(usage, now)
        t = midnight + margin
        basis = {"source": "clock", "resetEpoch": midnight, "fetchedAtMs": None, "percent": None}

    clock_hint = None
    if _clock_unsynced():
        t += consts.SKEW_EXTRA_S
        clock_hint = "clock_unsynced"
    if t - now > consts.HORIZON_S:
        raise ApError("time_too_far", field="trigger.kind")
    far_hint = "far" if t - now > _DAY_S else None
    return {"fireAt": t, "immediate": False, "basis": basis, "wait": "reset",
            "hint": _pick_hint(weekly_hint, clock_hint, stale_hint, far_hint)}


# ---------------------------------------------------------------- prefire

def _rearm_or_give_up(job, t, reason, now, reset_epoch):
    state = job.get("state") or {}
    if int(state.get("defers") or 0) >= consts.MAX_DEFERS:
        return {"action": "gave_up", "fireAt": None, "reason": "defers"}
    created = _int(job.get("createdAt")) or now
    if t - created > consts.HORIZON_S:
        return {"action": "gave_up", "fireAt": None, "reason": "horizon"}
    if _clock_unsynced():
        t += consts.SKEW_EXTRA_S
    return {"action": "rearm", "fireAt": t, "reason": reason, "resetEpoch": reset_epoch}


def prefire(job, now, usage):
    """Re-validation when the timer fires (R5 PREFIRE).

    A "rearm" answer also carries "resetEpoch", the reset the job now waits for, so the runner
    can store it as the new basis for the next re-validation. Only Claude Code jobs bound to the
    Claude reset are re-validated; a stored legacy OpenCode job with that trigger fires at its time.
    """
    state = job.get("state") or {}
    fire_at = _int(state.get("fireAt"))
    if fire_at is None:
        fire_at = now
    if now - fire_at > catchup_grace_s(job):
        return {"action": "missed", "fireAt": None, "reason": "late"}
    if (job.get("trigger") or {}).get("kind") != "claude_5h_reset" or job.get("harness") != "claude":
        return {"action": "fire", "fireAt": None, "reason": None}
    basis = state.get("basis") or {}
    reset_epoch = _int(basis.get("resetEpoch")) if isinstance(basis, dict) else None
    # A fire time before the awaited reset means the user asked for an earlier run (run now).
    if reset_epoch is None or fire_at < reset_epoch or state.get("wait") in ("transient", "busy"):
        return {"action": "fire", "fireAt": None, "reason": None}
    record = _claude_record(usage)
    if record is None or record.get("stale"):
        return {"action": "fire", "fireAt": None, "reason": None}
    margin = _margin(job)
    window = _session_window(record)
    resets = _int(window.get("resetsAt")) if window else None
    if resets is not None and resets > now + _WINDOW_SLACK_S:
        if resets - _FIVE_HOURS < reset_epoch - 60:
            return _rearm_or_give_up(job, resets + margin, "window_later", now, resets)
        if _percent(window) >= consts.EXHAUSTED:
            return _rearm_or_give_up(job, resets + margin, "window_exhausted", now, resets)
    try:
        t, _deferred = _apply_blockers(record, now, job, margin)
    except ApError:
        return {"action": "skip", "fireAt": None, "reason": "weekly_exhausted"}
    if t > now + _WINDOW_SLACK_S:
        return _rearm_or_give_up(job, t, "weekly_exhausted", now, t - margin)
    return {"action": "fire", "fireAt": None, "reason": None}


def defer_verdict(job, defer, now):
    """Apply a paid-usage defer (paid.paid_defer) with the prefire caps: defers and the horizon."""
    defer = defer if isinstance(defer, dict) else {}
    reason = defer.get("reason") if defer.get("reason") in consts.REASONS else "failed"
    reset_epoch = _int(defer.get("resetEpoch"))
    fire_at = _int(defer.get("fireAt"))
    if defer.get("action") != "rearm" or fire_at is None:
        return {"action": "skip", "fireAt": None, "reason": reason, "resetEpoch": reset_epoch}
    if fire_at - now > consts.HORIZON_S:
        return {"action": "skip", "fireAt": None, "reason": reason, "resetEpoch": reset_epoch}
    verdict = _rearm_or_give_up(job, max(fire_at, now + _MIN_RETRY_LEAD_S), reason, now, reset_epoch)
    verdict.setdefault("resetEpoch", reset_epoch)
    return verdict


# ---------------------------------------------------------------- postrun

def _armed(t, wait, counter, notify, reason=None):
    return {"status": "armed", "wait": wait, "fireAt": t, "reason": reason, "counter": counter, "notify": notify}


def _final(status, reason, notify):
    return {"status": status, "wait": None, "fireAt": None, "reason": reason, "counter": None, "notify": notify}


def _stale_auth(result, record):
    if result.get("detail") != "rate_limit_reached" or record is None or record.get("stale"):
        return False
    windows = _windows(record)
    return bool(windows) and all(_percent(w) < _STALE_AUTH_PERCENT for w in windows)


def _model_window_reset(record, job, now):
    best = None
    for window in _windows(record):
        if window.get("kind") != "model_weekly":
            continue
        resets = _int(window.get("resetsAt"))
        if resets is None or resets <= now:
            continue
        if _scope_matches(window, job.get("model")) or _percent(window) >= consts.EXHAUSTED:
            best = resets if best is None else min(best, resets)
    return best


def _limit_retry_at(job, result, limit, now, usage, record, margin, reason):
    harness = job.get("harness")
    epoch = _int(limit.get("resetEpoch"))
    model = job.get("model") if isinstance(job.get("model"), str) else ""
    if reason == "limit_suspected":
        if limit.get("source") == "computed" and epoch is not None:
            return epoch + consts.ZEN_FREE_REARM_EXTRA_S
        if model.startswith("opencode-go/"):
            window = reset_window(usage, "opencode-go", now)
            if window is not None:
                return window["resetsAt"] + margin
        return now + consts.LIMIT_SUSPECTED_BACKOFF_S
    if harness == "gemini" and limit.get("kind") == "daily" and epoch is None:
        return timeutil.next_midnight("America/Los_Angeles", now) + margin
    if epoch is not None:
        codex_rule = harness == "codex" or (harness == "pi" and job.get("provider") == "openai-codex")
        return epoch + (max(margin, _CODEX_MIN_MARGIN_S) if codex_rule else margin)
    if limit.get("kind") == "model_weekly" and record is not None and _model_window_reset(record, job, now):
        return _model_window_reset(record, job, now) + margin
    retries = int((job.get("state") or {}).get("limitRetries") or 0)
    return now + consts.LIMIT_BACKOFF_S[min(retries, len(consts.LIMIT_BACKOFF_S) - 1)]


def postrun(job, result, now, usage):
    """What the job becomes after a run (R5 POSTRUN, R0 2.6 act, v2 section 7.4)."""
    state = job.get("state") or {}
    outcome = result.get("outcome")
    harness = job.get("harness")
    margin = _margin(job)
    reason = result.get("reason") if result.get("reason") in consts.REASONS else None

    if outcome == "done":
        return _final("done", None, "done")
    if outcome == "timeout" and result.get("detail") == "sigterm":
        return _final("interrupted", "interrupted", "interrupted")
    if outcome == "interrupted":
        return _final("interrupted", "interrupted", "interrupted")
    if outcome == "failed" and reason is not None:
        return _final("failed", reason, "failed")

    if outcome == "limit":
        limit = result.get("limit") or {}
        if limit.get("rearm") is False:
            detail = result.get("detail")
            if detail not in consts.REASONS:
                detail = "monthly_limit" if harness == "cursor" else "quota_final"
            return _final("limit", detail, "limit_final")
        # Claude's usage record only speaks for Claude Code's own quota.
        claude_quota = harness == "claude"
        record = _claude_record(usage) if claude_quota else None
        if limit.get("isUsingOverage") and reason != "overage_blocked":
            return _final("limit", "overage", "limit_final")
        retries = int(state.get("limitRetries") or 0)
        if retries >= consts.MAX_LIMIT_RETRIES:
            return _final("limit", "limit_retries", "limit_final")
        if _int(limit.get("resetEpoch")) is None and claude_quota and _stale_auth(result, record):
            return _final("limit", "stale_auth", "limit_final")
        t = _limit_retry_at(job, result, limit, now, usage, record, margin, reason)
        if record is not None:
            try:
                t, _deferred = _apply_blockers(record, t, job, margin)
            except ApError:
                return _final("skipped", "weekly_exhausted", "skipped")
        t = max(t, now + _MIN_RETRY_LEAD_S)
        if t - now > consts.HORIZON_S:
            return _final("gave_up", "horizon", "gave_up")
        return _armed(t, "limit", "limitRetries", "limit_rearmed", reason=reason)

    if outcome == "transient":
        retries = int(state.get("transientRetries") or 0)
        if retries >= consts.MAX_TRANSIENT:
            return _final("gave_up", "transient_retries", "gave_up")
        backoff = consts.TRANSIENT_BACKOFF_S[min(retries, len(consts.TRANSIENT_BACKOFF_S) - 1)]
        return _armed(now + backoff, "transient", "transientRetries", "transient_rearmed", reason=reason)

    if outcome == "busy":
        defers = int(state.get("busyDefers") or 0)
        if defers >= consts.MAX_BUSY_DEFERS:
            return _final("busy", "session_busy", "busy_final")
        return _armed(now + consts.BUSY_DEFER_S, "busy", "busyDefers", "busy_deferred")

    if outcome == "failed" and result.get("detail") == "unclassified" and not int(state.get("unknownRetries") or 0):
        return _armed(now + consts.UNKNOWN_RETRY_S, "transient", "unknownRetries", "transient_rearmed")

    final_reason = outcome if outcome in consts.REASONS else "failed"
    return _final("failed", final_reason, "failed")
