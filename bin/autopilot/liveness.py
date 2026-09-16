"""Is the session a job is about to write into currently in use? (R0 D13)

Two writers on one transcript corrupt it, and Claude's print mode only refuses background
holders, not a conversation open in a terminal. So before resuming, the runner asks: Claude
through `claude agents --json`; the others by how recently their session store was updated.
Any failure answers "unknown" and the run proceeds; the per-session lock still applies.

Only resuming writes into an existing session. A fork reads the parent and writes a new one,
which is why "fork it instead" is the way out of a busy session.
"""

import json
import os
import stat

from . import bounded, consts, harness, sessions

_AGENTS_DEADLINE_S = 10.0
_AGENTS_CAP = 65536
_WALK_MAX = 4096
# The job's own previous run updates the session as it ends; that write is not someone else's.
_OWN_WRITE_SLACK_S = 5


def _contains_session(value, sid, budget):
    stack = [value]
    while stack:
        budget[0] -= 1
        if budget[0] < 0:
            return False
        item = stack.pop()
        if isinstance(item, dict):
            for key in ("sessionId", "session_id"):
                if item.get(key) == sid:
                    return True
            stack.extend(v for v in item.values() if isinstance(v, (dict, list)))
        elif isinstance(item, list):
            stack.extend(v for v in item if isinstance(v, (dict, list)))
    return False


def _own_write(job, updated_ms):
    last = (job.get("state") or {}).get("lastRun") or {}
    ended = last.get("endedAt")
    if not isinstance(ended, int) or isinstance(ended, bool):
        return False
    return updated_ms <= (ended + _OWN_WRITE_SLACK_S) * 1000


def session_busy(job, exec_prefix, now):
    """True when the job's session is live, False when it is not, None when that is unknown."""
    mode, sid = harness.effective_session(job)
    if mode != "resume" or not sid:
        return False
    name = job["harness"]
    try:
        if name == "claude":
            argv = list(exec_prefix) + ["agents", "--json"]
            res = bounded.run_bounded(argv, env=harness.agent_env(name, job["level"]), cwd=job["target"]["cwd"],
                                      deadline_s=_AGENTS_DEADLINE_S, stdout_cap=_AGENTS_CAP, stderr_cap=4096)
            if res.get("rc") != 0 or res.get("timedOut") or res.get("overflow") or res.get("error"):
                return None
            data = json.loads(res["stdout"].decode("utf-8", "replace"))
            return _contains_session(data, sid, [_WALK_MAX])
        if name == "pi":
            # Stat only: the session file is written as the agent works.
            path = harness.effective_session_path(job)
            if not harness.pi_session_path_ok(path):
                return None
            st = os.lstat(path)
            if not stat.S_ISREG(st.st_mode):
                return None
            found = {"updatedAtMs": int(st.st_mtime * 1000)}
        else:
            # Cursor: the lookup stats the chat folder and its store.db-wal, never the database.
            found = sessions.lookup_session(name, sid)
        if not found:
            return None
        updated = found.get("updatedAtMs")
        if not isinstance(updated, int) or isinstance(updated, bool):
            return None
        if _own_write(job, updated):
            return False
        return now * 1000 - updated < consts.LIVENESS_WINDOW_S * 1000
    except Exception:
        return None
