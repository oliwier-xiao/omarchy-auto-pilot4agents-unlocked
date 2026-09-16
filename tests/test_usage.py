"""Tests for usage records v2, limit windows, the limits history, the timeline verb and settings v2.

Every test builds its own HOME in a temporary folder and clears XDG_STATE_HOME, so nothing reads
the real usage folder or the real state folder. No test starts a process. Stand-ins replace what
other modules own: the job store, run records, agent discovery and the models cache.
"""
import datetime
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

from autopilot import (cli_scan, consts, fsio, identity, jobs, limits_history, sessions,  # noqa: E402
                       settings, timeutil, usage, windows)
from autopilot.errors import ApError  # noqa: E402

DAY = 86400
HOUR = 3600
USAGE_REL = ".local/state/omarchy/agents/usage"
_MISSING = object()
J1, J2, J3, J4, J5 = ("%016x" % n for n in (0xa1, 0xb2, 0xc3, 0xd4, 0xe5))

PROVIDER_KEYS = ["id", "name", "tier", "statusText", "scope", "source", "readable", "unreadableReason",
                 "updatedAtMs", "ageSec", "stale", "cadenceSec", "keptFromLastPoll", "harnesses", "relevant",
                 "headlineKey", "windows"]
WINDOW_KEYS = ["key", "label", "title", "shortLabel", "kind", "percent", "over", "resetsAt", "sliding", "source",
               "bindable"]


