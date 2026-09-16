"""Panel defaults kept in STATE/settings.json (0600, <= 16 KiB).

Loading never fails on content: a missing, oversized, unreadable or malformed file gives the
defaults, key by key, so a bad settings file can never stop a job from firing. Writes come
only through validate_partial, which refuses unknown keys and out-of-range values.
"""
import json

from . import consts, edition
from .errors import ApError

SETTINGS_FILE = "settings.json"

DEFAULTS = {"schemaVersion": 1, "defaultHarness": "claude", "defaultLevel": "plan", "resetMarginSec": 120,
            "eveningTime": "23:00", "morningTime": "07:00", "notify": "all", "motion": "full",
            "defaultAllowPaid": False, "limitsShown": "auto", "lastSeenAt": None}

_NOTIFY = ("all", "failures", "never")
_MOTION = ("full", "reduced")
_LIMITS_SHOWN_MAX = 32
_SOURCE_ID_RE = consts.SOURCE_ID_RE


def _limits_shown_ok(value):
    """"auto", or a list of at most 32 unique usage source ids in header order."""
    if value == "auto":
        return True
    if not isinstance(value, list) or len(value) > _LIMITS_SHOWN_MAX:
        return False
    if not all(isinstance(item, str) and _SOURCE_ID_RE.fullmatch(item) for item in value):
        return False
    return len(set(value)) == len(value)


def _valid(key, value):
    if key == "schemaVersion":
        return value == edition.SCHEMA_VERSION and not isinstance(value, bool)
    if key == "defaultHarness":
        return value in consts.HARNESSES
    if key == "defaultLevel":
        return isinstance(value, str) and value in edition.LEVEL_IDS
    if key == "resetMarginSec":
        return (isinstance(value, int) and not isinstance(value, bool)
                and consts.MARGIN_MIN <= value <= consts.MARGIN_MAX)
    if key in ("eveningTime", "morningTime"):
        return isinstance(value, str) and bool(consts.HHMM_RE.fullmatch(value))
    if key == "notify":
        return value in _NOTIFY
    if key == "motion":
        return value in _MOTION
    if key == "defaultAllowPaid":
        return isinstance(value, bool)
    if key == "limitsShown":
        return _limits_shown_ok(value)
    if key == "lastSeenAt":
        return value is None or (isinstance(value, int) and not isinstance(value, bool)
                                 and consts.EPOCH_MIN <= value <= consts.EPOCH_MAX)
    return False


def load(sd):
    """Defaults merged with every valid key of settings.json."""
    merged = dict(DEFAULTS)
    if sd is None:
        return merged
    try:
        raw = sd.read_json(SETTINGS_FILE, consts.SETTINGS_FILE_MAX)
    except ApError:
        return merged
    if not isinstance(raw, dict):
        return merged
    for key, value in raw.items():
        if key in DEFAULTS and _valid(key, value):
            merged[key] = value
    merged["schemaVersion"] = edition.SCHEMA_VERSION
    return merged


def validate_partial(obj):
    """The checked subset of Settings in obj; bad_input with the offending key as field."""
    if not isinstance(obj, dict):
        raise ApError("bad_input")
    out = {}
    for key, value in obj.items():
        if key not in DEFAULTS:
            raise ApError("bad_input")
        if not _valid(key, value):
            raise ApError("bad_input", key)
        out[key] = value
    return out


def save(sd, merged):
    data = {key: merged.get(key, DEFAULTS[key]) for key in DEFAULTS}
    for key, value in data.items():
        if not _valid(key, value):
            raise ApError("bad_input", key)
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > consts.SETTINGS_FILE_MAX:
        raise ApError("bad_input")
    sd.write_atomic(SETTINGS_FILE, encoded)
