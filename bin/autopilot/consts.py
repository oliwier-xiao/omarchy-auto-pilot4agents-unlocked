"""Caps, grammars, enums and absolute tool paths shared by every helper module."""
import re

from . import edition

HARNESSES = edition.HARNESS_IDS
HARNESS_NAMES = {"claude": "Claude Code", "opencode": "OpenCode", "codex": "Codex", "gemini": "Gemini CLI",
                 "cursor": "Cursor Agent", "pi": "Pi"}
CLI_NAMES = {"claude": "claude", "opencode": "opencode", "codex": "codex", "gemini": "gemini",
             "cursor": "cursor-agent", "pi": "pi"}
CAN_FORK = {"claude": True, "opencode": True, "codex": True, "gemini": False, "cursor": False, "pi": True}
# Harnesses that wait for a one-time live check before they can be armed (arm, run-now and the runner
# refuse them with harness_gated). Drafts and previews still work. Cursor's check passed on 2026-09-16
# (ask mode, composer-2.5, a folder Cursor already trusts): the prompt went on stdin, the run answered
# and the workspace was untouched, so nothing is gated today.
GATED_HARNESSES = ()
RESET_KINDS_FOR = {"claude": ("claude_5h_reset",), "opencode": ("zen_free_reset", "go_window_reset"),
                   "codex": ("codex_window_reset",), "gemini": ("gemini_daily_reset",),
                   "cursor": (), "pi": ("codex_window_reset",)}
# Reset kinds a stored job may still carry from an earlier release; drafts and arming refuse them.
LEGACY_RESET_KINDS = {"opencode": ("claude_5h_reset",)}
RESET_TRIGGER = {h: (RESET_KINDS_FOR[h][0] if RESET_KINDS_FOR[h] else None) for h in HARNESSES}
TRIGGER_KINDS = ("now", "in", "at", "claude_5h_reset", "codex_window_reset", "gemini_daily_reset",
                 "zen_free_reset", "go_window_reset")
RESET_KINDS = ("claude_5h_reset", "codex_window_reset", "gemini_daily_reset", "zen_free_reset", "go_window_reset")
TARGET_MODES = ("resume", "fork", "new")
WEEKLY_POLICIES = ("defer", "skip")

STATUSES = ("draft", "armed", "running", "done", "failed", "limit", "busy", "skipped", "missed",
            "interrupted", "paused", "needs_confirm", "disarmed", "gave_up")
CLOSED_STATUSES = ("done", "failed", "limit", "skipped", "gave_up")        # prompt deleted on entry
ATTENTION_STATUSES = ("missed", "busy", "interrupted", "paused", "needs_confirm")
ARMABLE_STATUSES = ("draft", "disarmed", "missed", "paused", "needs_confirm", "busy", "interrupted")
WAITS = (None, "reset", "limit", "transient", "busy", "deferred")
OUTCOMES = ("done", "limit", "transient", "auth", "not_found", "busy", "untrusted", "boundary_mismatch",
            "max_turns", "budget", "timeout", "failed", "interrupted")
HISTORY_EVENTS = ("created", "updated", "armed", "arm_failed", "rearmed", "deferred", "fired", "run_done",
                  "run_failed", "limit", "transient", "busy", "missed", "interrupted", "paused", "resumed",
                  "needs_confirm", "disarmed", "skipped", "gave_up", "rescheduled", "swapped", "shifted",
                  "cli_changed", "prompt_deleted", "late_result")
REASONS = ("auth", "not_found", "untrusted", "boundary_mismatch", "max_turns", "budget", "timeout", "failed",
           "cli_missing", "cli_untrusted", "cwd_refused", "weekly_exhausted", "window_later",
           "window_exhausted", "horizon", "overage", "stale_auth", "kill_switch", "plugin_disabled",
           "plugin_identity", "digest_mismatch", "late", "session_busy", "session_locked", "not_logged_in",
           "limit_retries", "transient_retries", "defers", "interrupted", "prompt_missing",
           "paid_blocked", "overage_blocked", "paid_defer", "paid_exhausted", "limit_full", "zen_billing",
           "limit_suspected", "stalled", "cursor_autorun_config", "cursor_network_config",
           "cursor_project_rules", "harness_gated", "monthly_limit", "quota_final", "cursor_sandbox")