def iso(epoch, fraction=""):
    """UTC ISO-8601 with an explicit offset; whole seconds unless a fraction such as ".250000" is given."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(int(epoch))) + fraction + "+00:00"


def iso_offset(epoch, hours, fraction=".234255"):
    """Local-offset ISO-8601 the way agent-collectors writes it (for example -05:00 with microseconds)."""
    sign = "-" if hours < 0 else "+"
    return (time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(int(epoch) + hours * HOUR)) + fraction
            + "%s%02d:00" % (sign, abs(hours)))


def utc(*parts):
    return int(datetime.datetime(*parts, tzinfo=datetime.timezone.utc).timestamp())


def cursor_triple(resets_at, included=1.05, own=0.3, other=0.96, suffix=""):
    return [{"label": "Included total" + suffix, "percent": included, "resetsAt": iso(resets_at)},
            {"label": "Cursor Models" + suffix, "percent": own, "resetsAt": iso(resets_at)},
            {"label": "Other Models" + suffix, "percent": other, "resetsAt": iso(resets_at)}]


class UsageCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="h4-usage-")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home, mode=0o700)
        self._saved_env = {key: os.environ.get(key) for key in ("HOME", "AP4A_STATE_DIR", "XDG_STATE_HOME", "TZ")}
        os.environ["HOME"] = self.home
        os.environ.pop("AP4A_STATE_DIR", None)
        os.environ.pop("XDG_STATE_HOME", None)
        self._patches = []
        self.now = int(time.time())
        self.now_ms = self.now * 1000
        self.installed = set()
        self.patch(identity, "discover_cli",
                   lambda h: {"harness": h, "ok": h in self.installed, "reason": None, "exec": []})

    def tearDown(self):
        for obj, name, value in reversed(self._patches):
            if value is _MISSING:
                try:
                    delattr(obj, name)
                except AttributeError:
                    pass
            else:
                setattr(obj, name, value)
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        time.tzset()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def patch(self, obj, name, value):
        self._patches.append((obj, name, getattr(obj, name, _MISSING)))
        setattr(obj, name, value)

    def write(self, rel, data, mode=0o600):
        path = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        os.chmod(path, mode)
        return path

    def record(self, stem, limits, raw_replace=None, **fields):
        document = {"schemaVersion": 1, "id": stem, "name": stem.title(), "updatedAt": iso(self.now - 60),
                    "ready": True, "tierLabel": "", "usageStatusText": "", "limits": limits}
        document.update(fields)
        text = json.dumps(document)
        for old, new in (raw_replace or {}).items():
            text = text.replace(old, new)
        return self.write("%s/%s.json" % (USAGE_REL, stem), text)

    def read(self, **kwargs):
        return usage.read_usage(self.now_ms, **kwargs)

    def row(self, result, source_id):
        return usage.provider(result, source_id)

    def state(self):
        sd = fsio.open_state(create=True)
        self.addCleanup(sd.close)
        return sd

    def stand_in_jobs(self, job_list):
        self.patch(jobs, "load_store", lambda sd: {"schemaVersion": 1, "updatedAt": 0, "reconciledAt": None,
                                                   "jobs": job_list})


# --- reader --------------------------------------------------------------------------------------

class ReaderTests(UsageCase):
    def test_usage_live_codex_shape_monthly_30_day(self):
        updated = self.now - 120
        self.record("codex", [{"label": "720h window", "percent": 0.01, "resetsAt": iso(self.now + 30 * DAY - HOUR)}],
                    name="Codex", tierLabel="free", updatedAt=iso(updated), hasLocalStats=True, todayPrompts=3,
                    modelUsage={"gpt-5.6-terra": {"inputTokens": 14200}})
        result = self.read()
        self.assertEqual(list(result), ["nowMs", "providers", "claude", "codex"])
        codex = self.row(result, "codex")
        self.assertEqual(list(codex), PROVIDER_KEYS)
        self.assertEqual([w["key"] for w in codex["windows"]], ["codex:30-day"])
        window = codex["windows"][0]
        self.assertEqual(list(window), WINDOW_KEYS)
        self.assertEqual((window["shortLabel"], window["kind"], window["percent"], window["over"], window["sliding"],
                          window["source"], window["bindable"], window["title"]),
                         ("30-day", "monthly", 0.01, False, False, "record", False, None))
        self.assertEqual(window["resetsAt"], self.now + 30 * DAY - HOUR)
        self.assertEqual((codex["headlineKey"], codex["harnesses"], codex["tier"], codex["cadenceSec"],
                          codex["stale"], codex["updatedAtMs"], codex["ageSec"]),
                         ("codex:30-day", ["codex"], "free", 900, False, updated * 1000, 120))
        self.assertEqual(result["codex"]["windows"],
                         [{"label": "720h window", "kind": "monthly", "percent": 0.01,
                           "resetsAt": self.now + 30 * DAY - HOUR, "title": None}])
        # The unused free window reports "read time + 720 h" at 0 %: sliding, never bindable, not in the alias.
        self.record("codex", [{"label": "720h window", "percent": 0, "resetsAt": iso(updated + 720 * HOUR)}],
                    updatedAt=iso(updated))
        result = self.read()
        window = self.row(result, "codex")["windows"][0]
        self.assertEqual((window["sliding"], window["bindable"], window["kind"]), (True, False, "monthly"))
        self.assertEqual(result["codex"]["windows"], [])

    def test_usage_percent_no_rescale_over_and_drop(self):
        limits = [
            {"label": "Session (5-hour)", "percent": 0.22, "resetsAt": iso(self.now + HOUR)},
            {"label": "Included total", "percent": 1.05, "resetsAt": iso(self.now + 25 * DAY)},
            {"label": "Weekly (7-day)", "percent": 10, "resetsAt": ""},
            {"label": "too much", "percent": 11},
            {"label": "old 0..100 scale", "percent": 22},
            {"label": "negative", "percent": -0.01},
            {"label": "bool", "percent": True},
            {"label": "string", "percent": "0.5"},
            {"label": "missing"},
            {"label": "infinite", "percent": "__HUGE__"},
            {"label": "big int", "percent": 10 ** 400},
            {"label": "zero int", "percent": 0},
        ]
        self.record("cursor", limits, raw_replace={'"__HUGE__"': "1e999"})
        windows_list = self.row(self.read(), "cursor")["windows"]
        self.assertEqual([(w["label"], w["percent"], w["over"]) for w in windows_list],
                         [("Session (5-hour)", 0.22, False), ("Included total", 1.05, True),
                          ("Weekly (7-day)", 10, True), ("zero int", 0, False)])
        self.record("cursor", limits, raw_replace={'"__HUGE__"': "NaN"})
        row = self.row(self.read(), "cursor")
        self.assertEqual((row["readable"], row["unreadableReason"]), (False, "invalid"))

    def test_usage_reset_horizon_45_days(self):
        offsets = [44 * DAY, 46 * DAY, -2 * DAY, -HOUR, 7 * DAY, 9 * DAY, 0, consts.HORIZON_S]
        self.record("fireworks", [{"label": "W%d" % i, "percent": 0.5, "resetsAt": iso(self.now + offset)}
                                  for i, offset in enumerate(offsets)])
        got = [(w["resetsAt"] - self.now if w["resetsAt"] is not None else None, w["bindable"])
               for w in self.row(self.read(), "fireworks")["windows"]]
        self.assertEqual(got, [(44 * DAY, False), (None, False), (None, False), (-HOUR, False), (7 * DAY, True),
                               (9 * DAY, False), (0, False), (consts.HORIZON_S, True)])
        self.assertEqual(windows.USAGE_RESET_HORIZON_S, 45 * DAY)

    def test_usage_scan_caps_33_files_dotfile_symlink_oversize(self):
        for index in range(33):
            self.record("p%02d" % index, [])
        for name in (".claude.AbC123", ".opencode.x1.tmp", "Upper.json", "bad name.json", "x.json.tmp", "-dash.json"):
            self.write("%s/%s" % (USAGE_REL, name), "{}")
        result = self.read()
        self.assertEqual([p["id"] for p in result["providers"] if p["source"] == "record"],
                         ["p%02d" % i for i in range(32)])
        self.assertEqual([p["id"] for p in result["providers"] if p["source"] == "computed"],
                         ["gemini-daily", "zen-free"])

        shutil.rmtree(os.path.join(self.home, USAGE_REL))
        path = self.record("claude", [{"label": "Session (5-hour)", "percent": 0.5, "resetsAt": iso(self.now + 60)}])
        target = os.path.join(self.tmp, "claude-target.json")
        os.rename(path, target)
        os.symlink(target, path)
        self.write("%s/cursor.json" % USAGE_REL,
                   json.dumps({"id": "cursor", "limits": [], "pad": " " * (consts.USAGE_RECORD_MAX + 1)}))
        os.makedirs(os.path.join(self.home, USAGE_REL, "dir.json"))
        os.mkfifo(os.path.join(self.home, USAGE_REL, "fifo.json"), 0o600)
        self.record("zai", [])
        started = time.monotonic()
        result = self.read()
        self.assertLess(time.monotonic() - started, 5)
        rows = {p["id"]: p for p in result["providers"] if p["source"] == "record"}
        self.assertEqual({key: (row["readable"], row["unreadableReason"]) for key, row in rows.items()},
                         {"claude": (False, "refused"), "cursor": (False, "too_large"), "dir": (False, "refused"),
                          "fifo": (False, "refused"), "zai": (True, None)})
        claude = rows["claude"]
        self.assertEqual(list(claude), PROVIDER_KEYS)
        self.assertEqual((claude["name"], claude["stale"], claude["windows"], claude["headlineKey"],
                          claude["harnesses"], claude["updatedAtMs"]), ("claude", True, [], None, ["claude"], None))
        self.assertFalse(result["claude"]["available"])

        # XDG_STATE_HOME is honoured only as a clean absolute folder inside HOME.
        self.write(".xdg-state/omarchy/agents/usage/xdgonly.json", json.dumps({"id": "xdgonly", "limits": []}))
        os.environ["XDG_STATE_HOME"] = os.path.join(self.home, ".xdg-state")
        self.assertIsNotNone(self.row(self.read(), "xdgonly"))
        self.assertIsNone(self.row(self.read(), "zai"))
        outside = os.path.join(self.tmp, "outside")
        os.makedirs(os.path.join(outside, "omarchy/agents/usage"))
        os.environ["XDG_STATE_HOME"] = outside
        self.assertIsNotNone(self.row(self.read(), "zai"))
        os.environ["XDG_STATE_HOME"] = "relative/state"
        self.assertIsNotNone(self.row(self.read(), "zai"))
        os.environ.pop("XDG_STATE_HOME")
        # A usage folder that is itself a symlink gives no records.
        real = os.path.join(self.tmp, "real-usage")
        os.rename(os.path.join(self.home, USAGE_REL), real)
        os.symlink(real, os.path.join(self.home, USAGE_REL))
        self.assertEqual([p["source"] for p in self.read()["providers"]], ["computed", "computed"])

    def test_usage_id_mismatch_and_schema_version(self):
        cases = (({"id": "codex"}, "id_mismatch"), ({"id": None}, "id_mismatch"), ({"schemaVersion": 2}, "schema"),
                 ({"schemaVersion": True}, "schema"), ({"schemaVersion": "1"}, "schema"),
                 ({"schemaVersion": 1.0}, "schema"), ({"limits": {"label": "x"}}, "schema"))
        for fields, reason in cases:
            document = {"schemaVersion": 1, "id": "claude", "limits": []}
            document.update(fields)
            self.write("%s/claude.json" % USAGE_REL, json.dumps(document))
            row = self.row(self.read(), "claude")
            self.assertEqual((row["readable"], row["unreadableReason"]), (False, reason), fields)
        for raw in ("[1, 2]", "{broken", '{"id": "claude", "id": "claude"}', b"\xff\xfe"):
            self.write("%s/claude.json" % USAGE_REL, raw)
            row = self.row(self.read(), "claude")
            self.assertEqual((row["readable"], row["unreadableReason"]), (False, "invalid"), raw)
        self.write("%s/claude.json" % USAGE_REL, json.dumps({"id": "claude", "limits": None, "updatedAt": iso(self.now)}))
        row = self.row(self.read(), "claude")
        self.assertEqual((row["readable"], row["windows"], row["stale"]), (True, [], False))
        self.write("%s/claude.json" % USAGE_REL, "[" * 5000 + "]" * 5000)
        self.assertEqual(self.row(self.read(), "claude")["unreadableReason"], "invalid")

    def test_usage_humaniser_table(self):
        table = [
            (("Session (5-hour)", None), ("5-hour", "session")),
            (("session (5-HOUR)", None), ("5-hour", "session")),
            (("5h window", None), ("5-hour", "session")),
            (("300m window", None), ("5-hour", "session")),
            (("Weekly (7-day)", "Weekly (7-day)"), ("Weekly", "weekly")),
            (("168h window", None), ("Weekly", "weekly")),
            (("10080m window", None), ("Weekly", "weekly")),
            (("720h window", None), ("30-day", "monthly")),
            (("672h window", None), ("30-day", "monthly")),
            (("Monthly (30-day)", None), ("Monthly", "monthly")),
            (("24h window", None), ("Daily", "daily")),
            (("Gemini 2.5 Pro daily quota", "Gemini 2.5 Pro"), ("Daily", "daily")),
            (("3h window", None), ("3-hour", "session")),
            (("90m window", None), ("90-min", "session")),
            (("48h window", None), ("48-hour", "other")),
            (("5h window", "Big 720h Session"), ("5-hour", "session")),
            (("Included total", None), ("Included", "billing_total")),
            (("Included usage", None), ("Included", "billing_total")),
            (("Included total (billing cycle)", "Included total"), ("Included", "billing_total")),
            (("Cursor Models", None), ("Cursor models", "billing_pool")),
            (("Cursor Models (billing cycle)", "Cursor Models"), ("Cursor models", "billing_pool")),
            (("Auto models", None), ("Cursor models", "billing_pool")),
            (("Other Models", None), ("Other models", "billing_pool")),
            (("API models", None), ("Other models", "billing_pool")),
            (("Spend limit", None), ("On-demand", "billing_pool")),
            (("Search (weekly)", None), ("Search weekly", "weekly")),
            (("Bot usage", "Grok Bot"), ("Grok Bot weekly", "weekly")),
            (("Fable Weekly", "Fable Weekly"), ("Fable weekly", "model_weekly")),
            (("Grok Weekly", "Grok Weekly"), ("Grok weekly", "model_weekly")),
            (("Pro Monthly", "Pro Monthly"), ("Pro monthly", "model_monthly")),
            (("Limit", None), ("Limit", "other")),
            (("Tokens", None), ("Tokens", "other")),
            (("Prompt credits", None), ("Prompt credits", "other")),
            (("Search (monthly)", None), ("Search (monthly)", "other")),
            (("Usage cycle", None), ("Usage cycle", "other")),
            (("Fable Weekly", None), ("Fable Weekly", "other")),
        ]
        for (label, title), expected in table:
            self.assertEqual(windows.humanise(label, title), expected, (label, title))
        # Digits in a model title are never read as a window length.
        short, kind = windows.humanise("Opus 5 (1M context) Session", "Opus 5 (1M context) Session")
        self.assertEqual(kind, "model_session")
        self.assertLessEqual(len(short), windows.SHORT_LABEL_MAX)
        self.assertTrue(short.startswith("Opus 5 (1M context)") and short.endswith("…"), short)
        short, kind = windows.humanise("Premium requests 120/300 used this month", None)
        self.assertEqual((len(short), kind), (windows.SHORT_LABEL_MAX, "other"))
        seen = {}
        self.assertEqual([windows.window_key("gemini", "Daily", seen) for _ in range(3)],
                         ["gemini:daily", "gemini:daily:2", "gemini:daily:3"])
        self.assertEqual([windows.window_key("x", label, {}) for label in ("Fable weekly", "On-demand", "***")],
                         ["x:fable-weekly", "x:on-demand", "x:window"])
        for kind in {expected[1] for _, expected in table}:
            self.assertIn(kind, windows.LIMIT_KINDS)

    def test_usage_sliding_codex_and_go_session(self):
        updated = self.now - 30
        limits = [
            {"label": "5h window", "percent": 0, "resetsAt": iso(updated + 5 * HOUR)},
            {"label": "Weekly (7-day)", "percent": 0, "resetsAt": iso(updated + 7 * DAY + 60)},
            {"label": "5h window", "percent": 0.4, "resetsAt": iso(updated + 3 * HOUR)},
            {"label": "Weekly (7-day)", "percent": 0, "resetsAt": iso(updated + 2 * DAY)},
            {"label": "90m window", "percent": 0, "resetsAt": iso(updated + 90 * 60 + 500)},
            {"label": "5h window", "percent": 1.0, "resetsAt": ""},
        ]
        self.record("codex", limits, updatedAt=iso(updated))
        result = self.read()
        self.assertEqual([w["sliding"] for w in self.row(result, "codex")["windows"]],
                         [True, True, False, False, False, False])
        self.assertEqual([w["bindable"] for w in self.row(result, "codex")["windows"]],
                         [False, False, True, True, True, False])
        codex = result["codex"]
        self.assertEqual((codex["available"], codex["stale"], codex["updatedAtMs"] // 1000), (True, False, updated))
        self.assertEqual([(w["label"], w["percent"]) for w in codex["windows"]],
                         [("5h window", 0.4), ("Weekly (7-day)", 0), ("90m window", 0)])
        self.record("codex", limits, updatedAt="not a time")
        codex = self.read()["codex"]
        self.assertEqual((codex["updatedAtMs"], codex["stale"]), (None, True))
        self.assertEqual([w["percent"] for w in codex["windows"]], [0.4])

        self.record("opencode-go", [
            {"label": "Session (5-hour)", "percent": 0, "resetsAt": iso(updated + 5 * HOUR)},
            {"label": "Weekly (7-day)", "percent": 0.3, "resetsAt": iso(updated + 3 * DAY)},
            {"label": "Monthly (30-day)", "percent": 0.1, "resetsAt": iso(updated + 20 * DAY)},
        ], updatedAt=iso(updated), name="OpenCode Go")
        self.record("claude", [
            {"label": "Session (5-hour)", "percent": 0, "resetsAt": iso(updated + 5 * HOUR)},
            {"label": "Weekly (7-day)", "percent": 0, "resetsAt": iso(updated + 7 * DAY)},
        ], updatedAt=iso(updated))
        result = self.read()
        go = self.row(result, "opencode-go")
        self.assertEqual([(w["kind"], w["sliding"]) for w in go["windows"]],
                         [("session", True), ("weekly", False), ("monthly", False)])
        self.assertEqual((go["headlineKey"], go["harnesses"]), ("opencode-go:weekly", ["opencode"]))
        self.assertEqual([w["sliding"] for w in self.row(result, "claude")["windows"]], [False, False])
        self.assertTrue(windows.is_sliding("codex", "5h window", 0, self.now + 5 * HOUR, None))
        self.assertFalse(windows.is_sliding("codex", "5h window", 0.1, self.now + 5 * HOUR, None))
        self.assertFalse(windows.is_sliding("opencode-go", "Session (5-hour)", 0.2, updated + 5 * HOUR, updated))
        self.assertFalse(windows.is_sliding("cursor", "Included total", 0, updated + 5 * HOUR, updated))

    def test_usage_stale_cadence_cursor_and_default(self):
        cases = (("cursor", 1700, False, 300), ("cursor", 1900, True, 300),
                 ("fireworks", 2600, False, 900), ("fireworks", 2800, True, 900))
        for stem, age, stale, cadence in cases:
            self.record(stem, [], updatedAt=iso(self.now - age))
            row = self.row(self.read(), stem)
            self.assertEqual((row["stale"], row["ageSec"], row["cadenceSec"]), (stale, age, cadence), (stem, age))
        self.record("claude", [], updatedAt=iso(self.now - 4000))
        self.write(".cache/omarchy/agent-usage/claude-limits.json", json.dumps({"fetchedAtMs": self.now_ms - 60000}),
                   mode=0o644)
        row = self.row(self.read(), "claude")
        self.assertEqual((row["updatedAtMs"], row["ageSec"], row["stale"]), (self.now_ms - 60000, 60, False))
        for updated in (None, iso(self.now + HOUR), "2026-09-15T10:00:00"):
            fields = {"updatedAt": updated} if updated is not None else {}
            document = {"id": "fireworks", "limits": []}
            document.update(fields)
            self.write("%s/fireworks.json" % USAGE_REL, json.dumps(document))
            row = self.row(self.read(), "fireworks")
            self.assertEqual((row["updatedAtMs"], row["ageSec"], row["stale"]), (None, None, True), updated)
        for computed in ("gemini-daily", "zen-free"):
            row = self.row(self.read(), computed)
            self.assertEqual((row["stale"], row["cadenceSec"], row["ageSec"], row["updatedAtMs"]),
                             (False, None, None, None))

    def test_usage_cursor_kept_one_poll(self):
        sd = self.state()
        self.record("claude", [])
        self.record("fireworks", [])
        path = self.record("cursor", cursor_triple(self.now + 25 * DAY), scope="account", tierLabel="Pro",
                           todayTotalTokens=999777123)
        first = self.read(sd=sd, record_history=True)
        self.assertFalse(self.row(first, "cursor")["keptFromLastPoll"])
        keep = os.path.join(sd.path, usage.KEEP_FILE)
        self.assertEqual(stat.S_IMODE(os.stat(keep).st_mode), 0o600)
        with open(keep, "rb") as handle:
            saved = handle.read()
        self.assertNotIn(b"999777123", saved)
        self.assertNotIn(b"todayTotalTokens", saved)
        os.unlink(path)
        self.assertIsNone(self.row(self.read(), "cursor"), "a library read never reuses the kept record")
        second = self.read(sd=sd, record_history=True)
        kept = self.row(second, "cursor")
        self.assertTrue(kept["keptFromLastPoll"])
        self.assertEqual(kept["windows"], self.row(first, "cursor")["windows"])
        self.assertEqual([p["id"] for p in second["providers"] if p["source"] == "record"],
                         ["claude", "cursor", "fireworks"])
        self.assertIsNone(self.row(self.read(sd=sd, record_history=True), "cursor"))
        self.assertIsNone(self.row(self.read(sd=sd, record_history=True), "cursor"))
        self.record("cursor", cursor_triple(self.now + 25 * DAY))
        self.assertFalse(self.row(self.read(sd=sd, record_history=True), "cursor")["keptFromLastPoll"])
        os.unlink(path)
        self.assertTrue(self.row(self.read(sd=sd, record_history=True), "cursor")["keptFromLastPoll"])

    def test_usage_cursor_headline_included_over(self):
        resets = self.now + 25 * DAY
        for suffix in ("", " (billing cycle)"):
            self.record("cursor", cursor_triple(resets, suffix=suffix), scope="account", tierLabel="Pro",
                        updatedAt=iso(self.now - 100))
            row = self.row(self.read(), "cursor")
            self.assertEqual([w["key"] for w in row["windows"]],
                             ["cursor:included", "cursor:cursor-models", "cursor:other-models"])
            self.assertEqual([(w["shortLabel"], w["kind"], w["over"], w["bindable"], w["resetsAt"])
                              for w in row["windows"]],
                             [("Included", "billing_total", True, False, resets),
                              ("Cursor models", "billing_pool", False, False, resets),
                              ("Other models", "billing_pool", False, False, resets)])
            self.assertEqual((row["headlineKey"], row["harnesses"], row["scope"], row["tier"]),
                             ("cursor:included", ["cursor"], "account", "Pro"))
        self.record("cursor", cursor_triple(resets, included=0.37, own=0.1, other=0.96))
        self.assertEqual(self.row(self.read(), "cursor")["headlineKey"], "cursor:included")
        self.record("cursor", [{"label": "Other Models", "percent": 0.96, "resetsAt": iso(resets)}], scope="team")
        row = self.row(self.read(), "cursor")
        self.assertEqual((row["headlineKey"], row["scope"]), (None, None))

    def test_usage_agent_collectors_opencode_windowless(self):
        self.installed = {"opencode", "pi"}
        stats = {"hasLocalStats": True, "todayPrompts": 12, "todaySessions": 3, "todayTotalTokens": 53168676,
                 "todayTokensByModel": {"claude-sonnet-5": 1000}, "recentDays": [{"date": "2026-09-14",
                                                                                  "messageCount": 99}],
                 "totalPrompts": 408, "totalSessions": 145, "activeDays": 20, "activeDates": ["2026-09-14"],
                 "modelUsage": {"claude-opus-5": {"inputTokens": 1, "outputTokens": 2}},
                 "authHelpText": "", "ready": True}
        for stem in ("opencode", "pi"):
            self.record(stem, [], updatedAt=iso_offset(self.now - 300, -5), tierLabel="", **stats)
        sd = self.state()
        self.stand_in_jobs([{"id": J1, "harness": "opencode", "model": "anthropic/claude-sonnet-5"}])
        result = self.read(sd=sd, with_relevance=True)
        for stem in ("opencode", "pi"):
            row = self.row(result, stem)
            self.assertEqual((row["readable"], row["windows"], row["headlineKey"], row["harnesses"], row["relevant"],
                              row["stale"]), (True, [], None, [], False, False), stem)
            self.assertEqual(row["updatedAtMs"], (self.now - 300) * 1000 + 234)
        self.assertEqual(limits_history.record_from_usage(sd, result, self.now), 0)

    def test_usage_gemini_record_session_weekly_not_bound(self):
        self.installed = {"gemini"}
        self.record("gemini", [
            {"label": "Session (5-hour)", "title": "Session (5-hour)", "percent": 0.2, "resetsAt": iso(self.now + 2 * HOUR)},
            {"label": "Weekly (7-day)", "title": "Weekly (7-day)", "percent": 0.5, "resetsAt": iso(self.now + 3 * DAY)},
        ], name="Gemini")
        result = self.read(with_relevance=True)
        row = self.row(result, "gemini")
        self.assertEqual(([w["kind"] for w in row["windows"]], row["harnesses"], row["relevant"], row["headlineKey"]),
                         (["session", "weekly"], [], False, "gemini:weekly"))
        self.assertTrue(self.row(result, "gemini-daily")["relevant"])
        self.assertEqual(usage.gemini_daily_reset(result, self.now), windows.next_la_midnight(self.now))
        self.assertEqual(windows.limit_source_for({"harness": "gemini"}, None, result), "gemini-daily")

        self.record("gemini", [
            {"label": "Gemini 2.5 Pro daily quota", "title": "Gemini 2.5 Pro", "percent": 0.4,
             "resetsAt": iso(self.now + 10 * HOUR)},
            {"label": "Weekly (7-day)", "percent": 0.5, "resetsAt": iso(self.now + 3 * DAY)},
        ], name="Gemini")
        result = self.read(with_relevance=True)
        row = self.row(result, "gemini")
        self.assertEqual((row["windows"][0]["kind"], row["windows"][0]["shortLabel"], row["harnesses"], row["relevant"]),
                         ("daily", "Daily", ["gemini"], True))
        self.assertEqual(usage.gemini_daily_reset(result, self.now), self.now + 10 * HOUR)
        self.assertEqual(windows.limit_source_for({"harness": "gemini"}, None, result), "gemini")

    def test_usage_fable_weekly_model_weekly(self):
        self.record("claude", [
            {"label": "Session (5-hour)", "percent": 0.14, "resetsAt": iso(self.now + 2 * HOUR)},
            {"label": "Weekly (7-day)", "percent": 0.31, "resetsAt": iso(self.now + 4 * DAY)},
            {"label": "Fable Weekly", "title": "Fable Weekly", "percent": 0.0, "resetsAt": iso(self.now + 4 * DAY)},
        ], tierLabel="Max 5x")
        result = self.read()
        row = self.row(result, "claude")
        self.assertEqual([(w["key"], w["shortLabel"], w["kind"], w["title"]) for w in row["windows"]],
                         [("claude:5-hour", "5-hour", "session", None), ("claude:weekly", "Weekly", "weekly", None),
                          ("claude:fable-weekly", "Fable weekly", "model_weekly", "Fable Weekly")])
        self.assertEqual((row["headlineKey"], row["harnesses"], row["tier"]), ("claude:weekly", ["claude"], "Max 5x"))
        self.assertEqual(result["claude"]["windows"][2],
                         {"label": "Fable Weekly", "kind": "model_weekly", "percent": 0.0,
                          "resetsAt": self.now + 4 * DAY, "title": "Fable Weekly"})
        self.assertEqual(usage.session_window(result)["percent"], 0.14)
        self.record("claude", [
            {"label": "Weekly (7-day)", "percent": 0.31, "resetsAt": iso(self.now + 4 * DAY)},
            {"label": "Session (5-hour)", "percent": 0.31, "resetsAt": iso(self.now + 2 * HOUR)},
            {"label": "Tokens", "percent": 0.9, "resetsAt": iso(self.now + HOUR)},
        ])
        self.assertEqual(self.row(self.read(), "claude")["headlineKey"], "claude:5-hour")

    def test_usage_never_reads_token_stats(self):
        self.record("claude", [{"label": "Session (5-hour)", "percent": 0.1, "resetsAt": iso(self.now + HOUR),
                                "tokenLimit": 123456789, "startedAt": iso(self.now)}],
                    todayTotalTokens=987654321, todayTokensByModel={"TOKENSENTINEL": 987654321},
                    modelUsage={"TOKENSENTINEL": {"inputTokens": 987654321, "cacheReadInputTokens": 5}},
                    recentDays=[{"date": "2026-09-14", "messageCount": 987654321}],
                    balance={"remaining": 42.5, "currency": "USD"}, cost={"estimateUsd": 3.5},
                    authHelpText="TOKENSENTINEL", retryAdvised=True)
        result = self.read()
        dumped = json.dumps(result)
        for needle in ("987654321", "123456789", "TOKENSENTINEL", "modelUsage", "balance", "tokenLimit", "authHelp",
                       "retryAdvised", "estimateUsd"):
            self.assertNotIn(needle, dumped)
        with open(os.path.join(self.home, USAGE_REL, "claude.json"), "rb") as handle:
            subset, reason = usage._check_doc("claude", json.loads(handle.read()))
        self.assertIsNone(reason)
        self.assertLessEqual(set(subset), {"id", "schemaVersion", "name", "tierLabel", "usageStatusText",
                                           "updatedAt", "scope", "limits"})

    def test_usage_legacy_aliases_claude_codex(self):
        result = self.read()
        self.assertEqual(result["claude"], {"available": False, "fetchedAtMs": None, "ageSec": None, "stale": True,
                                            "status": "", "tierLabel": "", "windows": []})
        self.assertEqual(result["codex"], {"available": False, "updatedAtMs": None, "stale": True, "status": "",
                                           "windows": []})
        self.assertIsNone(usage.session_window(result))
        self.record("claude", [{"label": "Session (5-hour)", "percent": 0.5, "resetsAt": iso(self.now + 600)}])
        claude = self.read()["claude"]
        self.assertEqual(sorted(claude), ["ageSec", "available", "fetchedAtMs", "stale", "status", "tierLabel",
                                          "windows"])
        self.assertEqual((claude["available"], claude["fetchedAtMs"], claude["ageSec"], claude["stale"]),
                         (True, None, None, True))
        self.assertEqual(claude["windows"], [{"label": "Session (5-hour)", "kind": "session", "percent": 0.5,
                                              "resetsAt": self.now + 600, "title": None}])
        limits_path = ".cache/omarchy/agent-usage/claude-limits.json"
        self.write(limits_path, json.dumps({"fetchedAtMs": self.now_ms - 3600000, "limits": []}), mode=0o644)
        claude = self.read()["claude"]
        self.assertEqual((claude["fetchedAtMs"], claude["ageSec"], claude["stale"]), (self.now_ms - 3600000, 3600, True))
        self.write(limits_path, json.dumps({"fetchedAtMs": self.now_ms - 60000}), mode=0o644)
        claude = self.read()["claude"]
        self.assertEqual((claude["ageSec"], claude["stale"]), (60, False))
        for bad in ("1789411895459", True, self.now_ms + 3600000, 5):
            self.write(limits_path, json.dumps({"fetchedAtMs": bad}), mode=0o644)
            claude = self.read()["claude"]
            self.assertEqual((claude["fetchedAtMs"], claude["stale"]), (None, True), bad)
        self.record("codex", [{"label": "5h window", "percent": 0.4, "resetsAt": iso(self.now + 3 * HOUR)}],
                    updatedAt=iso(self.now - 100), usageStatusText="Signed in")
        codex = self.read()["codex"]
        self.assertEqual(sorted(codex), ["available", "stale", "status", "updatedAtMs", "windows"])
        self.assertEqual((codex["available"], codex["stale"], codex["status"], len(codex["windows"])),
                         (True, False, "Signed in", 1))

    def test_usage_relevance_installed_or_jobs(self):
        for stem in ("claude", "codex", "cursor", "opencode-go", "fireworks", "opencode"):
            self.record(stem, [{"label": "Weekly (7-day)", "percent": 0.2, "resetsAt": iso(self.now + DAY)}])
        self.installed = {"claude"}
        self.stand_in_jobs([{"id": J1, "harness": "gemini", "model": None},
                            {"id": J2, "harness": "pi", "provider": "openai-codex", "model": "gpt-5.6"},
                            {"id": J3, "harness": "opencode", "model": "opencode/big-pickle"}])
        self.patch(usage, "_cached_billing", lambda model, now: "zen_free" if model == "opencode/big-pickle" else None)
        sd = self.state()
        result = self.read(sd=sd, with_relevance=True)
        self.assertEqual({p["id"]: p["relevant"] for p in result["providers"]},
                         {"claude": True, "codex": True, "cursor": False, "opencode-go": False, "fireworks": False,
                          "opencode": False, "gemini-daily": True, "zen-free": True})
        self.assertFalse(any(p["relevant"] for p in self.read(sd=sd)["providers"]))
        # OpenCode Go binds only to opencode-go/* models: a job on one, or the OpenCode default naming one.
        self.installed = {"claude", "opencode"}
        self.assertFalse(self.row(self.read(sd=sd, with_relevance=True), "opencode-go")["relevant"])
        self.stand_in_jobs([{"id": J3, "harness": "opencode", "model": "opencode-go/kimi-k2"}])
        self.assertTrue(self.row(self.read(sd=sd, with_relevance=True), "opencode-go")["relevant"])
        self.stand_in_jobs([{"id": J3, "harness": "opencode", "model": None}])
        config = os.path.join(os.environ["HOME"], ".config", "opencode")
        os.makedirs(config, exist_ok=True)
        with open(os.path.join(config, "opencode.json"), "w") as handle:
            json.dump({"model": "opencode-go/kimi-k2"}, handle)
        self.assertTrue(self.row(self.read(sd=sd, with_relevance=True), "opencode-go")["relevant"])
        os.remove(os.path.join(config, "opencode.json"))
        self.installed = {"claude"}
        self.stand_in_jobs([{"id": J1, "harness": "gemini", "model": None},
                            {"id": J2, "harness": "pi", "provider": "openai-codex", "model": "gpt-5.6"},
                            {"id": J3, "harness": "opencode", "model": "opencode/big-pickle"}])
        self.patch(usage, "_cached_billing", lambda model, now: None)
        self.assertFalse(self.row(self.read(sd=sd, with_relevance=True), "zen-free")["relevant"])

        def refused(sd_arg):
            raise ApError("state_corrupt")

        def broken(harness_id):
            raise ApError("bad_env")

        self.patch(jobs, "load_store", refused)
        result = self.read(sd=sd, with_relevance=True)
        self.assertEqual((self.row(result, "claude")["relevant"], self.row(result, "gemini-daily")["relevant"],
                          self.row(result, "codex")["relevant"]), (True, False, False))
        self.patch(identity, "discover_cli", broken)
        self.assertFalse(any(p["relevant"] for p in self.read(sd=sd, with_relevance=True)["providers"]))

    def test_usage_strings_cleaned(self):
        limits = [
            {"label": "Session\n(5-hour)", "percent": 0.22, "resetsAt": iso(self.now + HOUR, ".250000")},
            {"label": "L" * 80, "percent": 1, "resetsAt": ""},
            {"label": "Premium requests used this month in total", "title": "Premium\x00 requests", "percent": 0.3},
            "not an object",
            {"percent": 0.5},
            {"label": "   ", "percent": 0.5},
            {"label": "Extra 1", "percent": 0.1, "resetsAt": "yesterday"},
            {"label": "Extra 2", "percent": 0.2, "resetsAt": iso(self.now - 2 * DAY)},
            {"label": "Extra 3", "percent": 0.3},
            {"label": "Extra 4", "percent": 0.4, "resetsAt": 1789411801},
            {"label": "Extra 5", "percent": 0.5},
            {"label": "Extra 6 (ninth)", "percent": 0.6},
        ]
        self.record("claude", limits, name="Claude\x00 Code\n", usageStatusText="Waiting\x00 for auth",
                    tierLabel="T" * 90, scope="team")
        row = self.row(self.read(), "claude")
        self.assertEqual((row["name"], row["statusText"], len(row["tier"]), row["scope"]),
                         ("Claude Code", "Waiting for auth", consts.USAGE_LABEL_MAX, None))
        self.assertEqual(len(row["windows"]), consts.USAGE_LIMITS_MAX)
        first = row["windows"][0]
        self.assertEqual((first["label"], first["resetsAt"], first["shortLabel"]),
                         ("Session (5-hour)", self.now + HOUR, "5-hour"))
        self.assertEqual((len(row["windows"][1]["label"]), row["windows"][1]["label"][-1]),
                         (consts.USAGE_LABEL_MAX, "…"))
        third = row["windows"][2]
        self.assertEqual((third["title"], len(third["shortLabel"]), third["shortLabel"][-1]),
                         ("Premium requests", windows.SHORT_LABEL_MAX, "…"))
        self.assertEqual([w["label"] for w in row["windows"][3:]], ["Extra 1", "Extra 2", "Extra 3", "Extra 4", "Extra 5"])
        self.assertTrue(all(w["resetsAt"] is None for w in row["windows"][3:]))
        self.record("claude", [], name=None, scope="account")
        row = self.row(self.read(), "claude")
        self.assertEqual((row["name"], row["scope"]), ("claude", "account"))
        self.record("claude", [], name="N" * 90)
        self.assertEqual(len(self.row(self.read(), "claude")["name"]), consts.USAGE_LABEL_MAX)


# --- computed windows and bindings ---------------------------------------------------------------

class WindowTests(UsageCase):
    def test_computed_zen_free_utc_midnight_across_dst(self):
        os.environ["TZ"] = "America/Chicago"
        time.tzset()
        cases = ((utc(2026, 3, 8, 7, 30), utc(2026, 3, 9)), (utc(2026, 11, 1, 6, 59, 59), utc(2026, 11, 2)),
                 (utc(2026, 11, 1), utc(2026, 11, 2)), (utc(2026, 10, 31, 23, 59, 59), utc(2026, 11, 1)),
                 (utc(2026, 9, 15, 12), utc(2026, 9, 16)))
        for now, expected in cases:
            self.assertEqual(windows.next_utc_midnight(now), expected, now)
            row = usage.provider(usage.read_usage(now * 1000), "zen-free")
            self.assertEqual(row["windows"][0]["resetsAt"], expected)
        self.assertEqual(list(row), PROVIDER_KEYS)
        self.assertEqual(list(row["windows"][0]), WINDOW_KEYS)
        self.assertEqual((row["name"], row["source"], row["harnesses"], row["headlineKey"], row["readable"]),
                         ("OpenCode Zen free", "computed", ["opencode"], "zen-free:daily", True))
        window = row["windows"][0]
        self.assertEqual((window["percent"], window["kind"], window["bindable"], window["source"], window["label"]),
                         (None, "daily", True, "computed", ""))

    def test_computed_gemini_daily_la_midnight(self):
        cases = ((utc(2026, 9, 15, 12), utc(2026, 9, 16, 7)), (utc(2026, 9, 16, 6, 59, 59), utc(2026, 9, 16, 7)),
                 (utc(2026, 9, 16, 7), utc(2026, 9, 17, 7)), (utc(2026, 11, 1, 8, 30), utc(2026, 11, 2, 8)),
                 (utc(2026, 11, 1, 6, 30), utc(2026, 11, 1, 7)), (utc(2026, 3, 8, 9, 30), utc(2026, 3, 9, 7)))
        for now, expected in cases:
            self.assertEqual(windows.next_la_midnight(now), expected, now)
        row = usage.provider(usage.read_usage(utc(2026, 9, 15, 12) * 1000), "gemini-daily")
        self.assertEqual((row["name"], row["harnesses"], row["windows"][0]["resetsAt"]),
                         ("Gemini CLI daily", ["gemini"], utc(2026, 9, 16, 7)))
        self.assertEqual(usage.gemini_daily_reset(None, utc(2026, 9, 15, 12)), utc(2026, 9, 16, 7))

        def no_zone(zone, now):
            raise LookupError(zone)

        self.patch(timeutil, "next_midnight", no_zone)
        for now in (utc(2026, 9, 15, 12), utc(2026, 12, 1, 12), utc(2026, 12, 1, 7, 30)):
            fallback = windows.next_la_midnight(now)
            self.assertGreater(fallback, now)
            self.assertLessEqual(fallback - now, 86400)
            self.assertEqual(time.gmtime(fallback).tm_hour, 7)

    def test_binding_table_limit_source_for(self):
        live = {"providers": [
            {"id": "cursor", "readable": True, "windows": [{"kind": "billing_total"}]},
            {"id": "opencode-go", "readable": True, "windows": [{"kind": "weekly"}]},
            {"id": "gemini", "readable": True, "windows": [{"kind": "daily"}]}]}
        empty = {"providers": [{"id": "cursor", "readable": False, "windows": []},
                               {"id": "gemini", "readable": True, "windows": [{"kind": "session"}]}]}
        table = [
            ({"harness": "claude"}, None, None, "claude"),
            ({"harness": "claude", "model": "opus"}, None, empty, "claude"),
            ({"harness": "codex"}, None, empty, "codex"),
            ({"harness": "pi", "provider": "openai-codex"}, None, empty, "codex"),
            ({"harness": "pi", "provider": "anthropic"}, None, None, None),
            ({"harness": "pi", "provider": None}, None, live, None),
            ({"harness": "cursor"}, None, None, "cursor"),
            ({"harness": "cursor"}, None, empty, None),
            ({"harness": "cursor", "model": "gpt-5"}, None, live, "cursor"),
            ({"harness": "opencode", "model": "opencode-go/kimi-k2"}, "go", None, "opencode-go"),
            ({"harness": "opencode", "model": "opencode-go/kimi-k2"}, "go", empty, None),
            ({"harness": "opencode", "model": "opencode-go/kimi-k2"}, "go", live, "opencode-go"),
            ({"harness": "opencode", "model": "opencode/big-pickle"}, "zen_free", empty, "zen-free"),
            ({"harness": "opencode", "model": None}, "zen_free", None, "zen-free"),
            ({"harness": "opencode", "model": "opencode/gpt-5"}, "zen_paid", None, None),
            ({"harness": "opencode", "model": "anthropic/claude-sonnet-5"}, "anthropic", live, None),
            ({"harness": "opencode", "model": None}, None, None, None),
            ({"harness": "gemini"}, None, None, "gemini-daily"),
            ({"harness": "gemini"}, None, empty, "gemini-daily"),
            ({"harness": "gemini"}, None, live, "gemini"),
            ({"harness": "vim"}, None, None, None),
            ("not a job", None, None, None),
        ]
        for job, billing, usage_result, expected in table:
            self.assertEqual(windows.limit_source_for(job, billing, usage_result), expected, (job, billing))
        self.assertEqual({sid: windows.bindings(sid) for sid in ("claude", "codex", "cursor", "opencode-go", "gemini",
                                                                   "gemini-daily", "zen-free", "fireworks",
                                                                   "opencode", "pi", "zai")},
                         {"claude": ("claude",), "codex": ("codex",), "cursor": ("cursor",),
                          "opencode-go": ("opencode",), "gemini": ("gemini",), "gemini-daily": ("gemini",),
                          "zen-free": ("opencode",), "fireworks": (), "opencode": (), "pi": (), "zai": ()})
        for stem in ("fireworks", "zai"):
            self.record(stem, [{"label": "Session (5-hour)", "percent": 0.5, "resetsAt": iso(self.now + HOUR)}])
        result = self.read()
        self.assertEqual([self.row(result, stem)["harnesses"] for stem in ("fireworks", "zai")], [[], []])

    def test_cursor_pool_for_model(self):
        own, other = windows.CURSOR_POOL_OWN, windows.CURSOR_POOL_OTHER
        self.assertEqual((own, other), ("Cursor models", "Other models"))
        for model, expected in (("auto", own), ("Auto", own), ("composer-2", own), ("grok-4-code", own),
                                ("xai/Grok-5", own), ("claude-4.5-sonnet", other), ("gpt-5.6[reasoning=high]", other),
                                ("autocomplete-x", other), (None, None), ("", None), (5, None)):
            self.assertEqual(windows.cursor_pool_for_model(model), expected, model)


# --- limits history ------------------------------------------------------------------------------

class HistoryTests(UsageCase):
    def entries(self, sd):
        return limits_history.load(sd)

    def limits_history_same_reset_two_ways(self, sd):
        """One reset written with and without fractional seconds is one entry and one marker."""
        base = (self.now // HOUR + 5) * HOUR
        whole = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(base))
        fraction = whole.replace("+00:00", ".162149+00:00")
        self.record("claude", [{"label": "Session (5-hour)", "percent": 0.4, "resetsAt": fraction}],
                    updatedAt=iso(self.now - 60))
        first = self.read()
        claude = [p for p in first["providers"] if p["id"] == "claude"]
        self.assertEqual([w["resetsAt"] for w in claude[0]["windows"]], [base])
        sd.write_atomic(limits_history.HISTORY_FILE, json.dumps({"schemaVersion": 1, "entries": [
            {"source": "claude", "kind": "session", "shortLabel": "5-hour", "resetsAt": base + 1,
             "observedAt": self.now - 900, "origin": "record"},
            {"source": "claude", "kind": "session", "shortLabel": "5-hour", "resetsAt": base,
             "observedAt": self.now - 600, "origin": "record"}]}).encode())
        def claude_resets():
            return sorted(e["resetsAt"] for e in self.entries(sd) if e["source"] == "claude" and e["kind"] == "session")

        # The poll adds nothing for Claude; its write (a codex record is new) folds the planted pair
        # into its earliest observation.
        limits_history.record_from_usage(sd, first, self.now)
        self.assertEqual(claude_resets(), [base + 1])
        self.record("claude", [{"label": "Session (5-hour)", "percent": 0.4, "resetsAt": whole}],
                    updatedAt=iso(self.now - 30))
        second = self.read()
        limits_history.record_from_usage(sd, second, self.now + 900)
        self.assertEqual(claude_resets(), [base + 1])
        sd.write_atomic(limits_history.HISTORY_FILE, json.dumps({"schemaVersion": 1, "entries": [
            e for e in self.entries(sd) if e["source"] == "claude"]}).encode())
        self.assertTrue(limits_history.append_observed(sd, "claude", "weekly", "Weekly", base, self.now))
        # The next write folds the pair already on disk into its earliest observation.
        self.assertEqual([(e["kind"], e["resetsAt"], e["observedAt"]) for e in self.entries(sd)],
                         [("session", base + 1, self.now - 900), ("weekly", base, self.now)])
        timeline = limits_history.build_timeline(sd, base - DAY, base + DAY, self.now, second)
        self.assertEqual([(r["kind"], r["at"]) for r in timeline["resets"] if r["source"] == "claude"],
                         [("weekly", base), ("session", base + 1)])
        self.assertFalse(limits_history.append_observed(sd, "claude", "session", "5-hour", base + 45, self.now))
        self.assertTrue(limits_history.append_observed(sd, "claude", "session", "5-hour", base + 3600, self.now))
        sd.write_atomic(limits_history.HISTORY_FILE, json.dumps({"schemaVersion": 1, "entries": []}).encode())

    def test_limits_history_append_dedup_prune_cap(self):
        sd = self.state()
        self.record("claude", [{"label": "Session (5-hour)", "percent": 0.1, "resetsAt": iso(self.now + 2 * HOUR)},
                               {"label": "Weekly (7-day)", "percent": 0.3, "resetsAt": iso(self.now + 4 * DAY)}])
        result = self.read()
        self.assertEqual(limits_history.record_from_usage(sd, result, self.now), 2)
        self.assertEqual(limits_history.record_from_usage(sd, result, self.now + 60), 0)
        path = os.path.join(sd.path, limits_history.HISTORY_FILE)
        self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
        self.assertEqual(self.entries(sd), [
            {"source": "claude", "kind": "session", "shortLabel": "5-hour", "resetsAt": self.now + 2 * HOUR,
             "observedAt": self.now, "origin": "record"},
            {"source": "claude", "kind": "weekly", "shortLabel": "Weekly", "resetsAt": self.now + 4 * DAY,
             "observedAt": self.now, "origin": "record"}])
        # The verb appends on every poll.
        self.record("codex", [{"label": "5h window", "percent": 0.5, "resetsAt": iso(self.now + 3 * HOUR)}],
                    updatedAt=iso(self.now - 60))
        answer = cli_scan.cmd_usage([], None)
        self.assertEqual(list(answer)[:3], ["ok", "nowMs", "providers"])
        self.assertIn("codex", {entry["source"] for entry in self.entries(sd)})
        with self.assertRaises(ApError):
            cli_scan.cmd_usage(["x"], None)

        # Resets older than 14 days are dropped on the next write.
        self.limits_history_same_reset_two_ways(sd)
        planted = [{"source": "claude", "kind": "session", "shortLabel": "5-hour", "resetsAt": self.now - 15 * DAY,
                    "observedAt": self.now - 15 * DAY - HOUR, "origin": "record"},
                   {"source": "claude", "kind": "session", "shortLabel": "5-hour", "resetsAt": self.now - 13 * DAY,
                    "observedAt": self.now - 13 * DAY - HOUR, "origin": "record"},
                   {"source": "bad id!", "kind": "session", "shortLabel": "x", "resetsAt": self.now,
                    "observedAt": self.now, "origin": "record"}]
        sd.write_atomic(limits_history.HISTORY_FILE, json.dumps({"schemaVersion": 1, "entries": planted}).encode())
        self.assertEqual(len(self.entries(sd)), 2)
        self.assertTrue(limits_history.append_observed(sd, "codex", "session", "5-hour", self.now + HOUR, self.now))
        self.assertEqual(sorted(e["resetsAt"] - self.now for e in self.entries(sd)), [-13 * DAY, HOUR])

        # Over the cap, the oldest observations go first and the file stays within LIMITS_HISTORY_MAX.
        planted = [{"source": "p%03d" % i, "kind": "other", "shortLabel": "L" * 24, "resetsAt": self.now + i,
                    "observedAt": self.now - 10000 + i, "origin": "record"} for i in range(450)]
        data = json.dumps({"schemaVersion": 1, "entries": planted}, separators=(",", ":")).encode()
        self.assertLess(len(data), windows.LIMITS_HISTORY_MAX)
        sd.write_atomic(limits_history.HISTORY_FILE, data)
        synthetic = {"providers": [{"id": "q%03d" % i, "source": "record", "readable": True,
                                    "windows": [{"source": "record", "sliding": False, "kind": "weekly",
                                                 "shortLabel": "W" * 24, "resetsAt": self.now + DAY}]}
                                   for i in range(200)]}
        self.assertEqual(limits_history.record_from_usage(sd, synthetic, self.now), 200)
        self.assertLessEqual(os.stat(path).st_size, windows.LIMITS_HISTORY_MAX)
        kept = self.entries(sd)
        kept_planted = sorted(e["observedAt"] for e in kept if e["source"].startswith("p"))
        self.assertEqual(len([e for e in kept if e["source"].startswith("q")]), 200)
        self.assertLess(len(kept_planted), 450)
        self.assertEqual(kept_planted, [self.now - 10000 + i for i in range(450 - len(kept_planted), 450)])

    def test_limits_history_never_sliding_or_computed(self):
        sd = self.state()
        updated = self.now - 30
        self.record("codex", [{"label": "5h window", "percent": 0, "resetsAt": iso(updated + 5 * HOUR)},
                              {"label": "Weekly (7-day)", "percent": 0.2, "resetsAt": iso(self.now + 2 * DAY)}],
                    updatedAt=iso(updated))
        self.record("opencode", [])
        self.write("%s/broken.json" % USAGE_REL, "{nope")
        result = self.read()
        self.assertEqual(limits_history.record_from_usage(sd, result, self.now), 1)
        self.assertEqual([(e["source"], e["kind"]) for e in self.entries(sd)], [("codex", "weekly")])
        synthetic = {"providers": [
            {"id": "claude", "source": "record", "readable": True, "windows": [
                {"source": "record", "sliding": True, "kind": "session", "shortLabel": "5-hour", "resetsAt": self.now + 60},
                {"source": "record", "kind": "session", "shortLabel": "5-hour", "resetsAt": self.now + 120},
                {"source": "computed", "sliding": False, "kind": "daily", "shortLabel": "Daily", "resetsAt": self.now + 180},
                {"source": "record", "sliding": False, "kind": "bogus", "shortLabel": "x", "resetsAt": self.now + 240}]},
            {"id": "zen-free", "source": "computed", "readable": True, "windows": [
                {"source": "computed", "sliding": False, "kind": "daily", "shortLabel": "Daily", "resetsAt": self.now + 300}]}]}
        self.assertEqual(limits_history.record_from_usage(sd, synthetic, self.now), 0)
        self.read(sd=sd, record_history=True)
        self.assertFalse({e["source"] for e in self.entries(sd)} & {"gemini-daily", "zen-free"})

    def test_limits_history_observed_and_pending_reset(self):
        sd = self.state()
        first, second = self.now + 5 * HOUR, self.now + 29 * HOUR
        self.assertTrue(limits_history.append_observed(sd, "zen-free", "daily", "Daily", first, self.now))
        self.assertFalse(limits_history.append_observed(sd, "zen-free", "daily", "Daily", first, self.now + 5))
        self.assertTrue(limits_history.append_observed(sd, "zen-free", "daily", "Daily", second, self.now))
        self.assertEqual(limits_history.pending_reset(sd, "zen-free", self.now), first)
        self.assertEqual(limits_history.pending_reset(sd, "zen-free", first), second)
        self.assertIsNone(limits_history.pending_reset(sd, "zen-free", second))
        self.assertIsNone(limits_history.pending_reset(sd, "cursor", self.now))
        self.assertIsNone(limits_history.pending_reset(None, "zen-free", self.now))
        for args in (("Bad!", "daily", "Daily", first), ("zen-free", "hourly", "Daily", first),
                     ("zen-free", "daily", "", first), ("zen-free", "daily", "Daily", True),
                     ("zen-free", "daily", "Daily", 12), (None, "daily", "Daily", first)):
            self.assertFalse(limits_history.append_observed(sd, *args, self.now), args)
        self.assertTrue(limits_history.append_observed(sd, "cursor", "billing_total", "Cursor cycle",
                                                       self.now + 20 * DAY, self.now))
        self.assertEqual([e["origin"] for e in self.entries(sd)], ["observed", "observed", "observed"])
        synthetic = {"providers": [{"id": "codex", "source": "record", "readable": True, "windows": [
            {"source": "record", "sliding": False, "kind": "session", "shortLabel": "5-hour", "resetsAt": first}]}]}
        self.assertEqual(limits_history.record_from_usage(sd, synthetic, self.now), 1)
        self.assertFalse(limits_history.append_observed(sd, "codex", "session", "5-hour", first, self.now + 10))
        self.assertFalse(limits_history.append_observed(None, "codex", "session", "5-hour", first, self.now))

    def test_limits_history_write_failure_swallowed(self):
        sd = self.state()

        def refuse(name, data):
            raise ApError("state_refused")

        self.patch(sd, "write_atomic", refuse)
        self.assertFalse(limits_history.append_observed(sd, "codex", "weekly", "Weekly", self.now + DAY, self.now))
        self.record("claude", [{"label": "Session (5-hour)", "percent": 0.1, "resetsAt": iso(self.now + HOUR)}])
        self.record("cursor", cursor_triple(self.now + 20 * DAY))
        self.assertEqual(limits_history.record_from_usage(sd, self.read(), self.now), 0)
        result = self.read(sd=sd, record_history=True, with_relevance=True)
        self.assertIsNotNone(self.row(result, "claude"))
        self._patches = [p for p in self._patches if p[0] is not sd]
        del sd.write_atomic
        with sd.lock(1.0, name=limits_history.LOCK_NAME):
            started = time.monotonic()
            self.assertFalse(limits_history.append_observed(sd, "codex", "weekly", "Weekly", self.now + DAY, self.now))
            self.assertLess(time.monotonic() - started, 5)
        for junk in (b"{broken", b" " * (windows.LIMITS_HISTORY_MAX + 10), b'{"schemaVersion": 2, "entries": []}'):
            sd.write_atomic(limits_history.HISTORY_FILE, junk)
            self.assertEqual(self.entries(sd), [])
            self.assertTrue(limits_history.append_observed(sd, "codex", "weekly", "Weekly", self.now + DAY, self.now))
            self.assertEqual(len(self.entries(sd)), 1)


# --- timeline and verbs --------------------------------------------------------------------------

class TimelineTests(UsageCase):
    def test_timeline_day_runs_resets_computed_armed(self):
        sd = self.state()
        start, end = self.now - 6 * HOUR, self.now + 18 * HOUR
        limits_history.append_observed(sd, "zen-free", "daily", "Daily", self.now - 2 * HOUR, self.now - 3 * HOUR)
        history_usage = {"providers": [{"id": "claude", "source": "record", "readable": True, "windows": [
            {"source": "record", "sliding": False, "kind": "session", "shortLabel": "5-hour",
             "resetsAt": self.now - HOUR},
            {"source": "record", "sliding": False, "kind": "session", "shortLabel": "5-hour",
             "resetsAt": self.now - 10 * HOUR}]}]}
        self.assertEqual(limits_history.record_from_usage(sd, history_usage, self.now - 5 * HOUR), 2)
        self.record("claude", [{"label": "Session (5-hour)", "percent": 0.9, "resetsAt": iso(self.now - HOUR)}],
                    name="Claude")
        self.record("codex", [{"label": "5h window", "percent": 0.5, "resetsAt": iso(self.now + 3 * HOUR)}],
                    name="Codex")
        live = self.read()
        self.stand_in_jobs([
            {"id": J1, "label": "Gemini in api", "harness": "gemini", "model": None,
             "state": {"status": "armed", "fireAt": self.now + 4 * HOUR, "lastRun": None}},
            {"id": J2, "label": "Pi in api", "harness": "pi", "provider": "openai-codex", "model": "gpt-5.6",
             "state": {"status": "running", "fireAt": self.now - 600,
                       "lastRun": {"runId": J2 + "-g3", "startedAt": self.now - 300, "endedAt": None}}},
            {"id": J3, "label": "Later", "harness": "claude", "model": None,
             "state": {"status": "armed", "fireAt": self.now + 30 * HOUR, "lastRun": None}}])
        calls = []

        def run_records(sd_arg, *, since, until, cap=500):
            calls.append((since, until, cap))
            return [
                {"runId": J4 + "-g1", "jobId": J4, "harness": "opencode", "startedAt": self.now - 4 * HOUR,
                 "endedAt": self.now - 3 * HOUR, "outcome": "limit", "limitSource": "zen-free", "cwd": "/w/site",
                 "prompt": "SECRET PROMPT TEXT"},
                {"runId": J1 + "-g1", "jobId": J1, "harness": "gemini", "startedAt": self.now - 5 * HOUR,
                 "endedAt": self.now - 5 * HOUR + 60, "outcome": "done", "limitSource": None},
                {"runId": "bad", "jobId": J5, "harness": "codex", "startedAt": self.now - HOUR, "endedAt": self.now},
                {"runId": J5 + "-g2", "jobId": J5, "harness": "codex", "startedAt": self.now - 30 * HOUR,
                 "endedAt": self.now - 29 * HOUR, "outcome": "done", "limitSource": "codex"},
                {"runId": J5 + "-g3", "jobId": J5, "harness": "codex", "startedAt": self.now - 2 * HOUR,
                 "endedAt": self.now - HOUR, "outcome": "surprise", "limitSource": "Bad Source"}]

        self.patch(jobs, "list_run_records", run_records)
        timeline = limits_history.build_timeline(sd, start, end, self.now, live)
        self.assertEqual(calls, [(start, end, 500)])
        self.assertEqual(list(timeline), ["nowMs", "from", "to", "resets", "runs", "armed", "truncated"])
        self.assertEqual((timeline["from"], timeline["to"], timeline["truncated"]), (start, end, False))
        self.assertEqual([(r["runId"], r["status"], r["outcome"], r["limitSource"], r["label"]) for r in timeline["runs"]],
                         [(J2 + "-g3", "running", None, "codex", "Pi in api"),
                          (J5 + "-g3", None, None, None, "Codex job"),
                          (J4 + "-g1", None, "limit", "zen-free", "OpenCode in site"),
                          (J1 + "-g1", None, "done", None, "Gemini in api")])
        self.assertEqual(timeline["runs"][0]["endedAt"], None)
        self.assertEqual(timeline["armed"], [{"jobId": J1, "harness": "gemini", "fireAt": self.now + 4 * HOUR,
                                              "limitSource": "gemini-daily"}])
        resets = [(r["source"], r["kind"], r["at"], r["origin"]) for r in timeline["resets"]]
        zen_midnight = windows.next_utc_midnight(start - 1)
        la_midnight = windows.next_la_midnight(start - 1)
        expected = [("claude", "session", self.now - HOUR, "record"),
                    ("zen-free", "daily", self.now - 2 * HOUR, "observed"),
                    ("codex", "session", self.now + 3 * HOUR, "current")]
        if zen_midnight <= end:
            expected.append(("zen-free", "daily", zen_midnight, "computed"))
        if la_midnight <= end:
            expected.append(("gemini-daily", "daily", la_midnight, "computed"))
        self.assertEqual(sorted(resets, key=lambda r: (r[2], r[0])), sorted(expected, key=lambda r: (r[2], r[0])))
        by_source = {r["source"]: r for r in timeline["resets"]}
        self.assertEqual((by_source["claude"]["name"], by_source["claude"]["harnesses"],
                          by_source["claude"]["shortLabel"]), ("Claude", ["claude"], "5-hour"))
        self.assertEqual((by_source["zen-free"]["name"], by_source["zen-free"]["harnesses"]),
                         ("OpenCode Zen free", ["opencode"]))
        self.assertEqual(list(timeline["resets"][0]), ["source", "name", "kind", "shortLabel", "at", "origin",
                                                       "harnesses"])
        dumped = json.dumps(timeline)
        self.assertNotIn("SECRET PROMPT", dumped)
        self.assertNotIn('"prompt"', dumped)
        # Without a state folder only the live records answer.
        bare = limits_history.build_timeline(None, start, end, self.now, live)
        self.assertEqual((bare["runs"], bare["armed"]), ([], []))
        self.assertEqual({(r["source"], r["origin"]) for r in bare["resets"]},
                         {("claude", "current"), ("codex", "current")})

    def test_timeline_cursor_cycle_single_marker(self):
        sd = self.state()
        cycle = self.now + 5 * HOUR
        self.record("cursor", cursor_triple(cycle), name="Cursor")
        live = self.read()
        start, end = self.now - HOUR, self.now + 20 * HOUR
        timeline = limits_history.build_timeline(sd, start, end, self.now, live)
        self.assertEqual(timeline["resets"], [{"source": "cursor", "name": "Cursor", "kind": "billing_total",
                                               "shortLabel": "Cursor cycle", "at": cycle, "origin": "current",
                                               "harnesses": ["cursor"]}])
        # Dedupe is by (source, kind, resetsAt): the two pools of one cycle are a single billing_pool entry.
        self.assertEqual(limits_history.record_from_usage(sd, live, self.now), 2)
        limits_history.append_observed(sd, "cursor", "billing_total", "Cursor cycle", cycle, self.now)
        timeline = limits_history.build_timeline(sd, start, end, self.now, live)
        self.assertEqual([(r["shortLabel"], r["origin"]) for r in timeline["resets"]], [("Cursor cycle", "record")])
        timeline = limits_history.build_timeline(sd, start, end, self.now, {"providers": []})
        self.assertEqual([(r["name"], r["kind"], r["origin"]) for r in timeline["resets"]],
                         [("Cursor", "billing_total", "record")])
        self.record("cursor", cursor_triple(self.now + 25 * DAY), name="Cursor")
        self.assertEqual(limits_history.build_timeline(None, start, end, self.now, self.read())["resets"], [])

    def test_timeline_argv_ranges(self):
        fixed_ms = self.now_ms + 123
        self.patch(timeutil, "now_ms", lambda: fixed_ms)
        start, end = self.now - 6 * HOUR, self.now + 18 * HOUR
        good = cli_scan.cmd_timeline(["--from", str(start), "--to", str(end)], None)
        self.assertEqual(list(good), ["ok", "nowMs", "from", "to", "resets", "runs", "armed", "truncated"])
        self.assertEqual((good["ok"], good["nowMs"], good["from"], good["to"]), (True, fixed_ms, start, end))
        self.assertIsNone(fsio.open_state(create=False), "the timeline verb never creates the state folder")
        edges = ((self.now, self.now + windows.TIMELINE_RANGE_MAX_S),
                 (self.now - windows.TIMELINE_BACK_S, self.now - windows.TIMELINE_BACK_S + HOUR),
                 (self.now + windows.TIMELINE_AHEAD_S - HOUR, self.now + windows.TIMELINE_AHEAD_S))
        for low, high in edges:
            self.assertTrue(cli_scan.cmd_timeline(["--from", str(low), "--to", str(high)], None)["ok"])
        bad = ([], ["--from", str(start)], ["--to", str(end), "--from", str(start)],
               ["--from", "x", "--to", "y"], ["--from", str(start), "--to", str(start)],
               ["--from", str(end), "--to", str(start)],
               ["--from", str(self.now), "--to", str(self.now + windows.TIMELINE_RANGE_MAX_S + 1)],
               ["--from", str(self.now - windows.TIMELINE_BACK_S - 1), "--to", str(self.now - windows.TIMELINE_BACK_S + HOUR)],
               ["--from", str(self.now + windows.TIMELINE_AHEAD_S - HOUR), "--to", str(self.now + windows.TIMELINE_AHEAD_S + 1)],
               ["--from", str(start), "--to", str(end), "x"], ["--from", "-5", "--to", str(end)],
               ["--from", "1" + str(start), "--to", str(end)], ["--from", " %d" % start, "--to", str(end)])
        for argv in bad:
            with self.assertRaises(ApError, msg=argv) as caught:
                cli_scan.cmd_timeline(argv, None)
            self.assertEqual(caught.exception.code, "bad_args", argv)

    def test_sessions_verb_cwd_passthrough(self):
        calls = []

        def stand_in(harness_id, now, **kwargs):
            calls.append((harness_id, kwargs))
            return {"harness": harness_id, "sessions": [], "counts": {}, "truncated": False, "errors": []}

        self.patch(sessions, "list_sessions", stand_in)
        good = (([], (None, {})), (["--harness", "gemini"], ("gemini", {})),
                (["--harness", "gemini", "--cwd", "/home/u/proj"], ("gemini", {"cwd": "/home/u/proj"})),
                (["--cwd", "/w"], (None, {"cwd": "/w"})),
                (["--cwd", "/w/My Projekt ż"], (None, {"cwd": "/w/My Projekt ż"})))
        for argv, expected in good:
            del calls[:]
            result = cli_scan.cmd_sessions(argv, None)
            self.assertEqual(list(result)[0], "ok")
            self.assertEqual(calls, [expected], argv)
        bad = (["--cwd"], ["--cwd", "rel/path"], ["--cwd", "/w", "--harness", "gemini"], ["--cwd", "/w", "--cwd", "/x"],
               ["--cwd", "/w\n"], ["--cwd", "/w\x00"], ["--cwd", "/w\x9b"], ["--cwd", "/" + "a" * consts.CWD_MAX_BYTES],
               ["--harness", "vim", "--cwd", "/w"], ["--harness", "gemini", "x"], ["--harness"], ["gemini"])
        del calls[:]
        for argv in bad:
            with self.assertRaises(ApError, msg=argv) as caught:
                cli_scan.cmd_sessions(argv, None)
            self.assertEqual(caught.exception.code, "bad_args", argv)
        self.assertEqual(calls, [])


class SettingsTests(UsageCase):
    def test_settings_v2_keys(self):
        self.assertEqual({key: settings.DEFAULTS[key] for key in ("defaultAllowPaid", "limitsShown", "lastSeenAt")},
                         {"defaultAllowPaid": False, "limitsShown": "auto", "lastSeenAt": None})
        good = ({"defaultAllowPaid": True}, {"limitsShown": "auto"}, {"limitsShown": []},
                {"limitsShown": ["claude", "codex", "zen-free", "gemini-daily"]},
                {"limitsShown": ["p%02d" % i for i in range(32)]}, {"lastSeenAt": None}, {"lastSeenAt": self.now})
        for body in good:
            self.assertEqual(settings.validate_partial(body), body)
        bad = (({"defaultAllowPaid": 1}, "defaultAllowPaid"), ({"defaultAllowPaid": "true"}, "defaultAllowPaid"),
               ({"limitsShown": "custom"}, "limitsShown"), ({"limitsShown": ["claude", "claude"]}, "limitsShown"),
               ({"limitsShown": ["Claude"]}, "limitsShown"), ({"limitsShown": ["p%02d" % i for i in range(33)]},
                                                             "limitsShown"),
               ({"limitsShown": ["ok", 5]}, "limitsShown"), ({"limitsShown": None}, "limitsShown"),
               ({"limitsShown": {"claude": True}}, "limitsShown"),
               ({"lastSeenAt": True}, "lastSeenAt"), ({"lastSeenAt": 12}, "lastSeenAt"),
               ({"lastSeenAt": consts.EPOCH_MAX + 1}, "lastSeenAt"), ({"lastSeenAt": 1.5e9}, "lastSeenAt"),
               ({"lastSeen": self.now}, None))
        for body, field in bad:
            with self.assertRaises(ApError, msg=body) as caught:
                settings.validate_partial(body)
            self.assertEqual((caught.exception.code, caught.exception.field), ("bad_input", field), body)
        sd = self.state()
        merged = settings.load(sd)
        merged.update(limitsShown=["codex", "claude"], defaultAllowPaid=True, lastSeenAt=self.now)
        settings.save(sd, merged)
        self.assertEqual(settings.load(sd), merged)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(sd.path, settings.SETTINGS_FILE)).st_mode), 0o600)
        sd.write_atomic(settings.SETTINGS_FILE, json.dumps({"limitsShown": ["X"], "lastSeenAt": "yesterday",
                                                            "defaultAllowPaid": True}).encode())
        loaded = settings.load(sd)
        self.assertEqual((loaded["limitsShown"], loaded["lastSeenAt"], loaded["defaultAllowPaid"]),
                         ("auto", None, True))


if __name__ == "__main__":
    unittest.main()
