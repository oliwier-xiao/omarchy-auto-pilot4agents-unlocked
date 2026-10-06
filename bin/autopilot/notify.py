"""Desktop notifications from the runner, and the best-effort ping that refreshes the panel.

Every notification is a fixed template plus the job label, a local clock time, a duration or
a fixed reason sentence. No prompt text and no agent output ever reaches a notification.

The notification is sent over the session bus's socket (sessionbus), not by a program: a label is
typed by somebody or built from a folder name, and a program's arguments are readable by every
account on the computer.
"""

from . import bounded, consts, edition, fsio, sessionbus, settings, timeutil

TEMPLATES = {
    "done": ("Done", '"{label}" finished in {duration}.'),
    "failed": ("Failed", '"{label}" failed. {reason}'),
    "limit_rearmed": ("Limit hit", '"{label}" will retry at {time}.'),
    "limit_final": ("Limit hit", '"{label}" did not run. {reason}'),
    "transient_rearmed": ("Retrying", '"{label}" hit a temporary error and retries at {time}.'),
    "deferred": ("Deferred", '"{label}" moved to {time}. {reason}'),
    "busy_deferred": ("Session busy", '"{label}" waits because the session is in use. Next try at {time}.'),
    "busy_final": ("Session busy", '"{label}" did not run because the session stayed in use. Fork it from the panel.'),
    "missed": ("Missed", '"{label}" was due at {time}. Open {app} to run it.'),
    "interrupted": ("Interrupted", '"{label}" stopped before it finished.'),
    "paused": ("Paused", '"{label}" did not run. {reason}'),
    "needs_confirm": ("Check the job", '"{label}" changed since you armed it. Open {app} to confirm it.'),
    "skipped": ("Skipped", '"{label}" was skipped. {reason}'),
    "gave_up": ("Gave up", '"{label}" stopped retrying. {reason}'),
}

REASON_SENTENCES = {
    "auth": "The agent is not signed in.",
    "not_found": "The session no longer exists.",
    "untrusted": "The agent does not trust this folder yet.",
    "boundary_mismatch": "The agent did not start in the requested permission level.",
    "max_turns": "It reached the turn limit.",
    "budget": "It reached the budget limit.",
    "timeout": "It ran past its time limit.",
    "failed": "The agent exited with an error.",
    "cli_missing": "The agent command was not found.",
    "cli_untrusted": "The agent command failed a safety check.",
    "cwd_refused": "The working folder is not allowed: it has to exist, be yours, and be writable by you alone.",
    "weekly_exhausted": "The weekly limit is used up.",
    "window_later": "The 5-hour window ends later than expected.",
    "window_exhausted": "The new 5-hour window is already used up.",
    "horizon": "It waited longer than 8 days.",
    "overage": "Usage credits are in use, so it does not retry.",
    "stale_auth": "The agent reports a limit while usage is low. Sign in again.",
    "kill_switch": "The kill switch file is present.",
    "plugin_disabled": edition.DISPLAY_NAME + " is not enabled in the bar.",
    "plugin_identity": "The plugin folder does not match its manifest, or its path holds a space or another character a systemd unit cannot carry.",
    "digest_mismatch": "The job changed since it was armed.",
    "late": "The computer was off or asleep past the grace time.",
    "session_busy": "The session stayed in use.",
    "session_locked": "Another job was using the session.",
    "not_logged_in": "The agent is not signed in.",
    "limit_retries": "It gave up after 3 retries.",
    "transient_retries": "Temporary errors continued after 3 retries.",
    "defers": "It was deferred 4 times.",
    "interrupted": "It stopped before it finished.",
    "prompt_missing": "The prompt is no longer stored.",
    "paid_blocked": "It would have used paid usage, which is off for this job.",
    "overage_blocked": "Usage credits would have been used, so it waits for the reset.",
    "paid_defer": "Included usage is used up, so it waits for the reset.",
    "paid_exhausted": "Included usage is used up until after the 8-day limit, so it was skipped.",
    "limit_full": "The usage limit is used up, so it waits for the reset.",
    "zen_billing": "The OpenCode Zen balance or spending limit stopped it.",
    "limit_suspected": "The agent was waiting on a usage limit.",
    "stalled": "The agent produced no output.",
    "cursor_autorun_config": "Cursor is set to approve every tool.",
    "cursor_network_config": "Cursor's sandbox allows all network access.",
    "cursor_project_rules": "This folder has its own Cursor or Claude allow rules.",
    "harness_gated": "Cursor support is waiting for a one-time check.",
    "monthly_limit": "The monthly limit is used up, so it does not retry.",
    "cursor_sandbox": "Cursor's sandbox could not start, so nothing ran. Turn it off in cursor-agent.",
    "quota_final": "The provider reports no quota or balance left, so it does not retry.",
    "gemini_policy": "Gemini CLI would not apply the policy that keeps the job in its approval mode, so nothing ran.",
    "opencode_plugin_code": "This folder has OpenCode plugin code that would run at startup, so nothing ran.",
    "codex_project_config": "This folder has its own Codex settings that could reach past the sandbox, so nothing ran.",
    "gemini_project_config": "This folder has Gemini CLI settings or a .env that would run code or redirect it at startup, so nothing ran.",
    "opencode_zen_tools": "OpenCode's free Zen models need every tool on, which this permission level turns off, so nothing ran.",
}

