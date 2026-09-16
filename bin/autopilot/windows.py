"""Limit window vocabulary: short labels, kinds, sliding windows, bindings and computed daily resets.

Pure functions over values usage.py has already read, bounded and cleaned. Nothing here opens a
file, starts a process or talks to the network.

A record window gets its short label and kind from its label first and from its title only for
display (a model name such as "Opus 5 (1M context)" must never be read as a window length). A
window whose reset is just "read time + window length" is sliding: it moves with every read, so it
is never offered as a trigger nor drawn as a fixed marker.
"""
import re

from . import consts, timeutil
from .sessions import clean_text


LIMIT_KINDS = consts.LIMIT_KINDS
SOURCE_ID_RE = consts.SOURCE_ID_RE
USAGE_RECORDS_MAX = consts.USAGE_RECORDS_MAX
USAGE_RESET_HORIZON_S = consts.USAGE_RESET_HORIZON_S
USAGE_PERCENT_MAX = consts.USAGE_PERCENT_MAX
LIMITS_HISTORY_MAX = consts.LIMITS_HISTORY_MAX
LIMITS_HISTORY_KEEP_S = consts.LIMITS_HISTORY_KEEP_S
TIMELINE_RANGE_MAX_S = consts.TIMELINE_RANGE_MAX_S
TIMELINE_BACK_S = consts.TIMELINE_BACK_S
TIMELINE_AHEAD_S = consts.TIMELINE_AHEAD_S

SHORT_LABEL_MAX = 24
DAY_S = 86400
GEMINI_ZONE = "America/Los_Angeles"
_LA_DAYLIGHT_OFFSET_S = 7 * 3600

MODEL_KINDS = ("model_session", "model_weekly", "model_monthly")
HEADLINE_KINDS = ("session", "weekly", "monthly", "daily") + MODEL_KINDS
BILLING_KINDS = ("billing_total", "billing_pool")

COMPUTED_IDS = ("gemini-daily", "zen-free")
COMPUTED_NAMES = {"gemini-daily": "Gemini CLI daily", "zen-free": "OpenCode Zen free"}
COMPUTED_SHORT_LABEL = "Daily"
# Names for sources whose record is gone (a timeline day from the history file).
SOURCE_NAMES = {"claude": "Claude", "codex": "Codex", "cursor": "Cursor", "opencode-go": "OpenCode Go",
                "gemini": "Gemini"}
SOURCE_NAMES.update(COMPUTED_NAMES)

CURSOR_POOL_OWN = "Cursor models"
CURSOR_POOL_OTHER = "Other models"
CURSOR_CYCLE_LABEL = "Cursor cycle"

# R7-SYNTHESIS 5.4, unconditional part. "codex" also binds pi jobs whose provider is openai-codex,
# and "gemini" binds only through a daily window; usage.py applies both conditions.
_BINDINGS = {"claude": ("claude",), "codex": ("codex",), "cursor": ("cursor",), "opencode-go": ("opencode",),
             "gemini": ("gemini",), "gemini-daily": ("gemini",), "zen-free": ("opencode",)}

