"""Session scanners for the picker, lookups for job targets, and Claude transcript tails.

Everything here reads other programs' stores, so every read is bounded before it is
allocated and nothing is followed that the user did not put there as a plain file:

  - directories are walked with scandir and a symlinked entry is never entered;
  - files are opened O_NOFOLLOW | O_NONBLOCK and the descriptor is fstat-checked
    (regular, owned by this user) before one byte is read;
  - Claude transcripts are read from the head only (40 records, 64 KiB), because `cwd`
    first shows up a few records in, and from a 256 KiB tail for limit checks;
  - SQLite stores go through sqlite_ro (read-only URI, size ceiling, deadline);
  - a whole scan shares one wall-clock deadline and one byte budget, and each harness
    lists at most 50 sessions touched in the last 90 days;
  - Pi sessions are listed only from the one folder Pi derives from a working folder, and a
    Pi lookup accepts only an absolute path inside ~/.pi/agent/sessions whose header matches;
  - Cursor chats are listed only when an Auto Pilot run record created them, and their store
    is never opened: the chat folder and its store.db-wal are looked at with lstat only.

Nothing read here is ever written anywhere; titles and folders go out in the helper
answer only, stripped of control characters and capped.
"""

import hashlib
import json
import os
import re
import sqlite3
import stat
import time

from . import consts, fsio, h5_v2, sqlite_ro
from .errors import ApError

_OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC

_SCAN_BYTES_MAX = 48 * 1024 * 1024       # cumulative bytes read by one scan
_LOOKUP_DEADLINE_S = 4.0                 # one lookup (job-create, liveness, tails)
_SMALL_TEXT_MAX = 4096                   # Gemini .project_root
_GEMINI_PROJECTS_MAX = 262144            # Gemini projects.json
_GEMINI_CHAT_MAX = 1024 * 1024           # Gemini chat .json parsed whole up to this size
_GEMINI_HEAD_BYTES = 65536               # larger chat files: sessionId from the head only
_GEMINI_MESSAGES_MAX = 256
_ROLLOUT_HEAD_BYTES = 262144             # Codex session_meta carries long instructions
_ROLLOUT_SCAN_ENTRIES = 2000
_GEMINI_SCAN_ENTRIES = 2000
_TEXT_SCAN_CHARS = 4096                  # user text looked at before cleaning
_ID_FIELD_CHARS = 80
_TITLE_FIELD_CHARS = 512
_DIR_FIELD_CHARS = consts.CWD_MAX_BYTES + 1

# C0/C1 controls, soft hyphen, zero-width and bidi formatting, line/paragraph separators,
# byte order mark and lone surrogates (which could not be encoded as UTF-8 on output).
_CONTROL_RE = re.compile("[%s]" % "".join((
    "\x00-\x1f\x7f-\x9f",
    chr(0x00AD), chr(0x200B), "-", chr(0x200F), chr(0x2028), "-", chr(0x202E),
    chr(0x2060), "-", chr(0x206F), chr(0xFEFF), chr(0xD800), "-", chr(0xDFFF))))