LIMIT_KINDS = ("session", "weekly", "monthly", "daily", "billing_total", "billing_pool",
               "model_session", "model_weekly", "model_monthly", "other")
LIMIT_SOURCES = ("event", "transcript", "banner", "record", "backoff", "stderr", "retry_after", "computed")
BILLING_CLASSES = ("zen_free", "zen_paid", "go", "anthropic", "other", "unknown")

JOB_ID_RE = re.compile(r"^[0-9a-f]{16}$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
OPENCODE_ID_RE = re.compile(r"^ses_[A-Za-z0-9]{8,64}$")
MODEL_MAX = 128
MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,127}(?:\[[A-Za-z0-9._:/=,-]{1,96}\])?$")
MODEL_RE_V1 = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,79}$")    # legacy load only
PI_PROVIDER_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")               # usage record ids, computed ids
PI_TOOLS = ("read", "grep", "find", "ls")
PI_SESSIONS_REL = ".pi/agent/sessions"
CURSOR_WRITE_TOOLS = ("writeToolCall", "editToolCall", "deleteToolCall")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
HHMM_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
UNIT_RE = re.compile(r"^" + re.escape(edition.UNIT_PREFIX) + r"-([0-9a-f]{16})-g([0-9]{1,6})$")
UNIT_FILE_RE = re.compile(r"^" + re.escape(edition.UNIT_PREFIX) + r"-([0-9a-f]{16})-g([0-9]{1,6})\.(timer|service)$")
XDG_RUNTIME_RE = re.compile(r"^/run/user/[0-9]+$")

EPOCH_MIN, EPOCH_MAX = 1000000000, 4102444800
PROMPT_MAX_BYTES = 65536
STDIN_MAX_BYTES = 262144
STDIN_IDLE_S = 2.0
STDIN_TOTAL_S = 5.0
LOCK_WAIT_S = 5.0
RUNNER_LOCK_WAIT_S = 30.0
LABEL_MAX = 40
TITLE_MAX = 120
CWD_MAX_BYTES = 1024
JOBS_MAX = 200
# Sized for JOBS_MAX jobs at their largest (full history, long folder and title). job-create refuses
# past JOBS_CREATE_PERCENT of it, so the transitions of jobs already stored always have room.
JOBS_FILE_MAX = 1048576
JOBS_CREATE_PERCENT = 75
SETTINGS_FILE_MAX = 16384
RUN_RECORD_MAX = 65536
RUN_LOG_MAX = 262144
RUNS_PER_JOB = 20
RUNS_TOTAL = 500
HISTORY_MAX = 20
PROMPT_KEEP_OPEN_S = 86400          # attention/disarmed jobs keep the prompt this long (Decision)
CLOSED_RETENTION_S = 30 * 86400     # closed jobs pruned after this (Decision)
OUTPUT_CAP = {"list": 1048576, "job-get": 524288, "sessions": 921600, "settings-get": 16384,
              "settings-set": 16384, "copy-resume": 16384, "usage": 131072, "models": 262144,
              "timeline": 262144}
OUTPUT_CAP_DEFAULT = 65536
VERB_DEADLINE_S = {"edition": 2, "list": 4, "job-get": 4, "job-create": 8, "job-update": 25, "job-delete": 6,
                   "preview": 12, "arm": 40, "run-now": 40, "disarm": 25, "cancel-all": 90, "reschedule": 25,
                   "swap": 40, "shift": 85, "reconcile": 60, "settings-get": 2, "settings-set": 4,
                   "copy-resume": 3, "sessions": 8, "usage": 3, "agents": 30, "models": 25, "timeline": 4}

USAGE_RECORDS_MAX = 32
USAGE_RESET_HORIZON_S = 45 * 86400
USAGE_PERCENT_MAX = 10.0
LIMITS_HISTORY_MAX = 65536
LIMITS_HISTORY_KEEP_S = 14 * 86400
FIRST_EVENT_ZEN_S = 180
FIRST_EVENT_OTHER_S = 600
LIMIT_SUSPECTED_BACKOFF_S = 1800
ZEN_FREE_REARM_EXTRA_S = 120
TIMELINE_RANGE_MAX_S = 2 * 86400 + 3600
TIMELINE_BACK_S = 15 * 86400
TIMELINE_AHEAD_S = 9 * 86400