_WINDOW_RE = re.compile(r"^([0-9]{1,6})([hm]) window$")
_WEEKLY_SUFFIX_RE = re.compile(r"^(.+?)\s*\(weekly\)$", re.I)
_TITLE_PERIOD_RE = re.compile(r"^(.+) (session|weekly|monthly)$", re.I)
_BILLING_CYCLE_RE = re.compile(r"\s*\(billing cycle\)$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _short(text):
    return clean_text(text, SHORT_LABEL_MAX)


def _window_minutes(low_label):
    match = _WINDOW_RE.fullmatch(low_label)
    if not match:
        return None, None
    amount = int(match.group(1))
    return (amount * 60 if match.group(2) == "h" else amount), match.group(2)


def humanise(label, title):
    """(shortLabel <= 24, kind in LIMIT_KINDS) for one record window (R7-SYNTHESIS 5.3)."""
    text = clean_text(label, consts.USAGE_LABEL_MAX)
    low = text.lower()
    shown_title = clean_text(title, consts.USAGE_LABEL_MAX) if isinstance(title, str) else ""
    if low == "session (5-hour)":
        return "5-hour", "session"
    if low == "weekly (7-day)":
        return "Weekly", "weekly"
    if low == "monthly (30-day)":
        return "Monthly", "monthly"
    minutes, unit = _window_minutes(low)
    if minutes is not None:
        amount = minutes // 60 if unit == "h" else minutes
        if minutes == 300:
            return "5-hour", "session"
        if minutes == 10080:
            return "Weekly", "weekly"
        if minutes == 1440:
            return "Daily", "daily"
        if minutes >= 672 * 60:
            return "30-day", "monthly"
        short = ("%d-hour" % amount) if unit == "h" else ("%d-min" % amount)
        return short, ("session" if minutes <= 1440 else "other")
    if low.endswith("daily quota"):
        return "Daily", "daily"
    base = _BILLING_CYCLE_RE.sub("", low)
    if base in ("included total", "included usage"):
        return "Included", "billing_total"
    if base in ("cursor models", "auto models"):
        return CURSOR_POOL_OWN, "billing_pool"
    if base in ("other models", "api models"):
        return CURSOR_POOL_OTHER, "billing_pool"
    if base == "spend limit":
        return "On-demand", "billing_pool"
    match = _WEEKLY_SUFFIX_RE.fullmatch(text)
    if match:
        return _short(match.group(1) + " weekly"), "weekly"
    if shown_title.lower().startswith("grok bot"):
        return "Grok Bot weekly", "weekly"
    match = _TITLE_PERIOD_RE.fullmatch(shown_title)
    if match:
        period = match.group(2).lower()
        return _short(match.group(1) + " " + period), "model_" + period
    return _short(text), "other"


def is_sliding(source_id, label, percent, resets_at, updated_at):
    """True for an unused window whose reset is only "read time + window length".

    `<n>h window` / `<n>m window` labels of any source, a Codex `Weekly (7-day)` (the collector's
    label for a 10080-minute window) and an OpenCode Go `Session (5-hour)`, each at 0 %. Without a
    read time the window counts as sliding, so a trigger never arms on it.
    """
    if resets_at is None or isinstance(percent, bool) or percent != 0:
        return False
    low = label.lower() if isinstance(label, str) else ""
    minutes, _unit = _window_minutes(low)
    if minutes is not None:
        length = minutes * 60
    elif source_id == "codex" and low == "weekly (7-day)":
        length = 7 * DAY_S
    elif source_id == "opencode-go" and low == "session (5-hour)":
        length = 5 * 3600
    else:
        return False
    if updated_at is None:
        return True
    return abs(resets_at - (updated_at + length)) < consts.SLIDING_TOLERANCE_S


def window_key(source_id, short_label, seen):
    """"<id>:<slug>", then ":2", ":3" for repeats; seen is the per-provider counter dict."""
    slug = _SLUG_RE.sub("-", str(short_label).lower()).strip("-") or "window"
    base = "%s:%s" % (source_id, slug)
    count = seen.get(base, 0) + 1
    seen[base] = count
    return base if count == 1 else "%s:%d" % (base, count)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def headline_key(source_id, windows):
    """Key of the window the header chip shows, or None.

    Computed sources: their only window. Cursor: the Included (billing_total) window, never a pool.
    Others: highest percent among session, weekly, monthly, daily and model windows; ties go to a
    fixed window, then the soonest reset, then record order.
    """
    items = [w for w in windows or [] if isinstance(w, dict)]
    if not items:
        return None
    if source_id in COMPUTED_IDS or items[0].get("source") == "computed":
        return items[0].get("key")
    if source_id == "cursor":
        for window in items:
            if window.get("kind") == "billing_total":
                return window.get("key")
    candidates = [(index, w) for index, w in enumerate(items)
                  if w.get("kind") in HEADLINE_KINDS and _number(w.get("percent"))]
    if not candidates:
        return None

    def rank(pair):
        index, window = pair
        resets = window.get("resetsAt")
        return (-float(window["percent"]), bool(window.get("sliding")),
                resets if _number(resets) else float("inf"), index)

    return min(candidates, key=rank)[1].get("key")


def bindings(source_id):
    """Harness ids a source can bind to, before the per-job conditions of R7-SYNTHESIS 5.4."""
    return _BINDINGS.get(source_id, ())


def _usage_provider(usage, source_id):
    providers = usage.get("providers") if isinstance(usage, dict) else None
    for item in providers if isinstance(providers, list) else []:
        if isinstance(item, dict) and item.get("id") == source_id:
            return item
    return None


def _has_windows(provider, kinds=None):
    if not isinstance(provider, dict) or provider.get("readable") is not True:
        return False
    for window in provider.get("windows") or []:
        if isinstance(window, dict) and (kinds is None or window.get("kind") in kinds):
            return True
    return False


def limit_source_for(job, billing, usage=None):
    """The window source a job draws from (RunRecord/PublicJob `limitSource`), or None.

    usage None means "not read": the binding alone decides. With usage, Cursor and OpenCode Go need a
    readable record with windows, and Gemini uses its record only when that has a daily window.
    """
    if not isinstance(job, dict):
        return None
    harness = job.get("harness")
    model = job.get("model") if isinstance(job.get("model"), str) else ""
    if harness == "claude":
        return "claude"
    if harness == "codex":
        return "codex"
    if harness == "pi":
        return "codex" if job.get("provider") == "openai-codex" else None
    if harness == "cursor":
        if usage is None or _has_windows(_usage_provider(usage, "cursor")):
            return "cursor"
        return None
    if harness == "opencode":
        if model.startswith("opencode-go/"):
            if usage is None or _has_windows(_usage_provider(usage, "opencode-go")):
                return "opencode-go"
            return None
        if billing == "zen_free":  # the classifier already resolved an Agent default to its model
            return "zen-free"
        return None
    if harness == "gemini":
        if _has_windows(_usage_provider(usage, "gemini"), kinds=("daily",)):
            return "gemini"
        return "gemini-daily"
    return None


def cursor_pool_for_model(model):
    """The Cursor pool a model draws from: "Cursor models", "Other models", or None (Agent default)."""
    if not isinstance(model, str) or not model:
        return None
    low = model.lower()
    if low == "auto" or low.startswith("composer") or "grok" in low:
        return CURSOR_POOL_OWN
    return CURSOR_POOL_OTHER


def next_utc_midnight(now):
    """The next 00:00 UTC strictly after now (the OpenCode Zen free daily reset)."""
    return (int(now) // DAY_S + 1) * DAY_S


def next_la_midnight(now):
    """The next 00:00 America/Los_Angeles strictly after now (the Gemini CLI daily reset)."""
    try:
        return timeutil.next_midnight(GEMINI_ZONE, int(now))
    except Exception:  # no zone data on this machine: UTC-7, which is never later than the real reset
        shifted = int(now) - _LA_DAYLIGHT_OFFSET_S
        return (shifted // DAY_S + 1) * DAY_S + _LA_DAYLIGHT_OFFSET_S


def _computed(source_id, resets_at):
    key = "%s:%s" % (source_id, COMPUTED_SHORT_LABEL.lower())
    window = {"key": key, "label": "", "title": None, "shortLabel": COMPUTED_SHORT_LABEL, "kind": "daily",
              "percent": None, "over": False, "resetsAt": resets_at, "sliding": False, "source": "computed",
              "bindable": True}
    return {"id": source_id, "name": COMPUTED_NAMES[source_id], "tier": "", "statusText": "", "scope": None,
            "source": "computed", "readable": True, "unreadableReason": None, "updatedAtMs": None,
            "ageSec": None, "stale": False, "cadenceSec": None, "keptFromLastPoll": False,
            "harnesses": list(bindings(source_id)), "relevant": False, "headlineKey": key,
            "windows": [window]}


def computed_providers(now):
    """The two deterministic daily sources, gemini-daily and zen-free (relevance filled by usage.py)."""
    return [_computed("gemini-daily", next_la_midnight(now)), _computed("zen-free", next_utc_midnight(now))]