SUCCESS_EVENTS = ("done", "deferred", "limit_rearmed", "transient_rearmed", "busy_deferred")
_NOTIFY_DEADLINE_S = 5.0
_PING_DEADLINE_S = 2.0
_CAP = 4096


def sanitize_label(label):
    """The label as a notification body may carry it. Notification servers render a subset of
    markup in the body (mako, Omarchy's, among them), so &, < and > are escaped, after truncation,
    so a cut never lands inside an entity: a label is typed by somebody, or built from a folder
    name, and neither is markup."""
    text = label if isinstance(label, str) else ""
    text = "".join(ch for ch in text if ch.isprintable()).replace('"', "'").strip()
    text = text[: consts.LABEL_MAX].rstrip() or "Job"
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _notify_setting():
    try:
        sd = fsio.open_state(create=False)
        if sd is None:
            return "all"
        with sd:
            value = settings.load(sd).get("notify", "all")
    except Exception:
        return "all"
    return value if value in ("all", "failures", "never") else "all"


def render(event, job, *, now, fire_at=None, duration_s=None, reason=None, cli_changed=False):
    """(summary, body) for event, or None for an unknown event."""
    if event not in TEMPLATES:
        return None
    summary, body = TEMPLATES[event]
    values = {
        "label": sanitize_label(job.get("label")),
        "time": timeutil.local_hm(int(fire_at), int(now)) if isinstance(fire_at, int) else "",
        "duration": timeutil.duration_text(max(0, int(duration_s))) if isinstance(duration_s, int) else "",
        "reason": REASON_SENTENCES.get(reason, ""),
        "app": edition.DISPLAY_NAME,
    }
    text = body.format(**values).rstrip()
    if cli_changed:
        name = consts.HARNESS_NAMES.get(job.get("harness"), "agent")
        text += " The " + name + " command changed since the job was armed."
    return summary, text


def send(event, job, *, now, fire_at=None, duration_s=None, reason=None, cli_changed=False):
    """Show one notification through the session bus. True when the notification server answered."""
    rendered = render(event, job, now=now, fire_at=fire_at, duration_s=duration_s, reason=reason,
                      cli_changed=cli_changed)
    if rendered is None:
        return False
    policy = _notify_setting()
    if policy == "never" or (policy == "failures" and event in SUCCESS_EVENTS):
        return False
    summary, body = rendered
    deadline = _NOTIFY_DEADLINE_S
    remaining = bounded.remaining_budget()
    if remaining is not None:
        deadline = min(deadline, remaining)
    if deadline <= 0:
        return False
    try:
        return sessionbus.notify(edition.NOTIFY_APP_NAME, summary, body, 6000, deadline_s=deadline)
    except Exception:
        return False


def ipc_ping():
    """Ask a running shell to refresh the job list now. Failure is silent; the panel also polls."""
    argv = [consts.TOOLS["qs"], "ipc", "-p", consts.OMARCHY_SHELL_DIR, "call", edition.SERVICE_IPC_TARGET,
            "changed"]
    try:
        bounded.run_bounded(argv, env=bounded.tool_env(), deadline_s=_PING_DEADLINE_S, stdout_cap=_CAP,
                            stderr_cap=_CAP)
    except Exception:
        pass