MAX_TURNS_MIN, MAX_TURNS_MAX = 1, 200
BUDGET_MIN, BUDGET_MAX, BUDGET_DEFAULT = 0.10, 100.00, 5.00
RUNTIME_MIN, RUNTIME_MAX, RUNTIME_DEFAULT = 300, 14400, 5400
RUNNER_DEADLINE_MARGIN_S = 60        # agent deadline = runtimeSec - 60
AGENT_TERM_GRACE_S = 10
MARGIN_MIN, MARGIN_MAX, MARGIN_DEFAULT = 60, 540, 120
DELAY_MIN, UI_MIN_LEAD_S, ARM_MIN_LEAD_S = 60, 60, 2
HORIZON_S = 8 * 86400
GRACE_TIME_S, GRACE_RESET_S = 900, 10800
MAX_DEFERS, MAX_LIMIT_RETRIES, MAX_TRANSIENT, MAX_BUSY_DEFERS = 4, 3, 3, 3
TRANSIENT_BACKOFF_S = (120, 240, 480)
LIMIT_BACKOFF_S = (1800, 3600, 7200)
BUSY_DEFER_S = 600
SESSION_LOCK_DEFER_S = 300
UNKNOWN_RETRY_S = 1800               # unclassified failure: one retry (R0 U3)
LIVENESS_WINDOW_S = 120
EXHAUSTED = 0.99
USAGE_STALE_S = 1800
SKEW_EXTRA_S = 120
SLIDING_TOLERANCE_S = 120
LINE_MAX_BYTES = 1048576
STDERR_CAP = 65536
TOOL_OUTPUT_CAP = 65536
STOP_POLL_S, STOP_POLL_RUNNING_S = 5.0, 15.0
# How long reconcile leaves the live service of a runner that just changed its job without a run
# record (deferred, paused, refused): its notification (5 s) and panel ping (2 s), with room.
RUNNER_EXIT_WINDOW_S = 15

USAGE_LIMITS_MAX = 8
USAGE_LABEL_MAX = 64
USAGE_RECORD_MAX = 65536
SHELL_JSON_MAX = 1048576
MANIFEST_MAX = 65536

SESSIONS_PER_HARNESS = 50
SESSIONS_MAX_AGE_DAYS = 90
CLAUDE_HEAD_RECORDS = 40
CLAUDE_HEAD_BYTES = 65536
CLAUDE_TAIL_BYTES = 262144
CLAUDE_SCAN_ENTRIES = 2000
OPENCODE_DB_CEILING = 8 * 1024 ** 3
SQLITE_DEADLINE_S = 3.0
SESSIONS_DEADLINE_S = 6.0

# Bounds of the shift verb (edition caps shiftMaxIds / shiftRangeSec).
SHIFT_MAX_IDS = 50
SHIFT_RANGE_S = 604800

# Absolute tool identities. Runtime code never reads overrides from env, argv or files;
# tests patch this dict in-process through tests/support/launch.py.
TOOLS = {
    "systemd_run": "/usr/bin/systemd-run",
    "systemctl": "/usr/bin/systemctl",
    "busctl": "/usr/bin/busctl",
    "qs": "/usr/bin/qs",
    "timedatectl": "/usr/bin/timedatectl",
    "node": "/usr/bin/node",
    "python3": "/usr/bin/python3",
}
# Owners a system tool of TOOLS may have: root only (fsio.check_tool). Tests replace this in-process.
TOOL_OWNER_UIDS = (0,)
OMARCHY_SHELL_DIR = "/usr/share/omarchy/shell"

# CLI discovery candidates, "~" expanded against HOME; first existing candidate wins (identity.discover_cli).
CLI_CANDIDATES = {
    "claude": ["~/.local/share/mise/installs/claude/latest/claude", "~/.local/bin/claude", "/usr/bin/claude"],
    "opencode": ["/usr/bin/opencode", "~/.opencode/bin/opencode", "~/.local/bin/opencode"],
    "codex": ["~/.local/share/mise/installs/codex/latest/bin/codex", "~/.local/bin/codex", "/usr/bin/codex"],
    "gemini": ["/usr/lib/node_modules/@google/gemini-cli/bundle/gemini.js"],
    "cursor": ["/usr/bin/cursor-agent", "~/.local/bin/agent"],
    "pi": ["~/.local/share/mise/installs/pi/latest/pi/pi"],
}
MISE_SHIMS_REL = ".local/share/mise/shims"
