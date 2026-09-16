"""Tests for the scan verbs: session scanners, usage records and agent probes.

Every test runs against a temporary HOME built in code (transcripts, SQLite stores, usage
records, stub CLIs). Nothing here reads the real HOME, starts a real agent CLI or touches
the user systemd manager. Run with PYTHONDONTWRITEBYTECODE=1.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

from autopilot import agents, cli_scan, consts, edition, fsio, harness, identity, jobs, sessions, sqlite_ro  # noqa: E402
from autopilot.errors import ApError  # noqa: E402

DAY = 86400
SIX = ("claude", "opencode", "codex", "gemini", "cursor", "pi")
ENV_KEYS = ("HOME", "AP4A_STATE_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME")
_MISSING = object()


def uid(n):
    return "%08x-0000-4000-8000-%012x" % (n, n)


def iso(epoch):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(int(epoch))) + (".%06d+00:00" % 250000)


STUB_CLI = r'''#!/usr/bin/python3 -I -S
import json, os, sys, time
args = [a for a in sys.argv[1:] if not a.endswith(".js")]
log = os.environ.get("FAKE_CLI_LOG")
if log:
    with open(log, "a") as handle:
        handle.write(json.dumps([os.environ.get("FAKE_CLI_NAME", "")] + args) + "\n")
mode = os.environ.get("FAKE_CLI_MODE", "")
if args == ["--version"]:
    print(os.environ.get("FAKE_CLI_VERSION", "1.2.3"))
    sys.exit(0)
if args == ["login", "status"]:
    if mode == "logged_in":
        sys.stderr.write("Logged in using ChatGPT\n")
        sys.exit(0)
    if mode == "logged_out":
        sys.stderr.write("Not logged in\n")
        sys.exit(1)
    if mode == "hang":
        time.sleep(30)
    print("status unavailable")
    sys.exit(0)
if args == ["auth", "status", "--json"]:
    print(json.dumps({"loggedIn": mode == "logged_in", "authMethod": "none"}))
    sys.exit(0 if mode == "logged_in" else 1)
if args == ["status", "--format", "json"]:
    if mode == "garbage":
        print("not json")
        sys.exit(0)
    state = {"logged_in": ("authenticated", True), "partial": ("partially-authenticated", True)}.get(
        mode, ("unauthenticated", False))
    print(json.dumps({"status": state[0], "isAuthenticated": state[1], "hasAccessToken": state[1],
                      "hasRefreshToken": False, "message": "Logged in",
                      "userInfo": {"email": "someone@example.invalid", "userId": 42, "firstName": "Some",
                                   "lastName": "One", "teamId": 7}}))
    sys.exit(0)
sys.exit(2)
'''


class ScanCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="h3-scan-")
        self.home = os.path.join(self.tmp, "home")
        os.makedirs(self.home, mode=0o700)
        self._saved_env = {key: os.environ.get(key) for key in ENV_KEYS}
        os.environ["HOME"] = self.home
        for key in ENV_KEYS[1:]:
            os.environ.pop(key, None)
        self._patches = []
        self.now = int(time.time())
        if tuple(consts.HARNESSES) != SIX:
            self.patch(consts, "HARNESSES", SIX)

    def tearDown(self):
        for obj, name, value in reversed(self._patches):
            if value is _MISSING:
                delattr(obj, name)
            else:
                setattr(obj, name, value)
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.tmp, ignore_errors=True)

    def patch(self, obj, name, value):
        self._patches.append((obj, name, getattr(obj, name, _MISSING)))
        setattr(obj, name, value)

    # --- fixture writers ---------------------------------------------------------

    def write(self, rel, data, mode=0o600, mtime=None):
        path = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(data if isinstance(data, bytes) else data.encode("utf-8"))
        os.chmod(path, mode)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def transcript(self, project, session_id, records, age_s=60, raw_lines=None):
        lines = [json.dumps(record) for record in records]
        if raw_lines:
            lines = raw_lines + lines
        return self.write(".claude/projects/%s/%s.jsonl" % (project, session_id),
                          "\n".join(lines) + "\n", mtime=self.now - age_s)

    @staticmethod
    def claude_head(cwd, text="Fix the login bug", extra=()):
        return ([{"type": "mode", "mode": "normal"},
                 {"type": "permission-mode", "permissionMode": "default"},
                 {"type": "bridge-session"},
                 {"type": "file-history-snapshot", "messageId": "m1"}]
                + list(extra)
                + [{"type": "user", "cwd": cwd, "isSidechain": False,
                    "message": {"role": "user", "content": text}},
                   {"type": "assistant", "cwd": cwd, "message": {"role": "assistant", "content": []}}])

    def opencode_db(self, rows, rel=".local/share/opencode/opencode.db"):
        path = os.path.join(self.home, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE session (id TEXT PRIMARY KEY, project_id TEXT, parent_id TEXT,"
                           " slug TEXT, directory TEXT, title TEXT, version TEXT, time_created INTEGER,"
                           " time_updated INTEGER, time_archived INTEGER, summary_diffs TEXT)")
        connection.execute("CREATE INDEX session_parent_idx ON session(parent_id)")
        connection.executemany("INSERT INTO session (id, parent_id, directory, title, time_updated,"
                               " time_archived) VALUES (?, ?, ?, ?, ?, ?)", rows)
        connection.commit()
        connection.close()
        return path

    def codex_db(self, threads, edges=()):
        path = os.path.join(self.home, ".codex", "state_5.sqlite")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, rollout_path TEXT, created_at INTEGER,"
                           " updated_at INTEGER, cwd TEXT, title TEXT, archived INTEGER,"
                           " first_user_message TEXT, updated_at_ms INTEGER, name TEXT, preview TEXT)")
        connection.execute("CREATE TABLE thread_spawn_edges (parent_thread_id TEXT, child_thread_id TEXT"
                           " PRIMARY KEY, status TEXT)")
        connection.executemany("INSERT INTO threads (id, cwd, title, archived, first_user_message,"
                               " updated_at_ms, name, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)", threads)
        connection.executemany("INSERT INTO thread_spawn_edges VALUES (?, ?, 'open')", edges)
        connection.commit()
        connection.close()
        return path

    def rollout(self, session_id, cwd, text, age_s=60, source="cli"):
        records = [{"timestamp": iso(self.now), "type": "session_meta",
                    "payload": {"id": session_id, "cwd": cwd, "source": source,
                                "base_instructions": {"text": "x" * 80000}}},
                   {"type": "response_item", "payload": {"type": "message", "role": "user",
                                                         "content": [{"type": "input_text",
                                                                      "text": "<environment_context>"}]}},
                   {"type": "event_msg", "payload": {"type": "user_message", "message": text}}]
        day = time.strftime("%Y/%m/%d", time.gmtime(self.now - age_s))
        return self.write(".codex/sessions/%s/rollout-2026-09-13T10-00-00-%s.jsonl" % (day, session_id),
                          "\n".join(json.dumps(r) for r in records) + "\n", mtime=self.now - age_s)

    def gemini_chat(self, short, session_id, messages, age_s=60, name_id8=None, extra=None, jsonl=False):
        document = {"sessionId": session_id, "projectHash": "ab" * 16, "startTime": iso(self.now),
                    "lastUpdated": iso(self.now), "kind": "main", "messages": messages}
        document.update(extra or {})
        suffix = name_id8 or session_id[:8]
        if jsonl:
            meta = dict(document)
            meta.pop("messages")
            body = "\n".join([json.dumps(meta)] + [json.dumps(m) for m in messages]) + "\n"
            name = "session-2026-09-02T14-53-%s.jsonl" % suffix
        else:
            body = json.dumps(document)
            name = "session-2026-09-02T14-53-%s.json" % suffix
        return self.write(".gemini/tmp/%s/chats/%s" % (short, name), body, mtime=self.now - age_s)

    def mkdir(self, rel):
        path = os.path.join(self.home, rel)
        os.makedirs(path, exist_ok=True)
        return path

    def rows_for(self, result, name):
        return [row for row in result["sessions"] if row["harness"] == name]


# --- Claude Code ------------------------------------------------------------------

class ClaudeTests(ScanCase):
    def test_claude_cwd_from_line_5(self):
        sid = uid(1)
        self.transcript("-home-u-proj", sid, self.claude_head("/home/u/proj")
                        + [{"type": "user", "cwd": "/elsewhere", "message": {"content": "later"}}])
        result = sessions.list_sessions("claude", self.now)
        rows = self.rows_for(result, "claude")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["cwd"], "/home/u/proj")
        self.assertEqual(rows[0]["id"], sid)
        self.assertEqual(rows[0]["title"], "Fix the login bug")
        self.assertIsNone(rows[0]["messages"])
        self.assertTrue(rows[0]["canFork"])
        self.assertEqual(sorted(rows[0]), ["canFork", "cwd", "harness", "id", "messages", "path", "title",
                                           "updatedAtMs"])
        self.assertEqual(sorted(result["counts"]), ["claude"])
        self.assertEqual(result["errors"], {"claude": None})
        self.assertEqual(result["truncated"], {"claude": False})
        self.assertEqual((result["limitDays"], result["perHarnessCap"]), (90, 50))

    def test_claude_title_precedence(self):
        custom = [{"type": "ai-title", "aiTitle": "AI title", "sessionId": uid(1)},
                  {"type": "custom-title", "customTitle": "My\x1b[31m custom" + chr(0x202E) + " title", "sessionId": uid(1)}]
        self.transcript("p", uid(1), self.claude_head("/w/a", extra=custom), age_s=10)
        self.transcript("p", uid(2), self.claude_head("/w/b", extra=[{"type": "ai-title", "aiTitle": "AI title"}]),
                        age_s=20)
        caveat = [{"type": "user", "isMeta": True, "message": {"content": "Caveat: generated"}},
                  {"type": "user", "message": {"content": "<command-name>/clear</command-name>"}}]
        self.transcript("p", uid(3), self.claude_head("/w/c", text=[{"type": "text", "text": "Part one"},
                                                                     {"type": "text", "text": " two"}],
                                                      extra=caveat), age_s=30)
        self.transcript("p", uid(4), self.claude_head("/w/project-d/", text="<local-command-stdout>"), age_s=40)
        self.transcript("p", uid(5), self.claude_head("/w/e", text="word " * 400), age_s=50)
        rows = {row["id"]: row for row in sessions.list_sessions(None, self.now)["sessions"]}
        self.assertEqual(rows[uid(1)]["title"], "My [31m custom title")
        self.assertEqual(rows[uid(2)]["title"], "AI title")
        self.assertEqual(rows[uid(3)]["title"], "Part one two")
        self.assertEqual(rows[uid(4)]["title"], "project-d")
        self.assertEqual(len(rows[uid(5)]["title"]), consts.TITLE_MAX)
        self.assertTrue(rows[uid(5)]["title"].endswith(chr(0x2026)))

    def test_claude_head_caps(self):
        filler = [{"type": "file-history-snapshot", "n": n} for n in range(consts.CLAUDE_HEAD_RECORDS)]
        self.transcript("p", uid(1), filler + self.claude_head("/w/late"))
        huge = [json.dumps({"type": "mode", "pad": "x" * (consts.CLAUDE_HEAD_BYTES + 100)})]
        self.transcript("p", uid(2), self.claude_head("/w/huge"), raw_lines=huge)
        malformed = ["{not json", "[1, 2, 3]", "[" * 5000 + "]" * 5000, "\xff\xfe", "\"string\""]
        self.transcript("p", uid(3), self.claude_head("/w/ok"), raw_lines=malformed)
        self.transcript("p", uid(4), [{"type": "mode"}, {"type": "summary", "cwd": "/w/nomsg"}])
        self.transcript("p", uid(5), self.claude_head("relative/path"))
        self.write(".claude/projects/p/not-a-uuid.jsonl", json.dumps(self.claude_head("/w/x")[4]))
        sidechain = self.claude_head("/w/side")
        sidechain[4]["isSidechain"] = True
        self.transcript("p", uid(6), sidechain)
        result = sessions.list_sessions("claude", self.now)
        self.assertEqual([row["id"] for row in result["sessions"]], [uid(3)])
        self.assertEqual(result["sessions"][0]["cwd"], "/w/ok")
        self.assertIsNone(sessions.lookup_session("claude", uid(1)))
        self.assertIsNone(sessions.lookup_session("claude", uid(2)))
        self.assertEqual(sessions.lookup_session("claude", uid(3))["cwd"], "/w/ok")

    def test_claude_symlink_transcript_refused(self):
        outside = os.path.join(self.tmp, "outside.jsonl")
        with open(outside, "w") as handle:
            handle.write("\n".join(json.dumps(r) for r in self.claude_head("/w/linked")) + "\n")
        os.makedirs(os.path.join(self.home, ".claude/projects/p"))
        os.symlink(outside, os.path.join(self.home, ".claude/projects/p/%s.jsonl" % uid(1)))
        real_project = os.path.join(self.tmp, "real-project")
        os.makedirs(real_project)
        shutil.copy(outside, os.path.join(real_project, "%s.jsonl" % uid(2)))
        os.symlink(real_project, os.path.join(self.home, ".claude/projects/linked-dir"))
        self.transcript("q", uid(3), self.claude_head("/w/plain"))
        rows = sessions.list_sessions("claude", self.now)["sessions"]
        self.assertEqual([row["id"] for row in rows], [uid(3)])
        self.assertIsNone(sessions.lookup_session("claude", uid(1)))
        self.assertIsNone(sessions.lookup_session("claude", uid(2)))
        self.assertIsNone(sessions.claude_last_quota(uid(1)))
        self.assertIsNone(sessions.claude_last_user_sha256(uid(1)))

    def test_claude_lookup_prefers_newest_copy(self):
        sid = uid(21)
        limit = {"type": "assistant", "error": "rate_limit",
                 "quotaLimits": {"status": "rejected", "resetsAt": self.now + 60, "rateLimitType": "five_hour"}}
        self.transcript("a-old-project", sid, self.claude_head("/w/old", text="Old copy") + [limit], age_s=600)
        self.transcript("z-new-project", sid, self.claude_head("/w/new", text="New copy"), age_s=5)
        found = sessions.lookup_session("claude", sid)
        self.assertEqual((found["cwd"], found["title"]), ("/w/new", "New copy"))
        self.assertIsNone(sessions.claude_last_quota(sid))
        self.assertEqual(sessions.claude_last_user_sha256(sid), hashlib.sha256(b"New copy").hexdigest())
        rows = sessions.list_sessions("claude", self.now)["sessions"]
        self.assertEqual([(row["id"], row["cwd"]) for row in rows], [(sid, "/w/new")])

    def test_claude_last_quota_tail(self):
        sid = uid(7)
        old_limit = {"type": "assistant", "error": "rate_limit", "isApiErrorMessage": True,
                     "quotaLimits": {"status": "rejected", "resetsAt": self.now - 7 * DAY,
                                     "rateLimitType": "seven_day"}}
        padding = [{"type": "progress", "pad": "p" * 4000} for _ in range(80)]
        new_limit = {"type": "assistant", "error": "rate_limit", "apiErrorStatus": 429,
                     "quotaLimits": {"status": "rejected", "resetsAt": self.now + 3600,
                                     "rateLimitType": "five_hour", "isUsingOverage": False}}
        records = (self.claude_head("/w/q", text="Refactor the parser") + [old_limit] + padding
                   + [{"type": "user", "message": {"content": "Continue the refactor"}},
                      new_limit,
                      {"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}},
                      {"type": "user", "isMeta": True, "message": {"content": "meta"}},
                      {"type": "user", "isSidechain": True, "message": {"content": "side"}}])
        self.transcript("p", sid, records)
        self.assertGreater(os.path.getsize(os.path.join(self.home, ".claude/projects/p/%s.jsonl" % sid)),
                           consts.CLAUDE_TAIL_BYTES)
        self.assertEqual(sessions.claude_last_quota(sid),
                         {"status": "rejected", "resetsAt": self.now + 3600, "rateLimitType": "five_hour"})
        self.assertEqual(sessions.claude_last_user_sha256(sid),
                         hashlib.sha256("Continue the refactor".encode("utf-8")).hexdigest())

        only_old = uid(8)
        self.transcript("p", only_old, self.claude_head("/w/q") + [old_limit] + padding)
        self.assertIsNone(sessions.claude_last_quota(only_old))

        throttle = uid(9)
        self.transcript("p", throttle, self.claude_head("/w/q") + [
            {"type": "assistant", "error": "rate_limit", "message": {"content": "not your usage limit"}}])
        self.assertEqual(sessions.claude_last_quota(throttle),
                         {"status": "", "resetsAt": None, "rateLimitType": None})
        self.assertIsNone(sessions.claude_last_quota("../../etc/passwd"))
        self.assertIsNone(sessions.claude_last_quota(uid(99)))
        self.assertIsNone(sessions.claude_last_user_sha256(uid(99)))


# --- OpenCode ---------------------------------------------------------------------

class OpenCodeTests(ScanCase):
    def roots(self, count, start=0):
        base = self.now * 1000
        return [("ses_root%08d" % (start + n), None, "/w/oc/%d" % n, "Session %d" % n, base - n * 60000, None)
                for n in range(count)]

    def test_opencode_projection_and_limit_51(self):
        rows = self.roots(55)
        rows += [("ses_child%07d" % n, "ses_root00000000", "/w/child", "child", self.now * 1000, None)
                 for n in range(5)]
        rows += [("ses_archived01", None, "/w/arch", "archived", self.now * 1000 + 5, self.now * 1000)]
        rows += [("bad id", None, "/w/bad", "bad", self.now * 1000 + 10, None)]
        rows += [("ses_longtitle1", None, "/w/long", "T\x07" * 2000, self.now * 1000 + 20, None)]
        rows += [("ses_longdir001", None, "/" + "d" * 2000, "long dir", self.now * 1000 + 30, None)]
        self.opencode_db(rows)
        result = sessions.list_sessions("opencode", self.now)
        listed = result["sessions"]
        ids = [row["id"] for row in listed]
        self.assertEqual(result["counts"], {"opencode": len(listed)})
        self.assertTrue(result["truncated"]["opencode"])
        self.assertIsNone(result["errors"]["opencode"])
        self.assertLessEqual(len(listed), 50)
        self.assertFalse(any(i.startswith("ses_child") or i in ("ses_archived01", "bad id", "ses_longdir001")
                             for i in ids))
        self.assertEqual(ids[0], "ses_longtitle1")
        self.assertLessEqual(len(listed[0]["title"]), consts.TITLE_MAX)
        self.assertNotIn("\x07", listed[0]["title"])
        self.assertEqual(ids[1:4], ["ses_root00000000", "ses_root00000001", "ses_root00000002"])
        self.assertFalse(listed[0]["canFork"] is False)
        self.assertEqual(listed[1]["updatedAtMs"], self.now * 1000)

        os.unlink(os.path.join(self.home, ".local/share/opencode/opencode.db"))
        self.opencode_db(self.roots(50))
        result = sessions.list_sessions("opencode", self.now)
        self.assertEqual((result["counts"]["opencode"], result["truncated"]["opencode"]), (50, False))
        os.unlink(os.path.join(self.home, ".local/share/opencode/opencode.db"))
        self.opencode_db(self.roots(51))
        result = sessions.list_sessions("opencode", self.now)
        self.assertEqual((result["counts"]["opencode"], result["truncated"]["opencode"]), (50, True))

    def test_opencode_too_large(self):
        path = self.opencode_db(self.roots(20))
        self.patch(consts, "OPENCODE_DB_CEILING", 1024)
        result = sessions.list_sessions("opencode", self.now)
        self.assertEqual((result["counts"]["opencode"], result["errors"]["opencode"]), (0, "too_large"))
        self.assertFalse(result["truncated"]["opencode"])
        self._patches.pop()[0].OPENCODE_DB_CEILING = 8 * 1024 ** 3

        with open(path + "-wal", "wb") as handle:
            handle.truncate(consts.OPENCODE_DB_CEILING)
        result = sessions.list_sessions("opencode", self.now)
        self.assertEqual(result["errors"]["opencode"], "too_large")
        with self.assertRaises(sqlite_ro.TooLarge) as caught:
            sqlite_ro.open_ro(path, max_bytes=consts.OPENCODE_DB_CEILING, deadline_s=1.0)
        self.assertEqual(caught.exception.code, "state_refused")
        os.unlink(path + "-wal")

        sparse = os.path.join(self.home, "big.db")
        with open(sparse, "wb") as handle:
            handle.truncate(consts.OPENCODE_DB_CEILING + 1)
        with self.assertRaises(sqlite_ro.TooLarge) as caught:
            sqlite_ro.open_ro(sparse, max_bytes=consts.OPENCODE_DB_CEILING, deadline_s=1.0)
        self.assertEqual(caught.exception.code, "state_refused")
        self.assertEqual(sessions.list_sessions("opencode", self.now)["errors"]["opencode"], None)

    def test_opencode_deadline(self):
        path = self.opencode_db(self.roots(20000))
        self.patch(consts, "SQLITE_DEADLINE_S", 0.0)
        started = time.monotonic()
        result = sessions.list_sessions("opencode", self.now)
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(result["errors"]["opencode"], "timeout")
        self.assertTrue(result["truncated"]["opencode"])
        self.assertEqual(result["counts"]["opencode"], 0)

        connection = sqlite_ro.open_ro(path, max_bytes=consts.OPENCODE_DB_CEILING, deadline_s=0.2)
        try:
            with self.assertRaises(sqlite3.OperationalError) as caught:
                connection.execute("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c)"
                                   " SELECT count(*) FROM c").fetchall()
            self.assertTrue(sqlite_ro.interrupted(caught.exception))
        finally:
            connection.close()

    def test_sqlite_ro_refusals(self):
        path = self.opencode_db(self.roots(3))
        link = os.path.join(self.home, "link.db")
        os.symlink(path, link)
        with self.assertRaises(ApError) as caught:
            sqlite_ro.open_ro(link, max_bytes=consts.OPENCODE_DB_CEILING, deadline_s=1.0)
        self.assertEqual(caught.exception.code, "state_refused")
        with self.assertRaises(FileNotFoundError):
            sqlite_ro.open_ro(path + ".missing", max_bytes=consts.OPENCODE_DB_CEILING, deadline_s=1.0)
        os.symlink(os.path.join(self.tmp, "elsewhere-wal"), path + "-wal")
        with self.assertRaises(ApError) as caught:
            sqlite_ro.open_ro(path, max_bytes=consts.OPENCODE_DB_CEILING, deadline_s=1.0)
        self.assertEqual(caught.exception.code, "state_refused")
        self.assertEqual(sessions.list_sessions("opencode", self.now)["errors"]["opencode"], "unreadable")
        os.unlink(path + "-wal")
        connection = sqlite_ro.open_ro(path, max_bytes=consts.OPENCODE_DB_CEILING, deadline_s=1.0)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM session")
        finally:
            connection.close()
        self.assertEqual(sessions.list_sessions("opencode", self.now)["counts"]["opencode"], 3)


# --- Codex ------------------------------------------------------------------------

class CodexTests(ScanCase):
    def test_codex_threads_and_rollout_fallback(self):
        ms = self.now * 1000
        self.codex_db([
            (uid(1), "/w/cx/a", "", 0, "first message", ms - 1000, "Named thread", self.now),
            (uid(2), "/w/cx/b", "", 0, "Use the first message", ms - 2000, "", self.now),
            (uid(3), "/w/cx/child", "child", 0, "child", ms, "child", self.now),
            (uid(4), "/w/cx/arch", "archived", 1, "archived", ms, "archived", self.now),
            (uid(5), "/w/cx/old", "old", 0, "old", (self.now - 91 * DAY) * 1000, "old", self.now),
        ], edges=[(uid(1), uid(3))])
        result = sessions.list_sessions("codex", self.now)
        self.assertEqual([(r["id"], r["title"], r["cwd"]) for r in result["sessions"]],
                         [(uid(1), "Named thread", "/w/cx/a"), (uid(2), "Use the first message", "/w/cx/b")])
        self.assertEqual(result["errors"]["codex"], None)
        self.assertEqual(sessions.lookup_session("codex", uid(2))["cwd"], "/w/cx/b")

        os.unlink(os.path.join(self.home, ".codex", "state_5.sqlite"))
        self.codex_db([])
        self.rollout(uid(10), "/w/roll/a", "Rollout prompt", age_s=100)
        self.rollout(uid(11), "/w/roll/b", "Subagent", age_s=50, source={"subagent": {"thread_spawn": {}}})
        self.rollout(uid(12), "/w/roll/old", "Old", age_s=91 * DAY)
        result = sessions.list_sessions("codex", self.now)
        self.assertEqual([(r["id"], r["title"], r["cwd"]) for r in result["sessions"]],
                         [(uid(10), "Rollout prompt", "/w/roll/a")])
        os.unlink(os.path.join(self.home, ".codex", "state_5.sqlite"))
        self.assertEqual(sessions.list_sessions("codex", self.now)["counts"]["codex"], 1)
        found = sessions.lookup_session("codex", uid(10))
        self.assertEqual((found["cwd"], found["title"]), ("/w/roll/a", "Rollout prompt"))
        self.assertIsNone(sessions.lookup_session("codex", uid(11)))


# --- Gemini CLI -------------------------------------------------------------------

class GeminiTests(ScanCase):
    def test_gemini_project_root(self):
        root_a = os.path.join(self.tmp, "project-a")
        self.write(".gemini/tmp/project-a/.project_root", root_a)
        user = [{"id": "1", "type": "info", "content": "Welcome"},
                {"id": "2", "type": "user", "content": [{"text": "Plan the refactor"}]},
                {"id": "3", "type": "gemini", "content": "Sure"}]
        self.gemini_chat("project-a", uid(1), user, age_s=10)
        self.gemini_chat("project-a", uid(2), user, age_s=20, name_id8="deadbeef")
        self.gemini_chat("project-a", uid(3), user, age_s=30, extra={"kind": "subagent"})
        self.write(".gemini/tmp/project-a/chats/%s/child.json" % uid(1), json.dumps({"sessionId": uid(4)}))
        self.write(".gemini/projects.json", json.dumps({"projects": {"/w/from-projects": "project-b"}}))
        self.gemini_chat("project-b", uid(5), [{"id": "1", "type": "user", "content": "From jsonl"}],
                         age_s=40, jsonl=True)
        self.gemini_chat("orphan", uid(6), user, age_s=50)
        result = sessions.list_sessions("gemini", self.now)
        rows = [(r["id"], r["cwd"], r["title"], r["messages"], r["canFork"]) for r in result["sessions"]]
        self.assertEqual(rows, [(uid(1), root_a, "Plan the refactor", 3, False),
                                (uid(5), "/w/from-projects", "From jsonl", None, False)])
        found = sessions.lookup_session("gemini", uid(1))
        self.assertEqual((found["cwd"], found["title"]), (root_a, "Plan the refactor"))
        self.assertIsNone(sessions.lookup_session("gemini", uid(2)))
        self.assertIsNone(sessions.lookup_session("gemini", uid(6)))


# --- shared bounds ----------------------------------------------------------------

class BoundsTests(ScanCase):
    def test_sessions_age_cap_90_days(self):
        self.transcript("p", uid(1), self.claude_head("/w/new"), age_s=89 * DAY)
        self.transcript("p", uid(2), self.claude_head("/w/old"), age_s=91 * DAY)
        base = self.now * 1000
        self.opencode_db([("ses_recent0001", None, "/w/o", "recent", base - 89 * DAY * 1000, None),
                          ("ses_stale00001", None, "/w/o", "stale", base - 91 * DAY * 1000, None)])
        self.write(".gemini/tmp/g/.project_root", "/w/g")
        self.gemini_chat("g", uid(3), [], age_s=89 * DAY)
        self.gemini_chat("g", uid(4), [], age_s=91 * DAY)
        result = sessions.list_sessions(None, self.now)
        self.assertEqual(sorted(r["id"] for r in result["sessions"]), sorted([uid(1), "ses_recent0001", uid(3)]))
        self.assertEqual(sorted(result["counts"]), sorted(consts.HARNESSES))
        self.assertEqual(result["counts"], {"claude": 1, "opencode": 1, "codex": 0, "gemini": 1, "cursor": 0, "pi": 0})
        self.assertEqual(result["errors"], {name: None for name in consts.HARNESSES})
        self.assertEqual(result["limitDays"], 90)

    def test_sessions_output_cap(self):
        wide_dir = "/" + ("\"\\" * 511)
        self.assertEqual(len(wide_dir.encode("utf-8")), consts.CWD_MAX_BYTES - 1)
        emoji_title = "\U0001F600" * 400
        for n in range(55):
            self.transcript("p", uid(n), self.claude_head(wide_dir, text=emoji_title), age_s=n + 1)
        self.opencode_db([("ses_wide%08d" % n, None, wide_dir, emoji_title, self.now * 1000 - n, None)
                          for n in range(55)])
        self.codex_db([(uid(1000 + n), wide_dir, emoji_title, 0, "", self.now * 1000 - n, "", self.now)
                       for n in range(55)])
        self.write(".gemini/tmp/g/.project_root", wide_dir)
        for n in range(55):
            self.gemini_chat("g", uid(2000 + n), [{"id": "1", "type": "user", "content": emoji_title}],
                             age_s=n + 1)
        result = sessions.list_sessions(None, self.now)
        four = ("claude", "opencode", "codex", "gemini")
        self.assertEqual(result["counts"], dict({name: 50 for name in four}, cursor=0, pi=0))
        self.assertTrue(all(result["truncated"][name] for name in four))
        payload = json.dumps(dict({"ok": True}, **result), ensure_ascii=False,
                             separators=(",", ":")).encode("utf-8")
        self.assertLessEqual(len(payload), consts.OUTPUT_CAP["sessions"])
        self.assertTrue(all(len(row["title"]) <= consts.TITLE_MAX for row in result["sessions"]))

    def test_lookup_session_each_harness(self):
        self.transcript("p", uid(1), self.claude_head("/w/claude"))
        self.opencode_db([("ses_lookup0001", None, "/w/opencode", "OC", self.now * 1000, None),
                          ("ses_childof001", "ses_lookup0001", "/w/child", "child", self.now * 1000, None)])
        self.codex_db([(uid(2), "/w/codex", "Codex title", 0, "", self.now * 1000, "", self.now)])
        self.write(".gemini/tmp/g/.project_root", "/w/gemini")
        self.gemini_chat("g", uid(3), [{"id": "1", "type": "user", "content": "Gemini prompt"}])
        expected = {"claude": (uid(1), "/w/claude"), "opencode": ("ses_lookup0001", "/w/opencode"),
                    "codex": (uid(2), "/w/codex"), "gemini": (uid(3), "/w/gemini")}
        for name, (session_id, cwd) in expected.items():
            found = sessions.lookup_session(name, session_id)
            self.assertEqual(sorted(found), ["cwd", "harness", "id", "title", "updatedAtMs"], name)
            self.assertEqual((found["harness"], found["id"], found["cwd"]), (name, session_id, cwd))
            self.assertGreater(found["updatedAtMs"], (self.now - 3600) * 1000)
        self.assertIsNone(sessions.lookup_session("opencode", "ses_childof001"))
        self.transcript("p", uid(0xabc), self.claude_head("/w/upper"))
        self.assertEqual(sessions.lookup_session("claude", uid(0xabc))["cwd"], "/w/upper")
        for name, bad in (("claude", "../../x"), ("claude", uid(0xabc).upper()), ("opencode", "ses_bad!"),
                          ("codex", "ses_lookup0001"), ("gemini", uid(1) + "\n"), ("nope", uid(1)),
                          ("claude", None), ("claude", uid(42)), ("opencode", "ses_missing0001"),
                          ("codex", uid(43)), ("gemini", uid(44))):
            self.assertIsNone(sessions.lookup_session(name, bad), (name, bad))

    def test_scan_verbs_argv(self):
        for argv in (["--harness"], ["--harness", "vim"], ["claude"], ["--harness", "claude", "x"]):
            with self.assertRaises(ApError) as caught:
                cli_scan.cmd_sessions(argv, None)
            self.assertEqual(caught.exception.code, "bad_args")
        result = cli_scan.cmd_sessions(["--harness", "gemini"], None)
        self.assertEqual(list(result)[0], "ok")
        self.assertEqual((result["harness"], result["counts"]), ("gemini", {"gemini": 0}))
        with self.assertRaises(ApError):
            cli_scan.cmd_usage(["x"], None)
        with self.assertRaises(ApError):
            cli_scan.cmd_agents(["--login", "--login"], None)
        self.assertEqual(list(cli_scan.cmd_usage([], None))[:2], ["ok", "nowMs"])


# --- Cursor Agent and Pi ------------------------------------------------------------

class CursorSessionTests(ScanCase):
    def setUp(self):
        super().setUp()
        self.config = os.path.join(self.home, ".config", "cursor")
        self.patch(harness, "agent_env", lambda name, level_id: {
            "HOME": self.home, "PATH": "/usr/bin:/bin", "XDG_CONFIG_HOME": os.path.join(self.home, ".config")})
        self.labels = {}
        self.patch(jobs, "load_store",
                   lambda sd: {"jobs": [{"id": key, "label": value} for key, value in self.labels.items()]})
        self.work = self.mkdir("proj/app")

    def record(self, job_id, gen, harness_id="cursor", session_id=None, cwd=None, started=None):
        started = self.now - 600 if started is None else started
        record = {"schemaVersion": 1, "runId": "%s-g%d" % (job_id, gen), "jobId": job_id, "gen": gen,
                  "harness": harness_id, "level": "plan", "mode": "new", "scheduledFor": started,
                  "startedAt": started, "endedAt": started + 60, "lateSec": 0, "outcome": "done", "detail": None,
                  "exit": 0, "signal": None, "killedBy": None, "bytesDropped": 0, "stdoutBytes": 10,
                  "stderrBytes": 0, "logBytes": 10, "sessionId": session_id, "limit": None,
                  "cli": {"exec": "/opt/cursor-agent/cursor-agent", "changed": False, "from": None, "to": None},
                  "cwd": cwd or self.work, "action": {"status": "done", "fireAt": None, "reason": None}}
        sd = fsio.open_state(create=True)
        try:
            jobs.write_run_record(sd, record)
        finally:
            sd.close()
        return record

    def chat(self, chat_id, cwd=None, wal_age=None, dir_age=None):
        path = sessions.cursor_chat_dir(os.path.realpath(cwd or self.work), self.config, chat_id)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "store.db"), "wb") as handle:
            handle.write(b"SQLite format 3\x00" + b"blobEncryptionKey")
        if wal_age is not None:
            wal = os.path.join(path, "store.db-wal")
            with open(wal, "wb") as handle:
                handle.write(b"wal")
            os.utime(wal, (self.now - wal_age, self.now - wal_age))
        if dir_age is not None:
            os.utime(path, (self.now - dir_age, self.now - dir_age))
        return path

    def test_cursor_sessions_from_run_records_only(self):
        job_a, job_b, job_c = "a" * 16, "b" * 16, "c" * 16
        self.labels = {job_a: "Nightly docs"}
        self.chat(uid(1))
        self.assertEqual(sessions.list_sessions("cursor", self.now)["counts"], {"cursor": 0})
        self.chat(uid(3))
        self.chat(uid(4))
        self.record(job_a, 1, session_id=uid(1), started=self.now - 7200)
        self.record(job_a, 2, session_id=uid(1), started=self.now - 600)
        self.record(job_b, 1, session_id=uid(2))
        self.record(job_c, 1, harness_id="claude", session_id=uid(4))
        self.record(job_c, 2, session_id=None)
        self.record(job_c, 3, session_id="not-a-chat-id")
        result = sessions.list_sessions("cursor", self.now)
        rows = self.rows_for(result, "cursor")
        self.assertEqual([row["id"] for row in rows], [uid(1)])
        self.assertEqual((rows[0]["title"], rows[0]["cwd"], rows[0]["messages"], rows[0]["canFork"], rows[0]["path"]),
                         ("Nightly docs", self.work, None, False, None))
        self.assertEqual((result["counts"], result["errors"], result["needsCwd"]), ({"cursor": 1}, {"cursor": None}, []))
        self.labels = {}
        self.assertEqual(self.rows_for(sessions.list_sessions("cursor", self.now), "cursor")[0]["title"], "app")
        self.assertEqual(sessions.list_sessions(None, self.now)["counts"]["cursor"], 1)

    def test_cursor_chat_dir_stat_only(self):
        expected = "/home/u/.config/cursor/chats/%s/%s" % (hashlib.md5(b"/home/u/proj").hexdigest(), uid(1))
        self.assertEqual(sessions.cursor_chat_dir("/home/u/proj", "/home/u/.config/cursor", uid(1)), expected)
        link = os.path.join(self.home, "proj-link")
        os.symlink(self.work, link)
        chat = self.chat(uid(1), wal_age=120, dir_age=3600)
        os.chmod(os.path.join(chat, "store.db"), 0)
        self.record("a" * 16, 1, session_id=uid(1), cwd=link)
        opened = []
        real_open = os.open

        def recording_open(path, *args, **kwargs):
            opened.append(path if isinstance(path, int) else os.fsdecode(path))
            return real_open(path, *args, **kwargs)

        self.patch(os, "open", recording_open)
        rows = self.rows_for(sessions.list_sessions("cursor", self.now), "cursor")
        found = sessions.lookup_session("cursor", uid(1))
        setattr(os, "open", real_open)
        self.assertEqual([row["updatedAtMs"] // 1000 for row in rows], [self.now - 120])
        self.assertEqual((rows[0]["cwd"], found["cwd"], found["updatedAtMs"] // 1000), (link, link, self.now - 120))
        self.assertEqual([p for p in opened if isinstance(p, str) and ("store.db" in p or "chats" in p)], [])
        os.remove(os.path.join(chat, "store.db-wal"))
        os.utime(chat, (self.now - 3600, self.now - 3600))
        rows = self.rows_for(sessions.list_sessions("cursor", self.now), "cursor")
        self.assertEqual(rows[0]["updatedAtMs"] // 1000, self.now - 3600)
        os.chmod(os.path.join(chat, "store.db"), 0o600)

    def test_cursor_lookup_missing_dir(self):
        self.labels = {"a" * 16: "Docs"}
        chat = self.chat(uid(1))
        self.record("a" * 16, 1, session_id=uid(1))
        self.record("b" * 16, 1, session_id=uid(2))
        found = sessions.lookup_session("cursor", uid(1))
        self.assertEqual(sorted(found), ["cwd", "harness", "id", "title", "updatedAtMs"])
        self.assertEqual((found["harness"], found["id"], found["cwd"], found["title"]),
                         ("cursor", uid(1), self.work, "Docs"))
        for missing in (uid(2), uid(9), "not-a-uuid", None):
            self.assertIsNone(sessions.lookup_session("cursor", missing), missing)
        shutil.rmtree(chat)
        self.assertIsNone(sessions.lookup_session("cursor", uid(1)))
        with open(chat, "w") as handle:
            handle.write("not a folder")
        self.assertIsNone(sessions.lookup_session("cursor", uid(1)))


class PiSessionTests(ScanCase):
    STAMP = "2026-09-15T14:18:58.355Z"

    def pi_session(self, cwd, session_id, messages=(), stamp=None, age_s=60, extra=(), path=None):
        stamp = stamp or self.STAMP
        records = [{"type": "session", "version": 3, "id": session_id, "timestamp": stamp, "cwd": cwd},
                   {"type": "model_change", "id": "d20a35fd", "parentId": None, "timestamp": stamp,
                    "provider": "fakeok", "modelId": "fake-model"},
                   {"type": "thinking_level_change", "id": "e1", "parentId": "d20a35fd", "thinkingLevel": "off"}]
        records += list(extra)
        for n, content in enumerate(messages):
            records.append({"type": "message", "id": "m%d" % n, "parentId": None,
                            "message": {"role": "user", "content": content, "timestamp": self.now * 1000}})
        records.append({"type": "message", "id": "a1", "message": {
            "role": "assistant", "content": [{"type": "text", "text": "OK"}], "provider": "fakeok",
            "model": "fake-model", "stopReason": "stop"}})
        target = path or sessions.pi_session_path(cwd, stamp, session_id, self.home)
        return self.write(target, "\n".join(json.dumps(r) for r in records) + "\n", mtime=self.now - age_s)

    def lookup(self, session_id, path):
        return sessions.lookup_session("pi", session_id, session_path=path)

    def test_pi_sessions_dir_from_cwd(self):
        self.assertEqual(sessions.pi_session_dir("/tmp/claude-1000/-home-x/r7/proj", "/home/u"),
                         "/home/u/.pi/agent/sessions/--tmp-claude-1000--home-x-r7-proj--")
        sid = "01a0a56f-6db2-76b1-a858-8cc1c56c0a2f"
        self.assertEqual(sessions.pi_session_path("/tmp/claude-1000/-home-x/r7/proj", self.STAMP, sid, "/home/u"),
                         "/home/u/.pi/agent/sessions/--tmp-claude-1000--home-x-r7-proj--/"
                         "2026-09-15T14-18-58-355Z_%s.jsonl" % sid)
        self.assertEqual(sessions.pi_session_dir("/w/a:b\\c", "/h"), "/h/.pi/agent/sessions/--w-a-b-c--")
        for args in (("relative", self.STAMP, sid), ("/w", "yesterday", sid), ("/w", self.STAMP, "abc"),
                     ("/w", self.STAMP, sid.upper())):
            self.assertIsNone(sessions.pi_session_path(*args, "/home/u"), args)

        mine = self.pi_session("/w/proj", uid(1), ["Explain the build"], age_s=30)
        self.pi_session("/w/other", uid(2), ["Other project"])
        self.pi_session("/w-proj", uid(3), ["Look-alike folder"], stamp="2026-09-15T14:19:00.000Z")
        self.pi_session("/w/proj", uid(4), ["Too old"], stamp="2026-06-01T10:00:00.000Z", age_s=91 * DAY)
        wrong_name = os.path.join(sessions.pi_session_dir("/w/proj", self.home),
                                  "2026-09-15T14-20-00-000Z_%s.jsonl" % uid(9))
        self.pi_session("/w/proj", uid(5), ["Name does not match"], path=wrong_name)
        result = sessions.list_sessions("pi", self.now, cwd="/w/proj")
        rows = self.rows_for(result, "pi")
        self.assertEqual([row["id"] for row in rows], [uid(1)])
        self.assertEqual((rows[0]["path"], rows[0]["cwd"], rows[0]["canFork"], rows[0]["messages"], rows[0]["title"]),
                         (mine, "/w/proj", True, None, "Explain the build"))
        self.assertEqual((result["cwd"], result["needsCwd"], result["counts"], result["errors"]),
                         ("/w/proj", [], {"pi": 1}, {"pi": None}))

        for n in range(51):
            self.pi_session("/w/many", uid(100 + n), ["Session %d" % n], stamp="2026-09-15T10:%02d:00.000Z" % n,
                            age_s=n + 1)
        result = sessions.list_sessions("pi", self.now, cwd="/w/many")
        self.assertEqual((result["counts"]["pi"], result["truncated"]["pi"]), (50, True))
        self.assertEqual(self.rows_for(result, "pi")[0]["id"], uid(100))
        self.assertEqual(sessions.list_sessions("pi", self.now, cwd="/w/none")["counts"], {"pi": 0})

    def test_pi_titles_string_and_array_content(self):
        cases = [
            ([[{"type": "text", "text": "Summarise the README"}, {"type": "image", "data": "x"}]], (),
             "Summarise the README"),
            (["Plain string prompt"], (), "Plain string prompt"),
            ([[{"type": "text", "text": "<system-reminder>"}], "Second message"], (), "Second message"),
            ([], ({"type": "session_info", "id": "s1", "name": "autopilot-0123abcd"},), "autopilot-0123abcd"),
            ([], (), "proj"),
        ]
        for n, (messages, extra, title) in enumerate(cases):
            cwd = "/w/t%d/proj" % n
            self.pi_session(cwd, uid(n + 1), messages, extra=extra)
            rows = self.rows_for(sessions.list_sessions("pi", self.now, cwd=cwd), "pi")
            self.assertEqual([row["title"] for row in rows], [title], n)

    def test_pi_lookup_in_store_path_and_cwd_match(self):
        path = self.pi_session("/w/proj", uid(1), [[{"type": "text", "text": "Fix the tests"}]])
        found = self.lookup(uid(1), path)
        self.assertEqual(sorted(found), ["cwd", "harness", "id", "path", "title", "updatedAtMs"])
        self.assertEqual((found["harness"], found["id"], found["cwd"], found["title"], found["path"]),
                         ("pi", uid(1), "/w/proj", "Fix the tests", path))
        self.assertEqual(sessions.pi_header(path), {"id": uid(1), "timestamp": self.STAMP, "cwd": "/w/proj"})
        self.assertIsNone(sessions.lookup_session("pi", uid(1)))
        self.assertIsNone(self.lookup(uid(2), path))
        directory, name = os.path.split(path)
        for bad in ("relative/" + name, directory + "/../" + os.path.basename(directory) + "/" + name,
                    path + "\n", path + "x", None, 42):
            self.assertIsNone(self.lookup(uid(1), bad), bad)
        with open(path) as handle:
            body = handle.read()
        self.assertIsNone(self.lookup(uid(1), self.write("elsewhere/" + name, body)))
        link = os.path.join(directory, "2026-09-15T14-18-58-356Z_%s.jsonl" % uid(1))
        os.symlink(path, link)
        self.assertIsNone(self.lookup(uid(1), link))
        moved_dir = sessions.pi_session_dir("/w/other", self.home)
        os.makedirs(moved_dir)
        self.assertIsNone(self.lookup(uid(1), self.write(os.path.join(moved_dir, name), body)))
        header = {"type": "session", "version": 3, "id": uid(7), "timestamp": self.STAMP, "cwd": "/w/proj"}
        broken = self.write(os.path.join(directory, "2026-09-15T15-00-00-000Z_%s.jsonl" % uid(7)),
                            '{"type": "model_change"}\n' + json.dumps(header) + "\n")
        self.assertIsNone(self.lookup(uid(7), broken))
        self.assertIsNone(sessions.pi_header(broken))
        self.assertIsNone(sessions.pi_header("relative"))

    def test_pi_sessions_without_cwd_needs_cwd(self):
        self.pi_session("/w/proj", uid(1), ["Hello"])
        everything = sessions.list_sessions(None, self.now)
        self.assertEqual((everything["needsCwd"], everything["cwd"], everything["counts"]["pi"],
                          everything["errors"]["pi"], everything["truncated"]["pi"]), (["pi"], None, 0, None, False))
        self.assertEqual(sorted(everything["counts"]), sorted(SIX))
        self.assertEqual(sessions.list_sessions("pi", self.now)["needsCwd"], ["pi"])
        self.assertEqual(sessions.list_sessions("claude", self.now)["needsCwd"], [])
        with_cwd = sessions.list_sessions(None, self.now, cwd="/w/proj")
        self.assertEqual((with_cwd["needsCwd"], with_cwd["counts"]["pi"]), ([], 1))
        for bad in ("relative", "/w/\x00x", "/" + "a" * 2000):
            with self.assertRaises(ApError) as caught:
                sessions.list_sessions("pi", self.now, cwd=bad)
            self.assertEqual(caught.exception.code, "invalid_cwd")


# --- agents -----------------------------------------------------------------------

class AgentsTests(ScanCase):
    VERSIONS = {"claude": "2.1.270 (Claude Code)", "opencode": "1.18.30", "codex": "codex-cli 0.154.0",
                "gemini": "0.50.0", "cursor": "2026.09.10-fd3934a", "pi": "0.85.1"}

    def setUp(self):
        super().setUp()
        self.log = os.path.join(self.tmp, "cli.log")
        self.stub = os.path.join(self.tmp, "stub-cli")
        with open(self.stub, "w") as handle:
            handle.write(STUB_CLI)
        os.chmod(self.stub, 0o755)
        self.bundle = os.path.join(self.tmp, "gemini.js")
        with open(self.bundle, "w") as handle:
            handle.write("// bundle\n")
        self.modes = {}
        self.present = {"claude": "ok", "opencode": "ok", "codex": "ok", "gemini": "ok", "cursor": "not_found",
                        "pi": "not_found"}
        self.patch(identity, "discover_cli", self.fake_discover)
        self.patch(harness, "agent_env", self.fake_env)

    def fake_discover(self, name):
        state = self.present[name]
        if state == "not_found":
            return {"harness": name, "link": None, "real": None, "exec": [], "ok": False, "reason": "not_found"}
        exec_prefix = ["/usr/bin/python3", "-I", "-S", self.stub]
        if name == "gemini":
            exec_prefix.append(self.bundle)
        if state != "ok":
            return {"harness": name, "link": self.stub, "real": self.stub, "exec": [], "ok": False,
                    "reason": state}
        return {"harness": name, "link": self.stub, "real": self.stub, "exec": exec_prefix, "ok": True,
                "reason": None}

    def fake_env(self, name, level_id):
        self.assertEqual(level_id, "plan")
        return {"HOME": self.home, "PATH": "/usr/bin", "LANG": "C.UTF-8", "FAKE_CLI_LOG": self.log,
                "FAKE_CLI_NAME": name, "FAKE_CLI_VERSION": self.VERSIONS[name],
                "FAKE_CLI_MODE": self.modes.get(name, "")}

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def test_agents_discovery_and_versions(self):
        self.present.update(opencode="not_found", codex="shim")
        result = agents.list_agents(False)
        entries = result["agents"]
        self.assertFalse(result["checkedLogin"])
        self.assertEqual([e["harness"] for e in entries], list(consts.HARNESSES))
        by_name = {e["harness"]: e for e in entries}
        self.assertEqual(sorted(by_name["claude"]), ["armable", "available", "canFork", "cliName", "enabled",
                                                     "gated", "harness", "link", "loggedIn", "name", "real",
                                                     "reason", "triggers", "version"])
        self.assertEqual((by_name["claude"]["version"], by_name["claude"]["enabled"],
                          by_name["claude"]["loggedIn"], by_name["claude"]["reason"]),
                         ("2.1.270", True, None, None))
        self.assertEqual(by_name["claude"]["triggers"], ["now", "in", "at", "claude_5h_reset"])
        self.assertEqual((by_name["gemini"]["version"], by_name["gemini"]["canFork"],
                          by_name["gemini"]["triggers"][-1]), ("0.50.0", False, "gemini_daily_reset"))
        self.assertEqual((by_name["opencode"]["available"], by_name["opencode"]["reason"],
                          by_name["opencode"]["enabled"], by_name["opencode"]["link"]),
                         (False, "not_found", False, None))
        self.assertEqual((by_name["codex"]["available"], by_name["codex"]["reason"],
                          by_name["codex"]["enabled"], by_name["codex"]["version"]), (False, "shim", False, None))
        self.assertEqual(sorted(c[0] for c in self.calls()), ["claude", "gemini"])

        cache = os.path.join(self.home, ".local/state/omarchy", edition.STATE_DIR_NAME, "agents-cache.json")
        self.assertTrue(os.path.isfile(cache))
        self.assertEqual(os.stat(cache).st_mode & 0o777, 0o600)
        again = agents.list_agents(False)
        self.assertEqual(again, result)
        self.assertEqual(len(self.calls()), 2)

        self.VERSIONS = dict(self.VERSIONS, claude="2.2.0 (Claude Code)")
        os.utime(self.stub, (self.now + 5, self.now + 5))
        upgraded = {e["harness"]: e for e in agents.list_agents(False)["agents"]}
        self.assertEqual(upgraded["claude"]["version"], "2.2.0")
        self.assertEqual(len(self.calls()), 4)

        with open(cache, "w") as handle:
            handle.write("{\"schemaVersion\": 1, \"versions\": {\"claude\": {\"key\": \"x\"}}}")
        self.assertEqual({e["harness"]: e["version"] for e in agents.list_agents(False)["agents"]}["claude"],
                         "2.2.0")

    def test_agents_codex_login_states(self):
        self.present.update(claude="not_found", opencode="not_found", gemini="not_found")
        expected = {"logged_in": (True, True, None), "logged_out": (False, False, "not_logged_in"),
                    "": (None, False, None)}
        for mode, (logged_in, enabled, reason) in expected.items():
            self.modes["codex"] = mode
            codex = agents.list_agents(False)["agents"][2]
            self.assertEqual((codex["loggedIn"], codex["enabled"], codex["reason"], codex["version"]),
                             (logged_in, enabled, reason, "0.154.0"), mode)
            self.assertEqual(agents.codex_logged_in(["/usr/bin/python3", "-I", "-S", self.stub]), logged_in)
        self.modes["codex"] = "hang"
        self.patch(agents, "_LOGIN_DEADLINE_S", 1.0)
        started = time.monotonic()
        self.assertIsNone(agents.codex_logged_in(["/usr/bin/python3", "-I", "-S", self.stub]))
        self.assertLess(time.monotonic() - started, 8.0)
        self.assertEqual(sum(1 for c in self.calls() if c[1:] == ["login", "status"]), 7)

    def test_agents_login_flag_only_runs_claude_auth_with_flag(self):
        self.modes.update(claude="logged_in", codex="logged_out")
        plain = {e["harness"]: e for e in agents.list_agents(False)["agents"]}
        self.assertIsNone(plain["claude"]["loggedIn"])
        self.assertFalse(any(c[1:2] == ["auth"] for c in self.calls()))
        self.assertEqual(sum(1 for c in self.calls() if c[1:] == ["login", "status"]), 1)

        checked = agents.list_agents(True)
        self.assertTrue(checked["checkedLogin"])
        by_name = {e["harness"]: e for e in checked["agents"]}
        self.assertTrue(by_name["claude"]["loggedIn"])
        self.assertIsNone(by_name["opencode"]["loggedIn"])
        self.assertIsNone(by_name["gemini"]["loggedIn"])
        auth_calls = [c for c in self.calls() if c[1:2] == ["auth"]]
        self.assertEqual(auth_calls, [["claude", "auth", "status", "--json"]])
        self.assertEqual(sum(1 for c in self.calls() if c[1:] == ["login", "status"]), 2)

        self.modes["claude"] = "logged_out"
        self.assertFalse({e["harness"]: e for e in agents.list_agents(True)["agents"]}["claude"]["loggedIn"])

    def by_name(self, check_login=False):
        return {e["harness"]: e for e in agents.list_agents(check_login)["agents"]}

    def test_agents_six_entries_armable_gated(self):
        self.present.update(cursor="ok", pi="ok")
        self.modes["codex"] = "logged_in"
        self.patch(consts, "GATED_HARNESSES", ("cursor",))
        result = agents.list_agents(False)
        self.assertEqual([e["harness"] for e in result["agents"]], list(SIX))
        keys = ["armable", "available", "canFork", "cliName", "enabled", "gated", "harness", "link", "loggedIn",
                "name", "real", "reason", "triggers", "version"]
        self.assertTrue(all(sorted(e) == keys for e in result["agents"]))
        by_name = {e["harness"]: e for e in result["agents"]}
        cursor, pi = by_name["cursor"], by_name["pi"]
        self.assertEqual((cursor["name"], cursor["cliName"], cursor["gated"], cursor["enabled"], cursor["armable"],
                          cursor["reason"], cursor["canFork"], cursor["triggers"], cursor["loggedIn"]),
                         ("Cursor Agent", "cursor-agent", True, True, False, "gated", False, ["now", "in", "at"], None))
        self.assertEqual((pi["name"], pi["cliName"], pi["gated"], pi["armable"], pi["reason"], pi["canFork"],
                          pi["triggers"], pi["loggedIn"]),
                         ("Pi", "pi", False, True, None, True, ["now", "in", "at", "codex_window_reset"], None))
        self.assertEqual(by_name["opencode"]["triggers"], ["now", "in", "at", "zen_free_reset", "go_window_reset"])
        self.assertEqual((by_name["codex"]["armable"], by_name["claude"]["armable"]), (True, True))

        self.patch(consts, "GATED_HARNESSES", ())
        cursor = self.by_name()["cursor"]
        self.assertEqual((cursor["gated"], cursor["armable"], cursor["reason"]), (False, True, None))
        self.present["cursor"] = "not_found"
        cursor = self.by_name()["cursor"]
        self.assertEqual((cursor["available"], cursor["armable"], cursor["reason"], cursor["gated"]),
                         (False, False, "not_found", False))
        self.modes["codex"] = "logged_out"
        codex = self.by_name()["codex"]
        self.assertEqual((codex["enabled"], codex["armable"], codex["reason"]), (False, False, "not_logged_in"))

    def test_agents_cursor_status_keeps_only_auth_fields(self):
        self.present.update(cursor="ok")
        self.patch(consts, "GATED_HARNESSES", ("cursor",))
        stub = ["/usr/bin/python3", "-I", "-S", self.stub]
        self.modes["cursor"] = "logged_in"
        self.assertEqual(agents.cursor_status(stub), {"isAuthenticated": True, "status": "authenticated"})
        before = len(self.calls())
        self.assertIsNone(self.by_name()["cursor"]["loggedIn"])
        self.assertFalse(any(c[0] == "cursor" and c[1:2] == ["status"] for c in self.calls()[before:]))
        checked = agents.list_agents(True)
        cursor = {e["harness"]: e for e in checked["agents"]}["cursor"]
        self.assertEqual((cursor["loggedIn"], cursor["reason"]), (True, "gated"))
        text = json.dumps(checked)
        for secret in ("example.invalid", "userId", "firstName", "teamId"):
            self.assertNotIn(secret, text)
        cache = os.path.join(self.home, ".local/state/omarchy", edition.STATE_DIR_NAME, "agents-cache.json")
        with open(cache) as handle:
            self.assertNotIn("example.invalid", handle.read())
        for mode, logged_in, reason in (("logged_out", False, "not_logged_in"), ("partial", False, "not_logged_in"),
                                        ("garbage", None, "gated")):
            self.modes["cursor"] = mode
            entry = self.by_name(True)["cursor"]
            self.assertEqual((entry["loggedIn"], entry["reason"]), (logged_in, reason), mode)
        self.assertEqual(agents.cursor_status(stub), {"isAuthenticated": None, "status": None})

    def test_agents_version_patterns_cursor_pi(self):
        self.present.update(cursor="ok", pi="ok", claude="not_found", opencode="not_found", codex="not_found",
                            gemini="not_found")
        by_name = self.by_name()
        self.assertEqual((by_name["cursor"]["version"], by_name["pi"]["version"]), ("2026.09.10-fd3934a", "0.85.1"))
        self.assertEqual(agents.cached_version("cursor"), "2026.09.10-fd3934a")
        self.assertEqual(agents.cached_version("pi"), "0.85.1")
        self.assertIsNone(agents.cached_version("claude"))
        before = len(self.calls())
        self.assertEqual(agents.version_of("pi", self.fake_discover("pi"), 5), "0.85.1")
        self.assertEqual(len(self.calls()), before)
        stub = ["/usr/bin/python3", "-I", "-S", self.stub]
        for name, output, expected in (("cursor", "cursor-agent 2026.09.10-fd3934a", "2026.09.10-fd3934a"),
                                       ("cursor", "2026.9.10-fd3934a", None), ("cursor", "1.2.3", None),
                                       ("cursor", "2026.09.10-fd3934a0", None), ("pi", "pi v0.85.1", "0.85.1"),
                                       ("pi", "pi", None)):
            env = dict(self.fake_env(name, "plan"), FAKE_CLI_VERSION=output)
            self.assertEqual(agents._version(name, stub, env, 5), expected, (name, output))


if __name__ == "__main__":
    unittest.main()