_SPACE_RE = re.compile(r"\s+")
_ELLIPSIS = chr(0x2026)
_GEMINI_CHAT_RE = re.compile(r"^session-[0-9A-Za-z:._T-]{1,64}-([0-9a-f]{8})\.(json|jsonl)$")
_GEMINI_SHORT_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_GEMINI_SID_RE = re.compile(rb'"sessionId"\s*:\s*"([0-9a-f-]{36})"')
_ROLLOUT_RE = re.compile(r"^rollout-[0-9A-Za-z:._T-]{0,64}-?"
                         r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$")
_DATE_PART_RE = re.compile(r"^[0-9]{2,4}$")
_QUOTA_STATUS_RE = re.compile(r"^[a-z_]{1,32}$")
_QUOTA_TYPE_RE = re.compile(r"^[a-z0-9_]{1,40}$")

ERROR_TOO_LARGE = "too_large"
ERROR_TIMEOUT = "timeout"
ERROR_UNREADABLE = "unreadable"


# --- text and value hygiene ------------------------------------------------------

def clean_text(value, limit):
    """Printable single-line text of at most limit characters ("" for non-strings)."""
    if not isinstance(value, str):
        return ""
    text = _CONTROL_RE.sub(" ", value[: max(limit * 4, _TEXT_SCAN_CHARS)])
    text = _SPACE_RE.sub(" ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + _ELLIPSIS
    return text


def _valid_cwd(value):
    """Return value when it is an absolute, printable path within CWD_MAX_BYTES."""
    if not isinstance(value, str) or not value.startswith("/") \
            or len(value) > consts.CWD_MAX_BYTES:
        return None
    if any(ord(ch) < 0x20 or 0x7f <= ord(ch) <= 0x9f for ch in value):
        return None
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError:
        return None
    return value if size <= consts.CWD_MAX_BYTES else None


def _valid_id(harness, value):
    if not isinstance(value, str):
        return False
    if harness == "opencode":
        return bool(consts.OPENCODE_ID_RE.match(value))
    return bool(consts.UUID_RE.match(value))


def _title(candidates, cwd):
    for candidate in candidates:
        text = clean_text(candidate, consts.TITLE_MAX)
        if text:
            return text
    return clean_text(os.path.basename(cwd.rstrip("/")) or cwd, consts.TITLE_MAX)


def _usable_prompt(text):
    stripped = text.strip() if isinstance(text, str) else ""
    return bool(stripped) and not stripped.startswith("<") and not stripped.startswith("Caveat:")


def _content_text(content):
    """Text of a message content that is a string or a list of text parts."""
    if isinstance(content, str):
        return content[:_TEXT_SCAN_CHARS]
    if isinstance(content, list):
        parts, size = [], 0
        for part in content[:64]:
            if isinstance(part, str):
                text = part
            elif isinstance(part, dict) and part.get("type") in (None, "text") \
                    and isinstance(part.get("text"), str):
                text = part["text"]
            else:
                continue
            parts.append(text)
            size += len(text)
            if size >= _TEXT_SCAN_CHARS:
                break
        return "".join(parts)[:_TEXT_SCAN_CHARS]
    return ""


def _loads_dict(raw):
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _epoch_ms(value):
    """Milliseconds from a time column holding ms (or, in old rows, seconds)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value <= 0:
        return None
    ms = int(value) if value >= 1e11 else int(value * 1000)
    return ms if ms <= consts.EPOCH_MAX * 1000 else None


def _row(harness, session_id, title, cwd, updated_ms, messages, path=None):
    return {"harness": harness, "id": session_id, "title": title, "cwd": cwd,
            "updatedAtMs": int(updated_ms), "messages": messages,
            "canFork": bool(h5_v2.const("CAN_FORK").get(harness, False)), "path": path}


def _lookup_row(row):
    return {"harness": row["harness"], "id": row["id"], "cwd": row["cwd"], "title": row["title"],
            "updatedAtMs": row["updatedAtMs"]}


# --- bounded file access ---------------------------------------------------------

class _Budget:
    """Wall-clock deadline plus a cumulative read allowance shared by one scan."""

    def __init__(self, seconds, max_bytes=_SCAN_BYTES_MAX):
        self.deadline = time.monotonic() + seconds
        self.bytes_left = max_bytes

    def remaining(self):
        return self.deadline - time.monotonic()

    def expired(self):
        return self.remaining() <= 0

    def take(self, nbytes):
        if nbytes > self.bytes_left:
            return False
        self.bytes_left -= nbytes
        return True


class _Cut(Exception):
    """The scan deadline or byte budget ran out."""


def _open_regular(path):
    """(fd, stat) for a regular file owned by this user, opened without following a link."""
    try:
        fd = os.open(path, _OPEN_FLAGS)
    except (OSError, ValueError):
        return None
    try:
        info = os.fstat(fd)
    except OSError:
        os.close(fd)
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        os.close(fd)
        return None
    return fd, info


def _pread(fd, offset, length):
    chunks, total = [], 0
    while total < length:
        chunk = os.pread(fd, min(65536, length - total), offset + total)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _read_span(path, budget, max_bytes, *, tail=False, whole_only=False):
    """Read up to max_bytes from the head (or tail) of a regular file.

    Returns (data, info, complete) or None; complete is True when data is the whole file.
    whole_only refuses a file larger than max_bytes instead of reading part of it.
    Raises _Cut when the scan deadline or byte budget is exhausted.
    """
    if budget.expired():
        raise _Cut()
    opened = _open_regular(path)
    if opened is None:
        return None
    fd, info = opened
    try:
        size = info.st_size
        if whole_only and size > max_bytes:
            return None
        want = min(size, max_bytes)
        if not budget.take(want):
            raise _Cut()
        offset = size - want if tail else 0
        try:
            data = _pread(fd, offset, want)
        except OSError:
            return None
    finally:
        os.close(fd)
    return data, info, offset == 0 and len(data) >= size


def _head_records(path, budget, max_records, max_bytes):
    """Up to max_records JSON objects from the first max_bytes of a JSONL file.

    Malformed or non-object lines count toward max_records and are skipped. A last line
    cut by the byte cap is dropped. Returns (records, info) or (None, None).
    """
    span = _read_span(path, budget, max_bytes)
    if span is None:
        return None, None
    data, info, complete = span
    lines = data.split(b"\n")
    if not complete:
        lines.pop()
    records, seen = [], 0
    for line in lines:
        if seen >= max_records:
            break
        line = line.strip()
        if not line:
            continue
        seen += 1
        record = _loads_dict(line)
        if record is not None:
            records.append(record)
    return records, info


def _scandir(path, budget, counter, cap):
    """(entries, cut) of a real directory; a symlinked path lists nothing.

    counter is a one-element list shared across one walk; cut is True when the entry cap
    or the deadline stopped the listing. Raises OSError when path cannot be read.
    """
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode):
        return [], False
    entries = []
    with os.scandir(path) as iterator:
        for entry in iterator:
            if counter[0] >= cap or budget.expired():
                return entries, True
            counter[0] += 1
            entries.append(entry)
    return entries, False


def _home_path(*parts):
    return os.path.join(fsio.home(), *parts)


# --- Claude Code -----------------------------------------------------------------

def _claude_project_dirs(budget, counter):
    """(paths, cut) of real project folders, most recently changed first.

    A folder's mtime moves when a transcript is created in it, so when the entry cap cuts
    a large tree the folders holding the newest sessions have already been listed.
    """
    entries, cut = _scandir(_home_path(".claude", "projects"), budget, counter,
                            consts.CLAUDE_SCAN_ENTRIES)
    dirs = []
    for entry in entries:
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISDIR(info.st_mode):
            dirs.append((info.st_mtime_ns, entry.path))
    dirs.sort(reverse=True)
    return [path for _mtime, path in dirs], cut


def _claude_transcripts(session_id, budget):
    """Paths of <project>/<session_id>.jsonl that are plain files, newest first.

    The same id can sit in more than one project folder; the newest copy is the one the
    CLI resumes. Reads still go through the O_NOFOLLOW descriptor checks.
    """
    project_dirs, _cut = _claude_project_dirs(budget, [0])
    found = []
    for directory in project_dirs:
        path = os.path.join(directory, session_id + ".jsonl")
        try:
            info = os.lstat(path)
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode):
            found.append((info.st_mtime_ns, path))
    found.sort(reverse=True)
    return [path for _mtime, path in found]


def _claude_meta(records):
    """(cwd, title) from the head records of a transcript, or None when unusable.

    cwd comes from the first record that carries one. Title: custom-title, then ai-title,
    then the first user text that is not a command caveat or a tag block, then the folder.
    A transcript whose first message is a sidechain, or with no message at all, is skipped.
    """
    cwd_value, have_cwd = None, False
    custom = ai_title = user_text = None
    saw_message = False
    for record in records:
        if not have_cwd and "cwd" in record:
            have_cwd, cwd_value = True, record.get("cwd")
        kind = record.get("type")
        if kind == "custom-title" and isinstance(record.get("customTitle"), str):
            custom = record["customTitle"]
        elif kind == "ai-title" and isinstance(record.get("aiTitle"), str):
            ai_title = record["aiTitle"]
        elif kind in ("user", "assistant"):
            if not saw_message:
                saw_message = True
                if record.get("isSidechain") is True:
                    return None
            if kind == "user" and user_text is None and record.get("isMeta") is not True:
                message = record.get("message")
                text = _content_text(message.get("content")) if isinstance(message, dict) else ""
                if _usable_prompt(text):
                    user_text = text
    cwd = _valid_cwd(cwd_value)
    if not saw_message or cwd is None:
        return None
    return cwd, _title((custom, ai_title, user_text), cwd)


def _claude_list(budget, cap, cutoff_s):
    counter = [0]
    project_dirs, truncated = _claude_project_dirs(budget, counter)
    candidates = []
    for directory in project_dirs:
        if truncated:
            break
        try:
            entries, truncated = _scandir(directory, budget, counter, consts.CLAUDE_SCAN_ENTRIES)
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if not name.endswith(".jsonl") or not consts.UUID_RE.match(name[:-6]):
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISREG(info.st_mode) and info.st_mtime >= cutoff_s:
                candidates.append((info.st_mtime_ns, entry.path, name[:-6]))
    candidates.sort(reverse=True)

    rows, seen = [], set()
    try:
        for _mtime, path, session_id in candidates:
            if session_id in seen:
                continue
            records, info = _head_records(path, budget, consts.CLAUDE_HEAD_RECORDS,
                                          consts.CLAUDE_HEAD_BYTES)
            if records is None or info.st_mtime < cutoff_s:
                continue
            meta = _claude_meta(records)
            if meta is None:
                continue
            seen.add(session_id)
            if len(rows) >= cap:
                truncated = True
                break
            rows.append(_row("claude", session_id, meta[1], meta[0],
                             info.st_mtime_ns // 1000000, None))
    except _Cut:
        truncated = True
    return rows, truncated


def _claude_lookup(session_id, budget):
    for path in _claude_transcripts(session_id, budget):
        records, info = _head_records(path, budget, consts.CLAUDE_HEAD_RECORDS,
                                      consts.CLAUDE_HEAD_BYTES)
        if records is None:
            continue
        meta = _claude_meta(records)
        if meta is not None:
            return {"harness": "claude", "id": session_id, "cwd": meta[0], "title": meta[1],
                    "updatedAtMs": info.st_mtime_ns // 1000000}
    return None


def _claude_tail_records(session_id):
    """Records from the last CLAUDE_TAIL_BYTES of a session transcript, oldest first."""
    if not _valid_id("claude", session_id):
        return None
    budget = _Budget(_LOOKUP_DEADLINE_S)
    try:
        span = None
        for path in _claude_transcripts(session_id, budget):
            span = _read_span(path, budget, consts.CLAUDE_TAIL_BYTES, tail=True)
            if span is not None:
                break
    except (_Cut, OSError):
        return None
    if span is None:
        return None
    data, _info, complete = span
    lines = data.split(b"\n")
    if not complete:
        lines.pop(0)
    records = []
    for line in lines:
        line = line.strip()
        if line:
            record = _loads_dict(line)
            if record is not None:
                records.append(record)
    return records


def claude_last_quota(session_id):
    """Quota details of the newest rate_limit entry in the transcript tail, or None.

    Returns {"status": str, "resetsAt": int|None, "rateLimitType": str|None}. An entry
    without quotaLimits (a server-side throttle) gives status "" and no reset.
    """
    records = _claude_tail_records(session_id)
    for record in reversed(records or []):
        if record.get("error") != "rate_limit" or record.get("isSidechain") is True:
            continue
        quota = record.get("quotaLimits")
        quota = quota if isinstance(quota, dict) else {}
        status = quota.get("status")
        status = status if isinstance(status, str) and _QUOTA_STATUS_RE.match(status) else ""
        resets = quota.get("resetsAt")
        if isinstance(resets, bool) or not isinstance(resets, (int, float)) or resets != resets \
                or not consts.EPOCH_MIN <= resets <= consts.EPOCH_MAX:
            resets = None
        else:
            resets = int(-(-resets // 1))
        kind = quota.get("rateLimitType")
        kind = kind if isinstance(kind, str) and _QUOTA_TYPE_RE.match(kind) else None
        return {"status": status, "resetsAt": resets, "rateLimitType": kind}
    return None


def claude_last_user_sha256(session_id):
    """sha256 hex of the newest human user message in the transcript tail, or None.

    Tool results, meta records and sidechain records are not human messages and are
    passed over. The text is hashed as stored (UTF-8).
    """
    records = _claude_tail_records(session_id)
    for record in reversed(records or []):
        if record.get("type") != "user" or record.get("isSidechain") is True \
                or record.get("isMeta") is True:
            continue
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            if any(isinstance(part, dict) and part.get("type") == "tool_result" for part in content):
                continue
            text = "".join(part["text"] for part in content
                           if isinstance(part, dict) and part.get("type") == "text"
                           and isinstance(part.get("text"), str))
        else:
            continue
        if text:
            try:
                return hashlib.sha256(text.encode("utf-8")).hexdigest()
            except UnicodeEncodeError:
                return None
    return None


# --- SQLite stores ---------------------------------------------------------------

def _open_store(path, budget):
    """(connection, None), or (None, error) where error is None for an absent store."""
    remaining = budget.remaining()
    if remaining <= 0:
        return None, ERROR_TIMEOUT
    try:
        return sqlite_ro.open_ro(path, max_bytes=consts.OPENCODE_DB_CEILING,
                                 deadline_s=min(consts.SQLITE_DEADLINE_S, remaining)), None
    except FileNotFoundError:
        return None, None
    except sqlite_ro.TooLarge:
        return None, ERROR_TOO_LARGE
    except ApError:
        return None, ERROR_UNREADABLE
    except (OSError, sqlite3.Error) as err:
        return None, ERROR_TIMEOUT if isinstance(err, sqlite3.Error) and sqlite_ro.interrupted(err) \
            else ERROR_UNREADABLE


def _sql_error(err):
    return ERROR_TIMEOUT if sqlite_ro.interrupted(err) else ERROR_UNREADABLE


def _columns(connection, table):
    return {row[0] for row in connection.execute(
        "SELECT name FROM pragma_table_info(?) LIMIT 256", (table,)) if isinstance(row[0], str)}


# --- OpenCode --------------------------------------------------------------------

_OPENCODE_REQUIRED = {"id", "directory", "time_updated", "parent_id"}


def _opencode_path():
    return _home_path(".local", "share", "opencode", "opencode.db")


def _opencode_select(columns):
    title = "substr(title, 1, %d)" % _TITLE_FIELD_CHARS if "title" in columns else "NULL"
    return ("SELECT substr(id, 1, %d), %s, substr(directory, 1, %d), time_updated FROM session "
            % (_ID_FIELD_CHARS, title, _DIR_FIELD_CHARS))


def _opencode_rows(rows, cutoff_ms):
    out = []
    for session_id, title, directory, updated in rows:
        cwd = _valid_cwd(directory)
        updated_ms = _epoch_ms(updated)
        if _valid_id("opencode", session_id) and cwd is not None and updated_ms is not None \
                and updated_ms >= cutoff_ms:
            out.append(_row("opencode", session_id, _title((title,), cwd), cwd, updated_ms, None))
    return out


def _opencode_list(budget, cap, cutoff_ms):
    connection, error = _open_store(_opencode_path(), budget)
    if connection is None:
        return [], error == ERROR_TIMEOUT, error
    try:
        columns = _columns(connection, "session")
        if not _OPENCODE_REQUIRED <= columns:
            return [], False, ERROR_UNREADABLE
        archived = " AND time_archived IS NULL" if "time_archived" in columns else ""
        sql = (_opencode_select(columns) + "WHERE parent_id IS NULL" + archived
               + " AND time_updated >= ? ORDER BY time_updated DESC LIMIT ?")
        rows = connection.execute(sql, (cutoff_ms, cap + 1)).fetchmany(cap + 1)
    except sqlite3.Error as err:
        error = _sql_error(err)
        return [], error == ERROR_TIMEOUT, error
    finally:
        connection.close()
    return _opencode_rows(rows, cutoff_ms)[:cap], len(rows) > cap, None


def _opencode_lookup(session_id, budget):
    connection, _error = _open_store(_opencode_path(), budget)
    if connection is None:
        return None
    try:
        columns = _columns(connection, "session")
        if not _OPENCODE_REQUIRED <= columns:
            return None
        rows = connection.execute(_opencode_select(columns)
                                  + "WHERE id = ? AND parent_id IS NULL LIMIT 1",
                                  (session_id,)).fetchmany(1)
    except sqlite3.Error:
        return None
    finally:
        connection.close()
    listed = _opencode_rows(rows, 0)
    return _lookup_row(listed[0]) if listed else None


# --- Codex -----------------------------------------------------------------------

def _codex_select(columns, spawn_edges):
    """SELECT over threads built only from a fixed column whitelist."""
    title_parts = ["NULLIF(substr(%s, 1, %d), '')" % (name, _TITLE_FIELD_CHARS)
                   for name in ("name", "title", "first_user_message", "preview") if name in columns]
    if len(title_parts) > 1:
        title = "COALESCE(%s)" % ", ".join(title_parts)
    else:
        title = title_parts[0] if title_parts else "NULL"
    updated = "updated_at_ms" if "updated_at_ms" in columns else "updated_at * 1000"
    where = []
    if "archived" in columns:
        where.append("archived = 0")
    if spawn_edges:
        where.append("id NOT IN (SELECT child_thread_id FROM thread_spawn_edges)")
    select = ("SELECT substr(id, 1, %d), %s, substr(cwd, 1, %d), %s FROM threads "
              % (_ID_FIELD_CHARS, title, _DIR_FIELD_CHARS, updated))
    return select, updated, where


def _codex_store(budget):
    """(connection, (columns, spawn_edges), None), or (None, None, error)."""
    connection, error = _open_store(_home_path(".codex", "state_5.sqlite"), budget)
    if connection is None:
        return None, None, error
    try:
        columns = _columns(connection, "threads")
        edges = "child_thread_id" in _columns(connection, "thread_spawn_edges")
    except sqlite3.Error as err:
        connection.close()
        return None, None, _sql_error(err)
    if not {"id", "cwd"} <= columns or not {"updated_at_ms", "updated_at"} & columns:
        connection.close()
        return None, None, ERROR_UNREADABLE
    return connection, (columns, edges), None


def _codex_rows(rows, cutoff_ms):
    out = []
    for session_id, title, cwd_value, updated in rows:
        cwd = _valid_cwd(cwd_value)
        updated_ms = _epoch_ms(updated)
        if _valid_id("codex", session_id) and cwd is not None and updated_ms is not None \
                and updated_ms >= cutoff_ms:
            out.append(_row("codex", session_id, _title((title,), cwd), cwd, updated_ms, None))
    return out


def _rollout_files(budget):
    """([(mtime_ns, path, uuid)], cut) for ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl.

    Date folders are walked newest first, so an entry cap drops the oldest rollouts.
    """
    counter = [0]
    found = []
    cut = [False]

    def listing(path):
        try:
            entries, was_cut = _scandir(path, budget, counter, _ROLLOUT_SCAN_ENTRIES)
        except OSError:
            return []
        cut[0] = cut[0] or was_cut
        return entries

    def dated(path):
        return sorted((entry for entry in listing(path) if _DATE_PART_RE.match(entry.name)
                       and entry.is_dir(follow_symlinks=False)),
                      key=lambda entry: entry.name, reverse=True)

    for year in dated(_home_path(".codex", "sessions")):
        for month in dated(year.path):
            for day in dated(month.path):
                if cut[0]:
                    return found, True
                for entry in listing(day.path):
                    match = _ROLLOUT_RE.match(entry.name)
                    if not match:
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if stat.S_ISREG(info.st_mode):
                        found.append((info.st_mtime_ns, entry.path, match.group(1)))
    return found, cut[0]


def _rollout_meta(path, session_id, budget):
    """(cwd, title, info) from a rollout head whose session_meta id matches, else None."""
    records, info = _head_records(path, budget, consts.CLAUDE_HEAD_RECORDS, _ROLLOUT_HEAD_BYTES)
    if records is None:
        return None
    cwd = title = None
    saw_meta = False
    for record in records:
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        kind = record.get("type")
        if kind == "session_meta" and not saw_meta:
            if payload.get("id") != session_id:
                return None
            saw_meta = True
            source = payload.get("source")
            if payload.get("parent_thread_id") or (isinstance(source, dict) and (
                    "subagent" in source or "sub_agent" in source)):
                return None
            cwd = _valid_cwd(payload.get("cwd"))
        elif title is None and kind == "event_msg" and payload.get("type") == "user_message":
            if _usable_prompt(payload.get("message")):
                title = payload.get("message")
        elif title is None and kind == "response_item" and payload.get("type") == "message" \
                and payload.get("role") == "user":
            text = _content_text(payload.get("content"))
            if _usable_prompt(text):
                title = text
    if not saw_meta or cwd is None:
        return None
    return cwd, _title((title,), cwd), info


def _codex_rollout_list(budget, cap, cutoff_s):
    files, truncated = _rollout_files(budget)
    files.sort(reverse=True)
    rows, seen = [], set()
    try:
        for mtime_ns, path, session_id in files:
            if mtime_ns / 1e9 < cutoff_s or session_id in seen:
                continue
            meta = _rollout_meta(path, session_id, budget)
            if meta is None:
                continue
            seen.add(session_id)
            if len(rows) >= cap:
                truncated = True
                break
            rows.append(_row("codex", session_id, meta[1], meta[0],
                             meta[2].st_mtime_ns // 1000000, None))
    except _Cut:
        truncated = True
    return rows, truncated


def _codex_list(budget, cap, cutoff_ms):
    """Threads from state_5.sqlite; rollout files when the index is absent, unreadable or empty."""
    connection, shape, error = _codex_store(budget)
    if connection is not None:
        columns, edges = shape
        select, updated, where = _codex_select(columns, edges)
        sql = (select + "WHERE " + " AND ".join(where + ["%s >= ?" % updated])
               + " ORDER BY %s DESC LIMIT ?" % updated)
        try:
            fetched = connection.execute(sql, (cutoff_ms, cap + 1)).fetchmany(cap + 1)
        except sqlite3.Error as err:
            fetched, error = [], _sql_error(err)
        finally:
            connection.close()
        rows = _codex_rows(fetched, cutoff_ms)
        if rows:
            return rows[:cap], len(fetched) > cap, None
    if error == ERROR_TIMEOUT:
        return [], True, error
    rows, truncated = _codex_rollout_list(budget, cap, cutoff_ms // 1000)
    return rows, truncated, None if rows else error


def _codex_lookup(session_id, budget):
    connection, shape, _error = _codex_store(budget)
    if connection is not None:
        columns, edges = shape
        select, _updated, _where = _codex_select(columns, edges)
        try:
            rows = connection.execute(select + "WHERE id = ? LIMIT 1", (session_id,)).fetchmany(1)
        except sqlite3.Error:
            rows = []
        finally:
            connection.close()
        listed = _codex_rows(rows, 0)
        if listed:
            return _lookup_row(listed[0])
    files, _cut = _rollout_files(budget)
    for _mtime, path, file_id in sorted(files, reverse=True):
        if file_id == session_id:
            meta = _rollout_meta(path, session_id, budget)
            if meta is not None:
                return {"harness": "codex", "id": session_id, "cwd": meta[0], "title": meta[1],
                        "updatedAtMs": meta[2].st_mtime_ns // 1000000}
    return None


# --- Gemini CLI ------------------------------------------------------------------

def _gemini_projects(budget):
    """short id -> project root from ~/.gemini/projects.json ({"projects": {path: short}})."""
    span = _read_span(_home_path(".gemini", "projects.json"), budget, _GEMINI_PROJECTS_MAX,
                      whole_only=True)
    document = _loads_dict(span[0]) if span is not None else None
    projects = document.get("projects") if document else None
    mapping = {}
    if isinstance(projects, dict):
        for path, short in list(projects.items())[:1024]:
            if isinstance(short, str) and _GEMINI_SHORT_RE.match(short) and _valid_cwd(path):
                mapping.setdefault(short, path)
    return mapping


def _gemini_dirs(budget, counter):
    """([(project_root, chats_path)], cut) for each Gemini project folder with a known root.

    The root comes from <short>/.project_root, else from projects.json.
    """
    entries, cut = _scandir(_home_path(".gemini", "tmp"), budget, counter, _GEMINI_SCAN_ENTRIES)
    projects = None
    out = []
    for entry in entries:
        if not _GEMINI_SHORT_RE.match(entry.name) or not entry.is_dir(follow_symlinks=False):
            continue
        root = None
        span = _read_span(os.path.join(entry.path, ".project_root"), budget, _SMALL_TEXT_MAX,
                          whole_only=True)
        if span is not None:
            try:
                root = _valid_cwd(span[0].decode("utf-8").strip())
            except UnicodeDecodeError:
                root = None
        if root is None:
            if projects is None:
                projects = _gemini_projects(budget)
            root = projects.get(entry.name)
        if root is not None:
            out.append((root, os.path.join(entry.path, "chats")))
    return out, cut


def _gemini_chat_files(chats, budget, counter):
    """([(mtime_ns, path, id8, extension)], cut) for session-*.json[l] directly under chats/."""
    try:
        entries, cut = _scandir(chats, budget, counter, _GEMINI_SCAN_ENTRIES)
    except OSError:
        return [], False
    out = []
    for entry in entries:
        match = _GEMINI_CHAT_RE.match(entry.name)
        if not match:
            continue
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode):
            out.append((info.st_mtime_ns, entry.path, match.group(1), match.group(2)))
    return out, cut


def _gemini_child(metadata):
    kind = metadata.get("kind")
    return bool(metadata.get("parentSessionId")) or metadata.get("isSubagent") is True \
        or (isinstance(kind, str) and kind.lower().startswith("subagent"))


def _gemini_first_user(messages):
    for message in messages[:_GEMINI_MESSAGES_MAX]:
        if isinstance(message, dict) and (message.get("type") or message.get("role")) == "user":
            text = _content_text(message.get("content"))
            if _usable_prompt(text):
                return text
    return None


def _gemini_chat(path, extension, id8, budget):
    """(session_id, title|None, messages|None, info) for one main chat file, or None."""
    if extension == "json":
        span = _read_span(path, budget, _GEMINI_CHAT_MAX, whole_only=True)
        if span is not None:
            data, info, _complete = span
            document = _loads_dict(data)
            if document is None or _gemini_child(document):
                return None
            session_id = document.get("sessionId")
            messages = document.get("messages")
            messages = messages if isinstance(messages, list) else []
            summary = document.get("summary")
            title = summary if isinstance(summary, str) and summary.strip() \
                else _gemini_first_user(messages)
            count = len(messages)
        else:
            span = _read_span(path, budget, _GEMINI_HEAD_BYTES)
            if span is None:
                return None
            data, info, _complete = span
            match = _GEMINI_SID_RE.search(data)
            session_id = match.group(1).decode("ascii") if match else None
            title = count = None
    else:
        records, info = _head_records(path, budget, consts.CLAUDE_HEAD_RECORDS,
                                      consts.CLAUDE_HEAD_BYTES)
        if records is None:
            return None
        metadata, messages = {}, []
        for record in records:
            if isinstance(record.get("$set"), dict):
                metadata.update(record["$set"])
            elif record.get("sessionId") and "projectHash" in record:
                metadata.update(record)
                if isinstance(record.get("messages"), list):
                    messages.extend(record["messages"][:_GEMINI_MESSAGES_MAX])
            elif record.get("type") and "id" in record:
                messages.append(record)
        if _gemini_child(metadata):
            return None
        session_id = metadata.get("sessionId")
        summary = metadata.get("summary")
        title = summary if isinstance(summary, str) and summary.strip() \
            else _gemini_first_user(messages)
        count = None
    if not _valid_id("gemini", session_id) or session_id[:8] != id8:
        return None
    return session_id, title, count, info


def _gemini_list(budget, cap, cutoff_s):
    counter = [0]
    rows, seen, candidates = [], set(), []
    truncated = False
    try:
        dirs, truncated = _gemini_dirs(budget, counter)
        for root, chats in dirs:
            if truncated:
                break
            files, truncated = _gemini_chat_files(chats, budget, counter)
            candidates.extend((mtime_ns, path, id8, extension, root)
                              for mtime_ns, path, id8, extension in files
                              if mtime_ns / 1e9 >= cutoff_s)
        candidates.sort(reverse=True)
        for _mtime, path, id8, extension, root in candidates:
            chat = _gemini_chat(path, extension, id8, budget)
            if chat is None or chat[0] in seen:
                continue
            session_id, title, count, info = chat
            seen.add(session_id)
            if len(rows) >= cap:
                truncated = True
                break
            rows.append(_row("gemini", session_id, _title((title,), root), root,
                             info.st_mtime_ns // 1000000, count))
    except _Cut:
        truncated = True
    return rows, truncated


def _gemini_lookup(session_id, budget):
    counter = [0]
    dirs, _cut = _gemini_dirs(budget, counter)
    for root, chats in dirs:
        files, _cut = _gemini_chat_files(chats, budget, counter)
        for _mtime, path, id8, extension in sorted(files, reverse=True):
            if id8 != session_id[:8]:
                continue
            chat = _gemini_chat(path, extension, id8, budget)
            if chat is not None and chat[0] == session_id:
                return {"harness": "gemini", "id": session_id, "cwd": root,
                        "title": _title((chat[1],), root),
                        "updatedAtMs": chat[3].st_mtime_ns // 1000000}
    return None


# --- Pi --------------------------------------------------------------------------

_PI_HEAD_RECORDS = 30
_PI_HEAD_BYTES = 262144
_PI_SCAN_ENTRIES = 2000
_PI_PATH_MAX_BYTES = 4096
_PI_TIMESTAMP_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,9})?"
                              r"(?:Z|[+-][0-9]{2}:[0-9]{2})?$")


def _pi_store(home):
    return os.path.join(home, h5_v2.const("PI_SESSIONS_REL"))


def pi_session_dir(cwd, home):
    """Folder Pi keeps a working folder's sessions in: --<cwd without leading /, [/\\:] as ->--."""
    relative = cwd[1:] if cwd.startswith("/") else cwd
    return os.path.join(_pi_store(home), "--" + re.sub(r"[/\\:]", "-", relative) + "--")


def pi_session_path(cwd, timestamp, session_id, home):
    """Session file Pi writes for a header: <dir>/<timestamp with [:.] as ->_<id>.jsonl.

    None when the folder, the timestamp or the id fails its grammar.
    """
    if _valid_cwd(cwd) is None or not isinstance(timestamp, str) or not _PI_TIMESTAMP_RE.match(timestamp) \
            or not _valid_id("pi", session_id):
        return None
    return os.path.join(pi_session_dir(cwd, home), re.sub(r"[:.]", "-", timestamp) + "_" + session_id + ".jsonl")


def _pi_header_of(records):
    """{"id", "timestamp", "cwd"} when the first record is a valid Pi session header."""
    if not records:
        return None
    first = records[0]
    if first.get("type") != "session" or not _valid_id("pi", first.get("id")):
        return None
    timestamp = first.get("timestamp")
    cwd = _valid_cwd(first.get("cwd"))
    if not isinstance(timestamp, str) or not _PI_TIMESTAMP_RE.match(timestamp) or cwd is None:
        return None
    return {"id": first["id"], "timestamp": timestamp, "cwd": cwd}


def pi_header(path):
    """Header of one Pi session file (first line only, bounded, no link followed), or None."""
    if not isinstance(path, str) or not path.startswith("/"):
        return None
    try:
        records, _info = _head_records(path, _Budget(_LOOKUP_DEADLINE_S), 1, _PI_HEAD_BYTES)
    except (_Cut, OSError):
        return None
    return _pi_header_of(records)


def _pi_title(records, cwd):
    """First user message (string or text blocks), then the session name, then the folder."""
    user_text = name = None
    for record in records[1:]:
        kind = record.get("type")
        if kind == "message" and user_text is None:
            message = record.get("message")
            if isinstance(message, dict) and message.get("role") == "user":
                text = _content_text(message.get("content"))
                if _usable_prompt(text):
                    user_text = text
        elif kind == "session_info" and name is None and isinstance(record.get("name"), str):
            name = record["name"]
    return _title((user_text, name), cwd)


def _pi_list(budget, cap, cutoff_s, cwd):
    counter = [0]
    entries, truncated = _scandir(pi_session_dir(cwd, fsio.home()), budget, counter, _PI_SCAN_ENTRIES)
    files = []
    for entry in entries:
        if not entry.name.endswith(".jsonl"):
            continue
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode) and info.st_mtime >= cutoff_s:
            files.append((info.st_mtime_ns, entry.path, entry.name))
    files.sort(reverse=True)
    if len(files) > cap:
        files, truncated = files[:cap], True
    rows, seen = [], set()
    try:
        for _mtime, path, name in files:
            records, info = _head_records(path, budget, _PI_HEAD_RECORDS, _PI_HEAD_BYTES)
            header = _pi_header_of(records)
            if header is None or header["cwd"] != cwd or header["id"] in seen \
                    or not name.endswith("_" + header["id"] + ".jsonl"):
                continue
            seen.add(header["id"])
            rows.append(_row("pi", header["id"], _pi_title(records, cwd), cwd,
                             info.st_mtime_ns // 1000000, None, path=path))
    except _Cut:
        truncated = True
    return rows, truncated


def _pi_path_ok(value):
    if not isinstance(value, str) or not value.startswith("/") or "\0" in value or "\n" in value \
            or os.path.normpath(value) != value:
        return False
    try:
        return len(value.encode("utf-8")) <= _PI_PATH_MAX_BYTES
    except UnicodeEncodeError:
        return False


def _pi_lookup(session_id, session_path, budget):
    """Row for a Pi session file inside the store whose header id is session_id, else None.

    The file must be a regular file (not a link), sit in the folder Pi derives from the cwd in
    its own header, and be named after that id.
    """
    if not _pi_path_ok(session_path):
        return None
    home = fsio.home()
    store = os.path.realpath(_pi_store(home))
    real = os.path.realpath(session_path)
    if not real.startswith(store + os.sep):
        return None
    info = os.lstat(session_path)
    if not stat.S_ISREG(info.st_mode):
        return None
    records, info = _head_records(session_path, budget, _PI_HEAD_RECORDS, _PI_HEAD_BYTES)
    header = _pi_header_of(records)
    if header is None or header["id"] != session_id \
            or not os.path.basename(real).endswith("_" + session_id + ".jsonl") \
            or os.path.dirname(real) != os.path.realpath(pi_session_dir(header["cwd"], home)):
        return None
    return {"harness": "pi", "id": session_id, "cwd": header["cwd"], "title": _pi_title(records, header["cwd"]),
            "updatedAtMs": info.st_mtime_ns // 1000000, "path": session_path}


# --- Cursor Agent ----------------------------------------------------------------

_RUN_RECORD_NAME_RE = re.compile(r"^[0-9a-f]{16}-g[0-9]{1,6}\.json$")


def cursor_chat_dir(cwd_real, config_dir, chat_id):
    """<config>/chats/<md5 of the real workspace path>/<chat id> (Cursor's own layout)."""
    digest = hashlib.md5(cwd_real.encode("utf-8"), usedforsecurity=False).hexdigest()
    return os.path.join(config_dir, "chats", digest, chat_id)


def _cursor_config_dir():
    from . import paid  # paid pulls in harness, which this module must not load at import
    home = fsio.home()
    return paid.cursor_config_dir(paid.cursor_env(home))


def _run_records(sd, budget, since):
    """Run records newest first: jobs.list_run_records when present, else a bounded folder walk."""
    from . import jobs
    lister = getattr(jobs, "list_run_records", None)
    if callable(lister):
        return lister(sd, since=since, until=consts.EPOCH_MAX, cap=consts.RUNS_TOTAL)
    runs = sd.subdir(jobs.RUNS_DIR, create=False)
    if runs is None:
        return []
    records = []
    try:
        for name in reversed(runs.list_names()):
            if len(records) >= consts.RUNS_TOTAL:
                break
            if budget.expired():
                raise _Cut()
            if not _RUN_RECORD_NAME_RE.match(name):
                continue
            try:
                record = runs.read_json(name, consts.RUN_RECORD_MAX)
            except ApError:
                continue
            if isinstance(record, dict):
                records.append(record)
    finally:
        runs.close()
    return records


def _cursor_candidates(budget, since):
    """(records with harness cursor, {jobId: label}) from the state folder; ([], {}) without one."""
    from . import jobs
    sd = fsio.open_state(create=False)
    if sd is None:
        return [], {}
    try:
        records = [r for r in _run_records(sd, budget, since)
                   if isinstance(r, dict) and r.get("harness") == "cursor"]
        labels = {}
        try:
            store = jobs.load_store(sd)
            for job in store.get("jobs") or []:
                if isinstance(job, dict) and isinstance(job.get("id"), str):
                    labels[job["id"]] = job.get("label")
        except (ApError, AttributeError, TypeError):
            labels = {}
    finally:
        sd.close()
    records.sort(key=lambda r: r.get("startedAt") if isinstance(r.get("startedAt"), int) else 0, reverse=True)
    return records, labels


def _cursor_rows(records, labels, cutoff_ms, only_id=None):
    if not records:
        return []
    config_dir = _cursor_config_dir()
    rows, seen = [], set()
    for record in records:
        chat_id = record.get("sessionId")
        if not _valid_id("cursor", chat_id) or chat_id in seen or (only_id is not None and chat_id != only_id):
            continue
        cwd = _valid_cwd(record.get("cwd"))
        if cwd is None:
            continue
        seen.add(chat_id)
        chat = cursor_chat_dir(os.path.realpath(cwd), config_dir, chat_id)
        try:
            info = os.lstat(chat)
        except OSError:
            continue
        if not stat.S_ISDIR(info.st_mode):
            continue
        updated_ns = info.st_mtime_ns
        try:
            wal = os.lstat(os.path.join(chat, "store.db-wal"))
            if stat.S_ISREG(wal.st_mode):
                updated_ns = wal.st_mtime_ns
        except OSError:
            pass
        if updated_ns // 1000000 < cutoff_ms:
            continue
        rows.append(_row("cursor", chat_id, _title((labels.get(record.get("jobId")),), cwd), cwd,
                         updated_ns // 1000000, None))
    return rows


def _cursor_list(budget, cap, cutoff_ms):
    try:
        records, labels = _cursor_candidates(budget, cutoff_ms // 1000 - consts.RUNTIME_MAX)
    except ApError:
        return [], False, ERROR_UNREADABLE
    rows = _cursor_rows(records, labels, cutoff_ms)
    return rows[:cap], len(rows) > cap, None


def _cursor_lookup(session_id, budget):
    records, labels = _cursor_candidates(budget, 0)
    rows = _cursor_rows(records, labels, 0, only_id=session_id)
    return _lookup_row(rows[0]) if rows else None


# --- public API ------------------------------------------------------------------

def list_sessions(harness, now, *, cwd=None):
    """Sessions (3.12) without "ok": newest first per harness, at most 50 each, 90 days back.

    harness None lists every harness. A harness whose store is absent lists nothing and reports
    no error; a store that is refused, too large or too slow reports it in errors. Pi lists
    only the sessions of cwd, so without cwd it lists nothing and is named in needsCwd.
    """
    scope = consts.HARNESSES if harness is None else (harness,)
    if any(name not in consts.HARNESSES for name in scope):
        raise ApError("invalid_harness")
    if cwd is not None and _valid_cwd(cwd) is None:
        raise ApError("invalid_cwd", field="cwd")
    fsio.home()
    now = int(now)
    cap = consts.SESSIONS_PER_HARNESS
    cutoff_s = now - consts.SESSIONS_MAX_AGE_DAYS * 86400
    cutoff_ms = cutoff_s * 1000
    budget = _Budget(consts.SESSIONS_DEADLINE_S)

    sessions, counts, truncated, errors = [], {}, {}, {}
    for name in scope:
        rows, cut, error = [], False, None
        if budget.expired():
            cut, error = True, ERROR_TIMEOUT
        else:
            try:
                if name == "claude":
                    rows, cut = _claude_list(budget, cap, cutoff_s)
                elif name == "opencode":
                    rows, cut, error = _opencode_list(budget, cap, cutoff_ms)
                elif name == "codex":
                    rows, cut, error = _codex_list(budget, cap, cutoff_ms)
                elif name == "gemini":
                    rows, cut = _gemini_list(budget, cap, cutoff_s)
                elif name == "cursor":
                    rows, cut, error = _cursor_list(budget, cap, cutoff_ms)
                elif name == "pi" and cwd is not None:
                    rows, cut = _pi_list(budget, cap, cutoff_s, cwd)
            except _Cut:
                cut = True
            except FileNotFoundError:
                rows, cut = [], False
            except OSError:
                rows, cut, error = [], False, ERROR_UNREADABLE
            if error is None and cut and budget.expired():
                error = ERROR_TIMEOUT
        rows = sorted(rows, key=lambda row: row["updatedAtMs"], reverse=True)[:cap]
        sessions.extend(rows)
        counts[name] = len(rows)
        truncated[name] = bool(cut)
        errors[name] = error
    return {"harness": harness, "nowMs": now * 1000, "sessions": sessions, "counts": counts,
            "truncated": truncated, "errors": errors,
            "limitDays": consts.SESSIONS_MAX_AGE_DAYS, "perHarnessCap": cap,
            "cwd": cwd, "needsCwd": ["pi"] if "pi" in scope and cwd is None else []}


def lookup_session(harness, session_id, *, session_path=None):
    """{"harness","id","cwd","title","updatedAtMs"} for one session (Pi adds "path"), or None.

    The id grammar is checked before any path or query is built. An id that fails it, an
    unknown harness, a missing session and an unreadable store all give None. Pi needs
    session_path (an absolute path inside its session store); Cursor finds the run record that
    created the chat and checks that the chat folder still exists.
    """
    if harness not in consts.HARNESSES or not _valid_id(harness, session_id):
        return None
    fsio.home()
    budget = _Budget(_LOOKUP_DEADLINE_S)
    try:
        if harness == "claude":
            return _claude_lookup(session_id, budget)
        if harness == "opencode":
            return _opencode_lookup(session_id, budget)
        if harness == "codex":
            return _codex_lookup(session_id, budget)
        if harness == "gemini":
            return _gemini_lookup(session_id, budget)
        if harness == "cursor":
            return _cursor_lookup(session_id, budget)
        if harness == "pi":
            return _pi_lookup(session_id, session_path, budget)
        return None
    except (_Cut, OSError, ApError):
        return None
